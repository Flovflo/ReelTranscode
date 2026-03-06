#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import stat
import subprocess
import sys
import tempfile
from collections import deque
from pathlib import Path

SYSTEM_PREFIXES = (
    "/System/",
    "/usr/lib/",
    "/Library/Apple/System/",
    "/System/iOSSupport/",
)
OPT_PREFIXES = (
    "@@HOMEBREW_PREFIX@@/opt/",
    "/opt/homebrew/opt/",
    "/usr/local/opt/",
)
CELLAR_PREFIXES = (
    "@@HOMEBREW_CELLAR@@/",
    "/opt/homebrew/Cellar/",
    "/usr/local/Cellar/",
)


def run(cmd: list[str], *, capture_output: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=True,
        text=True,
        capture_output=capture_output,
    )


def stdout(cmd: list[str]) -> str:
    return run(cmd).stdout.strip()


def is_system_dependency(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in SYSTEM_PREFIXES)


def list_macho_dependencies(path: Path) -> list[str]:
    output = stdout(["otool", "-L", str(path)])
    lines = output.splitlines()[1:]
    deps: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        dep = line.split(" (", 1)[0].strip()
        deps.append(dep)
    return deps


def list_macho_rpaths(path: Path) -> list[str]:
    output = stdout(["otool", "-l", str(path)])
    rpaths: list[str] = []
    in_rpath = False
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line == "cmd LC_RPATH":
            in_rpath = True
            continue
        if in_rpath and line.startswith("path "):
            rpaths.append(line.split(" ", 2)[1])
            in_rpath = False
    return rpaths


def codesign_ad_hoc(path: Path) -> None:
    run(["codesign", "--force", "--sign", "-", str(path)], capture_output=True)


def ensure_writable_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IWUSR | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class BottleStage:
    def __init__(self, root_formula: str):
        self.root_formula = root_formula
        self.temp_dir = tempfile.TemporaryDirectory(prefix=f"bundle-{root_formula}-")
        self.stage_root = Path(self.temp_dir.name)
        self.formula_dirs: dict[str, Path] = {}
        self.formula_info: dict[str, dict[str, object]] = {}

    def close(self) -> None:
        self.temp_dir.cleanup()

    def build(self) -> None:
        closure = self._dependency_closure([self.root_formula])
        for formula in closure:
            self._fetch_bottle(formula)
        for formula in closure:
            self._extract_bottle(formula)
        self._discover_formula_dirs()

    def _brew_info(self, formulas: list[str]) -> dict[str, dict[str, object]]:
        pending = [formula for formula in formulas if formula not in self.formula_info]
        if pending:
            payload = json.loads(stdout(["brew", "info", "--json=v2", *pending]))
            for entry in payload.get("formulae", []):
                name = entry["name"]
                self.formula_info[name] = entry
        return {formula: self.formula_info[formula] for formula in formulas}

    def _dependency_closure(self, root_formulas: list[str]) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        queue: deque[str] = deque(root_formulas)
        while queue:
            formula = queue.popleft()
            if formula in seen:
                continue
            seen.add(formula)
            ordered.append(formula)
            entry = self._brew_info([formula])[formula]
            dependencies = entry.get("dependencies", [])
            for dependency in dependencies:
                if dependency not in seen:
                    queue.append(dependency)
        return ordered

    def _fetch_bottle(self, formula: str) -> None:
        print(f"fetch bottle: {formula}")
        run(["brew", "fetch", "--quiet", "--force-bottle", formula], capture_output=True)

    def _extract_bottle(self, formula: str) -> None:
        cache_path = Path(stdout(["brew", "--cache", formula]))
        if not cache_path.exists():
            raise FileNotFoundError(f"Bottle cache missing for {formula}: {cache_path}")
        print(f"extract bottle: {cache_path.name}")
        run(["tar", "-xzf", str(cache_path), "-C", str(self.stage_root)], capture_output=True)

    def _discover_formula_dirs(self) -> None:
        formula_dirs: dict[str, Path] = {}
        for formula_root in self.stage_root.iterdir():
            if not formula_root.is_dir():
                continue
            versions = sorted([path for path in formula_root.iterdir() if path.is_dir()])
            if not versions:
                continue
            formula_dirs[formula_root.name] = versions[-1]
        self.formula_dirs = formula_dirs

    def formula_binary(self, formula: str, relative_path: str) -> Path:
        formula_dir = self.formula_dirs.get(formula)
        if formula_dir is None:
            raise FileNotFoundError(f"No staged bottle found for formula {formula}")
        binary = formula_dir / relative_path
        if not binary.exists():
            raise FileNotFoundError(f"Binary {relative_path} missing from bottle {formula}")
        return binary

    def resolve_absolute(self, raw_path: str) -> Path | None:
        for prefix in CELLAR_PREFIXES:
            if raw_path.startswith(prefix):
                rel = raw_path[len(prefix):]
                candidate = self.stage_root / rel
                if candidate.exists():
                    return candidate
        for prefix in OPT_PREFIXES:
            if raw_path.startswith(prefix):
                rel = raw_path[len(prefix):]
                formula, _, remainder = rel.partition("/")
                if not formula:
                    return None
                formula_dir = self.formula_dirs.get(formula)
                if formula_dir is None:
                    return None
                candidate = formula_dir / remainder
                if candidate.exists():
                    return candidate
        candidate = Path(raw_path)
        if candidate.is_absolute() and candidate.exists():
            return candidate
        return None


class MachOBundler:
    def __init__(self, bin_dir: Path, lib_dir: Path, stage: BottleStage | None = None):
        self.bin_dir = bin_dir
        self.lib_dir = lib_dir
        self.stage = stage
        self._copied: dict[str, Path] = {}

    def bundle(self, source_binary: Path, target_name: str) -> None:
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.lib_dir.mkdir(parents=True, exist_ok=True)

        root_dest = self.bin_dir / target_name
        self._copy_file(source_binary, root_dest)
        queue: deque[tuple[Path, Path]] = deque([(source_binary, root_dest)])
        processed: set[Path] = set()

        while queue:
            source_path, dest_path = queue.popleft()
            dest_key = dest_path.resolve()
            if dest_key in processed:
                continue
            processed.add(dest_key)

            changes: list[tuple[str, str]] = []
            source_key = source_path.resolve()
            dependencies = list_macho_dependencies(source_path)
            rpaths = list_macho_rpaths(source_path)
            for dependency in dependencies:
                if is_system_dependency(dependency):
                    continue
                resolved = self._resolve_dependency(dependency, source_path, rpaths)
                if resolved is None:
                    raise FileNotFoundError(f"Unable to resolve dependency {dependency} for {source_path}")
                if resolved.resolve() == source_key:
                    continue

                bundled_name = Path(dependency).name
                if not bundled_name:
                    bundled_name = resolved.name
                bundled_dest = self.lib_dir / bundled_name
                rel = f"@loader_path/../lib/{bundled_name}" if dest_path.parent == self.bin_dir else f"@loader_path/{bundled_name}"
                changes.append((dependency, rel))

                existing = self._copied.get(bundled_name)
                if existing is not None:
                    if existing.resolve() != resolved.resolve():
                        raise RuntimeError(
                            f"Conflicting dylib basename {bundled_name}: {existing} vs {resolved}"
                        )
                    continue

                self._copy_file(resolved, bundled_dest)
                self._copied[bundled_name] = resolved
                queue.append((resolved, bundled_dest))

            self._patch_file(dest_path, changes)

    def _resolve_dependency(self, dependency: str, source_path: Path, rpaths: list[str]) -> Path | None:
        if dependency.startswith("@loader_path/"):
            candidate = source_path.parent / dependency.removeprefix("@loader_path/")
            if candidate.exists():
                return candidate
            return None
        if dependency.startswith("@executable_path/"):
            candidate = source_path.parent / dependency.removeprefix("@executable_path/")
            if candidate.exists():
                return candidate
            return None
        if dependency.startswith("@rpath/"):
            suffix = dependency.removeprefix("@rpath/")
            for raw_rpath in rpaths:
                resolved_rpath = self._resolve_rpath(raw_rpath, source_path)
                if resolved_rpath is None:
                    continue
                candidate = resolved_rpath / suffix
                if candidate.exists():
                    return candidate
            return None
        if self.stage is not None:
            staged = self.stage.resolve_absolute(dependency)
            if staged is not None:
                return staged
        absolute = Path(dependency)
        if absolute.is_absolute() and absolute.exists():
            return absolute
        return None

    def _resolve_rpath(self, raw_rpath: str, source_path: Path) -> Path | None:
        if raw_rpath.startswith("@loader_path/"):
            candidate = source_path.parent / raw_rpath.removeprefix("@loader_path/")
            return candidate if candidate.exists() else None
        if raw_rpath.startswith("@executable_path/"):
            candidate = source_path.parent / raw_rpath.removeprefix("@executable_path/")
            return candidate if candidate.exists() else None
        if self.stage is not None:
            staged = self.stage.resolve_absolute(raw_rpath)
            if staged is not None:
                return staged
        candidate = Path(raw_rpath)
        if candidate.is_absolute() and candidate.exists():
            return candidate
        return None

    def _copy_file(self, source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=True)
        ensure_writable_executable(destination)

    def _patch_file(self, destination: Path, changes: list[tuple[str, str]]) -> None:
        cmd = ["install_name_tool"]
        if destination.parent == self.lib_dir:
            cmd.extend(["-id", f"@loader_path/{destination.name}"])
        for old, new in changes:
            cmd.extend(["-change", old, new])
        if len(cmd) > 1:
            cmd.append(str(destination))
            run(cmd, capture_output=True)
        codesign_ad_hoc(destination)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bundle a macOS Mach-O binary and its non-system dylibs")
    parser.add_argument("--bin-dir", required=True, type=Path)
    parser.add_argument("--lib-dir", required=True, type=Path)
    parser.add_argument("--target-name", required=True)

    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--source-binary", type=Path)
    source_group.add_argument("--formula")

    parser.add_argument("--binary-relpath", help="Binary path inside the Homebrew bottle, e.g. bin/MP4Box")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    stage: BottleStage | None = None
    try:
        if args.source_binary is not None:
            source_binary = args.source_binary.expanduser().resolve()
            if not source_binary.exists():
                raise FileNotFoundError(f"Source binary not found: {source_binary}")
        else:
            if not args.binary_relpath:
                raise ValueError("--binary-relpath is required when using --formula")
            stage = BottleStage(args.formula)
            stage.build()
            source_binary = stage.formula_binary(args.formula, args.binary_relpath)

        bundler = MachOBundler(args.bin_dir, args.lib_dir, stage)
        bundler.bundle(source_binary, args.target_name)
        print(f"Bundled {args.target_name} into {args.bin_dir}")
        return 0
    finally:
        if stage is not None:
            stage.close()


if __name__ == "__main__":
    sys.exit(main())
