from __future__ import annotations

import re
import unicodedata

from reeltranscode.analyzer import FFprobeAnalyzer
from reeltranscode.config import AppConfig
from reeltranscode.models import Decision, ExecutionPlan, MediaInfo, ValidationResult

START_TIME_TOLERANCE_SECONDS = 0.25


class OutputValidator:
    def __init__(self, config: AppConfig):
        self.config = config

    def validate(
        self,
        source: MediaInfo,
        output: MediaInfo,
        decision: Decision,
        plan: ExecutionPlan | None = None,
    ) -> ValidationResult:
        reasons: list[str] = []
        notes: list[str] = []

        if self.config.remux.preferred_container == "mp4":
            if FFprobeAnalyzer.is_container_apple_compatible(output) is False:
                reasons.append("Output container is not Apple-compatible")

        video_ok, video_reasons = FFprobeAnalyzer.is_video_apple_compatible(output, self.config.video.max_4k_fps)
        if not video_ok:
            reasons.extend(video_reasons)

        audio_ok, audio_reasons = FFprobeAnalyzer.is_audio_apple_compatible(output)
        if not audio_ok:
            reasons.extend(audio_reasons)

        source_dv = FFprobeAnalyzer.inspect_dolby_vision(source)
        output_dv = FFprobeAnalyzer.inspect_dolby_vision(output)

        if self.config.remux.preferred_container == "mp4":
            video = output.primary_video
            if video and (video.codec_name or "").lower() == "hevc":
                codec_tag = (video.codec_tag_string or "").lower()
                expected = self.config.video.hevc_tag.lower()
                accepted_tags = {expected}
                if output_dv.present:
                    accepted_tags.update({"dvh1", "dvhe"})
                if codec_tag not in accepted_tags:
                    accepted = ", ".join(sorted(accepted_tags))
                    reasons.append(
                        f"HEVC codec tag mismatch: expected one of [{accepted}], got {codec_tag or 'unknown'}"
                    )

        if self.config.validation.require_dv_preservation and source_dv.present:
            source_desc = _dv_description(source_dv)
            if not output_dv.present:
                brand_note = "; dby1 compatible brand ignored as non-proof" if output_dv.brand_hint else ""
                reasons.append(
                    "Dolby Vision lost: source proven "
                    f"via {source_desc} but output has no explicit Dolby Vision proof{brand_note}"
                )
            elif source_dv.profile and output_dv.profile and source_dv.profile != output_dv.profile:
                if _looks_like_dv_profile_8_1_ambiguity(source_dv.profile, output_dv.profile, source, output):
                    notes.append(
                        "Dolby Vision preserved despite ambiguous 8.1/8.0 reporting because HDR10-compatible signaling remained intact"
                    )
                else:
                    reasons.append(
                        "Dolby Vision profile changed: "
                        f"source={source_dv.profile}, output={output_dv.profile}"
                    )
            else:
                notes.append(f"Dolby Vision preserved via {_dv_description(output_dv)}")

        if decision.preserve_hdr10 and FFprobeAnalyzer.detect_hdr10(source) and not FFprobeAnalyzer.detect_hdr10(output):
            reasons.append("HDR10 signaling lost while HDR preservation is required by decision policy")

        if self.config.remux.preferred_container == "mp4":
            dropped_subtitle_streams = list(plan.dropped_subtitle_streams) if plan else []
            subtitle_reasons, subtitle_notes = self._validate_mp4_subtitles(
                source,
                output,
                dropped_subtitle_streams=dropped_subtitle_streams,
            )
            reasons.extend(subtitle_reasons)
            notes.extend(subtitle_notes)

        tolerance = self.config.validation.verify_duration_tolerance_seconds
        expected_output_duration = _expected_output_duration(source, tolerance)
        if output.duration is not None and expected_output_duration is not None:
            delta = abs(output.duration - expected_output_duration)
            if delta > tolerance:
                chapter_tail_duration = _source_chapter_tail_duration(source, tolerance)
                padded_audio_timeline = _source_video_duration_when_audio_tracks_align_to_container_padding(
                    source,
                    tolerance=tolerance,
                )
                if (
                    chapter_tail_duration is not None
                    and abs(output.duration - chapter_tail_duration) <= tolerance
                ):
                    notes.append(
                        "Accepted shorter output duration because source chapters end earlier than the "
                        "container-reported timeline"
                    )
                elif padded_audio_timeline is not None and abs(output.duration - padded_audio_timeline) <= tolerance:
                    notes.append(
                        "Accepted shorter output duration because the source video timeline ends before "
                        "container-padded audio tails"
                    )
                elif _source_has_audio_track_near_duration(source, output.duration, tolerance):
                    notes.append(
                        "Accepted output duration because the source already contained an audio track "
                        "extending to the same timeline"
                    )
                else:
                    reasons.append(f"Duration delta too high: {delta:.2f}s")

        reasons.extend(self._validate_video_timing(source, output))

        externalized_subtitles = len(plan.external_subtitle_outputs) if plan else 0
        dropped_subtitles = len(plan.dropped_subtitle_streams) if plan else 0
        expected_output_stream_count = max(0, len(source.streams) - externalized_subtitles - dropped_subtitles)
        stream_delta = abs(expected_output_stream_count - len(output.streams))
        if stream_delta > self.config.validation.verify_stream_count_delta_max:
            reasons.append(f"Unexpected stream count delta: {stream_delta}")

        return ValidationResult(ok=not reasons, reasons=reasons, notes=notes)

    def _validate_mp4_subtitles(
        self,
        source: MediaInfo,
        output: MediaInfo,
        *,
        dropped_subtitle_streams: list[int] | None = None,
    ) -> tuple[list[str], list[str]]:
        source_tracks = FFprobeAnalyzer.subtitle_track_states(source)
        output_tracks = FFprobeAnalyzer.subtitle_track_states(output)
        dropped = set(dropped_subtitle_streams or [])
        source_tracks = [track for index, track in enumerate(source_tracks) if index not in dropped]
        if not source_tracks:
            if dropped:
                return [], [f"Dropped {len(dropped)} incompatible image subtitle track(s) for Apple-native MP4 output"]
            return [], []

        reasons: list[str] = []
        notes: list[str] = []
        source_has_default_subtitle = any(track.default for track in source_tracks)
        output_default_indices = [index for index, track in enumerate(output_tracks) if track.default]
        allow_inferred_first_default = not source_has_default_subtitle and output_default_indices == [0]

        if len(output_tracks) != len(source_tracks):
            reasons.append(
                "Subtitle track count mismatch after MP4 conversion: "
                f"source={len(source_tracks)}, output={len(output_tracks)}"
            )
            return reasons, []

        for index, output_track in enumerate(output_tracks):
            codec = (output_track.codec_name or "").lower()
            if codec not in {"mov_text"}:
                reasons.append(
                    f"Subtitle track {index} is not Apple-native mov_text/tx3g: {output_track.codec_name or 'unknown'}"
                )

        for index, (source_track, output_track) in enumerate(zip(source_tracks, output_tracks, strict=True)):
            source_hi = source_track.hearing_impaired or source_track.captions or _title_implies_hi(source_track.title)
            output_hi = output_track.hearing_impaired or output_track.captions or _title_implies_hi(output_track.title)
            source_language = _normalize_subtitle_language(source_track.language)
            output_language = _normalize_subtitle_language(output_track.language)

            if source_language != output_language:
                reasons.append(
                    f"Subtitle track {index} language changed: "
                    f"source={source_language}, output={output_language}"
                )

            source_title = _normalize_subtitle_title(source_track.title, source_hi)
            output_title = _normalize_subtitle_title(output_track.title, output_hi)
            if not _subtitle_titles_equivalent(source_title, output_title):
                reasons.append(
                    f"Subtitle track {index} title changed: "
                    f"source={source_track.title or '-'}, output={output_track.title or '-'}"
                )

            if source_track.default != output_track.default:
                if allow_inferred_first_default and index == 0 and output_track.default and not source_track.default:
                    continue
                reasons.append(
                    f"Subtitle track {index} default flag changed: "
                    f"source={source_track.default}, output={output_track.default}"
                )
            if source_track.forced != output_track.forced:
                reasons.append(
                    f"Subtitle track {index} forced flag changed: "
                    f"source={source_track.forced}, output={output_track.forced}"
                )

            if source_hi != output_hi:
                reasons.append(
                    f"Subtitle track {index} hearing-impaired/captions marker changed: "
                    f"source={source_hi}, output={output_hi}"
                )

        if reasons:
            return reasons, []

        notes = [f"Subtitle validation passed: {len(output_tracks)} mov_text tracks preserved"]
        if allow_inferred_first_default:
            notes.append("MP4 mux inferred a default subtitle track because the source had none")
        if dropped:
            notes.append(f"Dropped {len(dropped)} incompatible image subtitle track(s) for Apple-native MP4 output")
        return [], notes

    def _validate_video_timing(self, source: MediaInfo, output: MediaInfo) -> list[str]:
        source_video = source.primary_video
        output_video = output.primary_video
        if source_video is None or output_video is None:
            return []

        reasons: list[str] = []
        tolerance = self.config.validation.verify_duration_tolerance_seconds
        chapter_tail_duration = _source_chapter_tail_duration(source, tolerance)
        padded_audio_timeline = _source_video_duration_when_audio_tracks_align_to_container_padding(
            source,
            tolerance=tolerance,
        )

        source_fps = _preferred_video_frame_rate(source, source_video)
        output_fps = _preferred_video_frame_rate(output, output_video)
        if source_fps is not None and output_fps is not None:
            fps_delta = abs(source_fps - output_fps)
            allowed_delta = max(0.05, source_fps * 0.005)
            if fps_delta > allowed_delta:
                reasons.append(
                    "Video frame rate changed unexpectedly: "
                    f"source={source_fps:.3f}fps, output={output_fps:.3f}fps"
                )

        source_video_duration = _preferred_stream_duration(source, source_video, "Video") or source.duration
        output_video_duration = _preferred_stream_duration(output, output_video, "Video") or output.duration
        if source_video_duration is not None and output_video_duration is not None:
            delta = abs(source_video_duration - output_video_duration)
            if delta > tolerance and not _matches_chapter_tail_timeline(
                source_duration=source_video_duration,
                output_duration=output_video_duration,
                chapter_tail_duration=chapter_tail_duration,
                tolerance=tolerance,
            ):
                reasons.append(
                    "Video duration changed unexpectedly: "
                    f"source={source_video_duration:.2f}s, output={output_video_duration:.2f}s"
                )

        if output.duration is not None and output_video_duration is not None:
            output_container_video_delta = abs(output.duration - output_video_duration)
            source_container_video_delta = None
            if source.duration is not None and source_video_duration is not None:
                source_container_video_delta = abs(source.duration - source_video_duration)

            if (
                source_container_video_delta is not None
                and source_container_video_delta > tolerance
                and abs(output_container_video_delta - source_container_video_delta) <= tolerance
            ):
                pass
            elif output_container_video_delta > tolerance:
                reasons.append(
                    "Output video duration does not match container duration: "
                    f"video={output_video_duration:.2f}s, container={output.duration:.2f}s"
                )

        for audio_index, audio_stream in enumerate(output.audio_streams):
            source_audio = source.audio_streams[audio_index] if audio_index < len(source.audio_streams) else None
            source_audio_durations = _source_audio_duration_candidates_for_track(
                source,
                source_video,
                source_audio,
                fallback_index=audio_index,
            )
            output_audio_duration = _preferred_stream_duration(output, audio_stream, "Audio", fallback_index=audio_index)
            source_audio_start = _preferred_stream_start_time(source, source_audio, "Audio", fallback_index=audio_index)
            source_offset = None
            if source_audio_start is not None and source_video.start_time is not None:
                source_offset = max(0.0, source_audio_start - source_video.start_time)

            if source_audio_durations and output_audio_duration is not None:
                allowed_output_durations = list(source_audio_durations)
                if source_offset is not None and source_offset > START_TIME_TOLERANCE_SECONDS:
                    allowed_output_durations.extend(duration + source_offset for duration in source_audio_durations)
                if padded_audio_timeline is not None:
                    allowed_output_durations.append(padded_audio_timeline)
                if min(abs(output_audio_duration - duration) for duration in allowed_output_durations) > tolerance and not _matches_chapter_tail_timeline(
                    source_duration=min(source_audio_durations),
                    output_duration=output_audio_duration,
                    chapter_tail_duration=chapter_tail_duration,
                    tolerance=tolerance,
                ):
                    reasons.append(
                        "Output audio duration changed unexpectedly: "
                        f"track={audio_index}, source={source_audio_duration:.2f}s, output={output_audio_duration:.2f}s"
                    )
                continue

            if output_audio_duration is None:
                continue

            source_audio_durations = _source_audio_duration_candidates(source, source_video)
            if source_audio_durations:
                if min(abs(output_audio_duration - duration) for duration in source_audio_durations) <= tolerance:
                    continue

            if output_video_duration is None:
                continue

            delta = abs(output_audio_duration - output_video_duration)
            if delta > tolerance:
                reasons.append(
                    "Output audio/video duration mismatch: "
                    f"track={audio_index}, video={output_video_duration:.2f}s, audio={output_audio_duration:.2f}s"
                )

        reasons.extend(self._validate_stream_sync(source, output))

        return reasons

    def _validate_stream_sync(self, source: MediaInfo, output: MediaInfo) -> list[str]:
        source_video = source.primary_video
        output_video = output.primary_video
        if source_video is None or output_video is None:
            return []

        reasons: list[str] = []
        source_video_start = _preferred_stream_start_time(source, source_video, "Video")
        output_video_start = _preferred_stream_start_time(output, output_video, "Video")
        normalized_output_video_start = output_video_start

        if source_video_start is not None and output_video_start is not None:
            if _looks_like_mp4_video_start_time_quirk(
                source,
                output,
                source_video,
                output_video,
                source_video_start=source_video_start,
                output_video_start=output_video_start,
                duration_tolerance=self.config.validation.verify_duration_tolerance_seconds,
            ):
                normalized_output_video_start = source_video_start
            else:
                delta = abs(output_video_start - source_video_start)
                if delta > START_TIME_TOLERANCE_SECONDS:
                    reasons.append(
                        "Video start time changed unexpectedly: "
                        f"source={source_video_start:.3f}s, output={output_video_start:.3f}s"
                    )

        for audio_index, output_audio in enumerate(output.audio_streams):
            output_audio_start = _preferred_stream_start_time(output, output_audio, "Audio", fallback_index=audio_index)
            if output_audio_start is None or normalized_output_video_start is None:
                continue

            source_audio = source.audio_streams[audio_index] if audio_index < len(source.audio_streams) else None
            source_audio_start = _preferred_stream_start_time(source, source_audio, "Audio", fallback_index=audio_index)

            if source_audio_start is not None and source_video_start is not None:
                source_offset = source_audio_start - source_video_start
                output_offset = output_audio_start - normalized_output_video_start
                delta = abs(output_offset - source_offset)
                if delta > START_TIME_TOLERANCE_SECONDS:
                    reasons.append(
                        "Output audio/video start offset changed unexpectedly: "
                        f"track={audio_index}, source={source_offset:.3f}s, output={output_offset:.3f}s"
                    )
                continue

            source_offsets = [
                offset
                for candidate_index, track in enumerate(source.audio_streams)
                if (candidate_start := _preferred_stream_start_time(source, track, "Audio", fallback_index=candidate_index))
                is not None
                and source_video_start is not None
                and (offset := candidate_start - source_video_start) is not None
            ]
            if source_offsets:
                output_offset = output_audio_start - normalized_output_video_start
                if min(abs(output_offset - offset) for offset in source_offsets) <= START_TIME_TOLERANCE_SECONDS:
                    continue

            delta = abs(output_audio_start - normalized_output_video_start)
            if delta > START_TIME_TOLERANCE_SECONDS:
                reasons.append(
                    "Output audio/video start time mismatch: "
                    f"track={audio_index}, video={normalized_output_video_start:.3f}s, audio={output_audio_start:.3f}s"
                )

        return reasons


def _dv_description(info) -> str:
    if info.profile:
        return f"{info.source or 'unknown'} (profile {info.profile})"
    return info.source or "unknown"


def _normalize_title(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"\s+", " ", value.strip()).casefold()
    return normalized or None


def _normalize_subtitle_title(value: str | None, hi_marker: bool) -> str | None:
    normalized = _normalize_title(value)
    if normalized is None or not hi_marker:
        return normalized
    normalized = re.sub(r"\b(?:sdh|hearing impaired|closed captions|cc)\b", "", normalized)
    normalized = re.sub(r"[\(\)\[\]\-_:]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized or None


def _title_implies_hi(value: str | None) -> bool:
    if not value:
        return False
    text = value.casefold()
    return any(token in text for token in ["sdh", "hearing impaired", "closed captions", "cc"])


def _normalize_subtitle_language(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    return normalized or "und"


def _subtitle_titles_equivalent(source_title: str | None, output_title: str | None) -> bool:
    if source_title == output_title:
        return True
    if source_title is None or output_title is None:
        return False
    if _replacement_char_matches(source_title, output_title):
        return True
    return _replacement_char_matches(_ascii_fold(source_title), _ascii_fold(output_title))


def _replacement_char_matches(left: str | None, right: str | None) -> bool:
    if left is None or right is None or len(left) != len(right):
        return False
    for left_char, right_char in zip(left, right, strict=True):
        if left_char == right_char:
            continue
        if "\ufffd" in {left_char, right_char}:
            continue
        return False
    return True


def _ascii_fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _preferred_stream_duration(
    media: MediaInfo,
    stream,
    track_type: str,
    *,
    fallback_index: int | None = None,
) -> float | None:
    mediainfo_track = _match_mediainfo_track(media, stream, track_type, fallback_index=fallback_index)
    mediainfo_duration = _mediainfo_seconds(mediainfo_track.get("Duration")) if mediainfo_track else None
    if mediainfo_duration is not None:
        return mediainfo_duration
    if stream is None:
        return None
    return stream.duration


def _preferred_stream_start_time(
    media: MediaInfo,
    stream,
    track_type: str,
    *,
    fallback_index: int | None = None,
) -> float | None:
    mediainfo_track = _match_mediainfo_track(media, stream, track_type, fallback_index=fallback_index)
    mediainfo_delay = _mediainfo_seconds(mediainfo_track.get("Delay")) if mediainfo_track else None
    if mediainfo_delay is not None:
        return mediainfo_delay
    if stream is None:
        return None
    return stream.start_time


def _accepted_source_audio_duration(
    media: MediaInfo,
    source_video,
    stream,
    *,
    fallback_index: int | None = None,
) -> float | None:
    duration = _preferred_stream_duration(media, stream, "Audio", fallback_index=fallback_index)
    if duration is None:
        return None
    video_duration = _preferred_stream_duration(media, source_video, "Video")
    accepted_duration = duration
    if (
        video_duration is not None
        and duration - video_duration > START_TIME_TOLERANCE_SECONDS
        and media.duration is not None
        and abs(duration - media.duration) <= START_TIME_TOLERANCE_SECONDS
        and _has_other_audio_track_near_video_duration(
            media,
            stream,
            video_duration=video_duration,
            tolerance=START_TIME_TOLERANCE_SECONDS,
        )
    ):
        accepted_duration = video_duration
    return accepted_duration


def _source_audio_duration_candidates_for_track(
    media: MediaInfo,
    source_video,
    stream,
    *,
    fallback_index: int | None = None,
) -> list[float]:
    candidates: list[float] = []
    accepted_duration = _accepted_source_audio_duration(
        media,
        source_video,
        stream,
        fallback_index=fallback_index,
    )
    raw_duration = _preferred_stream_duration(media, stream, "Audio", fallback_index=fallback_index)
    for duration in (accepted_duration, raw_duration):
        _append_unique_duration(candidates, duration)
    return candidates


def _source_audio_duration_candidates(media: MediaInfo, source_video) -> list[float]:
    candidates: list[float] = []
    for candidate_index, track in enumerate(media.audio_streams):
        for duration in _source_audio_duration_candidates_for_track(
            media,
            source_video,
            track,
            fallback_index=candidate_index,
        ):
            _append_unique_duration(candidates, duration)
    return candidates


def _append_unique_duration(target: list[float], value: float | None, *, epsilon: float = 0.001) -> None:
    if value is None:
        return
    if any(abs(existing - value) <= epsilon for existing in target):
        return
    target.append(value)


def _looks_like_mp4_video_start_time_quirk(
    source: MediaInfo,
    output: MediaInfo,
    source_video,
    output_video,
    *,
    source_video_start: float,
    output_video_start: float,
    duration_tolerance: float,
) -> bool:
    if "mp4" not in output.container_names and "mov" not in output.container_names:
        return False
    if abs(source_video_start) > START_TIME_TOLERANCE_SECONDS:
        return False
    if output_video_start <= START_TIME_TOLERANCE_SECONDS:
        return False

    source_video_duration = _preferred_stream_duration(source, source_video, "Video") or source.duration
    output_video_duration = _preferred_stream_duration(output, output_video, "Video") or output.duration
    if (
        source_video_duration is not None
        and output_video_duration is not None
        and abs(output_video_duration - source_video_duration) > duration_tolerance
    ):
        return False

    source_audio_starts = [
        start_time
        for index, track in enumerate(source.audio_streams)
        if (start_time := _preferred_stream_start_time(source, track, "Audio", fallback_index=index)) is not None
    ]
    output_audio_starts = [
        start_time
        for index, track in enumerate(output.audio_streams)
        if (start_time := _preferred_stream_start_time(output, track, "Audio", fallback_index=index)) is not None
    ]
    if not source_audio_starts or not output_audio_starts:
        return False

    return all(
        min(abs(output_start - source_start) for source_start in source_audio_starts) <= START_TIME_TOLERANCE_SECONDS
        for output_start in output_audio_starts
    )


def _looks_like_dv_profile_8_1_ambiguity(
    source_profile: str,
    output_profile: str,
    source: MediaInfo,
    output: MediaInfo,
) -> bool:
    if source_profile != "8.1" or output_profile != "8.0":
        return False
    return FFprobeAnalyzer.detect_hdr10(source) and FFprobeAnalyzer.detect_hdr10(output)


def _has_other_audio_track_near_video_duration(
    media: MediaInfo,
    stream,
    *,
    video_duration: float,
    tolerance: float,
) -> bool:
    for candidate_index, candidate in enumerate(media.audio_streams):
        if candidate is stream:
            continue
        candidate_duration = _preferred_stream_duration(media, candidate, "Audio", fallback_index=candidate_index)
        if candidate_duration is None:
            continue
        if abs(candidate_duration - video_duration) <= tolerance:
            return True
    return False


def _source_video_duration_when_audio_tracks_align_to_container_padding(
    media: MediaInfo,
    *,
    tolerance: float,
) -> float | None:
    source_video = media.primary_video
    if source_video is None or media.duration is None:
        return None

    video_duration = _preferred_stream_duration(media, source_video, "Video")
    if video_duration is None or media.duration - video_duration <= tolerance:
        return None

    audio_durations: list[float] = []
    for candidate_index, candidate in enumerate(media.audio_streams):
        candidate_duration = _preferred_stream_duration(media, candidate, "Audio", fallback_index=candidate_index)
        if candidate_duration is None:
            return None
        if candidate_duration - video_duration <= tolerance:
            return None
        if abs(candidate_duration - media.duration) > tolerance:
            return None
        audio_durations.append(candidate_duration)

    return video_duration if audio_durations else None


def _match_mediainfo_track(
    media: MediaInfo,
    stream,
    track_type: str,
    *,
    fallback_index: int | None = None,
) -> dict:
    tracks = _mediainfo_tracks(media.raw_mediainfo, track_type)
    if not tracks:
        return {}
    if stream is not None:
        for track in tracks:
            stream_order = _mediainfo_track_int(track.get("StreamOrder"))
            if stream_order is not None and stream_order == stream.index:
                return track
        for track in tracks:
            track_id = _mediainfo_track_int(track.get("ID"))
            if track_id is not None and (track_id - 1) == stream.index:
                return track
    if fallback_index is not None and fallback_index < len(tracks):
        return tracks[fallback_index]
    return {}


def _mediainfo_tracks(raw_mediainfo: dict, track_type: str) -> list[dict]:
    if not raw_mediainfo:
        return []
    media_node = raw_mediainfo.get("media", {}) or {}
    tracks = media_node.get("track", []) or []
    return [track for track in tracks if str(track.get("@type")) == track_type]


def _mediainfo_track_int(value) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _mediainfo_seconds(value) -> float | None:
    if value in {None, ""}:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _frame_rate_to_float(value: str | None) -> float | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    if "/" in value:
        left, right = value.split("/", 1)
        try:
            denominator = float(right)
            if denominator == 0:
                return None
            return float(left) / denominator
        except ValueError:
            return None
    try:
        return float(value)
    except ValueError:
        return None


def _preferred_video_frame_rate(media: MediaInfo, stream) -> float | None:
    mediainfo_track = _match_mediainfo_track(media, stream, "Video")
    mediainfo_frame_rate = _frame_rate_to_float(mediainfo_track.get("FrameRate")) if mediainfo_track else None
    if mediainfo_frame_rate is not None:
        return mediainfo_frame_rate
    if stream is None:
        return None
    return _frame_rate_to_float(stream.avg_frame_rate or stream.r_frame_rate)


def _expected_output_duration(source: MediaInfo, tolerance: float) -> float | None:
    source_video = source.primary_video
    source_video_duration = _preferred_stream_duration(source, source_video, "Video")
    chapter_tail_duration = _source_chapter_tail_duration(source, tolerance)
    if source.duration is not None:
        if source_video is None or source_video_duration is None:
            if chapter_tail_duration is not None:
                return chapter_tail_duration
            return source.duration
        if abs(source.duration - source_video_duration) > tolerance:
            if chapter_tail_duration is not None:
                return chapter_tail_duration
            source_audio_durations = [
                duration
                for index, track in enumerate(source.audio_streams)
                if (
                    duration := _accepted_source_audio_duration(
                        source,
                        source_video,
                        track,
                        fallback_index=index,
                    )
                )
                is not None
            ]
            if source_audio_durations and min(abs(duration - source.duration) for duration in source_audio_durations) <= tolerance:
                return source.duration
            return source_video_duration
    if chapter_tail_duration is not None:
        return chapter_tail_duration
    if source_video_duration is not None:
        return source_video_duration
    return source.duration


def _source_has_audio_track_near_duration(media: MediaInfo, duration: float, tolerance: float) -> bool:
    for candidate_index, track in enumerate(media.audio_streams):
        candidate_duration = _preferred_stream_duration(media, track, "Audio", fallback_index=candidate_index)
        if candidate_duration is None:
            continue
        if abs(candidate_duration - duration) <= tolerance:
            return True
    return False


def _matches_chapter_tail_timeline(
    *,
    source_duration: float,
    output_duration: float,
    chapter_tail_duration: float | None,
    tolerance: float,
) -> bool:
    if chapter_tail_duration is None:
        return False
    if source_duration - chapter_tail_duration <= tolerance:
        return False
    return abs(output_duration - chapter_tail_duration) <= tolerance


def _source_chapter_tail_duration(media: MediaInfo, tolerance: float) -> float | None:
    if media.duration is None:
        return None
    chapter_end_times = [
        chapter_end
        for chapter in (media.raw_probe.get("chapters") or [])
        if (chapter_end := _chapter_end_seconds(chapter)) is not None
    ]
    if not chapter_end_times:
        return None
    chapter_tail_duration = max(chapter_end_times)
    if media.duration - chapter_tail_duration <= tolerance:
        return None
    return chapter_tail_duration


def _chapter_end_seconds(raw_chapter: dict) -> float | None:
    for key in ("end_time", "end"):
        value = raw_chapter.get(key)
        if value in {None, ""}:
            continue
        try:
            return float(str(value).strip())
        except ValueError:
            continue
    return None
