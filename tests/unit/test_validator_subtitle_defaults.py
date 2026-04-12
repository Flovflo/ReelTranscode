from __future__ import annotations

from pathlib import Path

from reeltranscode.config import AppConfig
from reeltranscode.models import (
    CaseLabel,
    Decision,
    ExecutionPlan,
    MediaInfo,
    StreamInfo,
    Strategy,
)
from reeltranscode.validator import OutputValidator


def _media(path: Path, *, first_output_default: bool) -> MediaInfo:
    return MediaInfo(
        path=path,
        format_name="mov,mp4,m4a,3gp,3g2,mj2",
        duration=120.0,
        bit_rate=4_000_000,
        size=4_000_000,
        streams=[
            StreamInfo.from_probe(
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "codec_tag_string": "hvc1",
                    "profile": "Main 10",
                    "pix_fmt": "yuv420p10le",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "24/1",
                    "disposition": {"default": 1},
                }
            ),
            StreamInfo.from_probe(
                {
                    "index": 1,
                    "codec_type": "audio",
                    "codec_name": "eac3",
                    "channels": 6,
                    "disposition": {"default": 1},
                }
            ),
            StreamInfo.from_probe(
                {
                    "index": 2,
                    "codec_type": "subtitle",
                    "codec_name": "mov_text",
                    "disposition": {"default": 1 if first_output_default else 0, "captions": 1, "hearing_impaired": 1},
                    "tags": {"language": "eng", "title": "British (SDH)"},
                }
            ),
            StreamInfo.from_probe(
                {
                    "index": 3,
                    "codec_type": "subtitle",
                    "codec_name": "mov_text",
                    "disposition": {"default": 0, "forced": 1},
                    "tags": {"language": "fre", "title": "Forced"},
                }
            ),
        ],
        raw_probe={},
    )


def _source_media(path: Path) -> MediaInfo:
    media = _media(path, first_output_default=False)
    media.format_name = "matroska,webm"
    return media


def test_validator_allows_single_inferred_default_subtitle_when_source_has_none(tmp_path: Path):
    cfg = AppConfig.from_dict({})
    validator = OutputValidator(cfg)
    source = _source_media(tmp_path / "movie.mkv")
    output = _media(tmp_path / "movie.mp4", first_output_default=True)
    decision = Decision(
        strategy=Strategy.SUBTITLE_ONLY,
        case_label=CaseLabel.D,
        reasons=["Subtitle codec subrip incompatible with MP4"],
        expected_container="mp4",
        expected_direct_play_safe=True,
    )
    plan = ExecutionPlan(
        source_path=source.path,
        target_path=output.path,
        temp_path=output.path,
        workspace_dir=None,
        strategy=decision.strategy,
        case_label=decision.case_label,
        steps=[],
    )

    result = validator.validate(source, output, decision, plan=plan)

    assert result.ok is True
    assert any("inferred a default subtitle track" in note for note in result.notes)


def test_validator_prefers_clean_mediainfo_title_over_ffprobe_mojibake(tmp_path: Path):
    cfg = AppConfig.from_dict({})
    validator = OutputValidator(cfg)
    source = _source_media(tmp_path / "movie.mkv")
    source.streams[3].title = "Forcés"

    output = _media(tmp_path / "movie.mp4", first_output_default=False)
    output.streams[3].title = "Forc\ufffds"
    output.raw_mediainfo = {
        "media": {
            "track": [
                {"@type": "General"},
                {"@type": "Video"},
                {"@type": "Audio"},
                {
                    "@type": "Text",
                    "StreamOrder": "3",
                    "ID": "4",
                    "Title": "Forcés",
                    "Language": "fr",
                    "Default": "No",
                    "Forced": "Yes / No",
                    "Format": "Timed Text",
                },
                {
                    "@type": "Text",
                    "StreamOrder": "2",
                    "ID": "3",
                    "Title": "British (SDH)",
                    "Language": "en",
                    "Default": "No",
                    "Forced": "No",
                    "Format": "Timed Text",
                },
            ]
        }
    }

    decision = Decision(
        strategy=Strategy.SUBTITLE_ONLY,
        case_label=CaseLabel.D,
        reasons=["Subtitle codec subrip incompatible with MP4"],
        expected_container="mp4",
        expected_direct_play_safe=True,
    )
    plan = ExecutionPlan(
        source_path=source.path,
        target_path=output.path,
        temp_path=output.path,
        workspace_dir=None,
        strategy=decision.strategy,
        case_label=decision.case_label,
        steps=[],
    )

    result = validator.validate(source, output, decision, plan=plan)

    assert result.ok is True
