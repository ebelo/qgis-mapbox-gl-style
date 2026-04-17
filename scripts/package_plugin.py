#!/usr/bin/env python3
"""Build a distributable QGIS plugin zip for qfit."""

from __future__ import annotations

import configparser
import importlib.util
import pathlib
import shutil
import tempfile
import zipfile

from importlib import metadata

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


def _resolve_package_dir(package_name: str) -> pathlib.Path:
    spec = importlib.util.find_spec(package_name)
    origin = getattr(spec, "origin", None) if spec is not None else None
    if not origin:
        raise RuntimeError(
            f"Packaging requires the '{package_name}' package to be installed locally. "
            f"Run: python -m pip install {package_name}"
        )
    return pathlib.Path(origin).resolve().parent


def _resolve_distribution_license(package_name: str) -> pathlib.Path | None:
    try:
        dist = metadata.distribution(package_name)
    except metadata.PackageNotFoundError:
        return None

    for file in dist.files or []:
        parts = pathlib.Path(file).parts
        if not parts:
            continue
        lowered = [part.lower() for part in parts]
        filename = lowered[-1]
        if filename.startswith("license") or filename.startswith("copying"):
            return pathlib.Path(dist.locate_file(file)).resolve()
        if "licenses" in lowered:
            return pathlib.Path(dist.locate_file(file)).resolve()
    return None


def _vendor_runtime_dependencies(plugin_dir: pathlib.Path) -> None:
    vendor_dir = plugin_dir / "vendor"
    vendor_dir.mkdir(parents=True, exist_ok=True)

    pypdf_source = _resolve_package_dir("pypdf")
    shutil.copytree(pypdf_source, vendor_dir / "pypdf", dirs_exist_ok=True)

    license_path = _resolve_distribution_license("pypdf")
    if license_path and license_path.is_file():
        licenses_dir = vendor_dir / "licenses"
        licenses_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(license_path, licenses_dir / "pypdf_LICENSE.txt")


def _build_staging_tree(plugin_name: str) -> pathlib.Path:
    staging_root = pathlib.Path(tempfile.mkdtemp(prefix="qfit-package-"))
    plugin_dir = staging_root / plugin_name
    _copy_project_tree(plugin_dir)
    _vendor_runtime_dependencies(plugin_dir)
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
