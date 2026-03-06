from __future__ import annotations

from pathlib import Path

from reeltranscode.config import AppConfig
from reeltranscode.models import CaseLabel, Decision, ExecutionPlan, MediaInfo, StreamInfo, Strategy
from reeltranscode.validator import OutputValidator


def _media(path: Path, format_name: str, *, has_dv: bool, codec_tag: str | None) -> MediaInfo:
    side_data = []
    if has_dv:
        side_data.append({"side_data_type": "DOVI configuration record", "dv_profile": "8.1"})

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
    output = _media(Path("/tmp/output.mp4"), "mov,mp4,m4a,3gp,3g2,mj2", has_dv=False, codec_tag="hvc1")

    result = validator.validate(source, output, _decision())

    assert result.ok is False
    assert any("Dolby Vision lost" in reason for reason in result.reasons)


def test_validator_accepts_output_when_dolby_vision_is_preserved():
    cfg = AppConfig.from_dict({})
    validator = OutputValidator(cfg)
    source = _media(Path("/tmp/source.mkv"), "matroska,webm", has_dv=True, codec_tag=None)
    output = _media(Path("/tmp/output.mp4"), "mov,mp4,m4a,3gp,3g2,mj2", has_dv=False, codec_tag="hvc1")
    output.streams[0].dv_profile = "8.1"

    result = validator.validate(source, output, _decision())

    assert result.ok is True


def test_validator_accepts_expected_stream_delta_when_subtitle_is_externalized():
    cfg = AppConfig.from_dict({})
    validator = OutputValidator(cfg)
    source = _media(Path("/tmp/source.mkv"), "matroska,webm", has_dv=True, codec_tag=None)
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
    output.streams[0].dv_profile = "8.1"
    plan = ExecutionPlan(
        source_path=source.path,
        target_path=output.path,
        temp_path=output.path,
        strategy=Strategy.REMUX_ONLY,
        case_label=CaseLabel.F,
        steps=[],
        external_subtitle_outputs=[Path("/tmp/output__stream_0.eng.sup")],
    )

    result = validator.validate(source, output, _decision(), plan=plan)

    assert result.ok is True
