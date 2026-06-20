#!/usr/bin/env python3
"""Build a distributable QGIS plugin zip."""

from __future__ import annotations

import configparser
import pathlib
import shutil
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
DIST_DIR = ROOT / "dist"
EXCLUDED_DIRS = {
    ".git",
    ".github",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "debug",
    "dist",
    "docs",
    "qfit",
    "scripts",
    "tests",
    "validation",
    "validation_artifacts",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".zip"}
EXCLUDED_FILES = {".coverage", ".gitignore", "sonar-project.properties", "symbology-style.db"}


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


def _copy_project_tree(destination: pathlib.Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for path in sorted(ROOT.rglob("*")):
        if not should_include(path):
            continue
        relative = path.relative_to(ROOT)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _build_staging_tree(plugin_name: str) -> pathlib.Path:
    staging_root = pathlib.Path(tempfile.mkdtemp(prefix="qgis-mapbox-gl-style-package-"))
    plugin_dir = staging_root / plugin_name
    _copy_project_tree(plugin_dir)
    return staging_root


def build_zip() -> pathlib.Path:
    plugin_name, version = read_metadata()
    DIST_DIR.mkdir(exist_ok=True)
    archive_path = DIST_DIR / f"{plugin_name.lower()}-{version}.zip"

    staging_root = _build_staging_tree(plugin_name)
    try:
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(staging_root.rglob("*")):
                if not path.is_file() or path.suffix in EXCLUDED_SUFFIXES:
                    continue
                archive.write(path, path.relative_to(staging_root).as_posix())
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)

    return archive_path


def main() -> int:
    archive_path = build_zip()
    print(f"Built {archive_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
