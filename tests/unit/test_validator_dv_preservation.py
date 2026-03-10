from __future__ import annotations

from pathlib import Path

from reeltranscode.config import AppConfig
from reeltranscode.models import CaseLabel, Decision, ExecutionPlan, MediaInfo, StreamInfo, Strategy
from reeltranscode.validator import OutputValidator


def _media(
    path: Path,
    format_name: str,
    *,
    has_dv: bool,
    codec_tag: str | None,
    raw_mediainfo: dict | None = None,
) -> MediaInfo:
    side_data = []
    if has_dv:
        side_data.append(
            {
                "side_data_type": "DOVI configuration record",
                "dv_profile": 8,
                "dv_bl_signal_compatibility_id": 1,
            }
        )

    streams = [
        StreamInfo.from_probe(
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "codec_tag_string": codec_tag,
                "profile": "Main 10",
                "pix_fmt": "yuv420p10le",
                "width": 3840,
                "height": 1606,
                "avg_frame_rate": "24/1",
                "color_primaries": "bt2020",
                "color_transfer": "smpte2084",
                "color_space": "bt2020nc",
                "disposition": {"default": 1},
                "side_data_list": side_data,
            }
        ),
        StreamInfo.from_probe(
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "eac3",
                "channels": 8,
                "channel_layout": "7.1",
                "disposition": {"default": 1},
                "tags": {"language": "fra"},
            }
        ),
    ]
    return MediaInfo(
        path=path,
        format_name=format_name,
        duration=120.0,
        bit_rate=20_000_000,
        size=1_000_000_000,
        streams=streams,
        raw_probe={},
        raw_mediainfo=raw_mediainfo or {},
    )


def _decision() -> Decision:
    return Decision(
        strategy=Strategy.REMUX_ONLY,
        case_label=CaseLabel.B,
        reasons=["remux"],
        expected_container="mp4",
        expected_direct_play_safe=True,
        preserve_hdr10=True,
    )


def test_validator_rejects_output_when_dolby_vision_is_lost():
    cfg = AppConfig.from_dict({})
    validator = OutputValidator(cfg)
    source = _media(Path("/tmp/source.mkv"), "matroska,webm", has_dv=True, codec_tag=None)
    output = _media(
        Path("/tmp/output.mp4"),
        "mov,mp4,m4a,3gp,3g2,mj2",
        has_dv=False,
        codec_tag="hvc1",
        raw_mediainfo={
            "media": {
                "track": [
                    {"@type": "General", "CodecID_Compatible": "isom/dby1/iso2/mp41"},
                    {"@type": "Video", "HDR_Format": "SMPTE ST 2086", "CodecID": "hvc1"},
                ]
            }
        },
    )

    result = validator.validate(source, output, _decision())

    assert result.ok is False
    assert any("Dolby Vision lost" in reason for reason in result.reasons)
    assert any("dby1 compatible brand ignored" in reason for reason in result.reasons)


def test_validator_accepts_output_when_dolby_vision_is_preserved():
    cfg = AppConfig.from_dict({})
    validator = OutputValidator(cfg)
    source = _media(Path("/tmp/source.mkv"), "matroska,webm", has_dv=True, codec_tag=None)
    output = _media(
        Path("/tmp/output.mp4"),
        "mov,mp4,m4a,3gp,3g2,mj2",
        has_dv=False,
        codec_tag="hvc1",
        raw_mediainfo={
            "media": {
                "track": [
                    {"@type": "General", "CodecID_Compatible": "isom/dby1/iso2/mp41"},
                    {
                        "@type": "Video",
                        "HDR_Format": "Dolby Vision / SMPTE ST 2086",
                        "HDR_Format_Profile": "dvhe.08",
                        "HDR_Format_Compatibility": "HDR10 / HDR10",
                        "CodecID": "hvc1",
                    },
                ]
            }
        },
    )

    result = validator.validate(source, output, _decision())

    assert result.ok is True
    assert any("Dolby Vision preserved via mediainfo" in note for note in result.notes)


def test_validator_validates_mp4_text_subtitles():
    cfg = AppConfig.from_dict({})
    validator = OutputValidator(cfg)
    source = _media(Path("/tmp/source.mkv"), "matroska,webm", has_dv=True, codec_tag=None)
    source.streams.append(
        StreamInfo.from_probe(
            {
                "index": 2,
                "codec_type": "subtitle",
                "codec_name": "subrip",
                "disposition": {"default": 1, "forced": 1},
                "tags": {"language": "fre", "title": "VFF Forced"},
            }
        )
    )
    source.streams.append(
        StreamInfo.from_probe(
            {
                "index": 3,
                "codec_type": "subtitle",
                "codec_name": "subrip",
                "disposition": {"default": 0, "forced": 0, "hearing_impaired": 1, "captions": 1},
                "tags": {"language": "eng", "title": "SDH"},
            }
        )
    )
    output = _media(
        Path("/tmp/output.mp4"),
        "mov,mp4,m4a,3gp,3g2,mj2",
        has_dv=False,
        codec_tag="hvc1",
        raw_mediainfo={
            "media": {
                "track": [
                    {"@type": "General", "CodecID_Compatible": "isom/dby1/iso2/mp41"},
                    {
                        "@type": "Video",
                        "HDR_Format": "Dolby Vision / SMPTE ST 2086",
                        "HDR_Format_Profile": "dvhe.08",
                        "HDR_Format_Compatibility": "HDR10 / HDR10",
                        "CodecID": "hvc1",
                    },
                    {
                        "@type": "Text",
                        "CodecID": "tx3g",
                        "Language": "fr",
                        "Title": "VFF Forced",
                        "Default": "Yes",
                        "Forced": "Yes",
                    },
                    {
                        "@type": "Text",
                        "CodecID": "tx3g",
                        "Language": "en",
                        "Title": "SDH",
                        "Default": "No",
                        "Forced": "No",
                        "ServiceKind": "HI",
                    },
                ]
            }
        },
    )
    output.streams.append(
        StreamInfo.from_probe(
            {
                "index": 2,
                "codec_type": "subtitle",
                "codec_name": "mov_text",
                "codec_tag_string": "tx3g",
                "disposition": {"default": 1, "forced": 1},
                "tags": {"language": "fre"},
            }
        )
    )
    output.streams.append(
        StreamInfo.from_probe(
            {
                "index": 3,
                "codec_type": "subtitle",
                "codec_name": "mov_text",
                "codec_tag_string": "tx3g",
                "disposition": {"default": 0, "forced": 0, "hearing_impaired": 1, "captions": 1},
                "tags": {"language": "eng"},
            }
        )
    )
    plan = ExecutionPlan(
        source_path=source.path,
        target_path=output.path,
        temp_path=output.path,
        workspace_dir=None,
        strategy=Strategy.REMUX_ONLY,
        case_label=CaseLabel.F,
        steps=[],
    )

    result = validator.validate(source, output, _decision(), plan=plan)

    assert result.ok is True
    assert any("Subtitle validation passed" in note for note in result.notes)


def test_validator_rejects_video_timing_mismatch_even_when_container_duration_matches():
    cfg = AppConfig.from_dict({})
    validator = OutputValidator(cfg)
    source = _media(Path("/tmp/source.mkv"), "matroska,webm", has_dv=True, codec_tag=None)
    source.streams[0].avg_frame_rate = "24000/1001"
    source.streams[0].duration = 8405.79
    source.duration = 8405.79

    output = _media(
        Path("/tmp/output.mp4"),
        "mov,mp4,m4a,3gp,3g2,mj2",
        has_dv=False,
        codec_tag="hvc1",
        raw_mediainfo={
            "media": {
                "track": [
                    {"@type": "General", "CodecID_Compatible": "isom/dby1/iso2/mp41"},
                    {
                        "@type": "Video",
                        "HDR_Format": "Dolby Vision / SMPTE ST 2086",
                        "HDR_Format_Profile": "dvhe.08",
                        "HDR_Format_Compatibility": "HDR10 / HDR10",
                        "CodecID": "hvc1",
                    },
                ]
            }
        },
    )
    output.streams[0].avg_frame_rate = "30/1"
    output.streams[0].duration = 6717.87
    output.streams[1].duration = 8405.76
    output.duration = 8405.79

    result = validator.validate(source, output, _decision())

    assert result.ok is False
    assert any("Video frame rate changed unexpectedly" in reason for reason in result.reasons)
    assert any("Video duration changed unexpectedly" in reason for reason in result.reasons)
    assert any("Output audio/video duration mismatch" in reason for reason in result.reasons)


def test_validator_rejects_audio_video_start_time_drift():
    cfg = AppConfig.from_dict({})
    validator = OutputValidator(cfg)
    source = _media(Path("/tmp/source.mkv"), "matroska,webm", has_dv=False, codec_tag=None)
    source.streams[0].start_time = 0.0
    source.streams[1].start_time = 0.0

    output = _media(
        Path("/tmp/output.mp4"),
        "mov,mp4,m4a,3gp,3g2,mj2",
        has_dv=False,
        codec_tag="hvc1",
    )
    output.streams[0].start_time = 0.0
    output.streams[1].start_time = 0.6

    result = validator.validate(source, output, _decision())

    assert result.ok is False
    assert any("start offset changed unexpectedly" in reason for reason in result.reasons)


def test_validator_accepts_dropped_image_subtitles_when_plan_declares_them():
    cfg = AppConfig.from_dict({})
    validator = OutputValidator(cfg)
    source = _media(Path("/tmp/source.mkv"), "matroska,webm", has_dv=False, codec_tag=None)
    source.streams.append(
        StreamInfo.from_probe(
            {
                "index": 2,
                "codec_type": "subtitle",
                "codec_name": "hdmv_pgs_subtitle",
                "disposition": {"default": 0},
                "tags": {"language": "eng"},
            }
        )
    )
    output = _media(Path("/tmp/output.mp4"), "mov,mp4,m4a,3gp,3g2,mj2", has_dv=False, codec_tag="hvc1")
    plan = ExecutionPlan(
        source_path=source.path,
        target_path=output.path,
        temp_path=output.path,
        workspace_dir=None,
        strategy=Strategy.REMUX_ONLY,
        case_label=CaseLabel.B,
        steps=[],
        dropped_subtitle_streams=[0],
    )

    result = validator.validate(source, output, _decision(), plan=plan)

    assert result.ok is True
    assert any("Dropped 1 incompatible image subtitle track" in note for note in result.notes)


def test_validator_uses_source_stream_duration_tags_as_expected_timeline():
    cfg = AppConfig.from_dict({})
    validator = OutputValidator(cfg)
    source = _media(Path("/tmp/source.mkv"), "matroska,webm", has_dv=True, codec_tag=None)
    source.duration = 110.0
    source.streams[0] = StreamInfo.from_probe(
        {
            "index": 0,
            "codec_type": "video",
            "codec_name": "hevc",
            "profile": "Main 10",
            "codec_tag_string": None,
            "pix_fmt": "yuv420p10le",
            "width": 3840,
            "height": 1606,
            "avg_frame_rate": "24/1",
            "color_primaries": "bt2020",
            "color_transfer": "smpte2084",
            "color_space": "bt2020nc",
            "disposition": {"default": 1},
            "tags": {"DURATION": "00:01:40.000000000"},
            "side_data_list": [
                {
                    "side_data_type": "DOVI configuration record",
                    "dv_profile": 8,
                    "dv_bl_signal_compatibility_id": 1,
                }
            ],
        }
    )
    source.streams[1] = StreamInfo.from_probe(
        {
            "index": 1,
            "codec_type": "audio",
            "codec_name": "eac3",
            "channels": 8,
            "channel_layout": "7.1",
            "disposition": {"default": 1},
            "tags": {"language": "fra", "DURATION": "00:01:40.000000000"},
        }
    )

    output = _media(
        Path("/tmp/output.mp4"),
        "mov,mp4,m4a,3gp,3g2,mj2",
        has_dv=False,
        codec_tag="hvc1",
        raw_mediainfo={
            "media": {
                "track": [
                    {"@type": "General", "CodecID_Compatible": "isom/dby1/iso2/mp41"},
                    {
                        "@type": "Video",
                        "HDR_Format": "Dolby Vision / SMPTE ST 2086",
                        "HDR_Format_Profile": "dvhe.08",
                        "HDR_Format_Compatibility": "HDR10 / HDR10",
                        "CodecID": "hvc1",
                    },
                ]
            }
        },
    )
    output.streams[0].duration = 100.0
    output.streams[1].duration = 100.0
    output.duration = 100.0

    result = validator.validate(source, output, _decision())

    assert result.ok is True
    assert not any("Duration delta too high" in reason for reason in result.reasons)


def test_validator_accepts_hi_marker_preserved_via_sdh_title_suffix():
    cfg = AppConfig.from_dict({})
    validator = OutputValidator(cfg)
    source = _media(Path("/tmp/source.mkv"), "matroska,webm", has_dv=True, codec_tag=None)
    source.streams.append(
        StreamInfo.from_probe(
            {
                "index": 2,
                "codec_type": "subtitle",
                "codec_name": "subrip",
                "disposition": {"hearing_impaired": 1},
                "tags": {"language": "eng", "title": "Full"},
            }
        )
    )
    output = _media(
        Path("/tmp/output.mp4"),
        "mov,mp4,m4a,3gp,3g2,mj2",
        has_dv=False,
        codec_tag="hvc1",
        raw_mediainfo={
            "media": {
                "track": [
                    {"@type": "General", "CodecID_Compatible": "isom/dby1/iso2/mp41"},
                    {
                        "@type": "Video",
                        "HDR_Format": "Dolby Vision / SMPTE ST 2086",
                        "HDR_Format_Profile": "dvhe.08",
                        "HDR_Format_Compatibility": "HDR10 / HDR10",
                        "CodecID": "hvc1",
                    },
                    {
                        "@type": "Text",
                        "CodecID": "tx3g",
                        "Language": "en",
                        "Title": "Full SDH",
                        "Default": "No",
                        "Forced": "No",
                    },
                ]
            }
        },
    )
    output.streams.append(
        StreamInfo.from_probe(
            {
                "index": 2,
                "codec_type": "subtitle",
                "codec_name": "mov_text",
                "codec_tag_string": "tx3g",
                "disposition": {"default": 0},
                "tags": {"language": "eng", "title": "Full SDH"},
            }
        )
    )

    result = validator.validate(source, output, _decision())

    assert result.ok is True
