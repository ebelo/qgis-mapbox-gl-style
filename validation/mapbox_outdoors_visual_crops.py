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
        COMPARISON_VISUAL_METRIC_KEYS,
        REPO_ROOT,
        _display_input_path,
        _largest_non_auxiliary_stroke_delta_rows,
        _non_auxiliary_dash_mismatch_rows,
        _top_count_labels,
        build_run_directory as _build_focus_run_directory,
        comparison_visual_artifacts_from_summary,
        load_json_object,
    )
except ImportError:  # pragma: no cover - direct script execution
    from mapbox_outdoors_comparison import build_all_cameras_contact_sheet  # type: ignore[no-redef]
    from mapbox_outdoors_path_pedestrian_focus import (  # type: ignore[no-redef]
        COMPARISON_VISUAL_METRIC_KEYS,
        REPO_ROOT,
        _display_input_path,
        _largest_non_auxiliary_stroke_delta_rows,
        _non_auxiliary_dash_mismatch_rows,
        _top_count_labels,
        build_run_directory as _build_focus_run_directory,
        comparison_visual_artifacts_from_summary,
        load_json_object,
    )

DEFAULT_OUTPUT_ROOT = REPO_ROOT / "debug" / "mapbox-outdoors-visual-crops"
DEFAULT_CROP_SIZE = (320, 240)
DEFAULT_CROPS_PER_CAMERA = 3
DEFAULT_FOCUS_DASH_CUE_LIMIT = 2
DEFAULT_FOCUS_STROKE_CUE_LIMIT = 3
MIN_SCORE_FOR_CROP = 1.0
MAX_OVERLAP_RATIO = 0.35
CROP_IMAGE_COLUMNS = (
    ("browser_reference", "Mapbox GL"),
    ("qgis_vector_render", "QGIS"),
    ("diff", "Diff"),
)
COMPARISON_CONTEXT_KEYS = (
    "status",
    "artifact_status",
    *COMPARISON_VISUAL_METRIC_KEYS,
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


def _non_empty_focus_value(value: object) -> bool:
    return value is not None and value != "" and value != []


def _focus_cue_from_row(row: Mapping[str, object], keys: Sequence[str]) -> dict[str, object]:
    cue = {key: row.get(key) for key in keys if _non_empty_focus_value(row.get(key))}
    candidate_types = _top_count_labels(row.get("decoded_candidate_type_counts"))
    if candidate_types:
        cue["candidate_types"] = candidate_types
    return cue


def _has_meaningful_width_delta(row: Mapping[str, object]) -> bool:
    value = row.get("line_width_abs_delta_mm")
    return isinstance(value, (int, float)) and abs(float(value)) > 1e-12


def _path_pedestrian_focus_cues_by_camera(
    focus_report: Mapping[str, object] | None,
    *,
    stroke_limit: int = DEFAULT_FOCUS_STROKE_CUE_LIMIT,
    dash_limit: int = DEFAULT_FOCUS_DASH_CUE_LIMIT,
) -> dict[str, dict[str, list[dict[str, object]]]]:
    if not focus_report:
        return {}
    cameras = focus_report.get("cameras")
    if not isinstance(cameras, list):
        return {}
    cues_by_camera: dict[str, dict[str, list[dict[str, object]]]] = {}
    for camera in cameras:
        if not isinstance(camera, Mapping):
            continue
        camera_name = str(camera.get("camera") or "")
        if not camera_name:
            continue
        width_delta_rows = [
            row
            for row in _largest_non_auxiliary_stroke_delta_rows([camera], limit=10_000)
            if _has_meaningful_width_delta(row)
        ][: max(0, stroke_limit)]
        width_rows = [
            _focus_cue_from_row(
                row,
                (
                    "source_layer_id",
                    "qgis_layer_id",
                    "decoded_candidate_count",
                    "line_width_delta_mm",
                    "line_width_abs_delta_mm",
                    "line_width_ratio",
                    "source_line_width_capped",
                ),
            )
            for row in width_delta_rows
        ]
        dash_rows = [
            _focus_cue_from_row(
                row,
                (
                    "source_layer_id",
                    "qgis_layer_id",
                    "decoded_candidate_count",
                    "source_dasharray",
                    "qgis_dasharray",
                    "line_width_delta_mm",
                    "line_width_ratio",
                ),
            )
            for row in _non_auxiliary_dash_mismatch_rows([camera])[: max(0, dash_limit)]
        ]
        if width_rows or dash_rows:
            cues_by_camera[camera_name] = {
                "stroke_width_deltas": width_rows,
                "dash_mismatches": dash_rows,
            }
    return cues_by_camera


def _camera_row_with_focus(
    *,
    camera_name: str,
    status: str,
    crops: list[dict[str, object]],
    comparison_context: Mapping[str, object] | None,
    focus_cues: Mapping[str, object] | None,
) -> dict[str, object]:
    camera_row: dict[str, object] = {"camera": camera_name, "status": status, "crops": crops}
    if comparison_context:
        camera_row["comparison"] = dict(comparison_context)
    if focus_cues:
        camera_row["path_pedestrian_focus"] = dict(focus_cues)
    return camera_row


def _comparison_context_from_artifacts(artifacts: Mapping[str, object]) -> dict[str, object]:
    return {
        key: value
        for key in COMPARISON_CONTEXT_KEYS
        if (
            isinstance((value := artifacts.get(key)), (str, int, float))
            and not isinstance(value, bool)
        )
    }


def _hotspot_crop_rows(
    *,
    camera_name: str,
    artifacts: Mapping[str, object],
    diff_path: Path,
    paths: VisualCropPaths,
    crop_size: tuple[int, int],
    crops_per_camera: int,
    image_module: Any,
    image_stat_module: Any,
) -> tuple[str, list[dict[str, object]], list[dict[str, object]]]:
    crops: list[dict[str, object]] = []
    contact_sheet_entries: list[dict[str, object]] = []
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
        crops.append(
            {
                "index": crop_index,
                "box": list(box),
                "score": crop["score"],
                "outputs": _display_crop_outputs(outputs),
            }
        )
        contact_sheet_entries.append(
            {
                "camera": f"{camera_name} crop {crop_index}",
                "outputs": _contact_sheet_outputs(outputs),
            }
        )
    status = "cropped" if crops else "no_hotspot_crops"
    return status, crops, contact_sheet_entries


def _camera_visual_crop_rows(
    *,
    camera_name: str,
    artifacts: Mapping[str, object],
    paths: VisualCropPaths,
    crop_size: tuple[int, int],
    crops_per_camera: int,
    image_module: Any,
    image_stat_module: Any,
) -> tuple[str, list[dict[str, object]], list[dict[str, object]]]:
    if not _required_visual_artifacts(artifacts):
        return "missing_required_artifacts", [], []
    diff_path = artifacts["diff"]
    if not isinstance(diff_path, Path):
        return "missing_diff", [], []
    return _hotspot_crop_rows(
        camera_name=camera_name,
        artifacts=artifacts,
        diff_path=diff_path,
        paths=paths,
        crop_size=crop_size,
        crops_per_camera=crops_per_camera,
        image_module=image_module,
        image_stat_module=image_stat_module,
    )


def generate_visual_crop_report(
    comparison_summary: Mapping[str, object],
    *,
    comparison_summary_path: Path,
    paths: VisualCropPaths,
    path_pedestrian_focus_report: Mapping[str, object] | None = None,
    path_pedestrian_focus_report_path: Path | None = None,
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
    focus_cues_by_camera = _path_pedestrian_focus_cues_by_camera(path_pedestrian_focus_report)
    camera_rows = []
    contact_sheet_entries: list[dict[str, object]] = []
    for camera_name in _selected_camera_names(visual_artifacts_by_camera, camera_names):
        status, crops, crop_contact_entries = _camera_visual_crop_rows(
            camera_name=camera_name,
            artifacts=visual_artifacts_by_camera[camera_name],
            paths=paths,
            crop_size=crop_size,
            crops_per_camera=crops_per_camera,
            image_module=image_module,
            image_stat_module=image_stat_module,
        )
        camera_rows.append(
            _camera_row_with_focus(
                camera_name=camera_name,
                status=status,
                crops=crops,
                comparison_context=_comparison_context_from_artifacts(
                    visual_artifacts_by_camera[camera_name]
                ),
                focus_cues=focus_cues_by_camera.get(camera_name),
            )
        )
        contact_sheet_entries.extend(crop_contact_entries)
    contact_sheet = build_all_cameras_contact_sheet(entries=contact_sheet_entries, output_path=paths.contact_sheet_path)
    generated = generated_at or dt.datetime.now(dt.timezone.utc)
    report = {
        "generated": generated.astimezone(dt.timezone.utc).isoformat(),
        "comparison_summary_json": _display_input_path(comparison_summary_path),
        "comparison_summary_run": _comparison_summary_run_metadata(
            comparison_summary,
            comparison_summary_path,
        ),
        "crop_size": {"width": crop_size[0], "height": crop_size[1]},
        "crops_per_camera": crops_per_camera,
        "camera_count": len(camera_rows),
        "crop_count": sum(len(row.get("crops", [])) for row in camera_rows),
        "contact_sheet": _display_input_path(contact_sheet) if contact_sheet is not None else None,
        "cameras": camera_rows,
    }
    if path_pedestrian_focus_report_path is not None:
        report["path_pedestrian_focus_json"] = _display_input_path(
            path_pedestrian_focus_report_path
        )
        focus_comparison_paths = _focus_comparison_summary_paths(path_pedestrian_focus_report)
        if focus_comparison_paths:
            report["path_pedestrian_focus_comparison_summary_jsons"] = focus_comparison_paths
            report["path_pedestrian_focus_comparison_match"] = (
                report["comparison_summary_json"] in focus_comparison_paths
            )
    return report


def _focus_comparison_summary_paths(focus_report: Mapping[str, object] | None) -> list[str]:
    if not focus_report:
        return []
    input_artifacts = focus_report.get("input_artifacts")
    if not isinstance(input_artifacts, Mapping):
        return []
    raw_paths = input_artifacts.get("comparison_summary_jsons")
    if isinstance(raw_paths, list):
        return [str(path) for path in raw_paths if path is not None]
    raw_runs = input_artifacts.get("comparison_summary_runs")
    if isinstance(raw_runs, list):
        return [
            path
            for item in raw_runs
            if isinstance(item, Mapping)
            and isinstance((path := item.get("path")), str)
            and path
        ]
    return []


def _comparison_summary_run_metadata(
    comparison_summary: Mapping[str, object],
    comparison_summary_path: Path,
) -> dict[str, object]:
    run_metadata: dict[str, object] = {"path": _display_input_path(comparison_summary_path)}
    for key in ("generated_at", "style_url"):
        value = comparison_summary.get(key)
        if isinstance(value, str) and value:
            run_metadata[key] = value
    return run_metadata


def _format_focus_number(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):.4g}"
    return str(value)


def _candidate_focus_summary(cue: Mapping[str, object]) -> str | None:
    candidate_count = cue.get("decoded_candidate_count")
    candidate_types = cue.get("candidate_types")
    if isinstance(candidate_types, list) and candidate_types:
        type_summary = ", ".join(str(value) for value in candidate_types[:3])
        if candidate_count is not None:
            return f"candidates={candidate_count} ({type_summary})"
        return f"candidate_types={type_summary}"
    if candidate_count is not None:
        return f"candidates={candidate_count}"
    return None


def _stroke_focus_cue_summary(cue: Mapping[str, object]) -> str:
    parts = [f"{cue.get('source_layer_id')}->{cue.get('qgis_layer_id')}"]
    if cue.get("line_width_delta_mm") is not None:
        parts.append(f"delta={_format_focus_number(cue.get('line_width_delta_mm'))}mm")
    if cue.get("line_width_ratio") is not None:
        parts.append(f"ratio={_format_focus_number(cue.get('line_width_ratio'))}")
    candidate_summary = _candidate_focus_summary(cue)
    if candidate_summary is not None:
        parts.append(candidate_summary)
    if cue.get("source_line_width_capped") is True:
        parts.append("source-capped")
    return " ".join(parts)


def _dash_focus_cue_summary(cue: Mapping[str, object]) -> str:
    parts = [
        f"{cue.get('source_layer_id')}->{cue.get('qgis_layer_id')}",
        f"dash={_compact_focus_value(cue.get('source_dasharray'))}!={_compact_focus_value(cue.get('qgis_dasharray'))}",
    ]
    candidate_summary = _candidate_focus_summary(cue)
    if candidate_summary is not None:
        parts.append(candidate_summary)
    return " ".join(parts)


def _compact_focus_value(value: object) -> str:
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    return str(value)


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
    comparison_summary_run = _comparison_summary_run_markdown(report.get("comparison_summary_run"))
    if comparison_summary_run:
        lines.append(f"Comparison summary run: {comparison_summary_run}")
    if report.get("path_pedestrian_focus_json"):
        lines.append(f"Path/pedestrian focus input: `{report.get('path_pedestrian_focus_json')}`")
    focus_comparison_paths = report.get("path_pedestrian_focus_comparison_summary_jsons")
    if isinstance(focus_comparison_paths, list) and focus_comparison_paths:
        lines.append(
            "Path/pedestrian focus comparison inputs: "
            f"`{', '.join(str(path) for path in focus_comparison_paths)}`"
        )
        match = report.get("path_pedestrian_focus_comparison_match")
        if match is not None:
            lines.append(
                "Path/pedestrian focus comparison match: "
                f"`{match}`"
            )
    return lines


def _comparison_summary_run_markdown(value: object) -> str:
    if not isinstance(value, Mapping):
        return ""
    path_value = value.get("path")
    if not isinstance(path_value, str) or not path_value:
        return ""
    details = [
        f"{key}={detail_value}"
        for key in ("generated_at", "style_url")
        if isinstance((detail_value := value.get(key)), str) and detail_value
    ]
    suffix = f" ({', '.join(details)})" if details else ""
    return f"`{path_value}`{suffix}"


def _summary_table_intro_lines() -> list[str]:
    return [
        "",
        (
            "Crops are selected from the highest-delta windows in the comparison diff image, "
            "then applied to the matching Mapbox GL, QGIS, and diff artifacts. "
            "Comparison metric columns come from the same all-camera comparison summary."
        ),
        "",
        "| Camera | Comparison status | Artifact status | Changed ratio | Mean delta | RMS delta | Crop status | Crop | Box | Score | Mapbox GL | QGIS render | Diff |",
        "| --- | --- | --- | ---: | ---: | ---: | --- | ---: | --- | ---: | --- | --- | --- |",
    ]


def _comparison_context(camera: Mapping[str, object]) -> Mapping[str, object]:
    comparison = camera.get("comparison")
    return comparison if isinstance(comparison, Mapping) else {}


def _comparison_context_cell(camera: Mapping[str, object], key: str) -> object:
    value = _comparison_context(camera).get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{float(value):.4f}"
    return value


def _summary_crop_row(camera: Mapping[str, object], crop: Mapping[str, object]) -> str:
    outputs = crop.get("outputs")
    output_paths = outputs if isinstance(outputs, Mapping) else {}
    return _markdown_table_row(
        [
            camera.get("camera"),
            _comparison_context_cell(camera, "status"),
            _comparison_context_cell(camera, "artifact_status"),
            _comparison_context_cell(camera, "changed_pixel_ratio"),
            _comparison_context_cell(camera, "normalized_mean_absolute_channel_delta"),
            _comparison_context_cell(camera, "normalized_rms_channel_delta"),
            camera.get("status"),
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
        return [
            _markdown_table_row(
                [
                    camera.get("camera"),
                    _comparison_context_cell(camera, "status"),
                    _comparison_context_cell(camera, "artifact_status"),
                    _comparison_context_cell(camera, "changed_pixel_ratio"),
                    _comparison_context_cell(camera, "normalized_mean_absolute_channel_delta"),
                    _comparison_context_cell(camera, "normalized_rms_channel_delta"),
                    camera.get("status"),
                    "-",
                    "-",
                    "-",
                    "-",
                    "-",
                    "-",
                ]
            )
        ]
    return [_summary_crop_row(camera, crop) for crop in crop_rows if isinstance(crop, Mapping)]


def _summary_focus_cue_lines(report: Mapping[str, object]) -> list[str]:
    rows: list[list[object]] = []
    for camera in report.get("cameras", []):
        if not isinstance(camera, Mapping):
            continue
        focus = camera.get("path_pedestrian_focus")
        if not isinstance(focus, Mapping):
            continue
        stroke_cues = focus.get("stroke_width_deltas")
        dash_cues = focus.get("dash_mismatches")
        stroke_rows = stroke_cues if isinstance(stroke_cues, list) else []
        dash_rows = dash_cues if isinstance(dash_cues, list) else []
        rows.append(
            [
                camera.get("camera"),
                [
                    _stroke_focus_cue_summary(cue)
                    for cue in stroke_rows
                    if isinstance(cue, Mapping)
                ],
                [
                    _dash_focus_cue_summary(cue)
                    for cue in dash_rows
                    if isinstance(cue, Mapping)
                ],
            ]
        )
    if not rows:
        return []
    lines = ["", "## Path/pedestrian focus cues", ""]
    lines.extend(
        [
            (
                "Shows the strongest per-camera path/pedestrian stroke cues from the focus report "
                "next to visual hotspots, so crop review can distinguish candidate-backed style gaps "
                "from capped-width or zero-candidate artifacts."
            ),
            "",
            "| Camera | Stroke width cues | Dash mismatch cues |",
            "| --- | --- | --- |",
        ]
    )
    lines.extend(_markdown_table_row(row) for row in rows)
    return lines


def build_summary_markdown(report: Mapping[str, object]) -> str:
    lines = _summary_header_lines(report)
    lines.extend(_summary_table_intro_lines())
    for camera in report.get("cameras", []):
        if not isinstance(camera, Mapping):
            continue
        lines.extend(_summary_camera_rows(camera))
    lines.extend(_summary_focus_cue_lines(report))
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
        "--path-pedestrian-focus-json",
        type=Path,
        help="Optional path-pedestrian-focus.json to annotate visual crops with per-camera stroke cues.",
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
    path_pedestrian_focus_report = None
    if args.path_pedestrian_focus_json is not None:
        path_pedestrian_focus_report = _load_cli_json_object(
            parser,
            args.path_pedestrian_focus_json,
            label="Path/pedestrian focus JSON",
        )
    paths = build_visual_crop_paths(build_run_directory())
    try:
        report = generate_visual_crop_report(
            comparison_summary,
            comparison_summary_path=args.comparison_summary_json,
            paths=paths,
            path_pedestrian_focus_report=path_pedestrian_focus_report,
            path_pedestrian_focus_report_path=args.path_pedestrian_focus_json,
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
