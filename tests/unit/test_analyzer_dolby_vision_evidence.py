from __future__ import annotations

from pathlib import Path

from reeltranscode.analyzer import FFprobeAnalyzer
from reeltranscode.models import MediaInfo, StreamInfo


def test_detect_dolby_vision_uses_ffprobe_compatibility_id_for_profile_8_1():
    media = MediaInfo(
        path=Path("/tmp/source.mkv"),
        format_name="matroska,webm",
        duration=120.0,
        bit_rate=20_000_000,
        size=1_000_000_000,
        streams=[
            StreamInfo.from_probe(
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "pix_fmt": "yuv420p10le",
                    "disposition": {"default": 1},
                    "side_data_list": [
                        {
                            "side_data_type": "DOVI configuration record",
                            "dv_profile": 8,
                            "dv_bl_signal_compatibility_id": 1,
                        }
                    ],
                }
            )
        ],
        raw_probe={},
    )

    info = FFprobeAnalyzer.inspect_dolby_vision(media)

    assert info.present is True
    assert info.profile == "8.1"
    assert info.source == "ffprobe"


def test_detect_dolby_vision_prefers_explicit_mediainfo_proof_for_mp4():
    media = MediaInfo(
        path=Path("/tmp/output.mp4"),
        format_name="mov,mp4,m4a,3gp,3g2,mj2",
        duration=120.0,
        bit_rate=20_000_000,
        size=1_000_000_000,
        streams=[
            StreamInfo.from_probe(
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "codec_tag_string": "hvc1",
                    "pix_fmt": "yuv420p10le",
                    "disposition": {"default": 1},
                }
            )
        ],
        raw_probe={},
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

    info = FFprobeAnalyzer.inspect_dolby_vision(media)

    assert info.present is True
    assert info.profile == "8.1"
    assert info.source == "mediainfo"


def test_detect_dolby_vision_does_not_treat_dby1_brand_as_proof():
    media = MediaInfo(
        path=Path("/tmp/output.mp4"),
        format_name="mov,mp4,m4a,3gp,3g2,mj2",
        duration=120.0,
        bit_rate=20_000_000,
        size=1_000_000_000,
        streams=[
            StreamInfo.from_probe(
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "codec_tag_string": "hvc1",
                    "pix_fmt": "yuv420p10le",
                    "disposition": {"default": 1},
                }
            )
        ],
        raw_probe={},
        raw_mediainfo={
            "media": {
                "track": [
                    {"@type": "General", "CodecID_Compatible": "isom/dby1/iso2/mp41"},
                    {"@type": "Video", "HDR_Format": "SMPTE ST 2086", "CodecID": "hvc1"},
                ]
            }
        },
    )

    info = FFprobeAnalyzer.inspect_dolby_vision(media)

    assert info.present is False
    assert info.brand_hint is True
