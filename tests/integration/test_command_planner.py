from pathlib import Path
from types import SimpleNamespace

import pytest

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


def test_plan_for_sample1_keeps_video_copy(tmp_path: Path):
    cfg = AppConfig.from_dict(
        {
            "remux": {"preferred_container": "mp4"},
            "output": {"output_root": str(tmp_path / "optimized")},
            "paths": {"temp_dir": str(tmp_path / "tmp")},
        }
    )
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
    assert plan.temp_path is not None
    assert plan.temp_path.parent == (tmp_path / "tmp").resolve()
    assert plan.steps[0].cwd == plan.target_path.parent


def test_mp4_plan_preserves_text_subtitle_metadata_and_flags():
    cfg = AppConfig.from_dict({"remux": {"preferred_container": "mp4"}})
    media = _media(
        "/Volumes/Media/Movies/SpiderVerse.mkv",
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
                "tags": {"language": "fre", "title": "VFF Forced"},
                "disposition": {"default": 1, "forced": 1},
            },
            {
                "index": 3,
                "codec_type": "subtitle",
                "codec_name": "subrip",
                "tags": {"language": "eng", "title": "SDH"},
                "disposition": {"default": 0, "hearing_impaired": 1, "captions": 1},
            },
        ],
    )

    engine = DecisionEngine(cfg)
    decision, comp = engine.decide(media)
    plan = CommandPlanner(cfg).build(media, decision, comp, Path("/Volumes/Media/Movies"))

    cmd = plan.steps[0].command
    assert cmd[cmd.index("-c:s:0") + 1] == "mov_text"
    assert cmd[cmd.index("-metadata:s:s:0") + 1] == "language=fre"
    assert "title=VFF Forced" in cmd
    assert cmd[cmd.index("-disposition:s:0") + 1] == "default+forced"
    assert cmd[cmd.index("-c:s:1") + 1] == "mov_text"
    assert "title=SDH" in cmd
    assert "hearing_impaired+captions" in cmd


def test_mp4_plan_drops_incompatible_image_subtitles_by_default():
    cfg = AppConfig.from_dict({"remux": {"preferred_container": "mp4"}})
    media = _media(
        "/Volumes/Media/Movies/ForeignMovie.mkv",
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
                "codec_type": "subtitle",
                "codec_name": "hdmv_pgs_subtitle",
                "tags": {"language": "eng"},
                "disposition": {"default": 0},
            },
        ],
    )

    engine = DecisionEngine(cfg)
    decision, comp = engine.decide(media)
    plan = CommandPlanner(cfg).build(media, decision, comp, Path("/Volumes/Media/Movies"))

    assert plan.dropped_subtitle_streams == [0]
    assert any("Dropped incompatible image subtitle" in note for note in plan.notes)


def test_mp4_plan_can_ocr_image_subtitles_into_mov_text():
    cfg = AppConfig.from_dict(
        {
            "remux": {"preferred_container": "mp4"},
            "subtitles": {
                "ocr_image_subtitles": True,
                "drop_incompatible_image_subtitles": False,
            },
        }
    )
    media = _media(
        "/Volumes/Media/Movies/ForeignMovie.mkv",
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
                "codec_type": "subtitle",
                "codec_name": "hdmv_pgs_subtitle",
                "tags": {"language": "eng"},
                "disposition": {"default": 0},
            },
        ],
    )

    engine = DecisionEngine(cfg)
    decision, comp = engine.decide(media)
    plan = CommandPlanner(cfg).build(media, decision, comp, Path("/Volumes/Media/Movies"))

    assert plan.workspace_dir is not None
    assert len(plan.ocr_subtitle_tasks) == 1
    assert plan.ocr_subtitle_tasks[0].source_subtitle_index == 0
    assert plan.ocr_subtitle_tasks[0].sup_path.name == "subtitle_0.sup"
    assert plan.ocr_subtitle_tasks[0].output_path.name == "subtitle_0.eng.srt"
    cmd = plan.steps[0].command
    assert str(plan.ocr_subtitle_tasks[0].output_path) in cmd
    assert cmd[cmd.index("-c:s:0") + 1] == "mov_text"
    assert any("OCR subtitle stream 0" in note for note in plan.notes)


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


def test_dovi_muxer_plan_uses_wrapper_to_force_video_frame_rate(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ["DoViMuxer", "MP4Box", "mediainfo", "mp4muxer", "ffmpeg", "ffmpeg_dovi_compat"]:
        path = bin_dir / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)

    cfg = AppConfig.from_dict(
        {
            "remux": {"preferred_container": "mp4"},
            "output": {"output_root": str(tmp_path / "optimized")},
            "paths": {"temp_dir": str(tmp_path / "tmp")},
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
    assert plan.workspace_dir is not None
    assert plan.workspace_dir in plan.cleanup_dirs
    assert plan.workspace_dir.exists()
    assert cmd[1].endswith(".tmp.mp4")
    assert not Path(cmd[1]).name.startswith(".")
    assert Path(cmd[1]).parent == plan.workspace_dir
    assert "-mp4box" in cmd
    assert "-mediainfo" in cmd
    wrapper_path = Path(cmd[cmd.index("-mp4muxer") + 1])
    assert wrapper_path in plan.cleanup_paths
    assert wrapper_path.exists()
    assert wrapper_path.parent == plan.workspace_dir
    wrapper_text = wrapper_path.read_text(encoding="utf-8")
    assert "--input-video-frame-rate" in wrapper_text
    assert "24/1" in wrapper_text
    assert str(bin_dir / "mp4muxer") in wrapper_text
    assert ["-map", "0:v:0"] == cmd[cmd.index("-map") : cmd.index("-map") + 2]
    assert "0:a:0" in cmd
    assert "0:s:0" in cmd
    assert "-default" in cmd
    assert "a:0" in cmd
    assert cmd[-1] == "-y"
    assert plan.steps[0].cwd == plan.workspace_dir


def test_dovi_muxer_plan_trims_only_overlong_audio_tracks_with_mp4box_wrapper(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ["DoViMuxer", "MP4Box", "mediainfo", "mp4muxer", "ffmpeg", "ffmpeg_dovi_compat"]:
        path = bin_dir / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)

    cfg = AppConfig.from_dict(
        {
            "remux": {"preferred_container": "mp4"},
            "output": {"output_root": str(tmp_path / "optimized")},
            "paths": {"temp_dir": str(tmp_path / "tmp")},
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
        "/Volumes/Media/Movies/dv_mismatch.mkv",
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
                "avg_frame_rate": "24000/1001",
                "tags": {"DURATION": "01:40:53.840000000"},
                "disposition": {"default": 1},
                "side_data_list": [{"side_data_type": "DOVI configuration record", "dv_profile": "8.1"}],
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "eac3",
                "channels": 6,
                "channel_layout": "5.1",
                "tags": {"language": "fre", "DURATION": "01:40:53.856000000"},
                "disposition": {"default": 1},
            },
            {
                "index": 2,
                "codec_type": "audio",
                "codec_name": "eac3",
                "channels": 6,
                "channel_layout": "5.1",
                "tags": {"language": "eng", "DURATION": "01:41:09.472000000"},
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
    assert plan.workspace_dir is not None
    assert plan.workspace_dir in plan.cleanup_dirs
    mp4box_wrapper = Path(cmd[cmd.index("-mp4box") + 1])
    assert mp4box_wrapper in plan.cleanup_paths
    assert mp4box_wrapper.exists()
    assert mp4box_wrapper.parent == plan.workspace_dir
    wrapper_text = mp4box_wrapper.read_text(encoding="utf-8")
    assert str(bin_dir / "MP4Box") in wrapper_text
    assert "_Audio1." in wrapper_text
    assert ":dur=6053.840" in wrapper_text
    assert "_Audio0." not in wrapper_text


def test_dovi_muxer_plan_drops_incompatible_image_subtitles_by_default(tmp_path: Path):
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

    assert plan.dropped_subtitle_streams == [0]
    assert any("Dropped incompatible image subtitle" in note for note in plan.notes)


def test_dovi_muxer_plan_can_ocr_image_subtitles_into_mov_text(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ["DoViMuxer", "MP4Box", "mediainfo", "mp4muxer", "ffmpeg", "ffmpeg_dovi_compat"]:
        path = bin_dir / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)

    cfg = AppConfig.from_dict(
        {
            "remux": {"preferred_container": "mp4"},
            "subtitles": {
                "ocr_image_subtitles": True,
                "drop_incompatible_image_subtitles": False,
            },
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

    assert len(plan.steps) == 3
    assert len(plan.ocr_subtitle_tasks) == 1
    assert plan.steps[0].name == "dovi_muxer"
    assert plan.steps[1].name == "dovi_subtitle_merge"
    assert plan.steps[2].name == "dovi_metadata_patch"
    assert str(plan.ocr_subtitle_tasks[0].output_path) in plan.steps[1].command
    assert plan.steps[2].command[0] == str(bin_dir / "MP4Box")
    assert f"self#video:dvp=8.1" in plan.steps[2].command
    assert any("OCR image subtitle" in note for note in plan.notes)
    assert any("Reapplied Dolby Vision signaling" in note for note in plan.notes)


def test_plan_uses_output_temp_root_when_configured_temp_dir_is_too_small(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    temp_dir = tmp_path / "tmp"
    output_root = tmp_path / "optimized"
    temp_dir.mkdir()
    output_root.mkdir()

    cfg = AppConfig.from_dict(
        {
            "remux": {"preferred_container": "mp4"},
            "output": {"mode": "keep_original", "output_root": str(output_root)},
            "paths": {"temp_dir": str(temp_dir)},
        }
    )
    media = _media(
        "/Volumes/Media/Movies/SpiderVerse.mkv",
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
        ],
    )

    def fake_disk_usage(path: str | Path):
        resolved = Path(path).resolve()
        if resolved == temp_dir.resolve():
            return SimpleNamespace(total=20 * 1024**3, used=18 * 1024**3, free=2 * 1024**3)
        return SimpleNamespace(total=40 * 1024**3, used=8 * 1024**3, free=32 * 1024**3)

    monkeypatch.setattr("reeltranscode.planner.shutil.disk_usage", fake_disk_usage)

    decision, comp = DecisionEngine(cfg).decide(media)
    plan = CommandPlanner(cfg).build(media, decision, comp, Path("/Volumes/Media/Movies"))

    assert plan.temp_path is not None
    assert plan.temp_path.parent == (output_root / ".reeltranscode-tmp").resolve()
    assert any("Using alternate temporary workspace volume" in note for note in plan.notes)


def test_dovi_muxer_plan_uses_output_temp_root_when_configured_temp_dir_is_too_small(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ["DoViMuxer", "MP4Box", "mediainfo", "mp4muxer", "ffmpeg", "ffmpeg_dovi_compat"]:
        path = bin_dir / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)

    temp_dir = tmp_path / "tmp"
    output_root = tmp_path / "optimized"
    temp_dir.mkdir()
    output_root.mkdir()

    cfg = AppConfig.from_dict(
        {
            "remux": {"preferred_container": "mp4"},
            "subtitles": {
                "ocr_image_subtitles": True,
                "drop_incompatible_image_subtitles": False,
            },
            "output": {"mode": "keep_original", "output_root": str(output_root)},
            "paths": {"temp_dir": str(temp_dir)},
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

    def fake_disk_usage(path: str | Path):
        resolved = Path(path).resolve()
        if resolved == temp_dir.resolve():
            return SimpleNamespace(total=20 * 1024**3, used=18 * 1024**3, free=2 * 1024**3)
        return SimpleNamespace(total=60 * 1024**3, used=10 * 1024**3, free=50 * 1024**3)

    monkeypatch.setattr("reeltranscode.planner.shutil.disk_usage", fake_disk_usage)

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

    expected_root = (output_root / ".reeltranscode-tmp").resolve()
    assert plan.workspace_dir is not None
    assert plan.workspace_dir.parent == expected_root
    assert plan.temp_path is not None
    assert plan.temp_path.parent == plan.workspace_dir
    assert any("Using alternate temporary workspace volume" in note for note in plan.notes)
