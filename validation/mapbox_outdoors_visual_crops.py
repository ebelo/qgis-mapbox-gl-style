from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from .mapbox_outdoors_comparison import build_all_cameras_contact_sheet
    from .mapbox_outdoors_path_pedestrian_focus import (
        REPO_ROOT,
        _display_input_path,
        build_run_directory as _build_focus_run_directory,
        comparison_visual_artifacts_from_summary,
        load_json_object,
    )
except ImportError:  # pragma: no cover - direct script execution
    from mapbox_outdoors_comparison import build_all_cameras_contact_sheet  # type: ignore[no-redef]
    from mapbox_outdoors_path_pedestrian_focus import (  # type: ignore[no-redef]
        REPO_ROOT,
        _display_input_path,
        build_run_directory as _build_focus_run_directory,
        comparison_visual_artifacts_from_summary,
        load_json_object,
    )

DEFAULT_OUTPUT_ROOT = REPO_ROOT / "debug" / "mapbox-outdoors-visual-crops"
DEFAULT_CROP_SIZE = (320, 240)
DEFAULT_CROPS_PER_CAMERA = 3
MIN_SCORE_FOR_CROP = 1.0
MAX_OVERLAP_RATIO = 0.35
CROP_IMAGE_COLUMNS = (
    ("browser_reference", "Mapbox GL"),
    ("qgis_vector_render", "QGIS"),
    ("diff", "Diff"),
)


@dataclass(frozen=True)
class VisualCropPaths:
    run_dir: Path
    json_path: Path
    summary_path: Path
    contact_sheet_path: Path


def build_run_directory(
    *,
    output_root: Path | None = None,
    now: dt.datetime | None = None,
) -> Path:
    root = DEFAULT_OUTPUT_ROOT if output_root is None else output_root
    return _build_focus_run_directory(output_root=root, now=now)


def build_visual_crop_paths(run_dir: Path) -> VisualCropPaths:
    return VisualCropPaths(
        run_dir=run_dir,
        json_path=run_dir / "visual-crops.json",
        summary_path=run_dir / "summary.md",
        contact_sheet_path=run_dir / "crop-sheet.jpg",
    )


def parse_crop_size(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"([1-9]\d*)x([1-9]\d*)", value)
    if match is None:
        raise argparse.ArgumentTypeError("Expected crop size WIDTHxHEIGHT, for example 320x240")
    return int(match.group(1)), int(match.group(2))


def _axis_positions(*, image_length: int, crop_length: int, step: int) -> list[int]:
    max_start = max(0, image_length - crop_length)
    if max_start == 0:
        return [0]
    positions = list(range(0, max_start + 1, step))
    if positions[-1] != max_start:
        positions.append(max_start)
    return positions


def _candidate_boxes(
    *,
    image_size: tuple[int, int],
    crop_size: tuple[int, int],
) -> Iterable[tuple[int, int, int, int]]:
    image_width, image_height = image_size
    crop_width = min(crop_size[0], image_width)
    crop_height = min(crop_size[1], image_height)
    step_x = max(1, crop_width // 2)
    step_y = max(1, crop_height // 2)
    for top in _axis_positions(image_length=image_height, crop_length=crop_height, step=step_y):
        for left in _axis_positions(image_length=image_width, crop_length=crop_width, step=step_x):
            yield left, top, left + crop_width, top + crop_height


def _box_area(box: tuple[int, int, int, int]) -> int:
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def _overlap_ratio(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    intersection = _box_area((left, top, right, bottom))
    if intersection == 0:
        return 0.0
    return intersection / max(1, min(_box_area(first), _box_area(second)))


def _diff_score(gray_image: Any, box: tuple[int, int, int, int], image_stat_module: Any) -> float:
    crop = gray_image.crop(box)
    try:
        return float(sum(image_stat_module.Stat(crop).sum))
    finally:
        crop.close()


def find_hotspot_crop_boxes(
    diff_path: Path,
    *,
    crop_size: tuple[int, int] = DEFAULT_CROP_SIZE,
    crop_count: int = DEFAULT_CROPS_PER_CAMERA,
    image_module: Any | None = None,
    image_stat_module: Any | None = None,
) -> list[dict[str, object]]:
    if crop_count <= 0:
        return []
    modules = _image_modules(image_module=image_module, image_stat_module=image_stat_module)
    image_module, image_stat_module = modules
    with image_module.open(diff_path) as opened:
        gray = opened.convert("L")
        try:
            scored_boxes = [
                {
                    "box": box,
                    "score": _diff_score(gray, box, image_stat_module),
                }
                for box in _candidate_boxes(image_size=gray.size, crop_size=crop_size)
            ]
        finally:
            gray.close()
    scored_boxes.sort(key=lambda item: (-float(item["score"]), item["box"]))
    selected: list[dict[str, object]] = []
    for item in scored_boxes:
        if float(item["score"]) < MIN_SCORE_FOR_CROP:
            break
        box = item["box"]
        if not isinstance(box, tuple):
            continue
        if all(_overlap_ratio(box, other["box"]) <= MAX_OVERLAP_RATIO for other in selected):
            selected.append(item)
        if len(selected) >= crop_count:
            break
    return selected


def _image_modules(*, image_module: Any | None, image_stat_module: Any | None) -> tuple[Any, Any]:
    if image_module is not None and image_stat_module is not None:
        return image_module, image_stat_module
    try:
        from PIL import Image, ImageStat  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on optional local toolchain
        raise RuntimeError("Visual crop generation requires Pillow. Install it with `python3 -m pip install pillow`.") from exc
    return image_module or Image, image_stat_module or ImageStat


def _safe_filename_stem(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return cleaned.strip(".-") or "camera"


def _crop_image(*, source_path: Path, box: tuple[int, int, int, int], output_path: Path, image_module: Any) -> None:
    with image_module.open(source_path) as opened:
        image = opened.convert("RGB")
        try:
            crop = image.crop(box)
            try:
                crop.save(output_path)  # NOSONAR - output root is validated before crop generation.
            finally:
                crop.close()
        finally:
            image.close()


def _write_crop_triplet(
    *,
    camera_name: str,
    crop_index: int,
    artifacts: Mapping[str, object],
    box: tuple[int, int, int, int],
    run_dir: Path,
    image_module: Any,
) -> dict[str, Path]:
    safe_camera = _safe_filename_stem(camera_name)
    outputs: dict[str, Path] = {}
    for key, label in CROP_IMAGE_COLUMNS:
        source_path = artifacts.get(key)
        if not isinstance(source_path, Path):
            continue
        safe_label = _safe_filename_stem(label.lower().replace(" ", "-"))
        output_path = run_dir / f"{safe_camera}-crop-{crop_index:02d}-{safe_label}.png"
        _crop_image(source_path=source_path, box=box, output_path=output_path, image_module=image_module)
        outputs[key] = output_path
    return outputs


def _display_crop_outputs(outputs: Mapping[str, Path]) -> dict[str, str]:
    return {key: _display_input_path(path) for key, path in outputs.items()}


def _contact_sheet_outputs(outputs: Mapping[str, Path]) -> dict[str, str]:
    return {key: str(path) for key, path in outputs.items()}


def _selected_camera_names(
    visual_artifacts_by_camera: Mapping[str, Mapping[str, object]],
    requested_cameras: Sequence[str] | None,
) -> list[str]:
    if requested_cameras:
        return [camera for camera in requested_cameras if camera in visual_artifacts_by_camera]
    return sorted(visual_artifacts_by_camera)


def _required_visual_artifacts(artifacts: Mapping[str, object]) -> bool:
    return all(isinstance(artifacts.get(key), Path) for key, _label in CROP_IMAGE_COLUMNS)


def generate_visual_crop_report(
    comparison_summary: Mapping[str, object],
    *,
    comparison_summary_path: Path,
    paths: VisualCropPaths,
    camera_names: Sequence[str] | None = None,
    crop_size: tuple[int, int] = DEFAULT_CROP_SIZE,
    crops_per_camera: int = DEFAULT_CROPS_PER_CAMERA,
    generated_at: dt.datetime | None = None,
    trusted_output_root: Path | None = None,
    image_module: Any | None = None,
    image_stat_module: Any | None = None,
) -> dict[str, object]:
    image_module, image_stat_module = _image_modules(
        image_module=image_module,
        image_stat_module=image_stat_module,
    )
    output_root = DEFAULT_OUTPUT_ROOT if trusted_output_root is None else trusted_output_root
    _assert_output_paths(paths, trusted_output_root=output_root)
    # Safe: output paths are timestamped beneath the trusted debug report root.
    paths.run_dir.mkdir(parents=True, exist_ok=True)  # NOSONAR
    visual_artifacts_by_camera = comparison_visual_artifacts_from_summary(
        comparison_summary,
        summary_path=comparison_summary_path,
    )
    camera_rows = []
    contact_sheet_entries: list[dict[str, object]] = []
    for camera_name in _selected_camera_names(visual_artifacts_by_camera, camera_names):
        artifacts = visual_artifacts_by_camera[camera_name]
        if not _required_visual_artifacts(artifacts):
            camera_rows.append({"camera": camera_name, "status": "missing_required_artifacts", "crops": []})
            continue
        diff_path = artifacts["diff"]
        if not isinstance(diff_path, Path):
            camera_rows.append({"camera": camera_name, "status": "missing_diff", "crops": []})
            continue
        crops = []
        for crop_index, crop in enumerate(
            find_hotspot_crop_boxes(
                diff_path,
                crop_size=crop_size,
                crop_count=crops_per_camera,
                image_module=image_module,
                image_stat_module=image_stat_module,
            ),
            start=1,
        ):
            box = crop["box"]
            if not isinstance(box, tuple):
                continue
            outputs = _write_crop_triplet(
                camera_name=camera_name,
                crop_index=crop_index,
                artifacts=artifacts,
                box=box,
                run_dir=paths.run_dir,
                image_module=image_module,
            )
            crop_row = {
                "index": crop_index,
                "box": list(box),
                "score": crop["score"],
                "outputs": _display_crop_outputs(outputs),
            }
            crops.append(crop_row)
            contact_sheet_entries.append(
                {
                    "camera": f"{camera_name} crop {crop_index}",
                    "outputs": _contact_sheet_outputs(outputs),
                }
            )
        status = "cropped" if crops else "no_hotspot_crops"
        camera_rows.append({"camera": camera_name, "status": status, "crops": crops})
    contact_sheet = build_all_cameras_contact_sheet(entries=contact_sheet_entries, output_path=paths.contact_sheet_path)
    generated = generated_at or dt.datetime.now(dt.timezone.utc)
    return {
        "generated": generated.astimezone(dt.timezone.utc).isoformat(),
        "comparison_summary_json": _display_input_path(comparison_summary_path),
        "crop_size": {"width": crop_size[0], "height": crop_size[1]},
        "crops_per_camera": crops_per_camera,
        "camera_count": len(camera_rows),
        "crop_count": sum(len(row.get("crops", [])) for row in camera_rows),
        "contact_sheet": _display_input_path(contact_sheet) if contact_sheet is not None else None,
        "cameras": camera_rows,
    }


def _markdown_cell(value: object) -> str:
    if value is None or value == "":
        text = "-"
    elif isinstance(value, list):
        text = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    else:
        text = str(value)
    return text.replace("\n", " ").replace("|", "\\|")


def _markdown_table_row(cells: Iterable[object]) -> str:
    return "| " + " | ".join(_markdown_cell(cell) for cell in cells) + " |"


def _summary_crop_size_label(report: Mapping[str, object]) -> str:
    crop_size = report.get("crop_size")
    if isinstance(crop_size, Mapping):
        return f"{crop_size.get('width')}x{crop_size.get('height')}"
    return "-"


def _summary_header_lines(report: Mapping[str, object]) -> list[str]:
    lines = [
        "# Mapbox Outdoors visual crop report",
        "",
        f"Generated: {report.get('generated')}",
        f"Comparison summary input: `{report.get('comparison_summary_json')}`",
        f"Crop size: `{_summary_crop_size_label(report)}`",
        f"Crops per camera: `{report.get('crops_per_camera')}`",
        f"Focused cameras: `{report.get('camera_count')}`",
        f"Generated crops: `{report.get('crop_count')}`",
    ]
    if report.get("contact_sheet"):
        lines.append(f"Crop contact sheet: `{report.get('contact_sheet')}`")
    return lines


def _summary_table_intro_lines() -> list[str]:
    return [
        "",
        (
            "Crops are selected from the highest-delta windows in the comparison diff image, "
            "then applied to the matching Mapbox GL, QGIS, and diff artifacts."
        ),
        "",
        "| Camera | Crop | Box | Score | Mapbox GL | QGIS render | Diff |",
        "| --- | ---: | --- | ---: | --- | --- | --- |",
    ]


def _summary_crop_row(camera: Mapping[str, object], crop: Mapping[str, object]) -> str:
    outputs = crop.get("outputs")
    output_paths = outputs if isinstance(outputs, Mapping) else {}
    return _markdown_table_row(
        [
            camera.get("camera"),
            crop.get("index"),
            crop.get("box"),
            f"{float(crop.get('score', 0.0)):.0f}",
            f"`{output_paths.get('browser_reference')}`",
            f"`{output_paths.get('qgis_vector_render')}`",
            f"`{output_paths.get('diff')}`",
        ]
    )


def _summary_camera_rows(camera: Mapping[str, object]) -> list[str]:
    crops = camera.get("crops")
    crop_rows = crops if isinstance(crops, list) else []
    if not crop_rows:
        return [_markdown_table_row([camera.get("camera"), "-", camera.get("status"), "-", "-", "-", "-"])]
    return [_summary_crop_row(camera, crop) for crop in crop_rows if isinstance(crop, Mapping)]


def build_summary_markdown(report: Mapping[str, object]) -> str:
    lines = _summary_header_lines(report)
    lines.extend(_summary_table_intro_lines())
    for camera in report.get("cameras", []):
        if not isinstance(camera, Mapping):
            continue
        lines.extend(_summary_camera_rows(camera))
    return "\n".join(lines) + "\n"


def _assert_output_paths(paths: VisualCropPaths, *, trusted_output_root: Path) -> None:
    root = trusted_output_root.resolve()
    for output_path in (paths.json_path, paths.summary_path, paths.contact_sheet_path):
        if not output_path.parent.resolve().is_relative_to(root):
            raise ValueError(f"Visual crop output must stay under {trusted_output_root}")


def write_report(
    report: Mapping[str, object],
    paths: VisualCropPaths,
    *,
    trusted_output_root: Path | None = None,
) -> None:
    output_root = DEFAULT_OUTPUT_ROOT if trusted_output_root is None else trusted_output_root
    _assert_output_paths(paths, trusted_output_root=output_root)
    with paths.json_path.open("w", encoding="utf-8") as handle:  # NOSONAR
        json.dump(report, handle, indent=2, sort_keys=True)  # NOSONAR
        handle.write("\n")
    with paths.summary_path.open("w", encoding="utf-8") as handle:  # NOSONAR
        handle.write(build_summary_markdown(report))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Crop the highest-delta regions from existing Mapbox Outdoors comparison artifacts."
    )
    parser.add_argument(
        "--comparison-summary-json",
        required=True,
        type=Path,
        help="All-camera comparison summary.json.",
    )
    parser.add_argument(
        "--camera",
        action="append",
        default=[],
        help="Camera to crop. May be repeated. Defaults to all cameras with complete visual artifacts.",
    )
    parser.add_argument(
        "--crop-size",
        default=DEFAULT_CROP_SIZE,
        type=parse_crop_size,
        help="Crop window size as WIDTHxHEIGHT. Defaults to 320x240.",
    )
    parser.add_argument(
        "--crops-per-camera",
        default=DEFAULT_CROPS_PER_CAMERA,
        type=int,
        help="Number of non-overlapping high-delta crops to write per camera.",
    )
    return parser


def _load_cli_json_object(parser: argparse.ArgumentParser, path: Path, *, label: str) -> dict[str, object]:
    try:
        return load_json_object(path)
    except FileNotFoundError:
        parser.error(f"{label} not found: {path}")
    except json.JSONDecodeError as error:
        parser.error(f"{label} is not valid JSON: {path}: {error.msg}")
    except ValueError as error:
        parser.error(str(error))
    raise AssertionError("argparse error should exit")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.crops_per_camera <= 0:
        parser.error("--crops-per-camera must be greater than zero")
    comparison_summary = _load_cli_json_object(
        parser,
        args.comparison_summary_json,
        label="Comparison summary JSON",
    )
    paths = build_visual_crop_paths(build_run_directory())
    try:
        report = generate_visual_crop_report(
            comparison_summary,
            comparison_summary_path=args.comparison_summary_json,
            paths=paths,
            camera_names=args.camera,
            crop_size=args.crop_size,
            crops_per_camera=args.crops_per_camera,
        )
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    write_report(report, paths)
    print(paths.summary_path)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised manually
    raise SystemExit(main())
