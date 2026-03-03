from pathlib import Path

from reeltranscode.config import AppConfig
from reeltranscode.decision_engine import DecisionEngine
from reeltranscode.models import CaseLabel, MediaInfo, StreamInfo


def _media(path: str, format_name: str, streams: list[dict]) -> MediaInfo:
    return MediaInfo(
        path=Path(path),
        format_name=format_name,
        duration=7200.0,
        bit_rate=20_000_000,
        size=15_000_000_000,
        streams=[StreamInfo.from_probe(s) for s in streams],
        raw_probe={},
    )


def _video_hevc_main10(index: int = 0, dv: bool = False, hdr10: bool = False) -> dict:
    side_data = []
    if dv:
        side_data.append({"side_data_type": "DOVI configuration record", "dv_profile": "8.1"})
    return {
        "index": index,
        "codec_type": "video",
        "codec_name": "hevc",
        "profile": "Main 10",
        "pix_fmt": "yuv420p10le",
        "width": 3840,
        "height": 1608,
        "avg_frame_rate": "24/1",
        "color_primaries": "bt2020" if hdr10 else "bt709",
        "color_transfer": "smpte2084" if hdr10 else "bt709",
        "color_space": "bt2020nc" if hdr10 else "bt709",
        "disposition": {"default": 1},
        "side_data_list": side_data,
    }


def _audio(index: int, codec: str, channels: int = 6, default: bool = False) -> dict:
    return {
        "index": index,
        "codec_type": "audio",
        "codec_name": codec,
        "channels": channels,
        "channel_layout": "5.1" if channels == 6 else "stereo",
        "disposition": {"default": 1 if default else 0},
        "tags": {"language": "fra"},
    }


def _subtitle(index: int, codec: str) -> dict:
    return {
        "index": index,
        "codec_type": "subtitle",
        "codec_name": codec,
        "disposition": {"default": 0},
        "tags": {"language": "eng"},
    }


def test_sample1_prefers_subtitle_adaptation_over_video_transcode():
    cfg = AppConfig.from_dict({"remux": {"preferred_container": "mp4"}})
    engine = DecisionEngine(cfg)
    media = _media(
        "Zootopia.2.2025.mkv",
        "matroska,webm",
        [
            _video_hevc_main10(),
            _audio(1, "eac3", default=True),
            _audio(2, "eac3"),
            _subtitle(3, "subrip"),
        ],
    )

    decision, _ = engine.decide(media)
    assert decision.case_label == CaseLabel.D
    assert decision.strategy.value in {"subtitle_only", "full_pipeline"}


def test_sample2_dv_fragile_uses_case_f_hdr10_fallback():
    cfg = AppConfig.from_dict(
        {
            "remux": {"preferred_container": "mp4"},
            "dolby_vision": {
                "safe_profiles": ["8.1"],
                "remux_dv_from_mkv_to_mp4_is_safe": False,
                "fragile_fallback": "preserve_hdr10",
            },
        }
    )
    engine = DecisionEngine(cfg)
    media = _media(
        "Fantastic.Four.2025.mkv",
        "matroska,webm",
        [
            _video_hevc_main10(dv=True, hdr10=True),
            _audio(1, "eac3", channels=8, default=True),
            _subtitle(2, "subrip"),
        ],
    )

    decision, _ = engine.decide(media)
    assert decision.case_label == CaseLabel.F
    assert decision.dv_fallback_applied is True
    assert decision.preserve_hdr10 is True


def test_audio_incompatible_picks_case_c():
    cfg = AppConfig.from_dict({})
    engine = DecisionEngine(cfg)
    media = _media(
        "movie.mkv",
        "matroska,webm",
        [
            _video_hevc_main10(),
            _audio(1, "dts", channels=6, default=True),
        ],
    )

    decision, _ = engine.decide(media)
    assert decision.case_label == CaseLabel.C
    assert decision.strategy.value in {"audio_only", "full_pipeline"}


def test_video_incompatible_picks_case_e():
    cfg = AppConfig.from_dict({})
    engine = DecisionEngine(cfg)
    media = _media(
        "movie_av1.mkv",
        "matroska,webm",
        [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "av1",
                "pix_fmt": "yuv420p10le",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "24/1",
                "disposition": {"default": 1},
            },
            _audio(1, "eac3", default=True),
        ],
    )

    decision, _ = engine.decide(media)
    assert decision.case_label == CaseLabel.E


def test_mp4_hevc_hev1_requires_remux_for_hvc1():
    cfg = AppConfig.from_dict({"remux": {"preferred_container": "mp4"}, "video": {"hevc_tag": "hvc1"}})
    engine = DecisionEngine(cfg)
    media = _media(
        "movie.mp4",
        "mov,mp4,m4a,3gp,3g2,mj2",
        [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "codec_tag_string": "hev1",
                "pix_fmt": "yuv420p10le",
                "width": 3840,
                "height": 2160,
                "avg_frame_rate": "24/1",
                "disposition": {"default": 1},
            },
            _audio(1, "eac3", channels=6, default=True),
        ],
    )

    decision, _ = engine.decide(media)
    assert decision.case_label == CaseLabel.B
    assert decision.strategy.value == "remux_only"
