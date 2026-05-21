from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from .mapbox_outdoors_comparison import build_all_cameras_contact_sheet
    from .mapbox_outdoors_path_pedestrian_focus import (
        COMPARISON_VISUAL_METRIC_KEYS,
        REPO_ROOT,
        _camera_non_auxiliary_stroke_delta_rows,
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
        _camera_non_auxiliary_stroke_delta_rows,
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
DEFAULT_STYLE_AUDIT_SAMPLE_LIMIT = 5
DEFAULT_STYLE_AUDIT_SIMPLIFICATION_LIMIT = 3
DEFAULT_CROP_COLOR_DELTA_SUMMARY_LIMIT = 5
DEFAULT_CROP_COLOR_MOVEMENT_GROUP_LIMIT = 8
DEFAULT_FOCUS_COVERAGE_SAMPLE_LIMIT = 3
MAX_STYLE_AUDIT_SIMPLIFICATION_VALUE_LENGTH = 96
MIN_SCORE_FOR_CROP = 1.0
MAX_OVERLAP_RATIO = 0.35
CROP_IMAGE_COLUMNS = (
    ("browser_reference", "Mapbox GL"),
    ("qgis_vector_render", "QGIS"),
    ("diff", "Diff"),
)
CROP_COLOR_DELTA_CHANNELS = ("red", "green", "blue")
CROP_COLOR_METRIC_KEYS = ("browser_reference", "qgis_vector_render")
COMPARISON_CONTEXT_KEYS = (
    "status",
    "artifact_status",
    *COMPARISON_VISUAL_METRIC_KEYS,
)
STYLE_AUDIT_AREA_FILL_SECTIONS = (
    {
        "key": "terrain_landcover",
        "label": "Terrain/landcover",
        "candidates": "terrain_landcover_palette_candidates",
        "by_source_layer": "terrain_landcover_palette_candidates_by_source_layer",
        "by_type": "terrain_landcover_palette_candidates_by_type",
        "simplified_by_property": "terrain_landcover_palette_simplified_by_property",
        "qgis_dependent_by_property": "terrain_landcover_palette_qgis_dependent_by_property",
    },
    {
        "key": "airport_special_landuse",
        "label": "Airport/special landuse",
        "candidates": "airport_special_landuse_candidates",
        "by_source_layer": "airport_special_landuse_candidates_by_source_layer",
        "by_type": "airport_special_landuse_candidates_by_type",
        "simplified_by_property": "airport_special_landuse_simplified_by_property",
        "qgis_dependent_by_property": "airport_special_landuse_qgis_dependent_by_property",
    },
)


@dataclass(frozen=True)
class VisualCropPaths:
    run_dir: Path
    json_path: Path
    summary_path: Path
    contact_sheet_path: Path


@dataclass(frozen=True)
class VisualCropAnnotationInputs:
    path_pedestrian_focus_report: Mapping[str, object] | None = None
    path_pedestrian_focus_report_path: Path | None = None
    style_audit_report: Mapping[str, object] | None = None
    style_audit_report_path: Path | None = None


@dataclass(frozen=True)
class _FocusAnnotationContext:
    comparison_paths: list[str]
    comparison_match: bool | None
    cues_by_camera: dict[str, dict[str, list[dict[str, object]]]]
    coverage_rows: list[dict[str, object]]


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


def _crop_color_metric(
    *,
    image_path: Path,
    image_module: Any,
    image_stat_module: Any,
) -> dict[str, object]:
    with image_module.open(image_path) as image:
        rgb_image = image.convert("RGB")
        try:
            stat = image_stat_module.Stat(rgb_image)
            mean_values = list(getattr(stat, "mean", []))
            if not mean_values:
                pixel_count = max(1, rgb_image.width * rgb_image.height)
                mean_values = [value / pixel_count for value in getattr(stat, "sum", [])]
            mean_values = _three_channel_color_values(mean_values)
            mean_rgb = [round(float(value), 3) for value in mean_values[:3]]
            luminance = round(
                (0.2126 * mean_rgb[0]) + (0.7152 * mean_rgb[1]) + (0.0722 * mean_rgb[2]),
                3,
            )
            return {"mean_rgb": mean_rgb, "luminance": luminance}
        finally:
            if rgb_image is not image:
                rgb_image.close()


def _three_channel_color_values(values: Sequence[object]) -> list[float]:
    numeric_values = [
        float(value)
        for value in values
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if not numeric_values:
        return [0.0, 0.0, 0.0]
    if len(numeric_values) == 1:
        return [numeric_values[0], numeric_values[0], numeric_values[0]]
    if len(numeric_values) == 2:
        return [numeric_values[0], numeric_values[1], 0.0]
    return numeric_values[:3]


def _dominant_crop_color_delta(rgb_values: Sequence[float]) -> dict[str, object] | None:
    if len(rgb_values) < len(CROP_COLOR_DELTA_CHANNELS):
        return None
    index, value = max(
        enumerate(rgb_values[: len(CROP_COLOR_DELTA_CHANNELS)]),
        key=lambda item: (abs(item[1]), -item[0]),
    )
    if value == 0:
        return None
    return {
        "channel": CROP_COLOR_DELTA_CHANNELS[index],
        "delta": round(value, 3),
        "direction": "higher" if value > 0 else "lower",
    }


def _crop_luminance_delta_direction(value: float) -> str:
    if value > 0:
        return "lighter"
    if value < 0:
        return "darker"
    return "unchanged"


def _crop_color_delta(
    browser: Mapping[str, object],
    qgis: Mapping[str, object],
) -> dict[str, object] | None:
    browser_rgb = browser.get("mean_rgb")
    qgis_rgb = qgis.get("mean_rgb")
    if not isinstance(browser_rgb, list) or not isinstance(qgis_rgb, list):
        return None
    mean_rgb = [
        round(float(qgis_value) - float(browser_value), 3)
        for browser_value, qgis_value in zip(browser_rgb[:3], qgis_rgb[:3])
    ]
    luminance = round(
        float(qgis.get("luminance", 0.0)) - float(browser.get("luminance", 0.0)),
        3,
    )
    delta: dict[str, object] = {
        "mean_rgb": mean_rgb,
        "luminance": luminance,
        "luminance_direction": _crop_luminance_delta_direction(luminance),
    }
    dominant_rgb_delta = _dominant_crop_color_delta(mean_rgb)
    if dominant_rgb_delta is not None:
        delta["dominant_rgb_delta"] = dominant_rgb_delta
    return delta


def _crop_color_metrics(
    *,
    outputs: Mapping[str, Path],
    image_module: Any,
    image_stat_module: Any,
) -> dict[str, object]:
    metrics = {
        key: _crop_color_metric(
            image_path=outputs[key],
            image_module=image_module,
            image_stat_module=image_stat_module,
        )
        for key in CROP_COLOR_METRIC_KEYS
        if key in outputs
    }
    browser = metrics.get("browser_reference")
    qgis = metrics.get("qgis_vector_render")
    if isinstance(browser, Mapping) and isinstance(qgis, Mapping):
        delta = _crop_color_delta(browser, qgis)
        if delta is not None:
            metrics["delta"] = delta
    return metrics


def _contact_sheet_outputs(outputs: Mapping[str, Path]) -> dict[str, str]:
    return {key: str(path) for key, path in outputs.items()}


def _selected_camera_names(
    visual_artifacts_by_camera: Mapping[str, Mapping[str, object]],
    requested_cameras: Sequence[str] | None,
    focus_camera_names: set[str] | None = None,
) -> list[str]:
    if requested_cameras:
        camera_names = [
            camera
            for camera in requested_cameras
            if camera in visual_artifacts_by_camera
        ]
    else:
        camera_names = sorted(visual_artifacts_by_camera)
    if focus_camera_names is None:
        return camera_names
    return [camera for camera in camera_names if camera in focus_camera_names]


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


def _decoded_candidate_count(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return 0


def _has_decoded_candidates(row: Mapping[str, object]) -> bool:
    if _decoded_candidate_count(row.get("decoded_candidate_count")) > 0:
        return True
    candidate_type_counts = row.get("decoded_candidate_type_counts")
    if isinstance(candidate_type_counts, Mapping) and candidate_type_counts:
        return True
    candidate_types = row.get("candidate_types")
    return isinstance(candidate_types, list) and bool(candidate_types)


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
            if _has_meaningful_width_delta(row) and _has_decoded_candidates(row)
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
            for row in _non_auxiliary_dash_mismatch_rows([camera])
            if _has_decoded_candidates(row)
        ]
        dash_rows = dash_rows[: max(0, dash_limit)]
        if width_rows or dash_rows:
            cues_by_camera[camera_name] = {
                "stroke_width_deltas": width_rows,
                "dash_mismatches": dash_rows,
            }
    return cues_by_camera


def _focus_coverage_sample_labels(
    rows: Sequence[Mapping[str, object]],
    formatter: Callable[[Mapping[str, object]], str],
    *,
    sample_limit: int = DEFAULT_FOCUS_COVERAGE_SAMPLE_LIMIT,
) -> list[str]:
    if sample_limit <= 0:
        return []
    return [formatter(row) for row in rows[:sample_limit]]


def _path_pedestrian_focus_coverage_rows(
    focus_report: Mapping[str, object] | None,
) -> list[dict[str, object]]:
    if not focus_report:
        return []
    cameras = focus_report.get("cameras")
    if not isinstance(cameras, list):
        return []
    coverage_rows: list[dict[str, object]] = []
    for camera in cameras:
        if not isinstance(camera, Mapping):
            continue
        camera_name = str(camera.get("camera") or "")
        if not camera_name:
            continue
        stroke_rows = _camera_non_auxiliary_stroke_delta_rows(camera)
        dash_rows = _non_auxiliary_dash_mismatch_rows([camera])
        if not stroke_rows and not dash_rows:
            continue
        candidate_stroke_rows = [
            row for row in stroke_rows if _has_decoded_candidates(row)
        ]
        candidate_zero_delta_rows = [
            row for row in candidate_stroke_rows if not _has_meaningful_width_delta(row)
        ]
        zero_candidate_stroke_rows = [
            row for row in stroke_rows if not _has_decoded_candidates(row)
        ]
        zero_candidate_dash_rows = [
            row for row in dash_rows if not _has_decoded_candidates(row)
        ]
        coverage_rows.append(
            {
                "camera": camera_name,
                "stroke_rows": len(stroke_rows),
                "candidate_backed_stroke_rows": len(candidate_stroke_rows),
                "candidate_backed_width_delta_rows": sum(
                    1 for row in candidate_stroke_rows if _has_meaningful_width_delta(row)
                ),
                "candidate_backed_zero_delta_rows": len(candidate_zero_delta_rows),
                "zero_candidate_stroke_rows": len(zero_candidate_stroke_rows),
                "source_capped_stroke_rows": sum(
                    1 for row in stroke_rows if row.get("source_line_width_capped") is True
                ),
                "dash_mismatch_rows": len(dash_rows),
                "candidate_backed_dash_rows": sum(
                    1 for row in dash_rows if _has_decoded_candidates(row)
                ),
                "zero_candidate_dash_rows": len(zero_candidate_dash_rows),
                "candidate_zero_delta_stroke_samples": _focus_coverage_sample_labels(
                    candidate_zero_delta_rows,
                    _stroke_focus_cue_summary,
                ),
                "zero_candidate_stroke_samples": _focus_coverage_sample_labels(
                    zero_candidate_stroke_rows,
                    _stroke_focus_cue_summary,
                ),
                "zero_candidate_dash_samples": _focus_coverage_sample_labels(
                    zero_candidate_dash_rows,
                    _dash_focus_cue_summary,
                ),
                "path_line_types": _top_count_labels(camera.get("path_line_type_counts")),
                "path_line_structures": _top_count_labels(camera.get("path_line_structure_counts")),
                "step_line_structures": _top_count_labels(camera.get("step_line_structure_counts")),
                "pedestrian_line_structures": _top_count_labels(
                    camera.get("pedestrian_line_structure_counts")
                ),
            }
        )
    return coverage_rows


def _focus_annotation_context(
    annotations: VisualCropAnnotationInputs,
    *,
    comparison_summary_path: Path,
    focus_cue_cameras_only: bool,
) -> _FocusAnnotationContext:
    comparison_paths = _focus_comparison_summary_paths(
        annotations.path_pedestrian_focus_report
    )
    comparison_match = None
    if comparison_paths:
        comparison_match = _display_input_path(comparison_summary_path) in comparison_paths
    if focus_cue_cameras_only and comparison_match is False:
        raise ValueError(
            "Focus cue camera filtering requires the path/pedestrian focus report "
            "to match the comparison summary."
        )
    cues_by_camera = (
        {}
        if comparison_match is False
        else _path_pedestrian_focus_cues_by_camera(
            annotations.path_pedestrian_focus_report
        )
    )
    coverage_rows = (
        []
        if comparison_match is False
        else _path_pedestrian_focus_coverage_rows(annotations.path_pedestrian_focus_report)
    )
    return _FocusAnnotationContext(
        comparison_paths=comparison_paths,
        comparison_match=comparison_match,
        cues_by_camera=cues_by_camera,
        coverage_rows=coverage_rows,
    )


def _style_audit_summary(style_audit_report: Mapping[str, object] | None) -> Mapping[str, object]:
    if not style_audit_report:
        return {}
    summary = style_audit_report.get("summary")
    return summary if isinstance(summary, Mapping) else {}


def _style_audit_mapping_rows(rows: object) -> list[Mapping[str, object]]:
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def _style_audit_rows(summary: Mapping[str, object], key: object) -> list[Mapping[str, object]]:
    return _style_audit_mapping_rows(summary.get(str(key)))


def _style_audit_qgis_converter_warnings_by_layer(
    style_audit_report: Mapping[str, object],
) -> dict[str, Mapping[str, object]]:
    warnings_by_layer: dict[str, Mapping[str, object]] = {}
    for layer_id, layer in _style_audit_layer_items(style_audit_report):
        qgis_converter_warnings = layer.get("qgis_converter_warnings")
        if isinstance(qgis_converter_warnings, Mapping):
            warnings_by_layer[layer_id] = qgis_converter_warnings
    return warnings_by_layer


def _style_audit_layer_items(
    style_audit_report: Mapping[str, object],
) -> list[tuple[str, Mapping[str, object]]]:
    layers = style_audit_report.get("layers")
    if not isinstance(layers, list):
        return []
    layer_items: list[tuple[str, Mapping[str, object]]] = []
    for layer in layers:
        if not isinstance(layer, Mapping):
            continue
        layer_id = str(layer.get("id") or "")
        if layer_id:
            layer_items.append((layer_id, layer))
    return layer_items


def _style_audit_layers_by_id(
    style_audit_report: Mapping[str, object],
) -> dict[str, Mapping[str, object]]:
    return dict(_style_audit_layer_items(style_audit_report))


def _compact_style_audit_value(value: object) -> str:
    text = str(value)
    if len(text) <= MAX_STYLE_AUDIT_SIMPLIFICATION_VALUE_LENGTH:
        return text
    return f"{text[: MAX_STYLE_AUDIT_SIMPLIFICATION_VALUE_LENGTH - 3]}..."


def _style_audit_simplification_rows(
    candidate: Mapping[str, object],
    *,
    style_audit_layer: Mapping[str, object] | None = None,
) -> list[Mapping[str, object]]:
    simplifications = _style_audit_mapping_rows(candidate.get("qfit_simplifies"))
    if simplifications or style_audit_layer is None:
        return simplifications
    return _style_audit_mapping_rows(style_audit_layer.get("qfit_simplifies"))


def _style_audit_ordered_simplification_rows(
    simplifications: list[Mapping[str, object]],
    *,
    relevant_properties: set[str],
) -> list[Mapping[str, object]]:
    if not relevant_properties:
        return []
    return [
        row for row in simplifications if row.get("property") in relevant_properties
    ]


def _style_audit_simplification_row_sample(row: Mapping[str, object]) -> dict[str, str] | None:
    property_name = str(row.get("property") or "")
    if not property_name:
        return None
    sample = {"property": property_name}
    for value_key in ("from", "to"):
        value = row.get(value_key)
        if _non_empty_focus_value(value):
            sample[value_key] = _compact_style_audit_value(value)
    return sample


def _style_audit_qfit_simplification_sample(
    candidate: Mapping[str, object],
    *,
    style_audit_layer: Mapping[str, object] | None = None,
    sample_limit: int = DEFAULT_STYLE_AUDIT_SIMPLIFICATION_LIMIT,
) -> list[dict[str, str]]:
    if sample_limit <= 0:
        return []
    simplifications = _style_audit_simplification_rows(
        candidate,
        style_audit_layer=style_audit_layer,
    )
    relevant_properties = set(_string_values(candidate.get("qfit_simplified_control_properties")))
    selected: list[dict[str, str]] = []
    selected_properties: set[str] = set()
    for simplification in _style_audit_ordered_simplification_rows(
        simplifications,
        relevant_properties=relevant_properties,
    ):
        sample = _style_audit_simplification_row_sample(simplification)
        if sample is None or sample["property"] in selected_properties:
            continue
        selected_properties.add(sample["property"])
        selected.append(sample)
        if len(selected) >= sample_limit:
            break
    return selected


def _style_audit_candidate_base_sample(candidate: Mapping[str, object]) -> dict[str, object]:
    sample: dict[str, object] = {}
    for key in (
        "layer",
        "source_layer",
        "type",
        "zoom_band",
        "filter_operator_signature",
    ):
        value = candidate.get(key)
        if _non_empty_focus_value(value):
            sample[key] = value
    return sample


def _style_audit_candidate_control_properties(candidate: Mapping[str, object]) -> object:
    control_properties = candidate.get("terrain_landcover_palette_control_properties")
    if _non_empty_focus_value(control_properties):
        return control_properties
    return candidate.get("airport_special_landuse_control_properties")


def _style_audit_candidate_layer(
    candidate: Mapping[str, object],
    style_audit_layers_by_id: Mapping[str, Mapping[str, object]] | None,
) -> Mapping[str, object] | None:
    layer_id = str(candidate.get("layer") or "")
    if style_audit_layers_by_id is None or not layer_id:
        return None
    return style_audit_layers_by_id.get(layer_id)


def _style_audit_candidate_qgis_warnings(
    candidate: Mapping[str, object],
    qgis_converter_warnings_by_layer: Mapping[str, Mapping[str, object]] | None,
) -> object:
    qgis_converter_warnings = candidate.get("qgis_converter_warnings")
    if isinstance(qgis_converter_warnings, Mapping) or not qgis_converter_warnings_by_layer:
        return qgis_converter_warnings
    return qgis_converter_warnings_by_layer.get(str(candidate.get("layer") or ""))


def _style_audit_candidate_sample(
    candidate: Mapping[str, object],
    *,
    qgis_converter_warnings_by_layer: Mapping[str, Mapping[str, object]] | None = None,
    style_audit_layers_by_id: Mapping[str, Mapping[str, object]] | None = None,
) -> dict[str, object]:
    sample = _style_audit_candidate_base_sample(candidate)
    control_properties = _style_audit_candidate_control_properties(candidate)
    if _non_empty_focus_value(control_properties):
        sample["control_properties"] = control_properties
    qfit_simplifications = _style_audit_qfit_simplification_sample(
        candidate,
        style_audit_layer=_style_audit_candidate_layer(candidate, style_audit_layers_by_id),
    )
    if qfit_simplifications:
        sample["qfit_simplifications"] = qfit_simplifications
    for source_key, target_key in (
        ("qfit_simplified_control_properties", "qfit_simplified_properties"),
        ("qgis_dependent_control_properties", "qgis_dependent_properties"),
    ):
        value = candidate.get(source_key)
        if _non_empty_focus_value(value):
            sample[target_key] = value
    qgis_converter_warnings = _style_audit_candidate_qgis_warnings(
        candidate,
        qgis_converter_warnings_by_layer,
    )
    if isinstance(qgis_converter_warnings, Mapping):
        sample["qgis_converter_warnings"] = qgis_converter_warnings
    return sample


def _style_audit_candidate_has_non_filter_qgis_dependency(candidate: Mapping[str, object]) -> bool:
    return any(
        property_name != "filter"
        for property_name in _string_values(candidate.get("qgis_dependent_control_properties"))
    )


def _style_audit_candidate_source_type(candidate: Mapping[str, object]) -> tuple[str, str]:
    return (
        str(candidate.get("source_layer") or ""),
        str(candidate.get("type") or ""),
    )


def _style_audit_sample_candidates(
    candidates: list[Mapping[str, object]],
    *,
    sample_limit: int,
) -> list[Mapping[str, object]]:
    if sample_limit <= 0:
        return []

    selected_indexes: set[int] = set()
    covered_source_types: set[tuple[str, str]] = set()

    def add_candidate(index: int, candidate: Mapping[str, object]) -> None:
        if len(selected_indexes) >= sample_limit:
            return
        if index in selected_indexes:
            return
        selected_indexes.add(index)
        covered_source_types.add(_style_audit_candidate_source_type(candidate))

    for index, candidate in enumerate(candidates):
        if _style_audit_candidate_has_non_filter_qgis_dependency(candidate):
            add_candidate(index, candidate)

    for index, candidate in enumerate(candidates):
        source_type = _style_audit_candidate_source_type(candidate)
        if source_type not in covered_source_types:
            add_candidate(index, candidate)

    for index, candidate in enumerate(candidates):
        add_candidate(index, candidate)

    return [candidates[index] for index in sorted(selected_indexes)]


def _style_audit_filter_signature_rows(
    candidates: list[Mapping[str, object]],
) -> list[dict[str, object]]:
    counts: dict[str, int] = {}
    for candidate in candidates:
        signature = candidate.get("filter_operator_signature")
        if isinstance(signature, str) and signature:
            counts[signature] = counts.get(signature, 0) + 1
    return [
        {"filter_operator_signature": signature, "count": count}
        for signature, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _style_audit_area_fill_focus(
    style_audit_report: Mapping[str, object] | None,
    *,
    sample_limit: int = DEFAULT_STYLE_AUDIT_SAMPLE_LIMIT,
) -> list[dict[str, object]]:
    summary = _style_audit_summary(style_audit_report)
    if not summary:
        return []
    qgis_converter_warnings_by_layer = _style_audit_qgis_converter_warnings_by_layer(
        style_audit_report
    )
    style_audit_layers_by_id = _style_audit_layers_by_id(style_audit_report)
    focus_rows: list[dict[str, object]] = []
    for section in STYLE_AUDIT_AREA_FILL_SECTIONS:
        candidate_rows = summary.get(str(section["candidates"]))
        if not isinstance(candidate_rows, list):
            continue
        candidates = _style_audit_mapping_rows(candidate_rows)
        focus_rows.append(
            {
                "key": section["key"],
                "label": section["label"],
                "candidate_count": len(candidates),
                "by_source_layer": list(_style_audit_rows(summary, section["by_source_layer"])),
                "by_type": list(_style_audit_rows(summary, section["by_type"])),
                "simplified_by_property": list(
                    _style_audit_rows(summary, section["simplified_by_property"])
                ),
                "qgis_dependent_by_property": list(
                    _style_audit_rows(summary, section["qgis_dependent_by_property"])
                ),
                "filter_signatures": _style_audit_filter_signature_rows(candidates),
                "sample_candidates": [
                    _style_audit_candidate_sample(
                        candidate,
                        qgis_converter_warnings_by_layer=qgis_converter_warnings_by_layer,
                        style_audit_layers_by_id=style_audit_layers_by_id,
                    )
                    for candidate in _style_audit_sample_candidates(
                        candidates,
                        sample_limit=sample_limit,
                    )
                ],
            }
        )
    return focus_rows


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


def _resolve_report_input_path(
    path_text: str,
    *,
    report_path: Path | None,
) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    if report_path is not None:
        return report_path.parent / path
    return path


def _same_resolved_path(first: Path, second: Path) -> bool:
    return first.resolve() == second.resolve()


def _comparison_delta_candidate_summary_path(
    comparison_delta_report: Mapping[str, object],
    *,
    comparison_delta_report_path: Path | None,
) -> Path | None:
    input_artifacts = comparison_delta_report.get("input_artifacts")
    if not isinstance(input_artifacts, Mapping):
        return None
    candidate = input_artifacts.get("candidate")
    if not isinstance(candidate, Mapping):
        return None
    summary_json = candidate.get("summary_json")
    if not isinstance(summary_json, str) or not summary_json:
        return None
    return _resolve_report_input_path(
        summary_json,
        report_path=comparison_delta_report_path,
    )


def _comparison_delta_metric(row: Mapping[str, object], metric_key: str) -> float | None:
    metrics = row.get("metrics")
    if not isinstance(metrics, Mapping):
        return None
    metric = metrics.get(metric_key)
    if not isinstance(metric, Mapping):
        return None
    value = metric.get("delta")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _comparison_delta_camera_context(row: Mapping[str, object]) -> dict[str, object]:
    context: dict[str, object] = {}
    for source_key, target_key in (
        ("changed_pixel_ratio", "changed_pixel_ratio_delta"),
        ("normalized_mean_absolute_channel_delta", "mean_delta"),
        ("normalized_rms_channel_delta", "rms_delta"),
    ):
        value = _comparison_delta_metric(row, source_key)
        if value is not None:
            context[target_key] = value
    for key in (
        "baseline_status",
        "candidate_status",
        "mean_delta_direction",
        "rms_delta_direction",
    ):
        value = row.get(key)
        if isinstance(value, str) and value:
            context[key] = value
    return context


def _comparison_delta_context_by_camera(
    comparison_delta_report: Mapping[str, object] | None,
) -> dict[str, dict[str, object]]:
    if comparison_delta_report is None:
        return {}
    cameras = comparison_delta_report.get("cameras")
    if not isinstance(cameras, list):
        return {}
    rows: dict[str, dict[str, object]] = {}
    for row in cameras:
        if not isinstance(row, Mapping):
            continue
        camera = row.get("camera")
        if not isinstance(camera, str) or not camera:
            continue
        context = _comparison_delta_camera_context(row)
        if context:
            rows[camera] = context
    return rows


def _annotated_delta_camera_rows(
    cameras: object,
    delta_context_by_camera: Mapping[str, Mapping[str, object]],
) -> list[object]:
    if not isinstance(cameras, list):
        return []
    annotated_rows: list[object] = []
    for camera in cameras:
        if not isinstance(camera, Mapping):
            annotated_rows.append(camera)
            continue
        camera_row = dict(camera)
        camera_name = camera_row.get("camera")
        delta_context = (
            delta_context_by_camera.get(camera_name)
            if isinstance(camera_name, str)
            else None
        )
        if delta_context:
            camera_row["comparison_delta"] = dict(delta_context)
        annotated_rows.append(camera_row)
    return annotated_rows


def annotate_visual_crop_report_with_comparison_delta(
    report: Mapping[str, object],
    *,
    comparison_summary_path: Path,
    comparison_delta_report: Mapping[str, object],
    comparison_delta_report_path: Path,
) -> dict[str, object]:
    delta_candidate_summary_path = _comparison_delta_candidate_summary_path(
        comparison_delta_report,
        comparison_delta_report_path=comparison_delta_report_path,
    )
    delta_candidate_match = (
        _same_resolved_path(delta_candidate_summary_path, comparison_summary_path)
        if delta_candidate_summary_path is not None
        else None
    )
    delta_context_by_camera = (
        _comparison_delta_context_by_camera(comparison_delta_report)
        if delta_candidate_match is True
        else {}
    )
    annotated_report = dict(report)
    annotated_report["cameras"] = _annotated_delta_camera_rows(
        report.get("cameras"),
        delta_context_by_camera,
    )
    annotated_report["comparison_delta_json"] = _display_input_path(comparison_delta_report_path)
    if delta_candidate_summary_path is not None:
        annotated_report["comparison_delta_candidate_summary_json"] = _display_input_path(
            delta_candidate_summary_path,
        )
        annotated_report["comparison_delta_candidate_summary_match"] = delta_candidate_match
    return annotated_report


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
        color_metrics = _crop_color_metrics(
            outputs=outputs,
            image_module=image_module,
            image_stat_module=image_stat_module,
        )
        crops.append(
            {
                "index": crop_index,
                "box": list(box),
                "score": crop["score"],
                "outputs": _display_crop_outputs(outputs),
                "color_metrics": color_metrics,
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


def _add_visual_crop_annotation_metadata(
    report: dict[str, object],
    *,
    annotations: VisualCropAnnotationInputs,
    focus_context: _FocusAnnotationContext,
) -> None:
    if annotations.style_audit_report_path is not None:
        report["style_audit_json"] = _display_input_path(annotations.style_audit_report_path)
        area_fill_focus = _style_audit_area_fill_focus(annotations.style_audit_report)
        if area_fill_focus:
            report["style_audit_area_fill_focus"] = area_fill_focus
    if annotations.path_pedestrian_focus_report_path is not None:
        report["path_pedestrian_focus_json"] = _display_input_path(
            annotations.path_pedestrian_focus_report_path
        )
        if focus_context.coverage_rows:
            report["path_pedestrian_focus_coverage"] = focus_context.coverage_rows
        if focus_context.comparison_paths:
            report["path_pedestrian_focus_comparison_summary_jsons"] = (
                focus_context.comparison_paths
            )
            report["path_pedestrian_focus_comparison_match"] = (
                focus_context.comparison_match
            )


def generate_visual_crop_report(
    comparison_summary: Mapping[str, object],
    *,
    comparison_summary_path: Path,
    paths: VisualCropPaths,
    annotation_inputs: VisualCropAnnotationInputs | None = None,
    camera_names: Sequence[str] | None = None,
    focus_cue_cameras_only: bool = False,
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
    annotations = annotation_inputs or VisualCropAnnotationInputs()
    focus_context = _focus_annotation_context(
        annotations,
        comparison_summary_path=comparison_summary_path,
        focus_cue_cameras_only=focus_cue_cameras_only,
    )
    focus_camera_names = set(focus_context.cues_by_camera) if focus_cue_cameras_only else None
    camera_rows = []
    contact_sheet_entries: list[dict[str, object]] = []
    for camera_name in _selected_camera_names(
        visual_artifacts_by_camera,
        camera_names,
        focus_camera_names=focus_camera_names,
    ):
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
                focus_cues=focus_context.cues_by_camera.get(camera_name),
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
        "focus_cue_cameras_only": focus_cue_cameras_only,
        "camera_count": len(camera_rows),
        "crop_count": sum(len(row.get("crops", [])) for row in camera_rows),
        "contact_sheet": _display_input_path(contact_sheet) if contact_sheet is not None else None,
        "cameras": camera_rows,
    }
    crop_color_movement_groups = _crop_color_movement_group_records(report)
    if crop_color_movement_groups:
        report["crop_color_movement_groups"] = crop_color_movement_groups
    _add_visual_crop_annotation_metadata(
        report,
        annotations=annotations,
        focus_context=focus_context,
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
    if report.get("focus_cue_cameras_only") is True:
        lines.append("Camera filter: `candidate-backed path/pedestrian focus cues`")
    if report.get("contact_sheet"):
        lines.append(f"Crop contact sheet: `{report.get('contact_sheet')}`")
    comparison_summary_run = _comparison_summary_run_markdown(report.get("comparison_summary_run"))
    if comparison_summary_run:
        lines.append(f"Comparison summary run: {comparison_summary_run}")
    if report.get("comparison_delta_json"):
        lines.append(f"Comparison delta input: `{report.get('comparison_delta_json')}`")
    if report.get("comparison_delta_candidate_summary_json"):
        lines.append(
            "Comparison delta candidate summary: "
            f"`{report.get('comparison_delta_candidate_summary_json')}`"
        )
        match = report.get("comparison_delta_candidate_summary_match")
        if match is not None:
            lines.append(f"Comparison delta candidate match: `{match}`")
    if report.get("style_audit_json"):
        lines.append(f"Style audit input: `{report.get('style_audit_json')}`")
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


def _include_comparison_delta_columns(report: Mapping[str, object]) -> bool:
    return any(
        isinstance(camera, Mapping) and isinstance(camera.get("comparison_delta"), Mapping)
        for camera in report.get("cameras", [])
    )


def _summary_table_intro_lines(report: Mapping[str, object]) -> list[str]:
    headers = [
        "Camera",
        "Comparison status",
        "Artifact status",
        "Changed ratio",
        "Mean delta",
        "RMS delta",
    ]
    alignments = ["---", "---", "---", "---:", "---:", "---:"]
    if _include_comparison_delta_columns(report):
        headers.extend(["Mean movement", "RMS movement"])
        alignments.extend(["---:", "---:"])
    headers.extend(["Crop status", "Crop", "Box", "Score", "Mapbox GL", "QGIS render", "Diff"])
    alignments.extend(["---", "---:", "---", "---:", "---", "---", "---"])
    return [
        "",
        (
            "Crops are selected from the highest-delta windows in the comparison diff image, "
            "then applied to the matching Mapbox GL, QGIS, and diff artifacts. "
            "Comparison metric columns come from the same all-camera comparison summary; "
            "movement columns come from the optional comparison-delta report."
        ),
        "",
        _markdown_table_row(headers),
        _markdown_table_row(alignments),
    ]


def _comparison_context(camera: Mapping[str, object]) -> Mapping[str, object]:
    comparison = camera.get("comparison")
    return comparison if isinstance(comparison, Mapping) else {}


def _comparison_context_cell(camera: Mapping[str, object], key: str) -> object:
    value = _comparison_context(camera).get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{float(value):.4f}"
    return value


def _comparison_delta_context(camera: Mapping[str, object]) -> Mapping[str, object]:
    comparison_delta = camera.get("comparison_delta")
    return comparison_delta if isinstance(comparison_delta, Mapping) else {}


def _comparison_delta_context_cell(camera: Mapping[str, object], key: str) -> object:
    value = _comparison_delta_context(camera).get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{float(value):+.9f}"
    return value


def _summary_crop_row(
    camera: Mapping[str, object],
    crop: Mapping[str, object],
    *,
    include_comparison_delta: bool,
) -> str:
    outputs = crop.get("outputs")
    output_paths = outputs if isinstance(outputs, Mapping) else {}
    cells = [
        camera.get("camera"),
        _comparison_context_cell(camera, "status"),
        _comparison_context_cell(camera, "artifact_status"),
        _comparison_context_cell(camera, "changed_pixel_ratio"),
        _comparison_context_cell(camera, "normalized_mean_absolute_channel_delta"),
        _comparison_context_cell(camera, "normalized_rms_channel_delta"),
    ]
    if include_comparison_delta:
        cells.extend(
            [
                _comparison_delta_context_cell(camera, "mean_delta"),
                _comparison_delta_context_cell(camera, "rms_delta"),
            ]
        )
    cells.extend(
        [
            camera.get("status"),
            crop.get("index"),
            crop.get("box"),
            f"{float(crop.get('score', 0.0)):.0f}",
            f"`{output_paths.get('browser_reference')}`",
            f"`{output_paths.get('qgis_vector_render')}`",
            f"`{output_paths.get('diff')}`",
        ]
    )
    return _markdown_table_row(cells)


def _summary_camera_rows(
    camera: Mapping[str, object],
    *,
    include_comparison_delta: bool,
) -> list[str]:
    crops = camera.get("crops")
    crop_rows = crops if isinstance(crops, list) else []
    if not crop_rows:
        cells = [
            camera.get("camera"),
            _comparison_context_cell(camera, "status"),
            _comparison_context_cell(camera, "artifact_status"),
            _comparison_context_cell(camera, "changed_pixel_ratio"),
            _comparison_context_cell(camera, "normalized_mean_absolute_channel_delta"),
            _comparison_context_cell(camera, "normalized_rms_channel_delta"),
        ]
        if include_comparison_delta:
            cells.extend(
                [
                    _comparison_delta_context_cell(camera, "mean_delta"),
                    _comparison_delta_context_cell(camera, "rms_delta"),
                ]
            )
        cells.extend([camera.get("status"), "-", "-", "-", "-", "-", "-"])
        return [
            _markdown_table_row(cells)
        ]
    return [
        _summary_crop_row(
            camera,
            crop,
            include_comparison_delta=include_comparison_delta,
        )
        for crop in crop_rows
        if isinstance(crop, Mapping)
    ]


def _metric_float(metrics: object, key: str) -> float | None:
    if not isinstance(metrics, Mapping):
        return None
    value = metrics.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _color_metric_values(metrics: object, key: str) -> list[float]:
    if not isinstance(metrics, Mapping):
        return []
    values = metrics.get(key)
    if not isinstance(values, list):
        return []
    numeric_values: list[float] = []
    for value in values[:3]:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return []
        numeric_values.append(float(value))
    return numeric_values if len(numeric_values) == 3 else []


def _color_metric_label(metrics: object, key: str) -> str:
    values = _color_metric_values(metrics, key)
    if not values:
        return "-"
    return ", ".join(f"{value:.1f}" for value in values)


def _luminance_metric_label(metrics: object) -> str:
    value = _metric_float(metrics, "luminance")
    if value is None:
        return "-"
    return f"{value:+.1f}"


def _dominant_color_delta_label(metrics: object) -> str:
    if not isinstance(metrics, Mapping):
        return "-"
    movement = []
    luminance_direction = metrics.get("luminance_direction")
    if isinstance(luminance_direction, str) and luminance_direction:
        movement.append(luminance_direction)
    dominant_rgb_delta = metrics.get("dominant_rgb_delta")
    if isinstance(dominant_rgb_delta, Mapping):
        channel = dominant_rgb_delta.get("channel")
        delta = _metric_float(dominant_rgb_delta, "delta")
        if isinstance(channel, str) and channel and delta is not None:
            movement.append(f"{channel} {delta:+.1f}")
    return "; ".join(movement) if movement else "-"


def _crop_box_label(crop: Mapping[str, object]) -> object:
    box = crop.get("box")
    if not isinstance(box, list):
        return "-"
    return box


def _crop_output_path_label(crop: Mapping[str, object], key: str) -> str:
    path = _crop_output_path_value(crop, key)
    if path is None:
        return "-"
    return f"`{path}`"


def _crop_output_path_value(crop: Mapping[str, object], key: str) -> str | None:
    outputs = crop.get("outputs")
    if not isinstance(outputs, Mapping):
        return None
    path = outputs.get(key)
    if not isinstance(path, str) or not path:
        return None
    return path


def _summary_crop_mappings(report: Mapping[str, object]) -> list[tuple[Mapping[str, object], Mapping[str, object]]]:
    crop_mappings: list[tuple[Mapping[str, object], Mapping[str, object]]] = []
    cameras = report.get("cameras")
    if not isinstance(cameras, list):
        return crop_mappings
    for camera in cameras:
        if not isinstance(camera, Mapping):
            continue
        crops = camera.get("crops")
        if not isinstance(crops, list):
            continue
        for crop in crops:
            if isinstance(crop, Mapping):
                crop_mappings.append((camera, crop))
    return crop_mappings


def _summary_crop_color_delta_entry(
    camera: Mapping[str, object],
    crop: Mapping[str, object],
) -> tuple[float, float, list[object]] | None:
    metrics = crop.get("color_metrics")
    if not isinstance(metrics, Mapping):
        return None
    delta = metrics.get("delta")
    rgb_values = _color_metric_values(delta, "mean_rgb")
    luminance = _metric_float(delta, "luminance")
    if not rgb_values and luminance is None:
        return None
    max_abs_rgb = max(abs(value) for value in rgb_values) if rgb_values else None
    luminance_score = abs(luminance) if luminance is not None else 0.0
    rgb_score = max_abs_rgb if max_abs_rgb is not None else 0.0
    return (
        max(rgb_score, luminance_score),
        luminance_score,
        [
            camera.get("camera"),
            crop.get("index"),
            _crop_box_label(crop),
            _crop_output_path_label(crop, "diff"),
            _color_metric_label(delta, "mean_rgb"),
            _luminance_metric_label(delta),
            f"{max_abs_rgb:.1f}" if max_abs_rgb is not None else "-",
            _dominant_color_delta_label(delta),
        ],
    )


def _summary_crop_color_delta_entries(
    report: Mapping[str, object],
) -> list[tuple[float, float, list[object]]]:
    entries = [
        entry
        for camera, crop in _summary_crop_mappings(report)
        if (entry := _summary_crop_color_delta_entry(camera, crop)) is not None
    ]
    entries.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return entries


def _crop_color_movement_group_key(delta: object) -> tuple[str, str, str] | None:
    if not isinstance(delta, Mapping):
        return None
    luminance_direction = delta.get("luminance_direction")
    dominant_rgb_delta = delta.get("dominant_rgb_delta")
    if not isinstance(luminance_direction, str) or not luminance_direction:
        return None
    if not isinstance(dominant_rgb_delta, Mapping):
        return None
    channel = dominant_rgb_delta.get("channel")
    rgb_direction = dominant_rgb_delta.get("direction")
    if not isinstance(channel, str) or not channel:
        return None
    if not isinstance(rgb_direction, str) or not rgb_direction:
        return None
    return luminance_direction, channel, rgb_direction


def _crop_color_movement_group_label(key: tuple[str, str, str]) -> str:
    luminance_direction, channel, rgb_direction = key
    return f"{luminance_direction} + {channel} {rgb_direction}"


def _increment_count(counts: dict[str, int], key: object) -> None:
    if isinstance(key, str) and key:
        counts[key] = counts.get(key, 0) + 1


def _crop_color_movement_representative_record(
    *,
    camera_name: object,
    crop: Mapping[str, object],
    delta: Mapping[str, object],
    score: float,
) -> dict[str, object]:
    record: dict[str, object] = {
        "camera": camera_name,
        "crop": crop.get("index"),
        "score": score,
    }
    box = crop.get("box")
    if isinstance(box, list):
        record["box"] = list(box)
    diff_path = _crop_output_path_value(crop, "diff")
    if diff_path is not None:
        record["diff"] = diff_path
    rgb_values = _color_metric_values(delta, "mean_rgb")
    if rgb_values:
        record["qgis_minus_mapbox_rgb"] = rgb_values
        record["max_abs_rgb_delta"] = max(abs(value) for value in rgb_values)
    luminance = _metric_float(delta, "luminance")
    if luminance is not None:
        record["qgis_minus_mapbox_luminance"] = luminance
    return record


@dataclass
class _CropColorMovementGroup:
    count: int = 0
    max_abs_rgb: float = 0.0
    max_abs_luminance: float = 0.0
    cameras: dict[str, int] = field(default_factory=dict)
    representative_crop: dict[str, object] | None = None
    representative_score: float = 0.0

    def add(self, delta: Mapping[str, object], camera_name: object, crop: Mapping[str, object]) -> None:
        self.count += 1
        dominant_rgb_delta = delta.get("dominant_rgb_delta")
        rgb_delta = (
            _metric_float(dominant_rgb_delta, "delta")
            if isinstance(dominant_rgb_delta, Mapping)
            else None
        )
        luminance = _metric_float(delta, "luminance")
        if rgb_delta is not None:
            self.max_abs_rgb = max(self.max_abs_rgb, abs(rgb_delta))
        if luminance is not None:
            self.max_abs_luminance = max(self.max_abs_luminance, abs(luminance))
        _increment_count(self.cameras, camera_name)
        score = max(
            abs(rgb_delta) if rgb_delta is not None else 0.0,
            abs(luminance) if luminance is not None else 0.0,
        )
        if self.representative_crop is None or score > self.representative_score:
            self.representative_score = score
            self.representative_crop = _crop_color_movement_representative_record(
                camera_name=camera_name,
                crop=crop,
                delta=delta,
                score=score,
            )

    def to_record(self, key: tuple[str, str, str]) -> dict[str, object]:
        luminance_direction, channel, rgb_direction = key
        record: dict[str, object] = {
            "movement": _crop_color_movement_group_label(key),
            "luminance_direction": luminance_direction,
            "dominant_rgb_channel": channel,
            "dominant_rgb_direction": rgb_direction,
            "crop_count": self.count,
            "max_abs_rgb_delta": self.max_abs_rgb,
            "max_abs_luminance_delta": self.max_abs_luminance,
            "cameras": dict(sorted(self.cameras.items())),
        }
        if self.representative_crop is not None:
            record["representative_crop"] = self.representative_crop
        return record


def _computed_crop_color_movement_group_records(report: Mapping[str, object]) -> list[dict[str, object]]:
    groups: dict[tuple[str, str, str], _CropColorMovementGroup] = {}
    for camera, crop in _summary_crop_mappings(report):
        metrics = crop.get("color_metrics")
        if not isinstance(metrics, Mapping):
            continue
        delta = metrics.get("delta")
        group_key = _crop_color_movement_group_key(delta)
        if group_key is None or not isinstance(delta, Mapping):
            continue
        groups.setdefault(group_key, _CropColorMovementGroup()).add(delta, camera.get("camera"), crop)
    sorted_groups = sorted(
        groups.items(),
        key=lambda item: (-item[1].count, -item[1].max_abs_rgb, item[0]),
    )
    return [group.to_record(group_key) for group_key, group in sorted_groups]


def _crop_color_movement_group_records(report: Mapping[str, object]) -> list[Mapping[str, object]]:
    records = report.get("crop_color_movement_groups")
    if isinstance(records, list):
        return [record for record in records if isinstance(record, Mapping)]
    return _computed_crop_color_movement_group_records(report)


def _crop_color_movement_group_record_float(record: Mapping[str, object], key: str) -> float:
    value = record.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return 0.0


def _movement_group_representative_label(record: Mapping[str, object]) -> str:
    representative = record.get("representative_crop")
    if not isinstance(representative, Mapping):
        return "-"
    parts = [str(representative.get("camera") or "-")]
    crop_index = representative.get("crop")
    if crop_index is not None:
        parts.append(f"crop {crop_index}")
    box = representative.get("box")
    if isinstance(box, list):
        parts.append(json.dumps(box, ensure_ascii=True, separators=(",", ":")))
    diff_path = representative.get("diff")
    if isinstance(diff_path, str) and diff_path:
        parts.append(f"`{diff_path}`")
    return " ".join(parts)


def _movement_group_representative_rgb_label(record: Mapping[str, object]) -> str:
    representative = record.get("representative_crop")
    if not isinstance(representative, Mapping):
        return "-"
    values = representative.get("qgis_minus_mapbox_rgb")
    if not isinstance(values, list):
        return "-"
    numeric_values: list[float] = []
    for value in values[:3]:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "-"
        numeric_values.append(float(value))
    if len(numeric_values) != 3:
        return "-"
    return ", ".join(f"{value:.1f}" for value in numeric_values)


def _movement_group_representative_luminance_label(record: Mapping[str, object]) -> str:
    representative = record.get("representative_crop")
    if not isinstance(representative, Mapping):
        return "-"
    value = representative.get("qgis_minus_mapbox_luminance")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "-"
    return f"{float(value):+.1f}"


def _movement_group_camera_labels(record: Mapping[str, object]) -> list[str]:
    cameras = record.get("cameras")
    labels = _top_count_labels(cameras)
    representative = record.get("representative_crop")
    if not isinstance(representative, Mapping) or not isinstance(cameras, Mapping):
        return labels
    representative_camera = representative.get("camera")
    if not isinstance(representative_camera, str) or not representative_camera:
        return labels
    representative_count = cameras.get(representative_camera)
    if not isinstance(representative_count, int) or isinstance(representative_count, bool):
        return labels
    prefix = f"{representative_camera}="
    if any(label.startswith(prefix) for label in labels):
        return labels
    return [*labels, f"{representative_camera}={representative_count} (representative)"]


def _summary_crop_color_movement_group_rows(report: Mapping[str, object]) -> list[list[object]]:
    return [
        [
            record.get("movement"),
            record.get("crop_count"),
            f"{_crop_color_movement_group_record_float(record, 'max_abs_rgb_delta'):.1f}",
            f"{_crop_color_movement_group_record_float(record, 'max_abs_luminance_delta'):.1f}",
            _joined_summary_labels(_movement_group_camera_labels(record)),
            _movement_group_representative_label(record),
            _movement_group_representative_rgb_label(record),
            _movement_group_representative_luminance_label(record),
        ]
        for record in _crop_color_movement_group_records(report)[:DEFAULT_CROP_COLOR_MOVEMENT_GROUP_LIMIT]
    ]


def _summary_crop_color_movement_group_lines(report: Mapping[str, object]) -> list[str]:
    rows = _summary_crop_color_movement_group_rows(report)
    if not rows:
        return []
    lines = [
        "",
        "## Crop color movement groups",
        "",
        (
            "Groups crop-local QGIS-minus-Mapbox color movements by luminance direction "
            "and dominant RGB channel, so repeated tint families are visible before "
            "tuning a single style rule."
        ),
        "",
        (
            "| Movement | Crops | Max abs RGB | Max abs luminance | Cameras | "
            "Representative crop | Representative RGB | Representative luminance |"
        ),
        "| --- | ---: | ---: | ---: | --- | --- | ---: | ---: |",
    ]
    lines.extend(_markdown_table_row(row) for row in rows)
    return lines


def _summary_largest_crop_color_delta_lines(report: Mapping[str, object]) -> list[str]:
    entries = _summary_crop_color_delta_entries(report)[:DEFAULT_CROP_COLOR_DELTA_SUMMARY_LIMIT]
    if not entries:
        return []
    lines = [
        "",
        "## Largest crop color deltas",
        "",
        (
            "Ranks crop-local QGIS-minus-Mapbox color deltas by the largest absolute "
            "RGB or luminance difference, so the worst terrain, landcover, water, "
            "or tint outliers and their dominant color direction are visible before "
            "reading every crop row."
        ),
        "",
        "| Camera | Crop | Box | Diff crop | QGIS-Mapbox RGB | QGIS-Mapbox luminance | Max abs RGB | Dominant movement |",
        "| --- | ---: | --- | --- | --- | ---: | ---: | --- |",
    ]
    lines.extend(_markdown_table_row(row) for _score, _luminance_score, row in entries)
    return lines


def _summary_crop_color_metric_rows(report: Mapping[str, object]) -> list[list[object]]:
    rows: list[list[object]] = []
    cameras = report.get("cameras")
    if not isinstance(cameras, list):
        return rows
    for camera in cameras:
        if not isinstance(camera, Mapping):
            continue
        crops = camera.get("crops")
        if not isinstance(crops, list):
            continue
        for crop in crops:
            if not isinstance(crop, Mapping):
                continue
            metrics = crop.get("color_metrics")
            if not isinstance(metrics, Mapping):
                continue
            delta = metrics.get("delta")
            rows.append(
                [
                    camera.get("camera"),
                    crop.get("index"),
                    _color_metric_label(metrics.get("browser_reference"), "mean_rgb"),
                    _color_metric_label(metrics.get("qgis_vector_render"), "mean_rgb"),
                    _color_metric_label(delta, "mean_rgb"),
                    _luminance_metric_label(delta),
                    _dominant_color_delta_label(delta),
                ]
            )
    return rows


def _summary_crop_color_metric_lines(report: Mapping[str, object]) -> list[str]:
    rows = _summary_crop_color_metric_rows(report)
    if not rows:
        return []
    lines = [
        "",
        "## Crop color metrics",
        "",
        (
            "Shows crop-local mean RGB values for the Mapbox GL and QGIS crops, plus "
            "QGIS minus Mapbox deltas and dominant color direction. Use this as triage "
            "context for broad terrain, landcover, water, and tint differences."
        ),
        "",
        "| Camera | Crop | Mapbox mean RGB | QGIS mean RGB | QGIS-Mapbox RGB | QGIS-Mapbox luminance | Dominant movement |",
        "| --- | ---: | --- | --- | --- | ---: | --- |",
    ]
    lines.extend(_markdown_table_row(row) for row in rows)
    return lines


def _candidate_backed_focus_summaries(
    focus: Mapping[str, object],
    cue_key: str,
    formatter: Callable[[Mapping[str, object]], str],
) -> list[str]:
    cues = focus.get(cue_key)
    cue_rows = cues if isinstance(cues, list) else []
    return [
        formatter(cue)
        for cue in cue_rows
        if isinstance(cue, Mapping) and _has_decoded_candidates(cue)
    ]


def _focus_movement_group_labels(
    movement_group_records: Sequence[Mapping[str, object]],
    camera_name: object,
) -> list[str]:
    if not isinstance(camera_name, str) or not camera_name:
        return []
    labels: list[str] = []
    for record in movement_group_records:
        movement = record.get("movement")
        cameras = record.get("cameras")
        if not isinstance(movement, str) or not isinstance(cameras, Mapping):
            continue
        count = cameras.get(camera_name)
        if isinstance(count, int) and not isinstance(count, bool):
            labels.append(f"{movement}={count}")
            if len(labels) >= DEFAULT_CROP_COLOR_MOVEMENT_GROUP_LIMIT:
                break
    return labels


def _summary_focus_row(
    movement_group_records: Sequence[Mapping[str, object]],
    camera: object,
) -> list[object] | None:
    if not isinstance(camera, Mapping):
        return None
    focus = camera.get("path_pedestrian_focus")
    if not isinstance(focus, Mapping):
        return None
    stroke_summaries = _candidate_backed_focus_summaries(
        focus,
        "stroke_width_deltas",
        _stroke_focus_cue_summary,
    )
    dash_summaries = _candidate_backed_focus_summaries(
        focus,
        "dash_mismatches",
        _dash_focus_cue_summary,
    )
    if not stroke_summaries and not dash_summaries:
        return None
    camera_name = camera.get("camera")
    return [
        camera_name,
        _joined_summary_labels(
            _focus_movement_group_labels(movement_group_records, camera_name)
        ),
        stroke_summaries,
        dash_summaries,
    ]


def _summary_focus_rows(report: Mapping[str, object]) -> list[list[object]]:
    movement_group_records = _crop_color_movement_group_records(report)
    return [
        row
        for camera in report.get("cameras", [])
        if (row := _summary_focus_row(movement_group_records, camera)) is not None
    ]


def _summary_focus_coverage_lines(report: Mapping[str, object]) -> list[str]:
    if report.get("path_pedestrian_focus_comparison_match") is False:
        return []
    rows = report.get("path_pedestrian_focus_coverage")
    coverage_rows = rows if isinstance(rows, list) else []
    if not coverage_rows:
        return []
    lines = [
        "",
        "## Path/pedestrian focus coverage",
        "",
        (
            "Shows per-camera non-auxiliary path/pedestrian focus coverage before the "
            "focus cue table filters down to meaningful candidate-backed rows."
        ),
        "",
        (
            "| Camera | Stroke rows | Candidate strokes | Candidate width deltas | "
            "Candidate zero-delta strokes | Zero-candidate strokes | Source-capped strokes | "
            "Dash mismatches | Candidate dashes | Zero-candidate dashes |"
        ),
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in coverage_rows:
        if not isinstance(row, Mapping):
            continue
        lines.append(
            _markdown_table_row(
                [
                    row.get("camera"),
                    row.get("stroke_rows"),
                    row.get("candidate_backed_stroke_rows"),
                    row.get("candidate_backed_width_delta_rows"),
                    row.get("candidate_backed_zero_delta_rows"),
                    row.get("zero_candidate_stroke_rows"),
                    row.get("source_capped_stroke_rows"),
                    row.get("dash_mismatch_rows"),
                    row.get("candidate_backed_dash_rows"),
                    row.get("zero_candidate_dash_rows"),
                ]
            )
        )
    return lines


def _joined_summary_label_values(value: object) -> str:
    return _joined_summary_labels(value if isinstance(value, list) else [])


def _summary_focus_coverage_sample_lines(report: Mapping[str, object]) -> list[str]:
    if report.get("path_pedestrian_focus_comparison_match") is False:
        return []
    rows = report.get("path_pedestrian_focus_coverage")
    coverage_rows = rows if isinstance(rows, list) else []
    sample_rows: list[list[object]] = []
    for row in coverage_rows:
        if not isinstance(row, Mapping):
            continue
        samples = [
            row.get("candidate_zero_delta_stroke_samples"),
            row.get("zero_candidate_stroke_samples"),
            row.get("zero_candidate_dash_samples"),
        ]
        if not any(isinstance(sample, list) and sample for sample in samples):
            continue
        sample_rows.append(
            [
                row.get("camera"),
                _joined_summary_label_values(row.get("candidate_zero_delta_stroke_samples")),
                _joined_summary_label_values(row.get("zero_candidate_stroke_samples")),
                _joined_summary_label_values(row.get("zero_candidate_dash_samples")),
            ]
        )
    if not sample_rows:
        return []
    lines = [
        "",
        "## Path/pedestrian focus coverage samples",
        "",
        (
            "Shows representative focus rows hidden from the cue table because they are "
            "candidate-backed zero-delta rows or zero-candidate rows."
        ),
        "",
        "| Camera | Candidate zero-delta strokes | Zero-candidate strokes | Zero-candidate dashes |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(_markdown_table_row(row) for row in sample_rows)
    return lines


def _summary_focus_decoded_feature_lines(report: Mapping[str, object]) -> list[str]:
    if report.get("path_pedestrian_focus_comparison_match") is False:
        return []
    rows = report.get("path_pedestrian_focus_coverage")
    coverage_rows = rows if isinstance(rows, list) else []
    feature_rows: list[list[object]] = []
    for row in coverage_rows:
        if not isinstance(row, Mapping):
            continue
        labels = [
            row.get("path_line_types"),
            row.get("path_line_structures"),
            row.get("step_line_structures"),
            row.get("pedestrian_line_structures"),
        ]
        if not any(isinstance(label, list) and label for label in labels):
            continue
        feature_rows.append(
            [
                row.get("camera"),
                _joined_summary_label_values(row.get("path_line_types")),
                _joined_summary_label_values(row.get("path_line_structures")),
                _joined_summary_label_values(row.get("step_line_structures")),
                _joined_summary_label_values(row.get("pedestrian_line_structures")),
            ]
        )
    if not feature_rows:
        return []
    lines = [
        "",
        "## Path/pedestrian decoded feature coverage",
        "",
        (
            "Shows decoded road-feature type and structure counts behind the focus rows, "
            "so bridge/tunnel zero-candidate samples can be separated from cameras that "
            "only decode surface path or step features."
        ),
        "",
        "| Camera | Path types | Path structures | Step structures | Pedestrian structures |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines.extend(_markdown_table_row(row) for row in feature_rows)
    return lines


def _summary_focus_cue_lines(report: Mapping[str, object]) -> list[str]:
    if report.get("path_pedestrian_focus_comparison_match") is False:
        return []
    rows = _summary_focus_rows(report)
    if not rows:
        return []
    lines = ["", "## Path/pedestrian focus cues", ""]
    lines.extend(
        [
            (
                "Shows the strongest per-camera path/pedestrian stroke cues from the focus report "
                "next to visual hotspots, so crop review can distinguish candidate-backed style gaps "
                "from capped-width or zero-candidate artifacts and relate those cues to crop-local "
                "color movement families."
            ),
            "",
            "| Camera | Crop movement groups | Stroke width cues | Dash mismatch cues |",
            "| --- | --- | --- | --- |",
        ]
    )
    lines.extend(_markdown_table_row(row) for row in rows)
    return lines


def _count_summary_labels(rows: object, value_key: str) -> list[str]:
    if not isinstance(rows, list):
        return []
    labels = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        value = row.get(value_key)
        count = row.get("count")
        if value is not None and isinstance(count, int) and not isinstance(count, bool):
            labels.append(f"{value}={count}")
    return labels


def _joined_summary_labels(labels: Iterable[str]) -> str:
    values = [label for label in labels if label]
    return ", ".join(values) if values else "-"


def _string_values(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item not in (None, "")]


def _candidate_sample_label(candidate: Mapping[str, object]) -> str:
    layer = str(candidate.get("layer") or "unknown-layer")
    source_layer = candidate.get("source_layer")
    layer_type = candidate.get("type")
    controls = _string_values(candidate.get("control_properties"))
    details = []
    if source_layer is not None or layer_type is not None:
        details.append(f"{source_layer}/{layer_type}")
    filter_signature = candidate.get("filter_operator_signature")
    if isinstance(filter_signature, str) and filter_signature:
        details.append(f"filter-ops: {filter_signature}")
    if controls:
        details.append(f"controls={', '.join(controls[:4])}")
    qfit_simplified = _string_values(candidate.get("qfit_simplified_properties"))
    if qfit_simplified:
        details.append(f"qfit={', '.join(qfit_simplified[:4])}")
    qfit_simplifications = _candidate_qfit_simplification_labels(candidate)
    if qfit_simplifications:
        details.append(f"simplifies={' | '.join(qfit_simplifications[:2])}")
    qgis_dependent = _string_values(candidate.get("qgis_dependent_properties"))
    if qgis_dependent:
        details.append(f"qgis={', '.join(qgis_dependent[:4])}")
    qgis_warnings = _candidate_qgis_converter_warning_labels(candidate, layer)
    if qgis_warnings:
        details.append(f"qgis-warnings={', '.join(qgis_warnings[:2])}")
    detail_suffix = f" ({'; '.join(details)})" if details else ""
    return f"{layer}{detail_suffix}"


def _candidate_qfit_simplification_labels(candidate: Mapping[str, object]) -> list[str]:
    simplifications = candidate.get("qfit_simplifications")
    if not isinstance(simplifications, list):
        return []
    labels = []
    for row in simplifications:
        if not isinstance(row, Mapping):
            continue
        property_name = row.get("property")
        if property_name in (None, ""):
            continue
        if row.get("from") not in (None, "") and row.get("to") not in (None, ""):
            labels.append(f"{property_name}: {row.get('from')} -> {row.get('to')}")
        elif row.get("to") not in (None, ""):
            labels.append(f"{property_name}: -> {row.get('to')}")
        else:
            labels.append(str(property_name))
    return labels


def _candidate_qgis_converter_warning_labels(
    candidate: Mapping[str, object],
    layer: str,
) -> list[str]:
    warnings_summary = candidate.get("qgis_converter_warnings")
    if not isinstance(warnings_summary, Mapping):
        return []
    warning_labels = [
        _candidate_qgis_converter_warning_label(str(warning), layer)
        for warning in _string_values(warnings_summary.get("warnings"))
    ]
    if warning_labels:
        return warning_labels
    by_message = warnings_summary.get("by_message")
    if not isinstance(by_message, list):
        return []
    return [
        str(row.get("message"))
        for row in by_message
        if isinstance(row, Mapping) and row.get("message") not in (None, "")
    ]


def _candidate_qgis_converter_warning_label(warning: str, layer: str) -> str:
    layer_prefix = f"{layer}: "
    if warning.startswith(layer_prefix):
        return warning[len(layer_prefix) :]
    return warning


def _candidate_sample_labels(candidates: object) -> list[str]:
    if not isinstance(candidates, list):
        return []
    return [
        _candidate_sample_label(candidate)
        for candidate in candidates
        if isinstance(candidate, Mapping)
    ]


def _style_audit_area_fill_focus_lines(report: Mapping[str, object]) -> list[str]:
    focus = report.get("style_audit_area_fill_focus")
    rows = focus if isinstance(focus, list) else []
    if not rows:
        return []
    lines = [
        "",
        "## Style audit area-fill focus",
        "",
        (
            "Shows global terrain/landcover and airport/special-landuse candidates from the "
            "style audit beside the crop sheet, so broad fill differences can be reviewed "
            "without reopening the full audit."
        ),
        "",
        (
            "| Area | Candidates | Source layers | Types | Simplified controls | "
            "QGIS-dependent controls | Filter signatures | Sample layers |"
        ),
        "| --- | ---: | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        lines.append(
            _markdown_table_row(
                [
                    row.get("label"),
                    row.get("candidate_count"),
                    _joined_summary_labels(
                        _count_summary_labels(row.get("by_source_layer"), "source_layer")
                    ),
                    _joined_summary_labels(_count_summary_labels(row.get("by_type"), "type")),
                    _joined_summary_labels(
                        _count_summary_labels(row.get("simplified_by_property"), "property")
                    ),
                    _joined_summary_labels(
                        _count_summary_labels(row.get("qgis_dependent_by_property"), "property")
                    ),
                    _joined_summary_labels(
                        _count_summary_labels(
                            row.get("filter_signatures"),
                            "filter_operator_signature",
                        )
                    ),
                    _joined_summary_labels(_candidate_sample_labels(row.get("sample_candidates"))),
                ]
            )
        )
    return lines


def build_summary_markdown(report: Mapping[str, object]) -> str:
    lines = _summary_header_lines(report)
    include_comparison_delta = _include_comparison_delta_columns(report)
    lines.extend(_summary_table_intro_lines(report))
    for camera in report.get("cameras", []):
        if not isinstance(camera, Mapping):
            continue
        lines.extend(
            _summary_camera_rows(
                camera,
                include_comparison_delta=include_comparison_delta,
            )
        )
    lines.extend(_summary_largest_crop_color_delta_lines(report))
    lines.extend(_summary_crop_color_movement_group_lines(report))
    lines.extend(_summary_crop_color_metric_lines(report))
    lines.extend(_style_audit_area_fill_focus_lines(report))
    lines.extend(_summary_focus_coverage_lines(report))
    lines.extend(_summary_focus_coverage_sample_lines(report))
    lines.extend(_summary_focus_decoded_feature_lines(report))
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
        "--comparison-delta-json",
        type=Path,
        help="Optional comparison-delta.json to annotate crops with per-camera metric movements.",
    )
    parser.add_argument(
        "--style-audit-json",
        type=Path,
        help="Optional style audit JSON to annotate crops with area-fill candidate context.",
    )
    parser.add_argument(
        "--camera",
        action="append",
        default=[],
        help="Camera to crop. May be repeated. Defaults to all cameras with complete visual artifacts.",
    )
    parser.add_argument(
        "--focus-cue-cameras",
        action="store_true",
        help=(
            "Crop only cameras with candidate-backed path/pedestrian focus cues. "
            "Use with --path-pedestrian-focus-json when visually inspecting focus gaps."
        ),
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
    if args.focus_cue_cameras and args.path_pedestrian_focus_json is None:
        parser.error("--focus-cue-cameras requires --path-pedestrian-focus-json")
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
    comparison_delta_report = None
    if args.comparison_delta_json is not None:
        comparison_delta_report = _load_cli_json_object(
            parser,
            args.comparison_delta_json,
            label="Comparison delta JSON",
        )
    style_audit_report = None
    if args.style_audit_json is not None:
        style_audit_report = _load_cli_json_object(
            parser,
            args.style_audit_json,
            label="Style audit JSON",
        )
    paths = build_visual_crop_paths(build_run_directory())
    try:
        report = generate_visual_crop_report(
            comparison_summary,
            comparison_summary_path=args.comparison_summary_json,
            paths=paths,
            annotation_inputs=VisualCropAnnotationInputs(
                path_pedestrian_focus_report=path_pedestrian_focus_report,
                path_pedestrian_focus_report_path=args.path_pedestrian_focus_json,
                style_audit_report=style_audit_report,
                style_audit_report_path=args.style_audit_json,
            ),
            camera_names=args.camera,
            focus_cue_cameras_only=args.focus_cue_cameras,
            crop_size=args.crop_size,
            crops_per_camera=args.crops_per_camera,
        )
        if comparison_delta_report is not None and args.comparison_delta_json is not None:
            report = annotate_visual_crop_report_with_comparison_delta(
                report,
                comparison_summary_path=args.comparison_summary_json,
                comparison_delta_report=comparison_delta_report,
                comparison_delta_report_path=args.comparison_delta_json,
            )
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    write_report(report, paths)
    print(paths.summary_path)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised manually
    raise SystemExit(main())
