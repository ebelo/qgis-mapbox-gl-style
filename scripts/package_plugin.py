#!/usr/bin/env python3
"""Build a distributable QGIS plugin zip for qfit."""

from __future__ import annotations

import configparser
import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
DIST_DIR = ROOT / "dist"
EXCLUDED_DIRS = {".git", "dist", "scripts", "docs", "__pycache__"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".zip"}
EXCLUDED_FILES = {".gitignore"}


def read_metadata() -> tuple[str, str]:
    parser = configparser.ConfigParser()
    parser.read(ROOT / "metadata.txt")
    name = parser.get("general", "name", fallback=ROOT.name)
    version = parser.get("general", "version", fallback="0.0.0")
    return name, version


def should_include(path: pathlib.Path) -> bool:
    relative = path.relative_to(ROOT)
    if any(part in EXCLUDED_DIRS for part in relative.parts):
        return False
    if path.name in EXCLUDED_FILES:
        return False
    if path.suffix in EXCLUDED_SUFFIXES:
        return False
    return path.is_file()


def build_zip() -> pathlib.Path:
    plugin_name, version = read_metadata()
    DIST_DIR.mkdir(exist_ok=True)
    archive_path = DIST_DIR / f"{plugin_name.lower()}-{version}.zip"

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(ROOT.rglob("*")):
            if not should_include(path):
                continue
            relative = path.relative_to(ROOT)
            archive_name = pathlib.Path(plugin_name) / relative
            archive.write(path, archive_name.as_posix())

    return archive_path


def main() -> int:
    archive_path = build_zip()
    print(f"Built {archive_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
