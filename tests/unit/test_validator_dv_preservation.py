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
    raw_probe: dict | None = None,
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
        raw_probe=raw_probe or {},
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


def test_validator_accepts_ambiguous_dv_8_1_report_when_hdr10_signaling_is_preserved():
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
                    {"@type": "General"},
                    {
                        "@type": "Video",
                        "HDR_Format": "Dolby Vision / SMPTE ST 2086",
                        "HDR_Format_Profile": "dvhe.08",
                        "CodecID": "hvc1",
                    },
                ]
            }
        },
    )

    result = validator.validate(source, output, _decision())

    assert result.ok is True
    assert not any("Dolby Vision profile changed" in reason for reason in result.reasons)


def test_validator_accepts_dvh1_tag_when_dolby_vision_is_preserved():
    cfg = AppConfig.from_dict({})
    validator = OutputValidator(cfg)
    source = _media(Path("/tmp/source.mkv"), "matroska,webm", has_dv=True, codec_tag=None)
    output = _media(
        Path("/tmp/output.mp4"),
        "mov,mp4,m4a,3gp,3g2,mj2",
        has_dv=False,
        codec_tag="dvh1",
        raw_mediainfo={
            "media": {
                "track": [
                    {"@type": "General", "CodecID_Compatible": "isom/dby1/iso2/mp41"},
                    {
                        "@type": "Video",
                        "HDR_Format": "Dolby Vision / SMPTE ST 2086",
                        "HDR_Format_Profile": "dvhe.08",
                        "HDR_Format_Compatibility": "HDR10 / HDR10",
                        "CodecID": "dvh1",
                    },
                ]
            }
        },
    )

    result = validator.validate(source, output, _decision())

    assert result.ok is True
    assert not any("HEVC codec tag mismatch" in reason for reason in result.reasons)


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


def test_validator_accepts_output_audio_duration_when_source_track_is_already_shorter():
    cfg = AppConfig.from_dict({})
    validator = OutputValidator(cfg)
    source = _media(Path("/tmp/source.mkv"), "matroska,webm", has_dv=False, codec_tag=None)
    source.streams[0].duration = 6254.916
    source.streams[1].duration = 6242.912
    source.duration = 6254.916

    output = _media(
        Path("/tmp/output.mp4"),
        "mov,mp4,m4a,3gp,3g2,mj2",
        has_dv=False,
        codec_tag="hvc1",
    )
    output.streams[0].duration = 6254.916
    output.streams[1].duration = 6242.912
    output.duration = 6254.916

    result = validator.validate(source, output, _decision())

    assert result.ok is True
    assert not any("Output audio/video duration mismatch" in reason for reason in result.reasons)
    assert not any("Output audio duration changed unexpectedly" in reason for reason in result.reasons)


def test_validator_accepts_preserved_source_container_video_duration_mismatch():
    cfg = AppConfig.from_dict({})
    validator = OutputValidator(cfg)
    source = _media(Path("/tmp/source.mkv"), "matroska,webm", has_dv=False, codec_tag=None)
    source.streams[0].duration = 7108.14
    source.streams[1].duration = 7113.45
    source.streams.append(
        StreamInfo.from_probe(
            {
                "index": 2,
                "codec_type": "audio",
                "codec_name": "aac",
                "channels": 2,
                "channel_layout": "stereo",
                "duration": "7113.45",
                "disposition": {"default": 0},
                "tags": {"language": "fra"},
            }
        )
    )
    source.duration = 7113.45

    output = _media(
        Path("/tmp/output.mp4"),
        "mov,mp4,m4a,3gp,3g2,mj2",
        has_dv=False,
        codec_tag="hvc1",
    )
    output.streams[0].duration = 7108.14
    output.streams[1].duration = 7113.45
    output.streams.append(
        StreamInfo.from_probe(
            {
                "index": 2,
                "codec_type": "audio",
                "codec_name": "aac",
                "codec_tag_string": "mp4a",
                "channels": 2,
                "channel_layout": "stereo",
                "duration": "7113.45",
                "disposition": {"default": 0},
                "tags": {"language": "fra"},
            }
        )
    )
    output.duration = 7113.45

    result = validator.validate(source, output, _decision())

    assert result.ok is True
    assert not any("Duration delta too high" in reason for reason in result.reasons)
    assert not any("Output video duration does not match container duration" in reason for reason in result.reasons)
    assert not any("Output audio/video duration mismatch" in reason for reason in result.reasons)


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


def test_validator_accepts_mp4_video_start_time_quirk_when_audio_starts_are_preserved():
    cfg = AppConfig.from_dict({})
    validator = OutputValidator(cfg)

    source = MediaInfo(
        path=Path("/tmp/source.mkv"),
        format_name="matroska,webm",
        duration=5292.121,
        bit_rate=7_900_000,
        size=5_255_045_243,
        streams=[
            StreamInfo.from_probe(
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "h264",
                    "codec_tag_string": None,
                    "pix_fmt": "yuv420p",
                    "duration": "5292.121000",
                    "start_time": "0.001000",
                    "disposition": {"default": 1},
                }
            ),
            StreamInfo.from_probe(
                {
                    "index": 1,
                    "codec_type": "audio",
                    "codec_name": "eac3",
                    "duration": "5282.144000",
                    "start_time": "0.000000",
                    "disposition": {"default": 1},
                    "tags": {"language": "fre"},
                }
            ),
            StreamInfo.from_probe(
                {
                    "index": 2,
                    "codec_type": "audio",
                    "codec_name": "eac3",
                    "duration": "5272.096000",
                    "start_time": "0.024000",
                    "disposition": {"default": 0},
                    "tags": {"language": "eng"},
                }
            ),
        ],
        raw_probe={},
        raw_mediainfo={},
    )

    output = MediaInfo(
        path=Path("/tmp/output.mp4"),
        format_name="mov,mp4,m4a,3gp,3g2,mj2",
        duration=5292.121,
        bit_rate=8_100_000,
        size=5_255_045_243,
        streams=[
            StreamInfo.from_probe(
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "h264",
                    "codec_tag_string": "avc1",
                    "pix_fmt": "yuv420p",
                    "duration": "5292.121000",
                    "start_time": "2.629000",
                    "disposition": {"default": 1},
                }
            ),
            StreamInfo.from_probe(
                {
                    "index": 1,
                    "codec_type": "audio",
                    "codec_name": "eac3",
                    "duration": "5282.144000",
                    "start_time": "0.000000",
                    "disposition": {"default": 1},
                    "tags": {"language": "fre"},
                }
            ),
            StreamInfo.from_probe(
                {
                    "index": 2,
                    "codec_type": "audio",
                    "codec_name": "eac3",
                    "duration": "5272.096000",
                    "start_time": "0.024000",
                    "disposition": {"default": 0},
                    "tags": {"language": "eng"},
                }
            ),
            StreamInfo.from_probe(
                {
                    "index": 3,
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "duration": "5282.144000",
                    "start_time": "0.000000",
                    "disposition": {"default": 0},
                    "tags": {"language": "fre"},
                }
            ),
        ],
        raw_probe={},
        raw_mediainfo={},
    )

    result = validator.validate(source, output, _decision())

    assert result.ok is True
    assert not any("Video start time changed unexpectedly" in reason for reason in result.reasons)
    assert not any("Output audio/video start offset changed unexpectedly" in reason for reason in result.reasons)


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


def test_validator_accepts_preserved_source_audio_delay_from_mediainfo():
    cfg = AppConfig.from_dict({})
    validator = OutputValidator(cfg)
    source = _media(
        Path("/tmp/source.mkv"),
        "matroska,webm",
        has_dv=False,
        codec_tag=None,
        raw_mediainfo={
            "media": {
                "track": [
                    {"@type": "General"},
                    {"@type": "Video", "Duration": "3609.857", "Delay": "0.000"},
                    {
                        "@type": "Audio",
                        "Duration": "3599.904",
                        "Delay": "9.984",
                        "Language": "fr",
                        "Title": "French EAC3 5.1",
                    },
                    {
                        "@type": "Audio",
                        "Duration": "3609.888",
                        "Delay": "0.000",
                        "Language": "en",
                        "Title": "English EAC3 5.1",
                    },
                ]
            }
        },
    )
    source.streams[0].duration = 3609.857
    source.streams[0].start_time = 0.0
    source.streams[1].duration = 3609.888
    source.streams[1].start_time = 0.0

    output = _media(
        Path("/tmp/output.mp4"),
        "mov,mp4,m4a,3gp,3g2,mj2",
        has_dv=False,
        codec_tag="hvc1",
    )
    output.streams[0].duration = 3609.857
    output.streams[0].start_time = 0.0
    output.streams[1].duration = 3599.904
    output.streams[1].start_time = 9.962
    output.duration = 3609.888

    result = validator.validate(source, output, _decision())

    assert result.ok is True
    assert not any("start offset changed unexpectedly" in reason for reason in result.reasons)
    assert not any("Output audio/video start time mismatch" in reason for reason in result.reasons)


def test_validator_accepts_subtitle_title_with_single_replacement_char():
    cfg = AppConfig.from_dict({})
    validator = OutputValidator(cfg)
    source = _media(Path("/tmp/source.mkv"), "matroska,webm", has_dv=False, codec_tag=None)
    source.streams.append(
        StreamInfo.from_probe(
            {
                "index": 2,
                "codec_type": "subtitle",
                "codec_name": "subrip",
                "disposition": {"default": 0, "forced": 0},
                "tags": {"language": "nor", "title": "Norwegian (Bokmål)"},
            }
        )
    )

    output = _media(Path("/tmp/output.mp4"), "mov,mp4,m4a,3gp,3g2,mj2", has_dv=False, codec_tag="hvc1")
    output.streams.append(
        StreamInfo.from_probe(
            {
                "index": 2,
                "codec_type": "subtitle",
                "codec_name": "mov_text",
                "codec_tag_string": "tx3g",
                "disposition": {"default": 0, "forced": 0},
                "tags": {"language": "nor", "title": "Norwegian (Bokm\ufffdl)"},
            }
        )
    )

    result = validator.validate(source, output, _decision())

    assert result.ok is True
    assert not any("title changed" in reason for reason in result.reasons)


def test_validator_accepts_audio_duration_when_positive_source_delay_is_flattened():
    cfg = AppConfig.from_dict({})
    validator = OutputValidator(cfg)
    source = _media(
        Path("/tmp/source.mkv"),
        "matroska,webm",
        has_dv=False,
        codec_tag=None,
        raw_mediainfo={
            "media": {
                "track": [
                    {"@type": "General"},
                    {"@type": "Video", "Duration": "3777.274", "Delay": "0.000"},
                    {"@type": "Audio", "Duration": "3766.272", "Delay": "12.990"},
                ]
            }
        },
    )
    source.streams[0].duration = 3777.274
    source.streams[0].start_time = 0.0
    source.streams[1].duration = 3766.272
    source.streams[1].start_time = 0.0

    output = _media(Path("/tmp/output.mp4"), "mov,mp4,m4a,3gp,3g2,mj2", has_dv=False, codec_tag="hvc1")
    output.streams[0].duration = 3777.274
    output.streams[0].start_time = 0.0
    output.streams[1].duration = 3779.262
    output.streams[1].start_time = 12.990
    output.duration = 3777.274

    result = validator.validate(source, output, _decision())

    assert result.ok is True
    assert not any("Output audio duration changed unexpectedly" in reason for reason in result.reasons)


def test_validator_uses_source_preferred_video_duration_when_container_runs_long():
    cfg = AppConfig.from_dict({})
    validator = OutputValidator(cfg)
    source = _media(
        Path("/tmp/source.mkv"),
        "matroska,webm",
        has_dv=False,
        codec_tag=None,
        raw_mediainfo={
            "media": {
                "track": [
                    {"@type": "General"},
                    {"@type": "Video", "StreamOrder": "0", "ID": "1", "Duration": "4155.110", "Delay": "0.005"},
                    {"@type": "Audio", "StreamOrder": "1", "ID": "2", "Duration": "4155.136", "Delay": "0.000", "Language": "fr"},
                    {"@type": "Audio", "StreamOrder": "2", "ID": "3", "Duration": "4155.136", "Delay": "0.000", "Language": "en"},
                ]
            }
        },
    )
    source.duration = 4402.336
    source.streams[0].start_time = 0.005
    source.streams[1].start_time = 0.0

    output = _media(
        Path("/tmp/output.mp4"),
        "mov,mp4,m4a,3gp,3g2,mj2",
        has_dv=False,
        codec_tag="hvc1",
        raw_mediainfo={
            "media": {
                "track": [
                    {"@type": "General"},
                    {"@type": "Video", "StreamOrder": "0", "ID": "1", "Duration": "4155.110", "Delay": "0.005", "CodecID": "hvc1"},
                    {"@type": "Audio", "StreamOrder": "1", "ID": "2", "Duration": "4155.136", "Delay": "0.000", "Language": "fr"},
                ]
            }
        },
    )
    output.duration = 4155.110
    output.streams[0].start_time = 0.005
    output.streams[1].start_time = 0.0

    result = validator.validate(source, output, _decision())

    assert result.ok is True
    assert not any("Duration delta too high" in reason for reason in result.reasons)


def test_validator_accepts_non_default_source_audio_that_runs_longer_than_video():
    cfg = AppConfig.from_dict({})
    validator = OutputValidator(cfg)
    source = _media(
        Path("/tmp/source.mkv"),
        "matroska,webm",
        has_dv=False,
        codec_tag=None,
        raw_mediainfo={
            "media": {
                "track": [
                    {"@type": "General"},
                    {"@type": "Video", "StreamOrder": "0", "ID": "1", "Duration": "1711.126", "Delay": "0.000"},
                    {"@type": "Audio", "StreamOrder": "1", "ID": "2", "Duration": "1714.144", "Delay": "0.000", "Language": "fr", "Default": "Yes"},
                    {"@type": "Audio", "StreamOrder": "2", "ID": "3", "Duration": "1711.168", "Delay": "0.000", "Language": "en"},
                ]
            }
        },
    )
    source.duration = 1714.144
    source.streams[0].start_time = 0.0
    source.streams[1].start_time = 0.0
    source.streams.append(
        StreamInfo.from_probe(
            {
                "index": 2,
                "codec_type": "audio",
                "codec_name": "eac3",
                "duration": "1711.168000",
                "start_time": "0.000000",
                "disposition": {"default": 0},
                "tags": {"language": "eng"},
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
                    {"@type": "General"},
                    {"@type": "Video", "StreamOrder": "0", "ID": "1", "Duration": "1711.126", "Delay": "0.000", "CodecID": "hvc1"},
                    {"@type": "Audio", "StreamOrder": "1", "ID": "2", "Duration": "1711.126", "Delay": "0.000", "Language": "fr", "Default": "Yes"},
                    {"@type": "Audio", "StreamOrder": "2", "ID": "3", "Duration": "1711.168", "Delay": "0.000", "Language": "en"},
                ]
            }
        },
    )
    output.duration = 1711.126
    output.streams[0].start_time = 0.0
    output.streams[1].start_time = 0.0
    output.streams.append(
        StreamInfo.from_probe(
            {
                "index": 2,
                "codec_type": "audio",
                "codec_name": "eac3",
                "codec_tag_string": "ec-3",
                "duration": "1711.168000",
                "start_time": "0.000000",
                "disposition": {"default": 0},
                "tags": {"language": "eng"},
            }
        )
    )

    result = validator.validate(source, output, _decision())

    assert result.ok is True
    assert not any("Duration delta too high" in reason for reason in result.reasons)
    assert not any("Output audio duration changed unexpectedly" in reason for reason in result.reasons)


def test_validator_accepts_when_all_source_audio_tracks_are_padded_to_container_duration():
    cfg = AppConfig.from_dict({})
    validator = OutputValidator(cfg)
    source = _media(
        Path("/tmp/source.mkv"),
        "matroska,webm",
        has_dv=True,
        codec_tag=None,
        raw_mediainfo={
            "media": {
                "track": [
                    {"@type": "General"},
                    {"@type": "Video", "StreamOrder": "0", "ID": "1", "Duration": "2814.604", "Delay": "0.000"},
                    {"@type": "Audio", "StreamOrder": "1", "ID": "2", "Duration": "2928.032", "Delay": "0.000", "Language": "fr"},
                    {"@type": "Audio", "StreamOrder": "2", "ID": "3", "Duration": "2927.712", "Delay": "0.000", "Language": "en"},
                    {"@type": "Text", "StreamOrder": "3", "ID": "4", "Duration": "2791.538", "Language": "fr"},
                ]
            }
        },
    )
    source.duration = 2928.032
    source.streams[0].duration = 2814.604
    source.streams[1].duration = 2928.032
    source.streams[0].start_time = 0.0
    source.streams[1].start_time = 0.0
    source.streams[0].codec_name = "hevc"
    source.streams.append(
        StreamInfo.from_probe(
            {
                "index": 2,
                "codec_type": "audio",
                "codec_name": "eac3",
                "duration": "2927.712000",
                "start_time": "0.000000",
                "disposition": {"default": 0},
                "tags": {"language": "eng"},
            }
        )
    )

    output = _media(
        Path("/tmp/output.mp4"),
        "mov,mp4,m4a,3gp,3g2,mj2",
        has_dv=True,
        codec_tag="dvh1",
        raw_mediainfo={
            "media": {
                "track": [
                    {"@type": "General"},
                    {"@type": "Video", "StreamOrder": "0", "ID": "1", "Duration": "2814.620", "Delay": "0.000", "CodecID": "dvh1"},
                    {"@type": "Audio", "StreamOrder": "1", "ID": "2", "Duration": "2814.620", "Delay": "0.000", "Language": "fr"},
                    {"@type": "Audio", "StreamOrder": "2", "ID": "3", "Duration": "2814.620", "Delay": "0.000", "Language": "en"},
                    {"@type": "Text", "StreamOrder": "3", "ID": "4", "Language": "fr", "Format": "Timed Text"},
                ]
            }
        },
    )
    output.duration = 2814.620
    output.streams[0].duration = 2814.620
    output.streams[1].duration = 2814.620
    output.streams[0].start_time = 0.0
    output.streams[1].start_time = 0.0
    output.streams.append(
        StreamInfo.from_probe(
            {
                "index": 2,
                "codec_type": "audio",
                "codec_name": "eac3",
                "codec_tag_string": "ec-3",
                "duration": "2814.620000",
                "start_time": "0.000000",
                "disposition": {"default": 0},
                "tags": {"language": "eng"},
            }
        )
    )

    result = validator.validate(source, output, _decision())

    assert result.ok is True
    assert not any("Duration delta too high" in reason for reason in result.reasons)
    assert not any("Output audio duration changed unexpectedly" in reason for reason in result.reasons)


def test_validator_accepts_output_matching_shorter_chapter_tail_timeline():
    cfg = AppConfig.from_dict({})
    validator = OutputValidator(cfg)
    source = _media(
        Path("/tmp/source.mkv"),
        "matroska,webm",
        has_dv=False,
        codec_tag=None,
        raw_probe={
            "chapters": [
                {"id": 0, "start_time": "0.000000", "end_time": "696.400000"},
                {"id": 1, "start_time": "696.400000", "end_time": "1562.960000"},
                {"id": 2, "start_time": "1562.960000", "end_time": "2303.160000"},
                {"id": 3, "start_time": "2303.160000", "end_time": "3205.040000"},
                {"id": 4, "start_time": "3205.040000", "end_time": "4029.920000"},
                {"id": 5, "start_time": "4029.920000", "end_time": "4113.000000"},
            ]
        },
        raw_mediainfo={
            "media": {
                "track": [
                    {"@type": "General"},
                    {"@type": "Video", "StreamOrder": "0", "ID": "1", "Duration": "4124.760", "Delay": "0.000"},
                    {"@type": "Audio", "StreamOrder": "1", "ID": "2", "Duration": "4124.769", "Delay": "0.000", "Language": "fr"},
                    {"@type": "Audio", "StreamOrder": "2", "ID": "3", "Duration": "4124.769", "Delay": "0.000", "Language": "en"},
                    {"@type": "Text", "StreamOrder": "3", "ID": "4", "Language": "fr"},
                ]
            }
        },
    )
    source.duration = 4124.769

    output = _media(
        Path("/tmp/output.mp4"),
        "mov,mp4,m4a,3gp,3g2,mj2",
        has_dv=False,
        codec_tag="hvc1",
        raw_mediainfo={
            "media": {
                "track": [
                    {"@type": "General"},
                    {"@type": "Video", "StreamOrder": "0", "ID": "1", "Duration": "4113.050", "Delay": "0.000", "CodecID": "hvc1"},
                    {"@type": "Audio", "StreamOrder": "1", "ID": "2", "Duration": "4113.060", "Delay": "0.000", "Language": "fr"},
                    {"@type": "Audio", "StreamOrder": "2", "ID": "3", "Duration": "4113.020", "Delay": "0.000", "Language": "en"},
                    {"@type": "Text", "StreamOrder": "3", "ID": "4", "Language": "fr", "Format": "Timed Text"},
                ]
            }
        },
    )
    output.duration = 4113.061

    result = validator.validate(source, output, _decision())

    assert result.ok is True
    assert not any("Duration delta too high" in reason for reason in result.reasons)
    assert not any("Video duration changed unexpectedly" in reason for reason in result.reasons)
    assert not any("Output audio duration changed unexpectedly" in reason for reason in result.reasons)
