from pathlib import Path

from reeltranscode.config import AppConfig
from reeltranscode.decision_engine import DecisionEngine
from reeltranscode.models import CaseLabel, Decision, MediaInfo, StreamInfo, Strategy
from reeltranscode.planner import CommandPlanner


def _media(path: str, format_name: str, streams: list[dict]) -> MediaInfo:
    return MediaInfo(
        path=Path(path),
        format_name=format_name,
        duration=7000.0,
        bit_rate=22_000_000,
        size=13_000_000_000,
        streams=[StreamInfo.from_probe(s) for s in streams],
        raw_probe={},
    )


def test_plan_for_sample1_keeps_video_copy():
    cfg = AppConfig.from_dict({"remux": {"preferred_container": "mp4"}})
    media = _media(
        "/Volumes/Media/Movies/Zootopia.2.2025.mkv",
        "matroska,webm",
        [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "profile": "Main 10",
                "pix_fmt": "yuv420p10le",
                "width": 3840,
                "height": 1608,
                "avg_frame_rate": "24/1",
                "disposition": {"default": 1},
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "eac3",
                "channels": 6,
                "channel_layout": "5.1",
                "tags": {"language": "fra"},
                "disposition": {"default": 1},
            },
            {
                "index": 2,
                "codec_type": "subtitle",
                "codec_name": "subrip",
                "tags": {"language": "eng"},
                "disposition": {"default": 0},
            },
        ],
    )
    engine = DecisionEngine(cfg)
    decision, comp = engine.decide(media)

    planner = CommandPlanner(cfg)
    plan = planner.build(media, decision, comp, Path("/Volumes/Media/Movies"))

    assert plan.steps
    cmd = plan.steps[0].command
    assert "-c:v" in cmd
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert "-tag:v" in cmd
    assert cmd[cmd.index("-tag:v") + 1] == "hvc1"
    assert str(plan.target_path).endswith(".mp4")


def test_video_transcode_plan_uses_videotoolbox():
    cfg = AppConfig.from_dict({"video": {"preferred_codec": "hevc"}})
    media = _media(
        "/Volumes/Media/Movies/movie_av1.mkv",
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
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "eac3",
                "channels": 6,
                "channel_layout": "5.1",
                "tags": {"language": "eng"},
                "disposition": {"default": 1},
            },
        ],
    )

    engine = DecisionEngine(cfg)
    decision, comp = engine.decide(media)
    planner = CommandPlanner(cfg)
    plan = planner.build(media, decision, comp, Path("/Volumes/Media/Movies"))

    cmd = plan.steps[0].command
    assert "hevc_videotoolbox" in cmd


def test_hdr_transcode_forces_hevc_main10_pipeline():
    cfg = AppConfig.from_dict({"video": {"preferred_codec": "hevc"}})
    media = _media(
        "/Volumes/Media/Movies/hdr_av1_source.mkv",
        "matroska,webm",
        [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "av1",
                "pix_fmt": "yuv420p",
                "width": 3840,
                "height": 2160,
                "avg_frame_rate": "24/1",
                "color_primaries": "bt2020",
                "color_transfer": "smpte2084",
                "color_space": "bt2020nc",
                "disposition": {"default": 1},
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "eac3",
                "channels": 6,
                "channel_layout": "5.1",
                "tags": {"language": "eng"},
                "disposition": {"default": 1},
            },
        ],
    )

    engine = DecisionEngine(cfg)
    decision, comp = engine.decide(media)
    planner = CommandPlanner(cfg)
    plan = planner.build(media, decision, comp, Path("/Volumes/Media/Movies"))

    cmd = plan.steps[0].command
    assert cmd[cmd.index("-tag:v") + 1] == "hvc1"
    assert cmd[cmd.index("-profile:v") + 1] == "main10"
    assert cmd[cmd.index("-pix_fmt") + 1] == "p010le"
    assert cmd[cmd.index("-color_primaries") + 1] == "bt2020"
    assert cmd[cmd.index("-color_trc") + 1] == "smpte2084"
    assert cmd[cmd.index("-colorspace") + 1] == "bt2020nc"


def test_mp4_plan_adds_aac_stereo_fallback_when_missing():
    cfg = AppConfig.from_dict({"remux": {"preferred_container": "mp4"}})
    media = _media(
        "/Volumes/Media/Movies/movie_eac3_only.mkv",
        "matroska,webm",
        [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "profile": "Main 10",
                "pix_fmt": "yuv420p10le",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "24/1",
                "disposition": {"default": 1},
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "eac3",
                "channels": 6,
                "channel_layout": "5.1",
                "tags": {"language": "eng"},
                "disposition": {"default": 1},
            },
        ],
    )
    engine = DecisionEngine(cfg)
    decision, comp = engine.decide(media)
    plan = CommandPlanner(cfg).build(media, decision, comp, Path("/Volumes/Media/Movies"))

    cmd = plan.steps[0].command
    assert "0:a:0" in cmd
    assert cmd[cmd.index("-c:a:1") + 1] == "aac"
    assert cmd[cmd.index("-ac:a:1") + 1] == "2"


def test_mp4_plan_skips_aac_fallback_if_already_present():
    cfg = AppConfig.from_dict({"remux": {"preferred_container": "mp4"}})
    media = _media(
        "/Volumes/Media/Movies/movie_with_aac.mkv",
        "matroska,webm",
        [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "profile": "Main 10",
                "pix_fmt": "yuv420p10le",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "24/1",
                "disposition": {"default": 1},
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "eac3",
                "channels": 6,
                "channel_layout": "5.1",
                "tags": {"language": "eng"},
                "disposition": {"default": 1},
            },
            {
                "index": 2,
                "codec_type": "audio",
                "codec_name": "aac",
                "channels": 2,
                "channel_layout": "stereo",
                "tags": {"language": "eng"},
                "disposition": {"default": 0},
            },
        ],
    )
    engine = DecisionEngine(cfg)
    decision, comp = engine.decide(media)
    plan = CommandPlanner(cfg).build(media, decision, comp, Path("/Volumes/Media/Movies"))

    cmd = plan.steps[0].command
    assert "-c:a:2" not in cmd
    assert "AAC Stereo Fallback" not in cmd


def test_replace_original_mode_keeps_series_tree_and_replaces_in_place():
    cfg = AppConfig.from_dict(
        {
            "remux": {"preferred_container": "mp4"},
            "output": {"mode": "replace_original"},
        }
    )
    media = _media(
        "/Volumes/Media/Series/Black Mirror/S1/Black.Mirror.S01E01.mkv",
        "matroska,webm",
        [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "profile": "Main 10",
                "pix_fmt": "yuv420p10le",
                "width": 3840,
                "height": 2160,
                "avg_frame_rate": "24/1",
                "disposition": {"default": 1},
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "eac3",
                "channels": 6,
                "channel_layout": "5.1",
                "tags": {"language": "eng"},
                "disposition": {"default": 1},
            },
        ],
    )

    engine = DecisionEngine(cfg)
    decision, comp = engine.decide(media)
    plan = CommandPlanner(cfg).build(media, decision, comp, Path("/Volumes/Media/Series"))

    assert str(plan.target_path) == "/Volumes/Media/Series/Black Mirror/S1/Black.Mirror.S01E01.mp4"


def test_dovi_muxer_plan_is_selected_for_dv_safe_remux(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ["DoViMuxer", "MP4Box", "mediainfo", "mp4muxer", "ffmpeg", "ffmpeg_dovi_compat"]:
        path = bin_dir / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)

    cfg = AppConfig.from_dict(
        {
            "remux": {"preferred_container": "mp4"},
            "tooling": {
                "ffmpeg_bin": str(bin_dir / "ffmpeg"),
                "dovi_muxer_bin": str(bin_dir / "DoViMuxer"),
                "mp4box_bin": str(bin_dir / "MP4Box"),
                "mediainfo_bin": str(bin_dir / "mediainfo"),
                "mp4muxer_bin": str(bin_dir / "mp4muxer"),
            },
        }
    )
    media = _media(
        "/Volumes/Media/Movies/dv_movie.mkv",
        "matroska,webm",
        [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "profile": "Main 10",
                "pix_fmt": "yuv420p10le",
                "width": 3840,
                "height": 1608,
                "avg_frame_rate": "24/1",
                "color_primaries": "bt2020",
                "color_transfer": "smpte2084",
                "color_space": "bt2020nc",
                "disposition": {"default": 1},
                "side_data_list": [{"side_data_type": "DOVI configuration record", "dv_profile": "8.1"}],
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "eac3",
                "channels": 6,
                "channel_layout": "5.1",
                "tags": {"language": "eng"},
                "disposition": {"default": 1},
            },
            {
                "index": 2,
                "codec_type": "subtitle",
                "codec_name": "subrip",
                "tags": {"language": "eng"},
                "disposition": {"default": 0},
            },
        ],
    )
    decision = Decision(
        strategy=Strategy.REMUX_ONLY,
        case_label=CaseLabel.F,
        reasons=["DoViMuxer path"],
        expected_container="mp4",
        expected_direct_play_safe=True,
        use_dovi_muxer=True,
    )
    _, comp = DecisionEngine(cfg).decide(media)
    plan = CommandPlanner(cfg).build(media, decision, comp, Path("/Volumes/Media/Movies"))

    cmd = plan.steps[0].command
    assert plan.steps[0].name == "dovi_muxer"
    assert cmd[0] == str(bin_dir / "DoViMuxer")
    assert cmd[cmd.index("-ffmpeg") + 1] == str(bin_dir / "ffmpeg_dovi_compat")
    assert cmd[1].endswith(".tmp.mp4")
    assert not Path(cmd[1]).name.startswith(".")
    assert "-mp4box" in cmd
    assert "-mediainfo" in cmd
    assert "-mp4muxer" in cmd
    assert ["-map", "0:v:0"] == cmd[cmd.index("-map") : cmd.index("-map") + 2]
    assert "0:a:0" in cmd
    assert "0:s:0" in cmd
    assert "-default" in cmd
    assert "a:0" in cmd
    assert cmd[-1] == "-y"


def test_dovi_muxer_plan_externalizes_pgs_sidecar_next_to_target(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ["DoViMuxer", "MP4Box", "mediainfo", "mp4muxer", "ffmpeg", "ffmpeg_dovi_compat"]:
        path = bin_dir / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)

    cfg = AppConfig.from_dict(
        {
            "remux": {"preferred_container": "mp4"},
            "output": {
                "mode": "keep_original",
                "output_root": str(tmp_path / "optimized"),
            },
            "tooling": {
                "ffmpeg_bin": str(bin_dir / "ffmpeg"),
                "dovi_muxer_bin": str(bin_dir / "DoViMuxer"),
                "mp4box_bin": str(bin_dir / "MP4Box"),
                "mediainfo_bin": str(bin_dir / "mediainfo"),
                "mp4muxer_bin": str(bin_dir / "mp4muxer"),
            },
        }
    )
    media = _media(
        "/Volumes/Media/Movies/dv_movie.mkv",
        "matroska,webm",
        [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "profile": "Main 10",
                "pix_fmt": "yuv420p10le",
                "width": 3840,
                "height": 1608,
                "avg_frame_rate": "24/1",
                "color_primaries": "bt2020",
                "color_transfer": "smpte2084",
                "color_space": "bt2020nc",
                "disposition": {"default": 1},
                "side_data_list": [{"side_data_type": "DOVI configuration record", "dv_profile": "8.1"}],
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "eac3",
                "channels": 6,
                "channel_layout": "5.1",
                "tags": {"language": "eng"},
                "disposition": {"default": 1},
            },
            {
                "index": 2,
                "codec_type": "subtitle",
                "codec_name": "hdmv_pgs_subtitle",
                "tags": {"language": "eng"},
                "disposition": {"default": 0},
            },
        ],
    )
    decision = Decision(
        strategy=Strategy.REMUX_ONLY,
        case_label=CaseLabel.F,
        reasons=["DoViMuxer path"],
        expected_container="mp4",
        expected_direct_play_safe=True,
        use_dovi_muxer=True,
    )
    _, comp = DecisionEngine(cfg).decide(media)
    plan = CommandPlanner(cfg).build(media, decision, comp, Path("/Volumes/Media/Movies"))

    assert len(plan.steps) == 2
    assert plan.steps[0].name == "dovi_muxer"
    assert plan.steps[1].name == "subtitle_export"
    assert "0:s:0" not in plan.steps[0].command
    assert str(plan.external_subtitle_outputs[0]).endswith("dv_movie__stream_0.eng.sup")
    assert str(plan.steps[1].command[-1]).endswith("dv_movie__stream_0.eng.sup")
