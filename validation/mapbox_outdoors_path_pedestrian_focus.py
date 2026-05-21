from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PARENT = REPO_ROOT.parent
package_parent = str(PACKAGE_PARENT)
if package_parent not in sys.path:
    sys.path.insert(0, package_parent)

from qfit.mapbox_config import (  # noqa: E402
    _MAPBOX_PIXEL_TO_MM,
    _MAX_LINE_WIDTH_MM,
    _extract_line_dasharray_literal,
    _extract_zoom_scalar_size_at_zoom,
    _literal_line_dasharray,
    base_mapbox_style_layer_id_for_qfit,
)

DEFAULT_OUTPUT_ROOT = REPO_ROOT / "debug" / "mapbox-outdoors-path-pedestrian-focus"
DEFAULT_COMPARISON_OUTPUT_ROOT = REPO_ROOT / "debug" / "mapbox-outdoors-comparison"
DEFAULT_ROAD_FEATURES_PATH = (
    REPO_ROOT
    / "debug"
    / "mapbox-outdoors-road-features"
    / "all-cameras"
    / "latest"
    / "road-features.json"
)
PATH_PEDESTRIAN_LAYER_ID_MARKERS = (
    "bridge-path",
    "bridge-pedestrian",
    "bridge-steps",
    "road-path",
    "road-pedestrian",
    "road-steps",
    "tunnel-path",
    "tunnel-pedestrian",
    "tunnel-steps",
)
PATH_PEDESTRIAN_STYLE_TYPES = {"fill", "line"}
PATH_PEDESTRIAN_LABEL_STYLE_IDS = {
    "path-pedestrian-label",
    "path-pedestrian-label-below-z15",
    "path-pedestrian-label-z15-plus",
    "road-label",
    "road-label-below-z12",
    "road-label-z12-to-z15",
    "road-label-z15-plus",
}
PATH_PEDESTRIAN_DETAIL_PAINT_KEYS = (
    "line-width",
    "line-color",
    "line-dasharray",
    "line-opacity",
    "fill-color",
    "fill-opacity",
)
PATH_PEDESTRIAN_STROKE_CONTROL_KEYS = (
    "line-width",
    "line-width_raw_mm",
    "line-width_capped",
    "line-color",
    "line-dasharray",
    "line-opacity",
)
PATH_PEDESTRIAN_STROKE_DELTA_KEYS = (
    "line-width_delta_mm",
    "line-width_ratio",
    "line-dasharray_match",
    "line-color_match",
    "line-opacity_delta",
)
PATH_PEDESTRIAN_SOURCE_LAYER_PREFIXES = ("road", "bridge", "tunnel")
PATH_PEDESTRIAN_SOURCE_STRUCTURES_BY_PREFIX = {
    "bridge": "bridge",
    "tunnel": "tunnel",
}
PATH_PEDESTRIAN_PATH_SOURCE_LAYER_IDS = frozenset(
    f"{prefix}-{suffix}"
    for prefix in PATH_PEDESTRIAN_SOURCE_LAYER_PREFIXES
    for suffix in ("path", "path-bg")
)
PATH_PEDESTRIAN_PATH_TRAIL_SOURCE_LAYER_IDS = frozenset(
    f"{prefix}-path-trail" for prefix in PATH_PEDESTRIAN_SOURCE_LAYER_PREFIXES
)
PATH_PEDESTRIAN_PATH_CYCLEWAY_PISTE_SOURCE_LAYER_IDS = frozenset(
    f"{prefix}-path-cycleway-piste" for prefix in PATH_PEDESTRIAN_SOURCE_LAYER_PREFIXES
)
PATH_PEDESTRIAN_PEDESTRIAN_SOURCE_LAYER_IDS = frozenset(
    f"{prefix}-{suffix}"
    for prefix in PATH_PEDESTRIAN_SOURCE_LAYER_PREFIXES
    for suffix in ("pedestrian", "pedestrian-case")
)
PATH_PEDESTRIAN_STEP_SOURCE_LAYER_IDS = frozenset(
    f"{prefix}-{suffix}"
    for prefix in PATH_PEDESTRIAN_SOURCE_LAYER_PREFIXES
    for suffix in ("steps", "steps-bg")
)
PATH_PEDESTRIAN_TRAIL_TYPES = frozenset({"hiking", "mountain_bike", "trail"})
PATH_PEDESTRIAN_CYCLEWAY_PISTE_TYPES = frozenset({"cycleway", "piste"})
PATH_PEDESTRIAN_LARGEST_STROKE_DELTA_LIMIT = 12
PATH_PEDESTRIAN_SOURCE_SPLIT_BASE_LAYER_IDS = frozenset(
    {
        "road-path",
    }
)
PATH_PEDESTRIAN_SOURCE_SPLIT_SUFFIXES = (
    "-below-z16",
    "-z16-to-z18",
    "-z18-plus",
    "-z16-plus",
)
PATH_PEDESTRIAN_CORE_CASE_LAYER_PAIRS = (
    ("road-pedestrian", "road-pedestrian-case"),
    ("bridge-pedestrian", "bridge-pedestrian-case"),
    ("tunnel-pedestrian", "tunnel-pedestrian-case"),
)
PATH_PEDESTRIAN_PALE_CASING_SUFFIX = "-pale-casing"
PATH_PEDESTRIAN_AUXILIARY_STROKE_SUFFIXES = (
    PATH_PEDESTRIAN_PALE_CASING_SUFFIX,
)
PATH_PEDESTRIAN_CORE_CASE_SOURCE_KEYS = (
    "source_both_widths_capped",
    "source_case_to_core_ratio",
    "source_case_over_core_mm",
    "source_core_width_mm",
    "source_case_width_mm",
    "source_core_raw_width_mm",
    "source_case_raw_width_mm",
    "source_core_capped",
    "source_case_capped",
)
PATH_PEDESTRIAN_CORE_CASE_QGIS_KEYS = (
    "qgis_case_to_core_ratio",
    "qgis_case_over_core_mm",
    "qgis_ratio_preserving_core_width_mm_at_cap",
    "qgis_core_width_mm",
    "qgis_case_width_mm",
    "qgis_pale_casing_width_mm",
    "qgis_core_layer_id",
    "qgis_case_layer_id",
    "qgis_pale_casing_layer_id",
)
TOP_COUNT_LIMIT = 3
SAMPLE_LAYER_LIMIT = 8
DUPLICATE_LABEL_CATEGORY_KEYS = (
    ("pedestrian", "top_pedestrian_line_duplicate_names"),
    ("path", "top_path_line_duplicate_names"),
    ("step", "top_step_line_duplicate_names"),
)
DUPLICATE_LABEL_CATEGORY_FEATURES = {
    "pedestrian": {
        "class": "pedestrian",
        "layer": 0,
        "name": "__qfit_label_probe__",
        "type": "pedestrian",
    },
    "path": {
        "class": "path",
        "layer": 0,
        "name": "__qfit_label_probe__",
        "type": "path",
    },
    "step": {
        "class": "path",
        "layer": 0,
        "name": "__qfit_label_probe__",
        "type": "steps",
    },
}
COMPARISON_VISUAL_OUTPUT_KEYS = ("browser_reference", "qgis_vector_render", "diff")
COMPARISON_VISUAL_METRIC_KEYS = (
    "changed_pixel_ratio",
    "normalized_mean_absolute_channel_delta",
    "normalized_rms_channel_delta",
    "ssim_status",
)
MARKDOWN_SEPARATOR_5_COLUMNS = "| --- | --- | --- | --- | --- |"
MARKDOWN_SEPARATOR_6_COLUMNS = "| --- | --- | --- | --- | --- | --- |"
MARKDOWN_SEPARATOR_7_COLUMNS = "| --- | --- | --- | --- | --- | --- | --- |"
MARKDOWN_SEPARATOR_8_COLUMNS = "| --- | --- | --- | --- | --- | --- | --- | --- |"
MARKDOWN_SEPARATOR_9_COLUMNS = "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"
UNSAMPLED_EXPRESSION_CONTROL = "expression-not-sampled"
ARGPARSE_EXIT_SENTINEL = "argparse error should exit"


@dataclass(frozen=True)
class PathPedestrianFocusPaths:
    run_dir: Path
    json_path: Path
    summary_path: Path


def _utc_timestamp(now: dt.datetime | None = None) -> str:
    timestamp = now or dt.datetime.now(dt.timezone.utc)
    return timestamp.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_run_directory(
    *,
    output_root: Path | None = None,
    now: dt.datetime | None = None,
) -> Path:
    root = DEFAULT_OUTPUT_ROOT if output_root is None else output_root
    return root / _utc_timestamp(now)


def build_path_pedestrian_focus_paths(run_dir: Path) -> PathPedestrianFocusPaths:
    return PathPedestrianFocusPaths(
        run_dir=run_dir,
        json_path=run_dir / "path-pedestrian-focus.json",
        summary_path=run_dir / "summary.md",
    )


def load_json_object(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return loaded


def load_json_list(path: Path) -> list[object]:
    with path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, list):
        raise ValueError(f"Expected JSON list in {path}")
    return loaded


def _resolve_trusted_debug_path(raw_path: str, *, base_dir: Path, trusted_root: Path) -> Path:
    path = Path(raw_path)
    candidate = path if path.is_absolute() else base_dir / path
    # Safe: the resolved candidate is rejected unless it stays beneath the trusted debug root.
    resolved = candidate.resolve()  # NOSONAR
    if not resolved.is_relative_to(trusted_root.resolve()):
        raise ValueError(f"Comparison artifact path must stay under {trusted_root}: {resolved}")
    return resolved


def _trusted_root_for_comparison_summary(summary_path: Path) -> Path:
    resolved = summary_path.resolve()
    for parent in resolved.parents:
        if parent.name == "all-cameras":
            return parent.parent
    return DEFAULT_COMPARISON_OUTPUT_ROOT


def _qgis_output_paths_from_comparison_summary(
    comparison_summary: Mapping[str, object],
    *,
    output_key: str,
    summary_path: Path | None = None,
    trusted_root: Path | None = None,
) -> dict[str, Path]:
    if trusted_root is not None:
        root = trusted_root
    elif summary_path is not None:
        root = _trusted_root_for_comparison_summary(summary_path)
    else:
        root = DEFAULT_COMPARISON_OUTPUT_ROOT
    base_dir = Path.cwd() if summary_path is None else summary_path.parent
    cameras = comparison_summary.get("cameras")
    rows = cameras if isinstance(cameras, list) else []
    paths: dict[str, Path] = {}
    for camera_report in rows:
        if not isinstance(camera_report, Mapping):
            continue
        camera_name = camera_report.get("camera")
        manifest_value = camera_report.get("manifest")
        if not isinstance(camera_name, str) or not isinstance(manifest_value, str):
            continue
        manifest_path = _resolve_trusted_debug_path(
            manifest_value,
            base_dir=base_dir,
            trusted_root=root,
        )
        manifest = load_json_object(manifest_path)  # NOSONAR - checked against the comparison debug root above.
        outputs = manifest.get("outputs")
        if not isinstance(outputs, Mapping):
            continue
        style_value = outputs.get(output_key)
        if not isinstance(style_value, str):
            continue
        paths[camera_name] = _resolve_trusted_debug_path(
            style_value,
            base_dir=manifest_path.parent,
            trusted_root=root,
        )
    return paths


def qgis_style_paths_from_comparison_summary(
    comparison_summary: Mapping[str, object],
    *,
    summary_path: Path | None = None,
    trusted_root: Path | None = None,
) -> dict[str, Path]:
    return _qgis_output_paths_from_comparison_summary(
        comparison_summary,
        output_key="qgis_preprocessed_style",
        summary_path=summary_path,
        trusted_root=trusted_root,
    )


def qgis_label_style_paths_from_comparison_summary(
    comparison_summary: Mapping[str, object],
    *,
    summary_path: Path | None = None,
    trusted_root: Path | None = None,
) -> dict[str, Path]:
    return _qgis_output_paths_from_comparison_summary(
        comparison_summary,
        output_key="qgis_label_styles",
        summary_path=summary_path,
        trusted_root=trusted_root,
    )


def _comparison_debug_root(summary_path: Path | None, trusted_root: Path | None) -> Path:
    if trusted_root is not None:
        return trusted_root
    if summary_path is not None:
        return _trusted_root_for_comparison_summary(summary_path)
    return DEFAULT_COMPARISON_OUTPUT_ROOT


def _comparison_summary_base_dir(summary_path: Path | None) -> Path:
    return Path.cwd() if summary_path is None else summary_path.parent


def _comparison_camera_rows(comparison_summary: Mapping[str, object]) -> list[object]:
    cameras = comparison_summary.get("cameras")
    return cameras if isinstance(cameras, list) else []


def _comparison_contact_sheet_path(
    comparison_summary: Mapping[str, object],
    *,
    base_dir: Path,
    trusted_root: Path,
) -> Path | None:
    contact_sheet_value = comparison_summary.get("contact_sheet")
    if not isinstance(contact_sheet_value, str) or not contact_sheet_value:
        return None
    return _resolve_trusted_debug_path(
        contact_sheet_value,
        base_dir=base_dir,
        trusted_root=trusted_root,
    )


def _camera_status_artifacts(camera_report: Mapping[str, object]) -> dict[str, object]:
    return {
        key: value
        for key in ("status", "artifact_status")
        if isinstance((value := camera_report.get(key)), str)
    }


def _camera_metric_artifacts(camera_report: Mapping[str, object]) -> dict[str, object]:
    metrics = camera_report.get("metrics")
    if not isinstance(metrics, Mapping):
        return {}
    return {
        key: value
        for key in COMPARISON_VISUAL_METRIC_KEYS
        if isinstance((value := metrics.get(key)), (str, int, float)) and not isinstance(value, bool)
    }


def _camera_output_artifacts(
    camera_report: Mapping[str, object],
    *,
    base_dir: Path,
    trusted_root: Path,
) -> dict[str, object]:
    outputs = camera_report.get("outputs")
    if not isinstance(outputs, Mapping):
        return {}
    return {
        key: _resolve_trusted_debug_path(value, base_dir=base_dir, trusted_root=trusted_root)
        for key in COMPARISON_VISUAL_OUTPUT_KEYS
        if isinstance((value := outputs.get(key)), str) and value
    }


def comparison_visual_artifacts_from_summary(
    comparison_summary: Mapping[str, object],
    *,
    summary_path: Path | None = None,
    trusted_root: Path | None = None,
) -> dict[str, dict[str, object]]:
    root = _comparison_debug_root(summary_path, trusted_root)
    base_dir = _comparison_summary_base_dir(summary_path)
    contact_sheet_path = _comparison_contact_sheet_path(
        comparison_summary,
        base_dir=base_dir,
        trusted_root=root,
    )
    artifacts: dict[str, dict[str, object]] = {}
    for camera_report in _comparison_camera_rows(comparison_summary):
        if not isinstance(camera_report, Mapping):
            continue
        camera_name = camera_report.get("camera")
        if not isinstance(camera_name, str):
            continue
        camera_artifacts = _camera_status_artifacts(camera_report)
        camera_artifacts.update(_camera_metric_artifacts(camera_report))
        camera_artifacts.update(
            _camera_output_artifacts(camera_report, base_dir=base_dir, trusted_root=root)
        )
        if contact_sheet_path is not None:
            camera_artifacts["contact_sheet"] = contact_sheet_path
        if camera_artifacts:
            artifacts[camera_name] = camera_artifacts
    return artifacts


def _compact_json(value: object, *, max_length: int = 110) -> str:
    try:
        text = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    except TypeError:
        text = str(value)
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 3]}..."


def _count_value(counts: object, key: str) -> int:
    if not isinstance(counts, Mapping):
        return 0
    value = counts.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _top_count_labels(counts: object, *, limit: int = TOP_COUNT_LIMIT) -> list[str]:
    if not isinstance(counts, Mapping):
        return []
    rows = [
        (str(key), value)
        for key, value in counts.items()
        if isinstance(value, int) and not isinstance(value, bool)
    ]
    rows.sort(key=lambda item: (-item[1], item[0]))
    return [f"{key}={value}" for key, value in rows[:limit]]


def _has_path_pedestrian_focus(camera_report: Mapping[str, object]) -> bool:
    return any(
        _count_value(camera_report, key) > 0
        for key in (
            "pedestrian_polygon_candidate_count",
            "pedestrian_line_candidate_count",
            "path_line_candidate_count",
            "step_line_candidate_count",
        )
    )


def _style_layers(style: Mapping[str, object]) -> list[dict[str, object]]:
    layers = style.get("layers")
    if not isinstance(layers, list):
        return []
    return [layer for layer in layers if isinstance(layer, dict)]


def _is_path_pedestrian_label_source_layer(layer: Mapping[str, object]) -> bool:
    layer_id = str(layer.get("id") or "")
    return layer.get("type") == "symbol" and layer_id in PATH_PEDESTRIAN_LABEL_STYLE_IDS


def _comparison_filter_value(
    operator: object,
    operands: list[object],
    properties: Mapping[str, object],
) -> bool:
    if len(operands) < 2:
        return False
    left = _mapbox_filter_value(operands[0], properties)
    right = _mapbox_filter_value(operands[1], properties)
    if operator in {"==", "!="}:
        matches = left == right
        return matches if operator == "==" else not matches
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return False
    if operator == ">=":
        return left >= right
    if operator == ">":
        return left > right
    if operator == "<=":
        return left <= right
    if operator == "<":
        return left < right
    return False


def _match_filter_value(expression: list[object], properties: Mapping[str, object]) -> object:
    if len(expression) < 4:
        return False
    candidate = _mapbox_filter_value(expression[1], properties)
    default = expression[-1]
    for index in range(2, len(expression) - 1, 2):
        labels = expression[index]
        if not isinstance(labels, list):
            labels = [labels]
        if candidate in labels:
            return _mapbox_filter_value(expression[index + 1], properties)
    return _mapbox_filter_value(default, properties)


def _case_filter_value(expression: list[object], properties: Mapping[str, object]) -> object:
    if len(expression) < 4:
        return False
    for index in range(1, len(expression) - 1, 2):
        if bool(_mapbox_filter_value(expression[index], properties)):
            return _mapbox_filter_value(expression[index + 1], properties)
    return _mapbox_filter_value(expression[-1], properties)


def _get_filter_value(expression: list[object], properties: Mapping[str, object]) -> object:
    if len(expression) < 2:
        return None
    key = expression[1]
    return properties.get(str(key)) if key is not None else None


def _has_filter_value(expression: list[object], properties: Mapping[str, object]) -> bool:
    if len(expression) < 2:
        return False
    key = expression[1]
    return key is not None and str(key) in properties


def _not_filter_value(expression: list[object], properties: Mapping[str, object]) -> bool:
    return len(expression) >= 2 and not bool(_mapbox_filter_value(expression[1], properties))


def _all_filter_value(expression: list[object], properties: Mapping[str, object]) -> bool:
    return all(bool(_mapbox_filter_value(child, properties)) for child in expression[1:])


def _any_filter_value(expression: list[object], properties: Mapping[str, object]) -> bool:
    return any(bool(_mapbox_filter_value(child, properties)) for child in expression[1:])


def _equal_filter_value(expression: list[object], properties: Mapping[str, object]) -> bool:
    return _comparison_filter_value("==", expression[1:], properties)


def _not_equal_filter_value(expression: list[object], properties: Mapping[str, object]) -> bool:
    return _comparison_filter_value("!=", expression[1:], properties)


def _greater_equal_filter_value(expression: list[object], properties: Mapping[str, object]) -> bool:
    return _comparison_filter_value(">=", expression[1:], properties)


def _greater_than_filter_value(expression: list[object], properties: Mapping[str, object]) -> bool:
    return _comparison_filter_value(">", expression[1:], properties)


def _less_equal_filter_value(expression: list[object], properties: Mapping[str, object]) -> bool:
    return _comparison_filter_value("<=", expression[1:], properties)


def _less_than_filter_value(expression: list[object], properties: Mapping[str, object]) -> bool:
    return _comparison_filter_value("<", expression[1:], properties)


def _zoom_filter_value(expression: list[object], properties: Mapping[str, object]) -> object:
    _ = expression
    return properties.get("zoom")


def _step_filter_value(expression: list[object], properties: Mapping[str, object]) -> object:
    if len(expression) < 3:
        return False
    input_value = _mapbox_filter_value(expression[1], properties)
    if not isinstance(input_value, (int, float)):
        return False
    selected = expression[2]
    for index in range(3, len(expression) - 1, 2):
        stop = expression[index]
        if not isinstance(stop, (int, float)) or input_value < stop:
            break
        selected = expression[index + 1]
    return _mapbox_filter_value(selected, properties)


MAPBOX_FILTER_OPERATOR_HANDLERS = {
    "get": _get_filter_value,
    "has": _has_filter_value,
    "zoom": _zoom_filter_value,
    "!": _not_filter_value,
    "all": _all_filter_value,
    "any": _any_filter_value,
    "==": _equal_filter_value,
    "!=": _not_equal_filter_value,
    ">=": _greater_equal_filter_value,
    ">": _greater_than_filter_value,
    "<=": _less_equal_filter_value,
    "<": _less_than_filter_value,
    "match": _match_filter_value,
    "case": _case_filter_value,
    "step": _step_filter_value,
}


def _mapbox_filter_value(expression: object, properties: Mapping[str, object]) -> object:
    if not isinstance(expression, list) or not expression:
        return expression
    handler = MAPBOX_FILTER_OPERATOR_HANDLERS.get(str(expression[0]))
    return handler(expression, properties) if handler is not None else False


def _duplicate_label_category_properties(
    category: str,
    *,
    camera_zoom: float | None,
) -> Mapping[str, object]:
    properties = dict(DUPLICATE_LABEL_CATEGORY_FEATURES[category])
    if camera_zoom is not None:
        properties["zoom"] = camera_zoom
    return properties


def _duplicate_label_categories_for_filter(
    filter_value: object,
    *,
    camera_zoom: float | None = None,
) -> list[str]:
    if filter_value is None:
        return list(DUPLICATE_LABEL_CATEGORY_FEATURES)
    return [
        category
        for category in DUPLICATE_LABEL_CATEGORY_FEATURES
        if bool(
            _mapbox_filter_value(
                filter_value,
                _duplicate_label_category_properties(category, camera_zoom=camera_zoom),
            )
        )
    ]


def _is_path_pedestrian_style_layer(layer: Mapping[str, object]) -> bool:
    layer_id = str(layer.get("id") or "")
    layer_type = layer.get("type")
    return (
        isinstance(layer_type, str)
        and layer_type in PATH_PEDESTRIAN_STYLE_TYPES
        and any(marker in layer_id for marker in PATH_PEDESTRIAN_LAYER_ID_MARKERS)
    )


def _layer_paint(layer: Mapping[str, object]) -> Mapping[str, object]:
    paint = layer.get("paint")
    return paint if isinstance(paint, Mapping) else {}


def _numeric_zoom(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _layer_is_visible_at_zoom(layer: Mapping[str, object], camera_zoom: float | None) -> bool:
    if camera_zoom is None:
        return True
    minzoom = _numeric_zoom(layer.get("minzoom"))
    maxzoom = _numeric_zoom(layer.get("maxzoom"))
    if minzoom is not None and camera_zoom < minzoom:
        return False
    return not (maxzoom is not None and camera_zoom >= maxzoom)


def _layer_control_sample(layers: Iterable[Mapping[str, object]], property_name: str) -> list[str]:
    samples: list[str] = []
    for layer in layers:
        paint = _layer_paint(layer)
        if property_name not in paint:
            continue
        layer_id = str(layer.get("id") or "")
        samples.append(f"{layer_id}={_compact_json(paint.get(property_name))}")
        if len(samples) >= SAMPLE_LAYER_LIMIT:
            break
    return samples


def _style_control_count(layers: Iterable[Mapping[str, object]], property_name: str) -> int:
    return sum(1 for layer in layers if property_name in _layer_paint(layer))


def _layer_detail(layer: Mapping[str, object]) -> dict[str, object]:
    detail = {
        "id": str(layer.get("id") or ""),
        "type": layer.get("type"),
    }
    for key in ("minzoom", "maxzoom", "filter"):
        value = layer.get(key)
        if value is not None:
            detail[key] = value
    paint = _layer_paint(layer)
    for key in PATH_PEDESTRIAN_DETAIL_PAINT_KEYS:
        if key in paint:
            detail[key] = paint[key]
    return detail


def _label_source_layer_detail(
    layer: Mapping[str, object],
    *,
    camera_zoom: float | None,
) -> dict[str, object]:
    detail = {
        "id": str(layer.get("id") or ""),
        "type": layer.get("type"),
        "duplicate_label_categories": _duplicate_label_categories_for_filter(
            layer.get("filter"),
            camera_zoom=camera_zoom,
        ),
    }
    for key in ("minzoom", "maxzoom", "filter"):
        value = layer.get(key)
        if value is not None:
            detail[key] = value
    return detail


def _path_pedestrian_style_summary(
    style: Mapping[str, object],
    *,
    camera_zoom: float | None = None,
    prefix: str,
    status: str,
) -> dict[str, object]:
    layers = [layer for layer in _style_layers(style) if _is_path_pedestrian_style_layer(layer)]
    label_source_layers = [
        layer for layer in _style_layers(style) if _is_path_pedestrian_label_source_layer(layer)
    ]
    line_layers = [layer for layer in layers if layer.get("type") == "line"]
    fill_layers = [layer for layer in layers if layer.get("type") == "fill"]
    visible_layers = [layer for layer in layers if _layer_is_visible_at_zoom(layer, camera_zoom)]
    visible_label_source_layers = [
        layer for layer in label_source_layers if _layer_is_visible_at_zoom(layer, camera_zoom)
    ]
    visible_line_layers = [layer for layer in visible_layers if layer.get("type") == "line"]
    visible_fill_layers = [layer for layer in visible_layers if layer.get("type") == "fill"]
    key_root = f"{prefix}_path_pedestrian"
    return {
        f"{prefix}_style_status": status,
        f"{key_root}_layer_count": len(layers),
        f"{key_root}_line_layer_count": len(line_layers),
        f"{key_root}_fill_layer_count": len(fill_layers),
        f"{key_root}_filter_layer_count": sum(1 for layer in layers if layer.get("filter") is not None),
        f"{key_root}_line_width_layer_count": _style_control_count(line_layers, "line-width"),
        f"{key_root}_line_color_layer_count": _style_control_count(line_layers, "line-color"),
        f"{key_root}_fill_color_layer_count": _style_control_count(fill_layers, "fill-color"),
        f"{key_root}_line_dasharray_layer_count": _style_control_count(line_layers, "line-dasharray"),
        f"{key_root}_line_opacity_layer_count": _style_control_count(line_layers, "line-opacity"),
        f"{key_root}_visible_layer_count": len(visible_layers),
        f"{key_root}_visible_line_layer_count": len(visible_line_layers),
        f"{key_root}_visible_fill_layer_count": len(visible_fill_layers),
        f"{key_root}_visible_filter_layer_count": sum(
            1 for layer in visible_layers if layer.get("filter") is not None
        ),
        f"{key_root}_visible_line_width_layer_count": _style_control_count(
            visible_line_layers,
            "line-width",
        ),
        f"{key_root}_visible_line_color_layer_count": _style_control_count(
            visible_line_layers,
            "line-color",
        ),
        f"{key_root}_visible_fill_color_layer_count": _style_control_count(
            visible_fill_layers,
            "fill-color",
        ),
        f"{key_root}_visible_line_dasharray_layer_count": _style_control_count(
            visible_line_layers,
            "line-dasharray",
        ),
        f"{key_root}_visible_line_opacity_layer_count": _style_control_count(
            visible_line_layers,
            "line-opacity",
        ),
        f"{key_root}_layer_ids": [str(layer.get("id") or "") for layer in layers],
        f"{key_root}_visible_layer_ids": [str(layer.get("id") or "") for layer in visible_layers],
        f"{key_root}_layer_details": [_layer_detail(layer) for layer in layers],
        f"{key_root}_visible_layer_details": [_layer_detail(layer) for layer in visible_layers],
        f"{key_root}_label_source_layer_count": len(label_source_layers),
        f"{key_root}_visible_label_source_layer_count": len(visible_label_source_layers),
        f"{key_root}_label_source_details": [
            _label_source_layer_detail(layer, camera_zoom=camera_zoom) for layer in label_source_layers
        ],
        f"{key_root}_visible_label_source_details": [
            _label_source_layer_detail(layer, camera_zoom=camera_zoom) for layer in visible_label_source_layers
        ],
        f"{key_root}_line_width_samples": _layer_control_sample(line_layers, "line-width"),
        f"{key_root}_line_color_samples": _layer_control_sample(line_layers, "line-color"),
        f"{key_root}_fill_color_samples": _layer_control_sample(fill_layers, "fill-color"),
        f"{key_root}_line_dasharray_samples": _layer_control_sample(line_layers, "line-dasharray"),
        f"{key_root}_visible_line_width_samples": _layer_control_sample(
            visible_line_layers,
            "line-width",
        ),
        f"{key_root}_visible_line_color_samples": _layer_control_sample(
            visible_line_layers,
            "line-color",
        ),
        f"{key_root}_visible_fill_color_samples": _layer_control_sample(
            visible_fill_layers,
            "fill-color",
        ),
        f"{key_root}_visible_line_dasharray_samples": _layer_control_sample(
            visible_line_layers,
            "line-dasharray",
        ),
    }


def qgis_path_pedestrian_style_summary(
    style: Mapping[str, object],
    *,
    camera_zoom: float | None = None,
) -> dict[str, object]:
    return _path_pedestrian_style_summary(
        style,
        camera_zoom=camera_zoom,
        prefix="qgis",
        status="available",
    )


def source_path_pedestrian_style_summary(
    style: Mapping[str, object],
    *,
    camera_zoom: float | None = None,
) -> dict[str, object]:
    return _path_pedestrian_style_summary(
        style,
        camera_zoom=camera_zoom,
        prefix="source",
        status="available",
    )


def _missing_style_summary(prefix: str) -> dict[str, object]:
    return _path_pedestrian_style_summary(
        {},
        prefix=prefix,
        status="missing",
    )


def _missing_qgis_style_summary() -> dict[str, object]:
    return _missing_style_summary("qgis")


def _missing_source_style_summary() -> dict[str, object]:
    return _missing_style_summary("source")


def _label_style_rows(label_styles: Iterable[object]) -> list[dict[str, object]]:
    return [style for style in label_styles if isinstance(style, dict)]


def _is_path_pedestrian_label_style(label_style: Mapping[str, object]) -> bool:
    style_name = label_style.get("style_name")
    return isinstance(style_name, str) and style_name in PATH_PEDESTRIAN_LABEL_STYLE_IDS


def _label_zoom_level(camera_zoom: float | None) -> int | None:
    if camera_zoom is None:
        return None
    return int(camera_zoom)


def _label_zoom_bound(value: object, *, max_bound: bool = False) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if max_bound and value < 0:
            return None
        return float(value)
    return None


def _label_is_visible_at_zoom(label_style: Mapping[str, object], camera_zoom: float | None) -> bool:
    zoom_level = _label_zoom_level(camera_zoom)
    if zoom_level is None:
        return True
    minzoom = _label_zoom_bound(label_style.get("min_zoom_level"))
    maxzoom = _label_zoom_bound(label_style.get("max_zoom_level"), max_bound=True)
    if minzoom is not None and zoom_level < minzoom:
        return False
    return not (maxzoom is not None and zoom_level > maxzoom)


def _label_settings(label_style: Mapping[str, object]) -> Mapping[str, object]:
    settings = label_style.get("label_settings")
    return settings if isinstance(settings, Mapping) else {}


def _label_detail(label_style: Mapping[str, object]) -> dict[str, object]:
    detail = {
        "style_name": str(label_style.get("style_name") or ""),
        "layer_name": label_style.get("layer_name"),
        "geometry_type": label_style.get("geometry_type"),
    }
    for key in ("min_zoom_level", "max_zoom_level", "filter_expression"):
        value = label_style.get(key)
        if value not in (None, ""):
            detail[key] = value
    settings = _label_settings(label_style)
    for key in (
        "field_name",
        "placement",
        "priority",
        "repeat_distance",
        "repeat_distance_unit",
        "label_per_part",
        "merge_lines",
        "text_size",
        "text_color",
        "buffer_enabled",
        "buffer_size",
        "buffer_color",
        "thinning_settings",
    ):
        if key in settings:
            detail[key] = settings[key]
    return detail


def qgis_path_pedestrian_label_summary(
    label_styles: Iterable[object],
    *,
    camera_zoom: float | None = None,
) -> dict[str, object]:
    label_rows = [
        label_style
        for label_style in _label_style_rows(label_styles)
        if _is_path_pedestrian_label_style(label_style)
    ]
    visible_rows = [row for row in label_rows if _label_is_visible_at_zoom(row, camera_zoom)]
    return {
        "qgis_label_style_status": "available",
        "qgis_path_pedestrian_label_style_count": len(label_rows),
        "qgis_path_pedestrian_visible_label_style_count": len(visible_rows),
        "qgis_path_pedestrian_label_style_names": [
            str(row.get("style_name") or "") for row in label_rows
        ],
        "qgis_path_pedestrian_visible_label_style_names": [
            str(row.get("style_name") or "") for row in visible_rows
        ],
        "qgis_path_pedestrian_label_details": [_label_detail(row) for row in label_rows],
        "qgis_path_pedestrian_visible_label_details": [_label_detail(row) for row in visible_rows],
    }


def _missing_qgis_label_summary() -> dict[str, object]:
    return {
        "qgis_label_style_status": "missing",
        "qgis_path_pedestrian_label_style_count": 0,
        "qgis_path_pedestrian_visible_label_style_count": 0,
        "qgis_path_pedestrian_label_style_names": [],
        "qgis_path_pedestrian_visible_label_style_names": [],
        "qgis_path_pedestrian_label_details": [],
        "qgis_path_pedestrian_visible_label_details": [],
    }


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _duplicate_label_categories(camera: Mapping[str, object]) -> list[dict[str, object]]:
    categories: list[dict[str, object]] = []
    for category, key in DUPLICATE_LABEL_CATEGORY_KEYS:
        duplicates = _string_list(camera.get(key))
        if duplicates:
            categories.append({"category": category, "top_duplicates": duplicates})
    return categories


def _visible_label_detail_rows(camera: Mapping[str, object]) -> list[Mapping[str, object]]:
    details = camera.get("qgis_path_pedestrian_visible_label_details")
    detail_rows = details if isinstance(details, list) else []
    return [detail for detail in detail_rows if isinstance(detail, Mapping)]


def _visible_merge_line_label_styles(camera: Mapping[str, object]) -> list[str]:
    style_names: list[str] = []
    for detail in _visible_label_detail_rows(camera):
        style_name = str(detail.get("style_name") or "")
        if style_name and detail.get("merge_lines") is True:
            style_names.append(style_name)
    return style_names


def _visible_label_repeat_distances(camera: Mapping[str, object]) -> list[str]:
    repeat_distances: list[str] = []
    for detail in _visible_label_detail_rows(camera):
        style_name = str(detail.get("style_name") or "")
        repeat_distance = detail.get("repeat_distance")
        if (
            style_name
            and isinstance(repeat_distance, (int, float))
            and not isinstance(repeat_distance, bool)
        ):
            repeat_distances.append(f"{style_name}={_compact_json(repeat_distance)}")
    return repeat_distances


def _visible_label_source_detail_rows(camera: Mapping[str, object]) -> list[Mapping[str, object]]:
    details = camera.get("qgis_path_pedestrian_visible_label_source_details")
    detail_rows = details if isinstance(details, list) else []
    return [detail for detail in detail_rows if isinstance(detail, Mapping)]


def _visible_label_source_category_matches(
    camera: Mapping[str, object],
    duplicate_categories: Iterable[str],
) -> tuple[list[str], set[str]]:
    duplicate_category_set = set(duplicate_categories)
    matches: list[str] = []
    matched_categories: set[str] = set()
    for detail in _visible_label_source_detail_rows(camera):
        layer_id = str(detail.get("id") or "")
        categories = [
            category
            for category in _string_list(detail.get("duplicate_label_categories"))
            if category in duplicate_category_set
        ]
        if layer_id and categories:
            matches.append(f"{layer_id}: {', '.join(categories)}")
            matched_categories.update(categories)
    return matches, matched_categories


def _duplicate_label_diagnostic(camera: Mapping[str, object]) -> dict[str, object]:
    duplicate_categories = _duplicate_label_categories(camera)
    category_names = [str(row.get("category") or "") for row in duplicate_categories]
    label_source_matches, matched_categories = _visible_label_source_category_matches(
        camera,
        category_names,
    )
    return {
        "has_duplicate_feature_names": bool(duplicate_categories),
        "duplicate_name_categories": duplicate_categories,
        "visible_merge_line_label_styles": _visible_merge_line_label_styles(camera),
        "visible_label_repeat_distances": _visible_label_repeat_distances(camera),
        "visible_label_source_category_matches": label_source_matches,
        "unmatched_duplicate_name_categories": [
            category for category in category_names if category and category not in matched_categories
        ],
    }


def _qfit_base_style_layer_id(layer_id: object) -> str:
    normalized = str(layer_id or "")
    base_layer_id = base_mapbox_style_layer_id_for_qfit(layer_id)
    if base_layer_id != normalized:
        return base_layer_id
    for split_suffix in PATH_PEDESTRIAN_SOURCE_SPLIT_SUFFIXES:
        if normalized.endswith(split_suffix):
            split_base_layer_id = normalized[: -len(split_suffix)]
            if split_base_layer_id in PATH_PEDESTRIAN_SOURCE_SPLIT_BASE_LAYER_IDS:
                return split_base_layer_id
    return base_layer_id


def _visible_line_details(camera: Mapping[str, object], detail_key: str) -> list[Mapping[str, object]]:
    details = camera.get(detail_key)
    detail_rows = details if isinstance(details, list) else []
    return [
        detail
        for detail in detail_rows
        if isinstance(detail, Mapping) and detail.get("type") == "line"
    ]


def _stroke_controls(detail: Mapping[str, object]) -> dict[str, object]:
    return {
        key: detail[key]
        for key in PATH_PEDESTRIAN_STROKE_CONTROL_KEYS
        if key in detail
    }


def _candidate_count_value(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _candidate_count_map(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): count
        for key, raw_count in value.items()
        if (count := _candidate_count_value(raw_count)) > 0
    }


def _filtered_candidate_counts(value: object, allowed_types: frozenset[str]) -> dict[str, int]:
    return {
        candidate_type: count
        for candidate_type, count in _candidate_count_map(value).items()
        if candidate_type in allowed_types
    }


def _candidate_count_summary(
    counts: Mapping[str, int],
    *,
    total_count: int | None = None,
) -> dict[str, object]:
    summary: dict[str, object] = {
        "decoded_candidate_count": sum(counts.values()) if total_count is None else total_count
    }
    if counts:
        summary["decoded_candidate_type_counts"] = dict(counts)
    return summary


def _source_layer_structure(source_layer_id: str) -> str | None:
    prefix, _separator, _suffix = source_layer_id.partition("-")
    return PATH_PEDESTRIAN_SOURCE_STRUCTURES_BY_PREFIX.get(prefix)


def _structure_candidate_count_summary(
    camera: Mapping[str, object],
    source_layer_id: str,
    structure_count_key: str,
) -> dict[str, object] | None:
    structure = _source_layer_structure(source_layer_id)
    if structure is None:
        return None
    return _candidate_count_summary(
        _filtered_candidate_counts(camera.get(structure_count_key), frozenset({structure}))
    )


def _source_layer_decoded_candidate_summary(
    camera: Mapping[str, object],
    source_layer_id: str,
) -> dict[str, object]:
    if source_layer_id in PATH_PEDESTRIAN_PEDESTRIAN_SOURCE_LAYER_IDS:
        if (summary := _structure_candidate_count_summary(
            camera,
            source_layer_id,
            "pedestrian_line_structure_counts",
        )) is not None:
            return summary
        return _candidate_count_summary(
            _candidate_count_map(camera.get("pedestrian_line_type_counts")),
            total_count=_candidate_count_value(camera.get("pedestrian_line_count")),
        )
    if source_layer_id in PATH_PEDESTRIAN_STEP_SOURCE_LAYER_IDS:
        if (summary := _structure_candidate_count_summary(
            camera,
            source_layer_id,
            "step_line_structure_counts",
        )) is not None:
            return summary
        return _candidate_count_summary(
            _candidate_count_map(camera.get("step_line_structure_counts")),
            total_count=_candidate_count_value(camera.get("step_line_count")),
        )
    if source_layer_id in PATH_PEDESTRIAN_PATH_TRAIL_SOURCE_LAYER_IDS:
        if (summary := _structure_candidate_count_summary(
            camera,
            source_layer_id,
            "path_line_structure_counts",
        )) is not None:
            return summary
        return _candidate_count_summary(
            _filtered_candidate_counts(
                camera.get("path_line_type_counts"),
                PATH_PEDESTRIAN_TRAIL_TYPES,
            )
        )
    if source_layer_id in PATH_PEDESTRIAN_PATH_CYCLEWAY_PISTE_SOURCE_LAYER_IDS:
        if (summary := _structure_candidate_count_summary(
            camera,
            source_layer_id,
            "path_line_structure_counts",
        )) is not None:
            return summary
        return _candidate_count_summary(
            _filtered_candidate_counts(
                camera.get("path_line_type_counts"),
                PATH_PEDESTRIAN_CYCLEWAY_PISTE_TYPES,
            )
        )
    if source_layer_id in PATH_PEDESTRIAN_PATH_SOURCE_LAYER_IDS:
        if (summary := _structure_candidate_count_summary(
            camera,
            source_layer_id,
            "path_line_structure_counts",
        )) is not None:
            return summary
        return _candidate_count_summary(
            _candidate_count_map(camera.get("path_line_type_counts")),
            total_count=_candidate_count_value(camera.get("path_line_count")),
        )
    return {}


def _source_sampled_stroke_controls(
    detail: Mapping[str, object],
    camera_zoom: float | None,
) -> dict[str, object]:
    if camera_zoom is None:
        return {}
    sampled_controls: dict[str, object] = {}
    sampled_controls.update(_source_line_width_controls(detail.get("line-width"), camera_zoom))
    line_color = detail.get("line-color")
    if isinstance(line_color, str) and line_color:
        sampled_controls["line-color"] = line_color
    elif isinstance(line_color, list):
        sampled_controls["line-color"] = UNSAMPLED_EXPRESSION_CONTROL
    line_dasharray = _extract_line_dasharray_literal(detail.get("line-dasharray"), target_zoom=camera_zoom)
    if line_dasharray is not None:
        sampled_controls["line-dasharray"] = line_dasharray
    line_opacity = _extract_zoom_scalar_size_at_zoom(detail.get("line-opacity"), camera_zoom)
    if line_opacity is not None:
        sampled_controls["line-opacity"] = line_opacity
    return sampled_controls


def _source_line_width_controls(line_width_expr: object, camera_zoom: float) -> dict[str, object]:
    line_width_px = _extract_zoom_scalar_size_at_zoom(line_width_expr, camera_zoom)
    if line_width_px is None:
        return {}
    raw_width_mm = line_width_px * _MAPBOX_PIXEL_TO_MM
    capped_width_mm = max(0.1, min(raw_width_mm, _MAX_LINE_WIDTH_MM))
    controls: dict[str, object] = {"line-width": capped_width_mm}
    if raw_width_mm > _MAX_LINE_WIDTH_MM + 1e-12:
        controls["line-width_raw_mm"] = raw_width_mm
        controls["line-width_capped"] = True
    return controls


def _numeric_control(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _stroke_control_deltas(
    source_sampled_controls: Mapping[str, object],
    qgis_controls: Mapping[str, object],
) -> dict[str, object]:
    deltas: dict[str, object] = {}
    source_width = _numeric_control(source_sampled_controls.get("line-width"))
    qgis_width = _numeric_control(qgis_controls.get("line-width"))
    if source_width is not None and qgis_width is not None:
        deltas["line-width_delta_mm"] = qgis_width - source_width
        if source_width:
            deltas["line-width_ratio"] = qgis_width / source_width
    source_dasharray = _literal_line_dasharray(source_sampled_controls.get("line-dasharray"))
    qgis_dasharray = _literal_line_dasharray(qgis_controls.get("line-dasharray"))
    if source_dasharray is not None and qgis_dasharray is not None:
        deltas["line-dasharray_match"] = qgis_dasharray == source_dasharray
    source_color = source_sampled_controls.get("line-color")
    qgis_color = qgis_controls.get("line-color")
    if (
        isinstance(source_color, str)
        and source_color
        and source_color != UNSAMPLED_EXPRESSION_CONTROL
        and isinstance(qgis_color, str)
        and qgis_color
    ):
        deltas["line-color_match"] = qgis_color == source_color
    source_opacity = _numeric_control(source_sampled_controls.get("line-opacity"))
    qgis_opacity = _numeric_control(qgis_controls.get("line-opacity"))
    if source_opacity is not None and qgis_opacity is not None:
        deltas["line-opacity_delta"] = qgis_opacity - source_opacity
    return deltas


def _qgis_stroke_details_by_source_id(
    qgis_details: Iterable[Mapping[str, object]],
) -> dict[str, list[Mapping[str, object]]]:
    details_by_source_id: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for detail in qgis_details:
        layer_id = str(detail.get("id") or "")
        source_id = _qfit_base_style_layer_id(layer_id)
        details_by_source_id[source_id].append(detail)
    return details_by_source_id


def _is_auxiliary_qgis_stroke_layer_id(layer_id: object) -> bool:
    normalized = str(layer_id or "")
    return normalized.endswith(PATH_PEDESTRIAN_AUXILIARY_STROKE_SUFFIXES)


def _auxiliary_qgis_stroke_layer_ids(details: Iterable[Mapping[str, object]]) -> list[str]:
    return [
        str(detail.get("id") or "")
        for detail in details
        if _is_auxiliary_qgis_stroke_layer_id(detail.get("id"))
    ]


def _source_qgis_stroke_control_comparisons(camera: Mapping[str, object]) -> list[dict[str, object]]:
    source_details = _visible_line_details(camera, "source_path_pedestrian_visible_layer_details")
    qgis_details = _visible_line_details(camera, "qgis_path_pedestrian_visible_layer_details")
    qgis_details_by_source_id = _qgis_stroke_details_by_source_id(qgis_details)
    camera_zoom = _numeric_zoom(camera.get("camera_zoom"))
    comparisons = []
    for source_detail in source_details:
        source_id = str(source_detail.get("id") or "")
        matched_details = qgis_details_by_source_id.get(source_id, [])
        source_sampled_controls = _source_sampled_stroke_controls(source_detail, camera_zoom)
        comparison: dict[str, object] = {
            "source_layer_id": source_id,
            "source_controls": _stroke_controls(source_detail),
            "source_sampled_controls": source_sampled_controls,
            **_source_layer_decoded_candidate_summary(camera, source_id),
            "qgis_layer_ids": [str(detail.get("id") or "") for detail in matched_details],
            "qgis_controls": [
                {
                    "layer_id": str(detail.get("id") or ""),
                    "controls": _stroke_controls(detail),
                }
                for detail in matched_details
            ],
            "qgis_control_deltas": [
                {
                    "layer_id": str(detail.get("id") or ""),
                    "deltas": _stroke_control_deltas(
                        source_sampled_controls,
                        _stroke_controls(detail),
                    ),
                }
                for detail in matched_details
            ],
        }
        auxiliary_layer_ids = _auxiliary_qgis_stroke_layer_ids(matched_details)
        if auxiliary_layer_ids:
            comparison["qgis_auxiliary_layer_ids"] = auxiliary_layer_ids
        comparisons.append(comparison)
    return comparisons


def _comparison_by_source_id(
    comparisons: Iterable[Mapping[str, object]],
) -> dict[str, Mapping[str, object]]:
    return {
        str(comparison.get("source_layer_id") or ""): comparison
        for comparison in comparisons
        if comparison.get("source_layer_id")
    }


def _source_line_width_summary(
    comparison: Mapping[str, object],
    *,
    prefix: str,
) -> dict[str, object]:
    controls = comparison.get("source_sampled_controls")
    if not isinstance(controls, Mapping):
        return {}
    width = _numeric_control(controls.get("line-width"))
    if width is None:
        return {}
    summary: dict[str, object] = {f"source_{prefix}_width_mm": width}
    raw_width = _numeric_control(controls.get("line-width_raw_mm"))
    if raw_width is not None:
        summary[f"source_{prefix}_raw_width_mm"] = raw_width
    summary[f"source_{prefix}_capped"] = controls.get("line-width_capped") is True
    return summary


def _qgis_line_width_summary(
    comparison: Mapping[str, object],
    *,
    prefix: str,
    include_layer_suffix: str | None = None,
    exclude_layer_suffix: str | None = None,
) -> dict[str, object]:
    qgis_controls = comparison.get("qgis_controls")
    control_rows = qgis_controls if isinstance(qgis_controls, list) else []
    for control_row in control_rows:
        if not isinstance(control_row, Mapping):
            continue
        layer_id = str(control_row.get("layer_id") or "")
        if include_layer_suffix is not None and not layer_id.endswith(include_layer_suffix):
            continue
        if exclude_layer_suffix is not None and layer_id.endswith(exclude_layer_suffix):
            continue
        controls = control_row.get("controls")
        if not isinstance(controls, Mapping):
            continue
        width = _numeric_control(controls.get("line-width"))
        if width is None:
            continue
        return {f"qgis_{prefix}_layer_id": layer_id, f"qgis_{prefix}_width_mm": width}
    return {}


def _width_relationship(numerator: object, denominator: object) -> float | None:
    numerator_value = _numeric_control(numerator)
    denominator_value = _numeric_control(denominator)
    if numerator_value is None or denominator_value is None or denominator_value == 0:
        return None
    return numerator_value / denominator_value


def _add_source_core_case_relationship_fields(relationship: dict[str, object]) -> None:
    core_width = _numeric_control(relationship.get("source_core_width_mm"))
    case_width = _numeric_control(relationship.get("source_case_width_mm"))
    if core_width is not None and case_width is not None:
        relationship["source_both_widths_capped"] = (
            relationship.get("source_core_capped") is True
            and relationship.get("source_case_capped") is True
        )
        relationship["source_case_over_core_mm"] = case_width - core_width
        source_ratio = _width_relationship(case_width, core_width)
        if source_ratio is not None:
            relationship["source_case_to_core_ratio"] = source_ratio


def _add_qgis_core_case_relationship_fields(relationship: dict[str, object]) -> None:
    core_width = _numeric_control(relationship.get("qgis_core_width_mm"))
    case_width = _numeric_control(relationship.get("qgis_case_width_mm"))
    if core_width is None or case_width is None:
        return
    relationship["qgis_case_over_core_mm"] = case_width - core_width
    qgis_ratio = _width_relationship(case_width, core_width)
    if qgis_ratio is not None:
        relationship["qgis_case_to_core_ratio"] = qgis_ratio
    if relationship.get("source_both_widths_capped") is True and qgis_ratio:
        relationship["qgis_ratio_preserving_core_width_mm_at_cap"] = _MAX_LINE_WIDTH_MM / qgis_ratio


def _pedestrian_core_case_cap_relationships(
    camera: Mapping[str, object],
) -> list[dict[str, object]]:
    comparisons = camera.get("source_qgis_stroke_control_comparisons")
    comparison_rows = comparisons if isinstance(comparisons, list) else []
    comparisons_by_source_id = _comparison_by_source_id(
        comparison for comparison in comparison_rows if isinstance(comparison, Mapping)
    )
    relationships: list[dict[str, object]] = []
    for core_layer_id, case_layer_id in PATH_PEDESTRIAN_CORE_CASE_LAYER_PAIRS:
        core_comparison = comparisons_by_source_id.get(core_layer_id)
        case_comparison = comparisons_by_source_id.get(case_layer_id)
        if core_comparison is None or case_comparison is None:
            continue
        relationship: dict[str, object] = {
            "source_core_layer_id": core_layer_id,
            "source_case_layer_id": case_layer_id,
            "cap_limit_mm": _MAX_LINE_WIDTH_MM,
        }
        relationship.update(_source_line_width_summary(core_comparison, prefix="core"))
        relationship.update(_source_line_width_summary(case_comparison, prefix="case"))
        relationship.update(
            _qgis_line_width_summary(
                core_comparison,
                prefix="core",
                exclude_layer_suffix=PATH_PEDESTRIAN_PALE_CASING_SUFFIX,
            )
        )
        relationship.update(
            _qgis_line_width_summary(
                case_comparison,
                prefix="case",
                exclude_layer_suffix=PATH_PEDESTRIAN_PALE_CASING_SUFFIX,
            )
        )
        relationship.update(
            _qgis_line_width_summary(
                case_comparison,
                prefix="pale_casing",
                include_layer_suffix=PATH_PEDESTRIAN_PALE_CASING_SUFFIX,
            )
        )
        _add_source_core_case_relationship_fields(relationship)
        _add_qgis_core_case_relationship_fields(relationship)
        relationships.append(relationship)
    return relationships


def _camera_focus_row(
    camera_report: Mapping[str, object],
    *,
    source_style: Mapping[str, object] | None,
    qgis_style: Mapping[str, object] | None,
    qgis_label_styles: Iterable[object] | None,
) -> dict[str, object]:
    row = {
        "camera": camera_report.get("camera"),
        "status": camera_report.get("status"),
        "camera_zoom": camera_report.get("camera_zoom"),
        "tile_zoom": camera_report.get("tile_zoom"),
        "pedestrian_path_polygon_count": camera_report.get("pedestrian_polygon_candidate_count", 0),
        "pedestrian_line_count": camera_report.get("pedestrian_line_candidate_count", 0),
        "path_line_count": camera_report.get("path_line_candidate_count", 0),
        "step_line_count": camera_report.get("step_line_candidate_count", 0),
        "pedestrian_line_type_counts": _candidate_count_map(camera_report.get("pedestrian_line_type_counts")),
        "pedestrian_line_structure_counts": _candidate_count_map(camera_report.get("pedestrian_line_structure_counts")),
        "path_line_type_counts": _candidate_count_map(camera_report.get("path_line_type_counts")),
        "path_line_structure_counts": _candidate_count_map(camera_report.get("path_line_structure_counts")),
        "step_line_structure_counts": _candidate_count_map(camera_report.get("step_line_structure_counts")),
        "top_pedestrian_line_types": _top_count_labels(camera_report.get("pedestrian_line_type_counts")),
        "top_path_line_types": _top_count_labels(camera_report.get("path_line_type_counts")),
        "top_step_structures": _top_count_labels(camera_report.get("step_line_structure_counts")),
        "top_pedestrian_line_duplicate_names": _top_count_labels(
            camera_report.get("pedestrian_line_duplicate_name_counts")
        ),
        "top_path_line_duplicate_names": _top_count_labels(camera_report.get("path_line_duplicate_name_counts")),
        "top_step_line_duplicate_names": _top_count_labels(camera_report.get("step_line_duplicate_name_counts")),
        "top_path_signatures": _top_count_labels(camera_report.get("path_line_signature_counts")),
        "top_step_signatures": _top_count_labels(camera_report.get("step_line_signature_counts")),
    }
    camera_zoom = _numeric_zoom(camera_report.get("camera_zoom"))
    row.update(
        source_path_pedestrian_style_summary(source_style, camera_zoom=camera_zoom)
        if source_style is not None
        else _missing_source_style_summary()
    )
    row.update(
        qgis_path_pedestrian_style_summary(qgis_style, camera_zoom=camera_zoom)
        if qgis_style is not None
        else _missing_qgis_style_summary()
    )
    row.update(
        qgis_path_pedestrian_label_summary(qgis_label_styles, camera_zoom=camera_zoom)
        if qgis_label_styles is not None
        else _missing_qgis_label_summary()
    )
    row["duplicate_label_diagnostic"] = _duplicate_label_diagnostic(row)
    row["source_qgis_stroke_control_comparisons"] = _source_qgis_stroke_control_comparisons(row)
    row["pedestrian_core_case_cap_relationships"] = _pedestrian_core_case_cap_relationships(row)
    return row


def _json_safe_artifact_value(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe_artifact_value(child) for key, child in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe_artifact_value(child) for child in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _json_safe_artifacts(input_artifacts: Mapping[str, object]) -> dict[str, object]:
    return {str(key): _json_safe_artifact_value(value) for key, value in input_artifacts.items()}


def _focus_camera_reports(road_feature_report: Mapping[str, object]) -> list[Mapping[str, object]]:
    camera_reports = road_feature_report.get("cameras")
    if not isinstance(camera_reports, list):
        return []
    return [camera_report for camera_report in camera_reports if isinstance(camera_report, Mapping)]


def _is_path_pedestrian_focus_camera(camera_report: Mapping[str, object]) -> bool:
    return camera_report.get("status") == "decoded" and _has_path_pedestrian_focus(camera_report)


def _build_camera_focus_rows(
    road_feature_report: Mapping[str, object],
    *,
    source_style: Mapping[str, object] | None,
    qgis_styles: Mapping[str, Mapping[str, object]],
    qgis_label_styles: Mapping[str, Iterable[object]],
    visual_artifacts: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    rows = []
    for camera_report in _focus_camera_reports(road_feature_report):
        if not _is_path_pedestrian_focus_camera(camera_report):
            continue
        camera_name = str(camera_report.get("camera") or "")
        row = _camera_focus_row(
            camera_report,
            source_style=source_style,
            qgis_style=qgis_styles.get(camera_name),
            qgis_label_styles=qgis_label_styles.get(camera_name),
        )
        if camera_name in visual_artifacts:
            row["visual_artifacts"] = _json_safe_artifact_value(visual_artifacts[camera_name])
        rows.append(row)
    return rows


def build_path_pedestrian_focus_report(
    road_feature_report: Mapping[str, object],
    *,
    source_style: Mapping[str, object] | None = None,
    qgis_styles_by_camera: Mapping[str, Mapping[str, object]] | None = None,
    qgis_label_styles_by_camera: Mapping[str, Iterable[object]] | None = None,
    visual_artifacts_by_camera: Mapping[str, Mapping[str, object]] | None = None,
    generated_at: dt.datetime | None = None,
    input_artifacts: Mapping[str, object] | None = None,
) -> dict[str, object]:
    qgis_styles = qgis_styles_by_camera or {}
    qgis_label_styles = qgis_label_styles_by_camera or {}
    visual_artifacts = visual_artifacts_by_camera or {}
    rows = _build_camera_focus_rows(
        road_feature_report,
        source_style=source_style,
        qgis_styles=qgis_styles,
        qgis_label_styles=qgis_label_styles,
        visual_artifacts=visual_artifacts,
    )
    generated = generated_at or dt.datetime.now(dt.timezone.utc)
    source_style_input_count = 1 if source_style is not None else 0
    source_matched_camera_count = sum(1 for row in rows if row.get("source_style_status") == "available")
    qgis_matched_camera_count = sum(1 for row in rows if row.get("qgis_style_status") == "available")
    qgis_label_matched_camera_count = sum(
        1 for row in rows if row.get("qgis_label_style_status") == "available"
    )
    report = {
        "generated": generated.astimezone(dt.timezone.utc).isoformat(),
        "road_feature_generated": road_feature_report.get("generated"),
        "style_owner": road_feature_report.get("style_owner"),
        "style_id": road_feature_report.get("style_id"),
        "camera_count": len(rows),
        "source_style_camera_count": source_matched_camera_count,
        "source_style_input_count": source_style_input_count,
        "qgis_style_camera_count": qgis_matched_camera_count,
        "qgis_style_input_count": len(qgis_styles),
        "qgis_label_style_camera_count": qgis_label_matched_camera_count,
        "qgis_label_style_input_count": len(qgis_label_styles),
        "cameras": rows,
    }
    if input_artifacts is not None:
        report["input_artifacts"] = _json_safe_artifacts(input_artifacts)
    return report


def _markdown_cell(value: object) -> str:
    if value is None or value == "":
        text = "-"
    elif isinstance(value, list):
        text = _compact_json(value, max_length=160)
    else:
        text = str(value)
    return text.replace("\n", " ").replace("|", "\\|")


def _markdown_table_row(cells: Iterable[object]) -> str:
    return "| " + " | ".join(_markdown_cell(cell) for cell in cells) + " |"


def _qgis_control_summary(camera: Mapping[str, object]) -> list[str]:
    return [
        f"filters={camera.get('qgis_path_pedestrian_filter_layer_count', 0)}",
        f"visible_filters={camera.get('qgis_path_pedestrian_visible_filter_layer_count', 0)}",
        f"widths={camera.get('qgis_path_pedestrian_line_width_layer_count', 0)}",
        f"visible_widths={camera.get('qgis_path_pedestrian_visible_line_width_layer_count', 0)}",
        f"dashes={camera.get('qgis_path_pedestrian_line_dasharray_layer_count', 0)}",
        f"visible_dashes={camera.get('qgis_path_pedestrian_visible_line_dasharray_layer_count', 0)}",
        f"line_colors={camera.get('qgis_path_pedestrian_line_color_layer_count', 0)}",
        f"fill_colors={camera.get('qgis_path_pedestrian_fill_color_layer_count', 0)}",
        f"opacities={camera.get('qgis_path_pedestrian_line_opacity_layer_count', 0)}",
    ]


def _qgis_color_samples(camera: Mapping[str, object]) -> list[str]:
    line_samples = camera.get("qgis_path_pedestrian_visible_line_color_samples")
    fill_samples = camera.get("qgis_path_pedestrian_visible_fill_color_samples")
    samples = []
    if isinstance(line_samples, list):
        samples.extend(str(sample) for sample in line_samples[:1])
    if isinstance(fill_samples, list):
        samples.extend(str(sample) for sample in fill_samples[:1])
    return samples


def _qgis_stroke_samples(camera: Mapping[str, object]) -> list[str]:
    width_samples = camera.get("qgis_path_pedestrian_visible_line_width_samples")
    dash_samples = camera.get("qgis_path_pedestrian_visible_line_dasharray_samples")
    samples = []
    if isinstance(width_samples, list):
        samples.extend(str(sample) for sample in width_samples[:2])
    if isinstance(dash_samples, list):
        samples.extend(str(sample) for sample in dash_samples[:2])
    return samples


def _input_artifact_markdown_lines(report: Mapping[str, object]) -> list[str]:
    input_artifacts = report.get("input_artifacts")
    if not isinstance(input_artifacts, Mapping):
        return []
    lines: list[str] = []
    road_features_json = input_artifacts.get("road_features_json")
    if isinstance(road_features_json, str) and road_features_json:
        lines.append(f"Road features input: `{road_features_json}`")
    source_style_json = input_artifacts.get("source_style_json")
    if isinstance(source_style_json, str) and source_style_json:
        lines.append(f"Source style input: `{source_style_json}`")
    comparison_summary_jsons = _string_list(input_artifacts.get("comparison_summary_jsons"))
    if comparison_summary_jsons:
        lines.append(f"Comparison summary inputs: `{', '.join(comparison_summary_jsons)}`")
    qgis_style_cameras = _string_list(input_artifacts.get("qgis_style_cameras"))
    if qgis_style_cameras:
        lines.append(f"QGIS style input cameras: `{', '.join(qgis_style_cameras)}`")
    qgis_label_style_cameras = _string_list(input_artifacts.get("qgis_label_style_cameras"))
    if qgis_label_style_cameras:
        lines.append(f"QGIS label style input cameras: `{', '.join(qgis_label_style_cameras)}`")
    return lines


def _detail_zoom_band(detail: Mapping[str, object]) -> str:
    minzoom = detail.get("minzoom")
    maxzoom = detail.get("maxzoom")
    if minzoom is None and maxzoom is None:
        return "all"
    if minzoom is None:
        return f"z<{maxzoom}"
    if maxzoom is None:
        return f"z>={minzoom}"
    return f"{minzoom}<=z<{maxzoom}"


def _detail_paint_controls(detail: Mapping[str, object]) -> list[str]:
    return [
        f"{key}={_compact_json(detail.get(key))}"
        for key in PATH_PEDESTRIAN_DETAIL_PAINT_KEYS
        if key in detail
    ]


def _visible_style_detail_markdown_lines(
    cameras: Iterable[object],
    *,
    detail_key: str,
    title: str,
) -> list[str]:
    lines = ["", title, ""]
    detail_row_count = 0
    for camera in cameras:
        if not isinstance(camera, Mapping):
            continue
        details = camera.get(detail_key)
        detail_rows = details if isinstance(details, list) else []
        mapping_rows = [detail for detail in detail_rows if isinstance(detail, Mapping)]
        if not mapping_rows:
            continue
        detail_row_count += len(mapping_rows)
        lines.extend(
            [
                f"### {camera.get('camera')}",
                "",
                "| Layer | Type | Zoom band | Paint controls | Filter |",
                MARKDOWN_SEPARATOR_5_COLUMNS,
            ]
        )
        for detail in mapping_rows:
            lines.append(
                _markdown_table_row(
                    [
                        detail.get("id"),
                        detail.get("type"),
                        _detail_zoom_band(detail),
                        _detail_paint_controls(detail),
                        detail.get("filter"),
                    ]
                )
            )
        lines.append("")
    return lines if detail_row_count else []


def _visible_source_detail_markdown_lines(cameras: Iterable[object]) -> list[str]:
    return _visible_style_detail_markdown_lines(
        cameras,
        detail_key="source_path_pedestrian_visible_layer_details",
        title="## Visible source Mapbox layer details",
    )


def _visible_detail_markdown_lines(cameras: Iterable[object]) -> list[str]:
    return _visible_style_detail_markdown_lines(
        cameras,
        detail_key="qgis_path_pedestrian_visible_layer_details",
        title="## Visible QGIS layer details",
    )


def _qgis_stroke_control_summaries(comparison: Mapping[str, object]) -> list[str]:
    controls = comparison.get("qgis_controls")
    control_rows = controls if isinstance(controls, list) else []
    summaries = []
    for control_row in control_rows:
        if not isinstance(control_row, Mapping):
            continue
        layer_id = str(control_row.get("layer_id") or "")
        layer_controls = control_row.get("controls")
        if layer_id and isinstance(layer_controls, Mapping):
            summaries.append(f"{layer_id}: {', '.join(_stroke_control_summaries(layer_controls))}")
    return summaries


def _qgis_stroke_delta_summaries(comparison: Mapping[str, object]) -> list[str]:
    delta_rows = comparison.get("qgis_control_deltas")
    rows = delta_rows if isinstance(delta_rows, list) else []
    summaries = []
    for delta_row in rows:
        if not isinstance(delta_row, Mapping):
            continue
        layer_id = str(delta_row.get("layer_id") or "")
        deltas = delta_row.get("deltas")
        delta_summary = _stroke_delta_summaries(deltas)
        if layer_id and delta_summary:
            summaries.append(f"{layer_id}: {', '.join(delta_summary)}")
    return summaries


def _decoded_candidate_summaries(comparison: Mapping[str, object]) -> list[str]:
    summaries = []
    if "decoded_candidate_count" in comparison:
        summaries.append(f"count={_compact_json(comparison.get('decoded_candidate_count'))}")
    type_counts = comparison.get("decoded_candidate_type_counts")
    if isinstance(type_counts, Mapping) and type_counts:
        summaries.append(f"types={_compact_json(type_counts)}")
    return summaries


def _stroke_delta_summaries(deltas: object) -> list[str]:
    if not isinstance(deltas, Mapping):
        return []
    return [
        f"{key}={_compact_json(deltas.get(key))}"
        for key in PATH_PEDESTRIAN_STROKE_DELTA_KEYS
        if key in deltas
    ]


def _stroke_control_summaries(controls: object) -> list[str]:
    if not isinstance(controls, Mapping):
        return []
    return [
        f"{key}={_compact_json(controls.get(key))}"
        for key in PATH_PEDESTRIAN_STROKE_CONTROL_KEYS
        if key in controls
    ]


def _source_qgis_stroke_control_markdown_lines(cameras: Iterable[object]) -> list[str]:
    rows: list[list[object]] = []
    for camera in cameras:
        if not isinstance(camera, Mapping):
            continue
        comparisons = camera.get("source_qgis_stroke_control_comparisons")
        comparison_rows = comparisons if isinstance(comparisons, list) else []
        for comparison in comparison_rows:
            if not isinstance(comparison, Mapping) or not comparison.get("source_layer_id"):
                continue
            rows.append(
                [
                    camera.get("camera"),
                    comparison.get("source_layer_id"),
                    _decoded_candidate_summaries(comparison),
                    _stroke_control_summaries(comparison.get("source_controls")),
                    _stroke_control_summaries(comparison.get("source_sampled_controls")),
                    comparison.get("qgis_layer_ids"),
                    _qgis_stroke_control_summaries(comparison),
                    _qgis_stroke_delta_summaries(comparison),
                ]
            )
    if not rows:
        return []
    lines = ["", "## Source vs QGIS stroke controls", ""]
    lines.extend(
        [
            (
                "Pairs visible source Mapbox path/pedestrian line layers with visible QGIS "
                "variants by original style-layer id."
            ),
            (
                "Source sampled controls evaluate zoom expressions at the camera zoom; "
                "line-width is shown in QGIS millimetres and dash arrays are the selected "
                "Mapbox line-width-relative pattern. Literal line colors are carried through; "
                "expression colors are marked as expression-not-sampled and remain available "
                "in the raw source controls."
            ),
            (
                "QGIS deltas compare visible QGIS controls against source sampled controls "
                "where both sides are directly comparable; line-width deltas are QGIS minus "
                "source in millimetres."
            ),
            (
                "Decoded candidate counts estimate how many currently decoded road features "
                "match each source stroke family, so visible control deltas without matching "
                "features can be treated as lower-confidence styling signals."
            ),
            "Rows with empty QGIS columns identify source strokes with no visible QGIS counterpart.",
            "",
            "| Camera | Source layer | Decoded candidates | Source controls | Source sampled controls | QGIS layer ids | QGIS controls | QGIS deltas |",
            MARKDOWN_SEPARATOR_8_COLUMNS,
        ]
    )
    lines.extend(_markdown_table_row(row) for row in rows)
    return lines


def _qgis_auxiliary_stroke_markdown_lines(cameras: Iterable[object]) -> list[str]:
    rows: list[list[object]] = []
    for camera in cameras:
        if not isinstance(camera, Mapping):
            continue
        comparisons = camera.get("source_qgis_stroke_control_comparisons")
        comparison_rows = comparisons if isinstance(comparisons, list) else []
        for comparison in comparison_rows:
            if not isinstance(comparison, Mapping):
                continue
            auxiliary_layer_ids = comparison.get("qgis_auxiliary_layer_ids")
            if isinstance(auxiliary_layer_ids, list) and auxiliary_layer_ids:
                rows.append(
                    [
                        camera.get("camera"),
                        comparison.get("source_layer_id"),
                        auxiliary_layer_ids,
                    ]
                )
    if not rows:
        return []
    lines = ["", "## QGIS auxiliary stroke layers", ""]
    lines.extend(
        [
            (
                "Lists qfit-only helper stroke layers paired with a source layer, such as pale "
                "casing underlays. Their deltas are useful for validating the helper layer, but "
                "should not be read as one-to-one source style mismatches."
            ),
            "",
            "| Camera | Source layer | QGIS auxiliary layer ids |",
            "| --- | --- | --- |",
        ]
    )
    lines.extend(_markdown_table_row(row) for row in rows)
    return lines


def _comparison_auxiliary_layer_ids(comparison: Mapping[str, object]) -> set[str]:
    auxiliary_layer_values = comparison.get("qgis_auxiliary_layer_ids")
    if not isinstance(auxiliary_layer_values, list):
        return set()
    return {str(layer_id) for layer_id in auxiliary_layer_values if layer_id}


def _comparison_delta_rows(comparison: Mapping[str, object]) -> list[object]:
    delta_rows = comparison.get("qgis_control_deltas")
    return delta_rows if isinstance(delta_rows, list) else []


def _non_auxiliary_stroke_delta_row(
    *,
    camera: Mapping[str, object],
    comparison: Mapping[str, object],
    delta_row: object,
    auxiliary_layer_ids: set[str],
) -> dict[str, object] | None:
    if not isinstance(delta_row, Mapping):
        return None
    layer_id = str(delta_row.get("layer_id") or "")
    if (
        not layer_id
        or layer_id in auxiliary_layer_ids
        or _is_auxiliary_qgis_stroke_layer_id(layer_id)
    ):
        return None
    deltas = delta_row.get("deltas")
    if not isinstance(deltas, Mapping):
        return None
    width_delta = _numeric_control(deltas.get("line-width_delta_mm"))
    if width_delta is None:
        return None
    row = {
        "camera": camera.get("camera"),
        "source_layer_id": comparison.get("source_layer_id"),
        "qgis_layer_id": layer_id,
        "line_width_delta_mm": width_delta,
        "line_width_abs_delta_mm": abs(width_delta),
        "line_width_ratio": _numeric_control(deltas.get("line-width_ratio")),
    }
    source_sampled_controls = comparison.get("source_sampled_controls")
    if isinstance(source_sampled_controls, Mapping):
        source_width = _numeric_control(source_sampled_controls.get("line-width"))
        source_raw_width = _numeric_control(source_sampled_controls.get("line-width_raw_mm"))
        if source_width is not None:
            row["source_line_width_mm"] = source_width
        if source_raw_width is not None:
            row["source_line_width_raw_mm"] = source_raw_width
        if source_sampled_controls.get("line-width_capped") is True:
            row["source_line_width_capped"] = True
    if "decoded_candidate_count" in comparison:
        row["decoded_candidate_count"] = comparison.get("decoded_candidate_count")
    if "decoded_candidate_type_counts" in comparison:
        row["decoded_candidate_type_counts"] = comparison.get("decoded_candidate_type_counts")
    return row


def _camera_source_qgis_stroke_comparisons(camera: object) -> list[Mapping[str, object]]:
    if not isinstance(camera, Mapping):
        return []
    comparisons = camera.get("source_qgis_stroke_control_comparisons")
    comparison_rows = comparisons if isinstance(comparisons, list) else []
    return [
        comparison
        for comparison in comparison_rows
        if isinstance(comparison, Mapping) and bool(comparison.get("source_layer_id"))
    ]


def _camera_non_auxiliary_stroke_delta_rows(camera: object) -> list[dict[str, object]]:
    if not isinstance(camera, Mapping):
        return []
    rows: list[dict[str, object]] = []
    for comparison in _camera_source_qgis_stroke_comparisons(camera):
        auxiliary_layer_ids = _comparison_auxiliary_layer_ids(comparison)
        for delta_row in _comparison_delta_rows(comparison):
            ranked_row = _non_auxiliary_stroke_delta_row(
                camera=camera,
                comparison=comparison,
                delta_row=delta_row,
                auxiliary_layer_ids=auxiliary_layer_ids,
            )
            if ranked_row is not None:
                rows.append(ranked_row)
    return rows


def _stroke_delta_sort_text(value: object) -> str:
    return str(value) if value else ""


def _stroke_delta_decoded_candidate_count(row: Mapping[str, object]) -> int:
    return _candidate_count_value(row.get("decoded_candidate_count"))


def _stroke_delta_source_width_capped(row: Mapping[str, object]) -> bool:
    return row.get("source_line_width_capped") is True


def _stroke_delta_sort_key(row: Mapping[str, object]) -> tuple[int, int, float, str, str, str]:
    decoded_candidate_count = _stroke_delta_decoded_candidate_count(row)
    return (
        0 if decoded_candidate_count > 0 else 1,
        1 if _stroke_delta_source_width_capped(row) else 0,
        -float(row["line_width_abs_delta_mm"]),
        _stroke_delta_sort_text(row.get("camera")),
        _stroke_delta_sort_text(row.get("source_layer_id")),
        _stroke_delta_sort_text(row.get("qgis_layer_id")),
    )


def _largest_non_auxiliary_stroke_delta_rows(
    cameras: Iterable[object],
    *,
    limit: int = PATH_PEDESTRIAN_LARGEST_STROKE_DELTA_LIMIT,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for camera in cameras:
        rows.extend(_camera_non_auxiliary_stroke_delta_rows(camera))
    rows.sort(key=_stroke_delta_sort_key)
    return rows[:limit]


def _stroke_delta_source_cap_summaries(row: Mapping[str, object]) -> list[str]:
    if not _stroke_delta_source_width_capped(row):
        return []
    summaries = ["source_width_capped=true"]
    source_width = _numeric_control(row.get("source_line_width_mm"))
    source_raw_width = _numeric_control(row.get("source_line_width_raw_mm"))
    if source_width is not None:
        summaries.append(f"source_width_mm={_compact_json(source_width)}")
    if source_raw_width is not None:
        summaries.append(f"source_raw_width_mm={_compact_json(source_raw_width)}")
    summaries.append(f"cap_limit_mm={_compact_json(_MAX_LINE_WIDTH_MM)}")
    return summaries


def _largest_non_auxiliary_stroke_delta_markdown_lines(cameras: Iterable[object]) -> list[str]:
    rows = _largest_non_auxiliary_stroke_delta_rows(cameras)
    if not rows:
        return []
    lines = ["", "## Largest non-auxiliary stroke width deltas", ""]
    lines.extend(
        [
            (
                "Ranks directly comparable QGIS stroke widths against sampled Mapbox source controls, "
                "excluding qfit-only auxiliary helper strokes such as pale casing underlays."
            ),
            (
                "Rows with decoded candidates sort ahead of zero-candidate rows; source-capped rows "
                "sort after uncapped rows because the apparent width delta can reflect qfit's QGIS "
                "line-width cap rather than a direct Mapbox/QGIS style mismatch. Within each group, "
                "positive deltas mean QGIS is wider than the sampled source stroke and negative deltas "
                "mean QGIS is narrower. Use this as a priority signal, not an automatic styling change."
            ),
            "",
            "| Camera | Source layer | QGIS layer | Decoded candidates | Candidate types | Source cap | Delta mm | Abs delta mm | Ratio |",
            MARKDOWN_SEPARATOR_9_COLUMNS,
        ]
    )
    lines.extend(
        _markdown_table_row(
            [
                row.get("camera"),
                row.get("source_layer_id"),
                row.get("qgis_layer_id"),
                row.get("decoded_candidate_count"),
                _top_count_labels(row.get("decoded_candidate_type_counts")),
                _stroke_delta_source_cap_summaries(row),
                row.get("line_width_delta_mm"),
                row.get("line_width_abs_delta_mm"),
                row.get("line_width_ratio"),
            ]
        )
        for row in rows
    )
    return lines


def _qgis_controls_by_layer_id(comparison: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    controls = comparison.get("qgis_controls")
    control_rows = controls if isinstance(controls, list) else []
    rows_by_layer_id: dict[str, Mapping[str, object]] = {}
    for control_row in control_rows:
        if not isinstance(control_row, Mapping):
            continue
        layer_id = str(control_row.get("layer_id") or "")
        layer_controls = control_row.get("controls")
        if layer_id and isinstance(layer_controls, Mapping):
            rows_by_layer_id[layer_id] = layer_controls
    return rows_by_layer_id


def _non_auxiliary_dash_mismatch_row(
    *,
    camera: Mapping[str, object],
    comparison: Mapping[str, object],
    delta_row: object,
    auxiliary_layer_ids: set[str],
    qgis_controls_by_layer_id: Mapping[str, Mapping[str, object]],
) -> dict[str, object] | None:
    if not isinstance(delta_row, Mapping):
        return None
    layer_id = str(delta_row.get("layer_id") or "")
    if (
        not layer_id
        or layer_id in auxiliary_layer_ids
        or _is_auxiliary_qgis_stroke_layer_id(layer_id)
    ):
        return None
    deltas = delta_row.get("deltas")
    if not isinstance(deltas, Mapping) or deltas.get("line-dasharray_match") is not False:
        return None
    source_sampled_controls = comparison.get("source_sampled_controls")
    qgis_controls = qgis_controls_by_layer_id.get(layer_id, {})
    row = {
        "camera": camera.get("camera"),
        "source_layer_id": comparison.get("source_layer_id"),
        "qgis_layer_id": layer_id,
        "source_dasharray": source_sampled_controls.get("line-dasharray")
        if isinstance(source_sampled_controls, Mapping)
        else None,
        "qgis_dasharray": qgis_controls.get("line-dasharray"),
        "line_width_delta_mm": _numeric_control(deltas.get("line-width_delta_mm")),
        "line_width_ratio": _numeric_control(deltas.get("line-width_ratio")),
    }
    if "decoded_candidate_count" in comparison:
        row["decoded_candidate_count"] = comparison.get("decoded_candidate_count")
    if "decoded_candidate_type_counts" in comparison:
        row["decoded_candidate_type_counts"] = comparison.get("decoded_candidate_type_counts")
    return row


def _camera_non_auxiliary_dash_mismatch_rows(camera: object) -> list[dict[str, object]]:
    if not isinstance(camera, Mapping):
        return []
    rows: list[dict[str, object]] = []
    for comparison in _camera_source_qgis_stroke_comparisons(camera):
        auxiliary_layer_ids = _comparison_auxiliary_layer_ids(comparison)
        qgis_controls_by_layer_id = _qgis_controls_by_layer_id(comparison)
        for delta_row in _comparison_delta_rows(comparison):
            ranked_row = _non_auxiliary_dash_mismatch_row(
                camera=camera,
                comparison=comparison,
                delta_row=delta_row,
                auxiliary_layer_ids=auxiliary_layer_ids,
                qgis_controls_by_layer_id=qgis_controls_by_layer_id,
            )
            if ranked_row is not None:
                rows.append(ranked_row)
    return rows


def _dash_mismatch_sort_key(row: Mapping[str, object]) -> tuple[int, int, str, str, str]:
    decoded_candidate_count = _stroke_delta_decoded_candidate_count(row)
    return (
        0 if decoded_candidate_count > 0 else 1,
        -decoded_candidate_count,
        _stroke_delta_sort_text(row.get("camera")),
        _stroke_delta_sort_text(row.get("source_layer_id")),
        _stroke_delta_sort_text(row.get("qgis_layer_id")),
    )


def _non_auxiliary_dash_mismatch_rows(cameras: Iterable[object]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for camera in cameras:
        rows.extend(_camera_non_auxiliary_dash_mismatch_rows(camera))
    rows.sort(key=_dash_mismatch_sort_key)
    return rows


def _non_auxiliary_dash_mismatch_markdown_lines(cameras: Iterable[object]) -> list[str]:
    rows = _non_auxiliary_dash_mismatch_rows(cameras)
    if not rows:
        return []
    lines = ["", "## Non-auxiliary dash mismatches", ""]
    lines.extend(
        [
            (
                "Ranks visible source/QGIS stroke pairs where sampled line-dasharray controls differ, "
                "excluding qfit-only auxiliary helper strokes."
            ),
            (
                "Rows with decoded candidates sort ahead of zero-candidate rows, so dash work can be "
                "prioritized only when it is backed by decoded path/pedestrian/step features."
            ),
            "",
            "| Camera | Source layer | QGIS layer | Decoded candidates | Candidate types | Source dasharray | QGIS dasharray | Width delta mm | Ratio |",
            MARKDOWN_SEPARATOR_9_COLUMNS,
        ]
    )
    lines.extend(
        _markdown_table_row(
            [
                row.get("camera"),
                row.get("source_layer_id"),
                row.get("qgis_layer_id"),
                row.get("decoded_candidate_count"),
                _top_count_labels(row.get("decoded_candidate_type_counts")),
                row.get("source_dasharray"),
                row.get("qgis_dasharray"),
                row.get("line_width_delta_mm"),
                row.get("line_width_ratio"),
            ]
        )
        for row in rows
    )
    return lines


def _core_case_relationship_summary(
    relationship: Mapping[str, object],
    keys: Iterable[str],
) -> list[str]:
    return [
        f"{key}={_compact_json(relationship.get(key))}"
        for key in keys
        if key in relationship
    ]


def _pedestrian_core_case_cap_markdown_lines(cameras: Iterable[object]) -> list[str]:
    rows: list[list[object]] = []
    for camera in cameras:
        if not isinstance(camera, Mapping):
            continue
        relationships = camera.get("pedestrian_core_case_cap_relationships")
        relationship_rows = relationships if isinstance(relationships, list) else []
        for relationship in relationship_rows:
            if not isinstance(relationship, Mapping):
                continue
            rows.append(
                [
                    camera.get("camera"),
                    (
                        f"{relationship.get('source_core_layer_id')}/"
                        f"{relationship.get('source_case_layer_id')}"
                    ),
                    _core_case_relationship_summary(
                        relationship,
                        PATH_PEDESTRIAN_CORE_CASE_SOURCE_KEYS,
                    ),
                    _core_case_relationship_summary(
                        relationship,
                        PATH_PEDESTRIAN_CORE_CASE_QGIS_KEYS,
                    ),
                    f"cap_limit_mm={_compact_json(relationship.get('cap_limit_mm'))}",
                ]
            )
    if not rows:
        return []
    lines = ["", "## Pedestrian core/case cap relationships", ""]
    lines.extend(
        [
            (
                "Compares pedestrian core and casing widths when source Mapbox widths "
                "approach or exceed qfit's QGIS line-width cap."
            ),
            (
                "The QGIS ratio-preserving core width is diagnostic only; it shows the "
                "core width that would preserve the current QGIS case/core ratio if the "
                "case stroke hit the cap."
            ),
            "",
            "| Camera | Source pair | Source relationship | QGIS relationship | Cap |",
            MARKDOWN_SEPARATOR_5_COLUMNS,
        ]
    )
    lines.extend(_markdown_table_row(row) for row in rows)
    return lines


def _label_detail_zoom_band(detail: Mapping[str, object]) -> str:
    minzoom = detail.get("min_zoom_level")
    maxzoom = detail.get("max_zoom_level")
    if isinstance(maxzoom, (int, float)) and not isinstance(maxzoom, bool) and maxzoom < 0:
        maxzoom = None
    if minzoom is None and maxzoom is None:
        return "all"
    if minzoom is None:
        return f"z<={maxzoom}"
    if maxzoom is None:
        return f"z>={minzoom}"
    return f"{minzoom}<=z<={maxzoom}"


def _label_controls(detail: Mapping[str, object]) -> list[str]:
    return [
        f"{key}={_compact_json(detail.get(key))}"
        for key in (
            "field_name",
            "placement",
            "priority",
            "repeat_distance",
            "label_per_part",
            "merge_lines",
            "text_size",
            "text_color",
            "buffer_size",
        )
        if key in detail
    ]


def _visible_label_detail_markdown_lines(cameras: Iterable[object]) -> list[str]:
    lines = ["", "## Visible QGIS label details", ""]
    detail_row_count = 0
    for camera in cameras:
        if not isinstance(camera, Mapping):
            continue
        details = camera.get("qgis_path_pedestrian_visible_label_details")
        detail_rows = details if isinstance(details, list) else []
        mapping_rows = [detail for detail in detail_rows if isinstance(detail, Mapping)]
        if not mapping_rows:
            continue
        detail_row_count += len(mapping_rows)
        lines.extend(
            [
                f"### {camera.get('camera')}",
                "",
                "| Style | Layer | Zoom band | Controls | Filter |",
                MARKDOWN_SEPARATOR_5_COLUMNS,
            ]
        )
        for detail in mapping_rows:
            lines.append(
                _markdown_table_row(
                    [
                        detail.get("style_name"),
                        detail.get("layer_name"),
                        _label_detail_zoom_band(detail),
                        _label_controls(detail),
                        detail.get("filter_expression"),
                    ]
                )
            )
        lines.append("")
    return lines if detail_row_count else []


def _visible_label_thinning_markdown_lines(cameras: Iterable[object]) -> list[str]:
    rows: list[list[object]] = []
    for camera in cameras:
        if not isinstance(camera, Mapping):
            continue
        for detail in _visible_label_detail_rows(camera):
            thinning_settings = detail.get("thinning_settings")
            if not isinstance(thinning_settings, Mapping):
                continue
            rows.append(
                [
                    camera.get("camera"),
                    detail.get("style_name"),
                    thinning_settings.get("allow_duplicate_removal"),
                    thinning_settings.get("minimum_distance_to_duplicate"),
                    thinning_settings.get("minimum_distance_to_duplicate_unit"),
                    thinning_settings.get("label_margin_distance"),
                    thinning_settings.get("label_margin_distance_unit"),
                    thinning_settings.get("limit_number_of_labels_enabled"),
                    thinning_settings.get("maximum_number_labels"),
                    thinning_settings.get("minimum_feature_size"),
                ]
            )
    if not rows:
        return []
    lines = ["", "## Visible QGIS label thinning details", ""]
    lines.extend(
        [
            "| Camera | Style | Duplicate removal | Duplicate distance | Duplicate distance unit | Label margin | Label margin unit | Limit labels | Max labels | Min feature size |",
            "| --- | --- | --- | ---: | --- | ---: | --- | --- | ---: | ---: |",
        ]
    )
    lines.extend(_markdown_table_row(row) for row in rows)
    return lines


def _artifact_path_cell(value: object) -> object:
    if isinstance(value, str) and value:
        return f"`{value}`"
    return value


def _visual_artifact_markdown_lines(cameras: Iterable[object]) -> list[str]:
    rows: list[list[object]] = []
    for camera in cameras:
        if not isinstance(camera, Mapping):
            continue
        artifacts = camera.get("visual_artifacts")
        if not isinstance(artifacts, Mapping):
            continue
        rows.append(
            [
                camera.get("camera"),
                artifacts.get("status"),
                artifacts.get("artifact_status"),
                artifacts.get("changed_pixel_ratio"),
                artifacts.get("normalized_mean_absolute_channel_delta"),
                artifacts.get("normalized_rms_channel_delta"),
                _artifact_path_cell(artifacts.get("browser_reference")),
                _artifact_path_cell(artifacts.get("qgis_vector_render")),
                _artifact_path_cell(artifacts.get("diff")),
                _artifact_path_cell(artifacts.get("contact_sheet")),
            ]
        )
    if not rows:
        return []
    lines = ["", "## Visual comparison artifacts", ""]
    lines.extend(
        [
            "| Camera | Status | Artifact status | Changed ratio | Mean delta | RMS delta | Mapbox GL | QGIS render | Diff | Contact sheet |",
            "| --- | --- | --- | ---: | ---: | ---: | --- | --- | --- | --- |",
        ]
    )
    lines.extend(_markdown_table_row(row) for row in rows)
    return lines


def _duplicate_category_summaries(diagnostic: Mapping[str, object]) -> list[str]:
    categories = diagnostic.get("duplicate_name_categories")
    category_rows = categories if isinstance(categories, list) else []
    summaries: list[str] = []
    for category_row in category_rows:
        if not isinstance(category_row, Mapping):
            continue
        category = str(category_row.get("category") or "")
        duplicates = _string_list(category_row.get("top_duplicates"))
        if category and duplicates:
            summaries.append(f"{category}: {', '.join(duplicates)}")
    return summaries


def _duplicate_label_diagnostic_markdown_lines(cameras: Iterable[object]) -> list[str]:
    rows: list[list[object]] = []
    for camera in cameras:
        if not isinstance(camera, Mapping):
            continue
        diagnostic = camera.get("duplicate_label_diagnostic")
        if not isinstance(diagnostic, Mapping):
            continue
        duplicate_summaries = _duplicate_category_summaries(diagnostic)
        if not duplicate_summaries:
            continue
        merge_line_styles = _string_list(diagnostic.get("visible_merge_line_label_styles"))
        repeat_distances = _string_list(diagnostic.get("visible_label_repeat_distances"))
        label_source_matches = _string_list(diagnostic.get("visible_label_source_category_matches"))
        unmatched_categories = _string_list(diagnostic.get("unmatched_duplicate_name_categories"))
        rows.append(
            [
                camera.get("camera"),
                duplicate_summaries,
                merge_line_styles,
                repeat_distances,
                label_source_matches,
                unmatched_categories,
            ]
        )
    if not rows:
        return []
    lines = ["", "## Duplicate label diagnostics", ""]
    lines.extend(
        [
            (
                "Connects duplicate source feature names with the visible QGIS "
                "line-label merge/repeat controls and source-label category matches "
                "active at each camera zoom."
            ),
            "",
            "| Camera | Duplicate feature names | Visible merge-line label styles | Visible label repeat distances | Visible source-label category matches | Unmatched duplicate categories |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    lines.extend(_markdown_table_row(row) for row in rows)
    return lines


def build_summary_markdown(report: Mapping[str, object]) -> str:
    cameras = report.get("cameras")
    rows = cameras if isinstance(cameras, list) else []
    source_style_input_count = report.get("source_style_input_count", 0)
    source_style_denominator = report.get("camera_count", 0) if source_style_input_count else 0
    lines = [
        "# Mapbox Outdoors path/pedestrian focus",
        "",
        f"Generated: {report.get('generated')}",
        f"Road feature generated: {report.get('road_feature_generated')}",
        f"Style: {report.get('style_owner')}/{report.get('style_id')}",
        f"Focused cameras: {report.get('camera_count')}",
        (
            "Source style cameras: "
            f"{report.get('source_style_camera_count', 0)}/{source_style_denominator} matched"
        ),
        (
            "QGIS preprocessed style cameras: "
            f"{report.get('qgis_style_camera_count')}/{report.get('qgis_style_input_count', 0)} matched"
        ),
        (
            "QGIS label style cameras: "
            f"{report.get('qgis_label_style_camera_count')}/{report.get('qgis_label_style_input_count', 0)} matched"
        ),
        "",
    ]
    input_lines = _input_artifact_markdown_lines(report)
    if input_lines:
        lines.extend(input_lines)
        lines.append("")
    lines.extend(
        [
            (
                "Cross-links decoded road-feature counts with the camera-specific QGIS-preprocessed "
                "style layers that can render path, pedestrian, trail, piste, or step features."
            ),
            (
                "Visible source and QGIS counts apply the camera zoom to style-layer minzoom/maxzoom, "
                "so zoom-banded preprocessing can be reviewed against the layers active in each camera."
            ),
            "",
        ]
    )
    if not rows:
        lines.append("No decoded cameras include path/pedestrian focus features.")
        return "\n".join(lines) + "\n"
    lines.extend(
        [
            "| Camera | Camera zoom | Tile zoom | Feature counts | Top pedestrian types | Top path types | Duplicate pedestrian labels | Duplicate path labels | Duplicate step labels | Top path signatures | Top step signatures | QGIS layers | QGIS labels | QGIS controls | Sample visible QGIS strokes | Sample visible QGIS colors | Sample visible QGIS layer ids | Visible QGIS label styles |",
            "| --- | ---: | ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for camera in rows:
        if not isinstance(camera, Mapping):
            continue
        feature_counts = [
            f"polygons={camera.get('pedestrian_path_polygon_count', 0)}",
            f"pedestrian_lines={camera.get('pedestrian_line_count', 0)}",
            f"path_lines={camera.get('path_line_count', 0)}",
            f"steps={camera.get('step_line_count', 0)}",
        ]
        qgis_layers = [
            f"status={camera.get('qgis_style_status')}",
            f"total={camera.get('qgis_path_pedestrian_layer_count', 0)}",
            f"visible={camera.get('qgis_path_pedestrian_visible_layer_count', 0)}",
            f"line={camera.get('qgis_path_pedestrian_line_layer_count', 0)}",
            f"fill={camera.get('qgis_path_pedestrian_fill_layer_count', 0)}",
        ]
        qgis_labels = [
            f"status={camera.get('qgis_label_style_status')}",
            f"total={camera.get('qgis_path_pedestrian_label_style_count', 0)}",
            f"visible={camera.get('qgis_path_pedestrian_visible_label_style_count', 0)}",
        ]
        lines.append(
            _markdown_table_row(
                [
                    camera.get("camera"),
                    camera.get("camera_zoom"),
                    camera.get("tile_zoom"),
                    feature_counts,
                    camera.get("top_pedestrian_line_types"),
                    camera.get("top_path_line_types"),
                    camera.get("top_pedestrian_line_duplicate_names"),
                    camera.get("top_path_line_duplicate_names"),
                    camera.get("top_step_line_duplicate_names"),
                    camera.get("top_path_signatures"),
                    camera.get("top_step_signatures"),
                    qgis_layers,
                    qgis_labels,
                    _qgis_control_summary(camera),
                    _qgis_stroke_samples(camera),
                    _qgis_color_samples(camera),
                    camera.get("qgis_path_pedestrian_visible_layer_ids"),
                    camera.get("qgis_path_pedestrian_visible_label_style_names"),
                ]
            )
        )
    lines.extend(_visual_artifact_markdown_lines(rows))
    lines.extend(_duplicate_label_diagnostic_markdown_lines(rows))
    lines.extend(_source_qgis_stroke_control_markdown_lines(rows))
    lines.extend(_largest_non_auxiliary_stroke_delta_markdown_lines(rows))
    lines.extend(_non_auxiliary_dash_mismatch_markdown_lines(rows))
    lines.extend(_qgis_auxiliary_stroke_markdown_lines(rows))
    lines.extend(_pedestrian_core_case_cap_markdown_lines(rows))
    lines.extend(_visible_source_detail_markdown_lines(rows))
    lines.extend(_visible_detail_markdown_lines(rows))
    lines.extend(_visible_label_thinning_markdown_lines(rows))
    lines.extend(_visible_label_detail_markdown_lines(rows))
    return "\n".join(lines) + "\n"


def _assert_report_output_paths(paths: PathPedestrianFocusPaths, *, trusted_output_root: Path) -> None:
    root = trusted_output_root.resolve()
    for output_path in (paths.json_path, paths.summary_path):
        if not output_path.parent.resolve().is_relative_to(root):
            raise ValueError(f"Path/pedestrian focus output must stay under {trusted_output_root}")


def write_report(
    report: Mapping[str, object],
    paths: PathPedestrianFocusPaths,
    *,
    trusted_output_root: Path | None = None,
) -> None:
    output_root = DEFAULT_OUTPUT_ROOT if trusted_output_root is None else trusted_output_root
    _assert_report_output_paths(paths, trusted_output_root=output_root)
    # Safe: report output paths are timestamped beneath the trusted debug report root.
    paths.run_dir.mkdir(parents=True, exist_ok=True)  # NOSONAR
    with paths.json_path.open("w", encoding="utf-8") as handle:  # NOSONAR
        json.dump(report, handle, indent=2, sort_keys=True)  # NOSONAR
        handle.write("\n")
    with paths.summary_path.open("w", encoding="utf-8") as handle:  # NOSONAR
        handle.write(build_summary_markdown(report))


def _parse_qgis_style_json(value: str) -> tuple[str, Path]:
    camera, separator, path = value.partition("=")
    if not separator or not camera or not path:
        raise argparse.ArgumentTypeError("Expected CAMERA=PATH")
    return camera, Path(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build an offline Mapbox Outdoors path/pedestrian focus report from road-feature "
            "diagnostics and camera-specific QGIS-preprocessed styles."
        )
    )
    parser.add_argument(
        "--road-features-json",
        type=Path,
        default=DEFAULT_ROAD_FEATURES_PATH,
        help="Path to all-camera road-features.json.",
    )
    parser.add_argument(
        "--source-style-json",
        type=Path,
        help="Source Mapbox style JSON used to capture browser reference artifacts.",
    )
    parser.add_argument(
        "--qgis-style-json",
        action="append",
        default=[],
        type=_parse_qgis_style_json,
        metavar="CAMERA=PATH",
        help="Camera-specific qgis-preprocessed-style.json. May be repeated.",
    )
    parser.add_argument(
        "--qgis-label-styles-json",
        action="append",
        default=[],
        type=_parse_qgis_style_json,
        metavar="CAMERA=PATH",
        help="Camera-specific qgis-label-styles.json. May be repeated.",
    )
    parser.add_argument(
        "--comparison-summary-json",
        action="append",
        default=[],
        type=Path,
        help=(
            "All-camera comparison summary.json whose manifests identify camera-specific "
            "qgis-preprocessed-style.json artifacts. May be repeated."
        ),
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
    raise AssertionError(ARGPARSE_EXIT_SENTINEL)


def _load_cli_json_list(parser: argparse.ArgumentParser, path: Path, *, label: str) -> list[object]:
    try:
        return load_json_list(path)
    except FileNotFoundError:
        parser.error(f"{label} not found: {path}")
    except json.JSONDecodeError as error:
        parser.error(f"{label} is not valid JSON: {path}: {error.msg}")
    except ValueError as error:
        parser.error(str(error))
    raise AssertionError(ARGPARSE_EXIT_SENTINEL)


def _comparison_inputs_from_cli_summary(
    parser: argparse.ArgumentParser,
    path: Path,
) -> tuple[dict[str, Path], dict[str, Path], dict[str, dict[str, object]]]:
    comparison_summary = _load_cli_json_object(parser, path, label="Comparison summary JSON")
    try:
        qgis_style_paths = qgis_style_paths_from_comparison_summary(comparison_summary, summary_path=path)
        qgis_label_style_paths = qgis_label_style_paths_from_comparison_summary(
            comparison_summary,
            summary_path=path,
        )
        visual_artifacts = comparison_visual_artifacts_from_summary(comparison_summary, summary_path=path)
        return qgis_style_paths, qgis_label_style_paths, visual_artifacts
    except FileNotFoundError as error:
        parser.error(f"Comparison manifest not found: {error.filename}")
    except json.JSONDecodeError as error:
        parser.error(f"Comparison manifest is not valid JSON: {error.msg}")
    except ValueError as error:
        parser.error(str(error))
    raise AssertionError(ARGPARSE_EXIT_SENTINEL)


def _display_input_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT))
    except ValueError:
        return str(resolved)


def _display_visual_artifacts_by_camera(
    visual_artifacts_by_camera: Mapping[str, Mapping[str, object]],
) -> dict[str, dict[str, object]]:
    displayed: dict[str, dict[str, object]] = {}
    for camera, artifacts in visual_artifacts_by_camera.items():
        displayed[camera] = {
            str(key): _display_input_path(value) if isinstance(value, Path) else value
            for key, value in artifacts.items()
        }
    return displayed


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    road_feature_report = _load_cli_json_object(
        parser,
        args.road_features_json,
        label="Road features JSON",
    )
    source_style = (
        _load_cli_json_object(parser, args.source_style_json, label="Source style JSON")
        if args.source_style_json is not None
        else None
    )
    qgis_style_paths_by_camera: dict[str, Path] = {}
    qgis_label_style_paths_by_camera: dict[str, Path] = {}
    visual_artifacts_by_camera: dict[str, dict[str, object]] = {}
    for comparison_summary_path in args.comparison_summary_json:
        qgis_style_paths, qgis_label_style_paths, visual_artifacts = _comparison_inputs_from_cli_summary(
            parser,
            comparison_summary_path,
        )
        qgis_style_paths_by_camera.update(qgis_style_paths)
        qgis_label_style_paths_by_camera.update(qgis_label_style_paths)
        visual_artifacts_by_camera.update(visual_artifacts)
    qgis_style_paths_by_camera.update(dict(args.qgis_style_json))
    qgis_label_style_paths_by_camera.update(dict(args.qgis_label_styles_json))
    qgis_styles_by_camera = {
        camera: _load_cli_json_object(parser, path, label=f"QGIS style JSON for {camera}")
        for camera, path in qgis_style_paths_by_camera.items()
    }
    qgis_label_styles_by_camera = {
        camera: _load_cli_json_list(parser, path, label=f"QGIS label styles JSON for {camera}")
        for camera, path in qgis_label_style_paths_by_camera.items()
    }
    report = build_path_pedestrian_focus_report(
        road_feature_report,
        source_style=source_style,
        qgis_styles_by_camera=qgis_styles_by_camera,
        qgis_label_styles_by_camera=qgis_label_styles_by_camera,
        visual_artifacts_by_camera=_display_visual_artifacts_by_camera(visual_artifacts_by_camera),
        input_artifacts={
            "road_features_json": _display_input_path(args.road_features_json),
            "source_style_json": (
                _display_input_path(args.source_style_json)
                if args.source_style_json is not None
                else None
            ),
            "comparison_summary_jsons": [_display_input_path(path) for path in args.comparison_summary_json],
            "qgis_style_cameras": sorted(qgis_style_paths_by_camera),
            "qgis_label_style_cameras": sorted(qgis_label_style_paths_by_camera),
        },
    )
    paths = build_path_pedestrian_focus_paths(build_run_directory())
    write_report(report, paths)
    print(paths.summary_path)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised manually
    raise SystemExit(main())
