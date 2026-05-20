from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PARENT = REPO_ROOT.parent
package_parent = str(PACKAGE_PARENT)
if package_parent not in sys.path:
    sys.path.insert(0, package_parent)

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
    "road-path",
    "road-pedestrian",
    "road-steps",
)
PATH_PEDESTRIAN_STYLE_TYPES = {"fill", "line"}
PATH_PEDESTRIAN_DETAIL_PAINT_KEYS = (
    "line-width",
    "line-color",
    "line-dasharray",
    "line-opacity",
    "fill-color",
    "fill-opacity",
)
TOP_COUNT_LIMIT = 3
SAMPLE_LAYER_LIMIT = 8


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


def _resolve_trusted_debug_path(raw_path: str, *, base_dir: Path, trusted_root: Path) -> Path:
    path = Path(raw_path)
    candidate = path if path.is_absolute() else base_dir / path
    resolved = candidate.resolve()
    if not resolved.is_relative_to(trusted_root.resolve()):
        raise ValueError(f"Comparison artifact path must stay under {trusted_root}: {resolved}")
    return resolved


def _trusted_root_for_comparison_summary(summary_path: Path) -> Path:
    resolved = summary_path.resolve()
    for parent in resolved.parents:
        if parent.name == "all-cameras":
            return parent.parent
    return DEFAULT_COMPARISON_OUTPUT_ROOT


def qgis_style_paths_from_comparison_summary(
    comparison_summary: Mapping[str, object],
    *,
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
        style_value = outputs.get("qgis_preprocessed_style")
        if not isinstance(style_value, str):
            continue
        paths[camera_name] = _resolve_trusted_debug_path(
            style_value,
            base_dir=manifest_path.parent,
            trusted_root=root,
        )
    return paths


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


def qgis_path_pedestrian_style_summary(
    style: Mapping[str, object],
    *,
    camera_zoom: float | None = None,
) -> dict[str, object]:
    layers = [layer for layer in _style_layers(style) if _is_path_pedestrian_style_layer(layer)]
    line_layers = [layer for layer in layers if layer.get("type") == "line"]
    fill_layers = [layer for layer in layers if layer.get("type") == "fill"]
    visible_layers = [layer for layer in layers if _layer_is_visible_at_zoom(layer, camera_zoom)]
    visible_line_layers = [layer for layer in visible_layers if layer.get("type") == "line"]
    visible_fill_layers = [layer for layer in visible_layers if layer.get("type") == "fill"]
    return {
        "qgis_style_status": "available",
        "qgis_path_pedestrian_layer_count": len(layers),
        "qgis_path_pedestrian_line_layer_count": len(line_layers),
        "qgis_path_pedestrian_fill_layer_count": len(fill_layers),
        "qgis_path_pedestrian_filter_layer_count": sum(1 for layer in layers if layer.get("filter") is not None),
        "qgis_path_pedestrian_line_width_layer_count": _style_control_count(line_layers, "line-width"),
        "qgis_path_pedestrian_line_color_layer_count": _style_control_count(line_layers, "line-color"),
        "qgis_path_pedestrian_fill_color_layer_count": _style_control_count(fill_layers, "fill-color"),
        "qgis_path_pedestrian_line_dasharray_layer_count": _style_control_count(line_layers, "line-dasharray"),
        "qgis_path_pedestrian_line_opacity_layer_count": _style_control_count(line_layers, "line-opacity"),
        "qgis_path_pedestrian_visible_layer_count": len(visible_layers),
        "qgis_path_pedestrian_visible_line_layer_count": len(visible_line_layers),
        "qgis_path_pedestrian_visible_fill_layer_count": len(visible_fill_layers),
        "qgis_path_pedestrian_visible_filter_layer_count": sum(
            1 for layer in visible_layers if layer.get("filter") is not None
        ),
        "qgis_path_pedestrian_visible_line_width_layer_count": _style_control_count(
            visible_line_layers,
            "line-width",
        ),
        "qgis_path_pedestrian_visible_line_color_layer_count": _style_control_count(
            visible_line_layers,
            "line-color",
        ),
        "qgis_path_pedestrian_visible_fill_color_layer_count": _style_control_count(
            visible_fill_layers,
            "fill-color",
        ),
        "qgis_path_pedestrian_visible_line_dasharray_layer_count": _style_control_count(
            visible_line_layers,
            "line-dasharray",
        ),
        "qgis_path_pedestrian_visible_line_opacity_layer_count": _style_control_count(
            visible_line_layers,
            "line-opacity",
        ),
        "qgis_path_pedestrian_layer_ids": [str(layer.get("id") or "") for layer in layers],
        "qgis_path_pedestrian_visible_layer_ids": [str(layer.get("id") or "") for layer in visible_layers],
        "qgis_path_pedestrian_layer_details": [_layer_detail(layer) for layer in layers],
        "qgis_path_pedestrian_visible_layer_details": [_layer_detail(layer) for layer in visible_layers],
        "qgis_path_pedestrian_line_width_samples": _layer_control_sample(line_layers, "line-width"),
        "qgis_path_pedestrian_line_color_samples": _layer_control_sample(line_layers, "line-color"),
        "qgis_path_pedestrian_fill_color_samples": _layer_control_sample(fill_layers, "fill-color"),
        "qgis_path_pedestrian_line_dasharray_samples": _layer_control_sample(line_layers, "line-dasharray"),
        "qgis_path_pedestrian_visible_line_width_samples": _layer_control_sample(
            visible_line_layers,
            "line-width",
        ),
        "qgis_path_pedestrian_visible_line_color_samples": _layer_control_sample(
            visible_line_layers,
            "line-color",
        ),
        "qgis_path_pedestrian_visible_fill_color_samples": _layer_control_sample(
            visible_fill_layers,
            "fill-color",
        ),
        "qgis_path_pedestrian_visible_line_dasharray_samples": _layer_control_sample(
            visible_line_layers,
            "line-dasharray",
        ),
    }


def _missing_qgis_style_summary() -> dict[str, object]:
    return {
        "qgis_style_status": "missing",
        "qgis_path_pedestrian_layer_count": 0,
        "qgis_path_pedestrian_line_layer_count": 0,
        "qgis_path_pedestrian_fill_layer_count": 0,
        "qgis_path_pedestrian_filter_layer_count": 0,
        "qgis_path_pedestrian_line_width_layer_count": 0,
        "qgis_path_pedestrian_line_color_layer_count": 0,
        "qgis_path_pedestrian_fill_color_layer_count": 0,
        "qgis_path_pedestrian_line_dasharray_layer_count": 0,
        "qgis_path_pedestrian_line_opacity_layer_count": 0,
        "qgis_path_pedestrian_visible_layer_count": 0,
        "qgis_path_pedestrian_visible_line_layer_count": 0,
        "qgis_path_pedestrian_visible_fill_layer_count": 0,
        "qgis_path_pedestrian_visible_filter_layer_count": 0,
        "qgis_path_pedestrian_visible_line_width_layer_count": 0,
        "qgis_path_pedestrian_visible_line_color_layer_count": 0,
        "qgis_path_pedestrian_visible_fill_color_layer_count": 0,
        "qgis_path_pedestrian_visible_line_dasharray_layer_count": 0,
        "qgis_path_pedestrian_visible_line_opacity_layer_count": 0,
        "qgis_path_pedestrian_layer_ids": [],
        "qgis_path_pedestrian_visible_layer_ids": [],
        "qgis_path_pedestrian_layer_details": [],
        "qgis_path_pedestrian_visible_layer_details": [],
        "qgis_path_pedestrian_line_width_samples": [],
        "qgis_path_pedestrian_line_color_samples": [],
        "qgis_path_pedestrian_fill_color_samples": [],
        "qgis_path_pedestrian_line_dasharray_samples": [],
        "qgis_path_pedestrian_visible_line_width_samples": [],
        "qgis_path_pedestrian_visible_line_color_samples": [],
        "qgis_path_pedestrian_visible_fill_color_samples": [],
        "qgis_path_pedestrian_visible_line_dasharray_samples": [],
    }


def _camera_focus_row(
    camera_report: Mapping[str, object],
    *,
    qgis_style: Mapping[str, object] | None,
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
        "top_pedestrian_line_types": _top_count_labels(camera_report.get("pedestrian_line_type_counts")),
        "top_path_line_types": _top_count_labels(camera_report.get("path_line_type_counts")),
        "top_step_structures": _top_count_labels(camera_report.get("step_line_structure_counts")),
        "top_path_signatures": _top_count_labels(camera_report.get("path_line_signature_counts")),
        "top_step_signatures": _top_count_labels(camera_report.get("step_line_signature_counts")),
    }
    camera_zoom = _numeric_zoom(camera_report.get("camera_zoom"))
    row.update(
        qgis_path_pedestrian_style_summary(qgis_style, camera_zoom=camera_zoom)
        if qgis_style is not None
        else _missing_qgis_style_summary()
    )
    return row


def build_path_pedestrian_focus_report(
    road_feature_report: Mapping[str, object],
    *,
    qgis_styles_by_camera: Mapping[str, Mapping[str, object]] | None = None,
    generated_at: dt.datetime | None = None,
) -> dict[str, object]:
    qgis_styles = qgis_styles_by_camera or {}
    camera_reports = road_feature_report.get("cameras")
    source_rows = camera_reports if isinstance(camera_reports, list) else []
    rows = []
    for camera_report in source_rows:
        if not isinstance(camera_report, Mapping):
            continue
        if camera_report.get("status") != "decoded" or not _has_path_pedestrian_focus(camera_report):
            continue
        camera_name = str(camera_report.get("camera") or "")
        rows.append(
            _camera_focus_row(
                camera_report,
                qgis_style=qgis_styles.get(camera_name),
            )
        )
    generated = generated_at or dt.datetime.now(dt.timezone.utc)
    qgis_matched_camera_count = sum(1 for row in rows if row.get("qgis_style_status") == "available")
    return {
        "generated": generated.astimezone(dt.timezone.utc).isoformat(),
        "road_feature_generated": road_feature_report.get("generated"),
        "style_owner": road_feature_report.get("style_owner"),
        "style_id": road_feature_report.get("style_id"),
        "camera_count": len(rows),
        "qgis_style_camera_count": qgis_matched_camera_count,
        "qgis_style_input_count": len(qgis_styles),
        "cameras": rows,
    }


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


def build_summary_markdown(report: Mapping[str, object]) -> str:
    cameras = report.get("cameras")
    rows = cameras if isinstance(cameras, list) else []
    lines = [
        "# Mapbox Outdoors path/pedestrian focus",
        "",
        f"Generated: {report.get('generated')}",
        f"Road feature generated: {report.get('road_feature_generated')}",
        f"Style: {report.get('style_owner')}/{report.get('style_id')}",
        f"Focused cameras: {report.get('camera_count')}",
        (
            "QGIS preprocessed style cameras: "
            f"{report.get('qgis_style_camera_count')}/{report.get('qgis_style_input_count', 0)} matched"
        ),
        "",
        (
            "Cross-links decoded road-feature counts with the camera-specific QGIS-preprocessed "
            "style layers that can render path, pedestrian, trail, piste, or step features."
        ),
        (
            "Visible QGIS counts apply the camera zoom to style-layer minzoom/maxzoom, "
            "so zoom-banded preprocessing can be reviewed against the layers active in each camera."
        ),
        "",
    ]
    if not rows:
        lines.append("No decoded cameras include path/pedestrian focus features.")
        return "\n".join(lines) + "\n"
    lines.extend(
        [
            "| Camera | Camera zoom | Tile zoom | Feature counts | Top pedestrian types | Top path types | Top path signatures | Top step signatures | QGIS layers | QGIS controls | Sample visible QGIS strokes | Sample visible QGIS colors | Sample visible QGIS layer ids |",
            "| --- | ---: | ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
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
        lines.append(
            _markdown_table_row(
                [
                    camera.get("camera"),
                    camera.get("camera_zoom"),
                    camera.get("tile_zoom"),
                    feature_counts,
                    camera.get("top_pedestrian_line_types"),
                    camera.get("top_path_line_types"),
                    camera.get("top_path_signatures"),
                    camera.get("top_step_signatures"),
                    qgis_layers,
                    _qgis_control_summary(camera),
                    _qgis_stroke_samples(camera),
                    _qgis_color_samples(camera),
                    camera.get("qgis_path_pedestrian_visible_layer_ids"),
                ]
            )
        )
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
        "--qgis-style-json",
        action="append",
        default=[],
        type=_parse_qgis_style_json,
        metavar="CAMERA=PATH",
        help="Camera-specific qgis-preprocessed-style.json. May be repeated.",
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
    raise AssertionError("argparse error should exit")


def _qgis_style_paths_from_cli_comparison_summary(
    parser: argparse.ArgumentParser,
    path: Path,
) -> dict[str, Path]:
    comparison_summary = _load_cli_json_object(parser, path, label="Comparison summary JSON")
    try:
        return qgis_style_paths_from_comparison_summary(comparison_summary, summary_path=path)
    except FileNotFoundError as error:
        parser.error(f"Comparison manifest not found: {error.filename}")
    except json.JSONDecodeError as error:
        parser.error(f"Comparison manifest is not valid JSON: {error.msg}")
    except ValueError as error:
        parser.error(str(error))
    raise AssertionError("argparse error should exit")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    road_feature_report = _load_cli_json_object(
        parser,
        args.road_features_json,
        label="Road features JSON",
    )
    qgis_style_paths_by_camera: dict[str, Path] = {}
    for comparison_summary_path in args.comparison_summary_json:
        qgis_style_paths_by_camera.update(
            _qgis_style_paths_from_cli_comparison_summary(parser, comparison_summary_path)
        )
    qgis_style_paths_by_camera.update(dict(args.qgis_style_json))
    qgis_styles_by_camera = {
        camera: _load_cli_json_object(parser, path, label=f"QGIS style JSON for {camera}")
        for camera, path in qgis_style_paths_by_camera.items()
    }
    report = build_path_pedestrian_focus_report(
        road_feature_report,
        qgis_styles_by_camera=qgis_styles_by_camera,
    )
    paths = build_path_pedestrian_focus_paths(build_run_directory())
    write_report(report, paths)
    print(paths.summary_path)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised manually
    raise SystemExit(main())
