from __future__ import annotations

from pathlib import Path

from reeltranscode.config import AppConfig
from reeltranscode.utils import is_media_file


def iter_media_files(config: AppConfig) -> list[tuple[Path, Path]]:
    items: list[tuple[Path, Path]] = []
    for root in config.watch.folders:
        if not root.exists():
            continue
        if config.is_excluded_from_watch(root):
            continue
        for path in root.rglob("*"):
            if config.is_excluded_from_watch(path):
                continue
            if is_media_file(path, config.watch.allowed_extensions):
                items.append((path, root))
    return sorted(items, key=lambda x: str(x[0]))
