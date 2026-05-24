from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

try:
    from .mapbox_outdoors_comparison import (
        DEFAULT_OUTPUT_ROOT as COMPARISON_DEFAULT_OUTPUT_ROOT,
        ImageMetrics,
        build_image_diff,
        load_style_definition,
        redact_sensitive_text,
        resolve_mapbox_token,
    )
    from .mapbox_outdoors_rendered_layer_mask import (
        RenderedLayerMaskContext as StyleAdjustmentProbeContext,
        RenderedLayerMaskPaths as StyleAdjustmentProbePaths,
        _extend_crop_movement_lines,
        _format_number,
        _list_of_mappings,
        _manifest_output_path,
        _mapping_value,
        _repo_relative,
        _utc_timestamp,
        camera_from_manifest,
        image_changed_bbox,
        image_delta_metrics,
        load_json_object,
        metric_delta,
        parse_crop_box,
        render_qgis_vector_in_subprocess,
        safe_path_segment,
    )
except ImportError:  # pragma: no cover - direct script execution
    from mapbox_outdoors_comparison import (  # type: ignore[no-redef]
        DEFAULT_OUTPUT_ROOT as COMPARISON_DEFAULT_OUTPUT_ROOT,
        ImageMetrics,
        build_image_diff,
        load_style_definition,
        redact_sensitive_text,
        resolve_mapbox_token,
    )
    from mapbox_outdoors_rendered_layer_mask import (  # type: ignore[no-redef]
        RenderedLayerMaskContext as StyleAdjustmentProbeContext,
        RenderedLayerMaskPaths as StyleAdjustmentProbePaths,
        _extend_crop_movement_lines,
        _format_number,
        _list_of_mappings,
        _manifest_output_path,
        _mapping_value,
        _repo_relative,
        _utc_timestamp,
        camera_from_manifest,
        image_changed_bbox,
        image_delta_metrics,
        load_json_object,
        metric_delta,
        parse_crop_box,
        render_qgis_vector_in_subprocess,
        safe_path_segment,
    )


DEFAULT_OUTPUT_ROOT = COMPARISON_DEFAULT_OUTPUT_ROOT.parent / "mapbox-outdoors-style-adjustment-probe"
REPORT_CAMERA_DIRECTORY = "comparison-camera"
STYLE_ADJUSTMENT_OUTPUT = "qgis-adjusted-style.json"
QGIS_RENDER_OUTPUT = "qgis-vector-render.png"
MAPBOX_DIFF_OUTPUT = "mapbox-gl-vs-qgis-diff.png"
QGIS_MOVEMENT_DIFF_OUTPUT = "qgis-vs-baseline-diff.png"
CONTROL_MOVEMENT_DIFF_OUTPUT = "qgis-vs-rerender-control-diff.png"
REPORT_JSON_OUTPUT = "style-adjustment-probe.json"
SUMMARY_OUTPUT = "summary.md"
AGGREGATE_METRIC_KEYS = (
    "normalized_mean_absolute_channel_delta",
    "normalized_rms_channel_delta",
)
METRIC_KEYS = (
    "changed_pixel_ratio",
    "normalized_mean_absolute_channel_delta",
    "normalized_rms_channel_delta",
)
CROP_METRIC_KEYS = ("mean_absolute_channel_delta", "rms_channel_delta", "mean_luminance_delta")


@dataclass(frozen=True)
class StyleAdjustment:
    layer_id: str
    paint: Mapping[str, object] = dataclasses.field(default_factory=dict)
    layout: Mapping[str, object] = dataclasses.field(default_factory=dict)
    minzoom: float | None = None
    maxzoom: float | None = None
    filter: object | None = None


@dataclass(frozen=True)
class StyleAdjustmentVariant:
    name: str
    adjustments: tuple[StyleAdjustment, ...]


@dataclass(frozen=True)
class StyleAdjustmentProbeConfig:
    baseline_manifest: Path
    output_root: Path
    variants: tuple[StyleAdjustmentVariant, ...]
    token: str
    crop_boxes: tuple[tuple[int, int, int, int], ...] = ()
    include_rerender_control: bool = True
    now: dt.datetime | None = None


def _require_mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object.")
    return value


def _optional_mapping(value: object, *, label: str) -> Mapping[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object when provided.")
    return value


def _optional_zoom(value: object, *, label: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric when provided.")
    return float(value)


def _style_adjustment_from_json(value: object, *, variant_name: str) -> StyleAdjustment:
    adjustment = _require_mapping(value, label=f"variant {variant_name} adjustment")
    layer_id = adjustment.get("layer_id")
    if not isinstance(layer_id, str) or not layer_id:
        raise ValueError(f"Variant {variant_name} adjustment must include layer_id.")
    paint = _optional_mapping(adjustment.get("paint"), label=f"variant {variant_name} paint")
    layout = _optional_mapping(adjustment.get("layout"), label=f"variant {variant_name} layout")
    minzoom = _optional_zoom(adjustment.get("minzoom"), label=f"variant {variant_name} minzoom")
    maxzoom = _optional_zoom(adjustment.get("maxzoom"), label=f"variant {variant_name} maxzoom")
    filter_expression = adjustment.get("filter") if "filter" in adjustment else None
    if not paint and not layout and minzoom is None and maxzoom is None and filter_expression is None:
        raise ValueError(f"Variant {variant_name} adjustment for {layer_id} does not change anything.")
    return StyleAdjustment(
        layer_id=layer_id,
        paint=dict(paint),
        layout=dict(layout),
        minzoom=minzoom,
        maxzoom=maxzoom,
        filter=filter_expression,
    )


def _style_adjustment_variant_from_json(value: object) -> StyleAdjustmentVariant:
    variant = _require_mapping(value, label="variant")
    name = variant.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("Each variant must include a name.")
    safe_path_segment(name)
    adjustments_value = variant.get("adjustments")
    if not isinstance(adjustments_value, list) or not adjustments_value:
        raise ValueError(f"Variant {name} must include at least one adjustment.")
    return StyleAdjustmentVariant(
        name=name,
        adjustments=tuple(
            _style_adjustment_from_json(adjustment, variant_name=name)
            for adjustment in adjustments_value
        ),
    )


def load_style_adjustment_variants(path: Path) -> tuple[StyleAdjustmentVariant, ...]:
    payload = load_json_object(path)
    variants_value = payload.get("variants")
    if not isinstance(variants_value, list) or not variants_value:
        raise ValueError("Style adjustment plan must include a non-empty variants array.")
    variants = tuple(_style_adjustment_variant_from_json(variant) for variant in variants_value)
    names = [variant.name for variant in variants]
    if len(names) != len(set(names)):
        raise ValueError("Style adjustment variant names must be unique.")
    return variants


def build_style_adjustment_probe_paths(
    *,
    output_root: Path,
    camera_name: str,
    now: dt.datetime | None = None,
) -> StyleAdjustmentProbePaths:
    run_dir = output_root / safe_path_segment(camera_name) / _utc_timestamp(now)
    return StyleAdjustmentProbePaths(
        run_dir=run_dir,
        summary_json=run_dir / REPORT_JSON_OUTPUT,
        summary_md=run_dir / SUMMARY_OUTPUT,
    )


def _ensure_layer_mapping(layer: object) -> dict[str, object] | None:
    return layer if isinstance(layer, dict) else None


def _update_mapping_property(
    layer: dict[str, object],
    key: str,
    updates: Mapping[str, object],
) -> None:
    existing = layer.get(key)
    target = existing if isinstance(existing, dict) else {}
    target.update(dict(updates))
    layer[key] = target


def _apply_adjustment_to_layer(layer: dict[str, object], adjustment: StyleAdjustment) -> None:
    if adjustment.paint:
        _update_mapping_property(layer, "paint", adjustment.paint)
    if adjustment.layout:
        _update_mapping_property(layer, "layout", adjustment.layout)
    if adjustment.minzoom is not None:
        layer["minzoom"] = adjustment.minzoom
    if adjustment.maxzoom is not None:
        layer["maxzoom"] = adjustment.maxzoom
    if adjustment.filter is not None:
        layer["filter"] = adjustment.filter


def _matching_layers(layers: Sequence[object], layer_id: str) -> list[dict[str, object]]:
    return [
        layer
        for layer_value in layers
        if (layer := _ensure_layer_mapping(layer_value)) is not None and layer.get("id") == layer_id
    ]


def apply_style_adjustments(
    style: Mapping[str, object],
    *,
    adjustments: Sequence[StyleAdjustment],
) -> tuple[dict[str, object], list[str], list[str]]:
    adjusted = deepcopy(dict(style))
    layers = adjusted.get("layers")
    if not isinstance(layers, list):
        raise ValueError("Mapbox style JSON must include a layers array.")
    matched_ids: list[str] = []
    missing_ids: list[str] = []
    for adjustment in adjustments:
        matches = _matching_layers(layers, adjustment.layer_id)
        if matches:
            for layer in matches:
                _apply_adjustment_to_layer(layer, adjustment)
            matched_ids.append(adjustment.layer_id)
        else:
            missing_ids.append(adjustment.layer_id)
    return adjusted, matched_ids, missing_ids


def _write_variant_style(path: Path, style: Mapping[str, object], *, token: str) -> None:
    path.write_text(redact_sensitive_text(json.dumps(style, indent=2), token) + "\n", encoding="utf-8")


def _variant_directory(run_dir: Path, variant_name: str) -> Path:
    return run_dir / safe_path_segment(variant_name)


def _variant_target_layer_ids(variant: StyleAdjustmentVariant) -> list[str]:
    return [adjustment.layer_id for adjustment in variant.adjustments]


def _variant_artifact_paths(variant_dir: Path) -> dict[str, Path]:
    return {
        "style": variant_dir / STYLE_ADJUSTMENT_OUTPUT,
        "qgis": variant_dir / QGIS_RENDER_OUTPUT,
        "mapbox_diff": variant_dir / MAPBOX_DIFF_OUTPUT,
        "movement_diff": variant_dir / QGIS_MOVEMENT_DIFF_OUTPUT,
        "control_movement_diff": variant_dir / CONTROL_MOVEMENT_DIFF_OUTPUT,
    }


def _render_adjusted_style(
    *,
    context: StyleAdjustmentProbeContext,
    style: Mapping[str, object],
    style_path: Path,
    qgis_path: Path,
) -> None:
    _write_variant_style(style_path, style, token=context.token)
    context.qgis_renderer(
        camera=context.camera,
        token=context.token,
        output_path=qgis_path,
        style_definition=style,
        qgis_preprocessed_style_path=None,
        qgis_label_styles_path=None,
    )


def _variant_image_metrics(
    *,
    context: StyleAdjustmentProbeContext,
    qgis_path: Path,
    mapbox_diff_path: Path,
    movement_diff_path: Path,
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    mapbox_metrics = context.diff_builder(
        reference_path=context.browser_reference_path,
        candidate_path=qgis_path,
        output_path=mapbox_diff_path,
    ) or {}
    qgis_movement_metrics = context.diff_builder(
        reference_path=context.baseline_qgis_path,
        candidate_path=qgis_path,
        output_path=movement_diff_path,
    ) or {}
    return mapbox_metrics, qgis_movement_metrics


def _variant_crop_metrics(
    *,
    context: StyleAdjustmentProbeContext,
    qgis_path: Path,
) -> list[Mapping[str, object]]:
    return [
        image_delta_metrics(
            reference_path=context.browser_reference_path,
            candidate_path=qgis_path,
            crop_box=crop_box,
        )
        for crop_box in context.crop_boxes
    ]


def _render_variant_report(
    *,
    variant: StyleAdjustmentVariant,
    context: StyleAdjustmentProbeContext,
    control_qgis_path: Path | None = None,
    control_metrics: Mapping[str, object] | None = None,
    control_crop_metrics: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    variant_dir = _variant_directory(context.run_dir, variant.name)
    variant_dir.mkdir(parents=True, exist_ok=True)
    paths = _variant_artifact_paths(variant_dir)
    variant_style, matched_ids, missing_ids = apply_style_adjustments(
        context.base_style,
        adjustments=variant.adjustments,
    )
    _render_adjusted_style(
        context=context,
        style=variant_style,
        style_path=paths["style"],
        qgis_path=paths["qgis"],
    )
    metrics, qgis_movement_metrics = _variant_image_metrics(
        context=context,
        qgis_path=paths["qgis"],
        mapbox_diff_path=paths["mapbox_diff"],
        movement_diff_path=paths["movement_diff"],
    )
    crop_metrics = _variant_crop_metrics(context=context, qgis_path=paths["qgis"])
    report: dict[str, object] = {
        "name": variant.name,
        "target_layer_ids": _variant_target_layer_ids(variant),
        "matched_layer_ids": matched_ids,
        "missing_layer_ids": missing_ids,
        "adjustments": [dataclasses.asdict(adjustment) for adjustment in variant.adjustments],
        "directory": _repo_relative(variant_dir),
        "style_json": _repo_relative(paths["style"]),
        "qgis_vector_render": _repo_relative(paths["qgis"]),
        "mapbox_diff": _repo_relative(paths["mapbox_diff"]),
        "qgis_movement_diff": _repo_relative(paths["movement_diff"]),
        "metrics": metrics,
        "metric_delta_vs_baseline": metric_delta(
            metrics,
            context.baseline_metrics,
            keys=METRIC_KEYS,
        ),
        "qgis_movement_metrics": qgis_movement_metrics,
        "diff_bbox_vs_baseline_qgis": image_changed_bbox(
            reference_path=context.baseline_qgis_path,
            candidate_path=paths["qgis"],
        ),
        "crop_metrics": crop_metrics,
        "crop_delta_vs_baseline": [
            metric_delta(crop_metrics[index], context.baseline_crop_metrics[index], keys=CROP_METRIC_KEYS)
            for index in range(len(crop_metrics))
        ],
    }
    if control_qgis_path is not None and control_metrics is not None:
        report["metric_delta_vs_rerender_control"] = metric_delta(metrics, control_metrics, keys=METRIC_KEYS)
        report["qgis_movement_vs_rerender_control_metrics"] = context.diff_builder(
            reference_path=control_qgis_path,
            candidate_path=paths["qgis"],
            output_path=paths["control_movement_diff"],
        ) or {}
        report["qgis_movement_vs_rerender_control_diff"] = _repo_relative(paths["control_movement_diff"])
        report["diff_bbox_vs_rerender_control_qgis"] = image_changed_bbox(
            reference_path=control_qgis_path,
            candidate_path=paths["qgis"],
        )
        report["crop_delta_vs_rerender_control"] = [
            metric_delta(crop_metrics[index], control_crop_metrics[index], keys=CROP_METRIC_KEYS)
            for index in range(len(crop_metrics))
        ]
    return report


def _rerender_control_variant() -> StyleAdjustmentVariant:
    return StyleAdjustmentVariant("qgis-rerender-control", ())


def _build_probe_context(
    *,
    config: StyleAdjustmentProbeConfig,
    manifest_path: Path,
    manifest: Mapping[str, object],
    paths: StyleAdjustmentProbePaths,
    qgis_renderer: Callable[..., None],
    diff_builder: Callable[..., ImageMetrics | None],
) -> StyleAdjustmentProbeContext:
    camera = camera_from_manifest(manifest)
    browser_reference_path = _manifest_output_path(
        manifest,
        manifest_path=manifest_path,
        key="browser_reference",
    )
    baseline_qgis_path = _manifest_output_path(
        manifest,
        manifest_path=manifest_path,
        key="qgis_vector_render",
    )
    qgis_style_path = _manifest_output_path(
        manifest,
        manifest_path=manifest_path,
        key="qgis_preprocessed_style",
    )
    base_style = load_style_definition(qgis_style_path)
    baseline_metrics = manifest.get("metrics") if isinstance(manifest.get("metrics"), Mapping) else {}
    baseline_crop_metrics = [
        image_delta_metrics(
            reference_path=browser_reference_path,
            candidate_path=baseline_qgis_path,
            crop_box=crop_box,
        )
        for crop_box in config.crop_boxes
    ]
    return StyleAdjustmentProbeContext(
        run_dir=paths.run_dir,
        camera=camera,
        token=config.token,
        base_style=base_style,
        browser_reference_path=browser_reference_path,
        baseline_qgis_path=baseline_qgis_path,
        baseline_metrics=baseline_metrics,
        baseline_crop_metrics=baseline_crop_metrics,
        crop_boxes=config.crop_boxes,
        qgis_renderer=qgis_renderer,
        diff_builder=diff_builder,
    )


def build_style_adjustment_probe_report(
    config: StyleAdjustmentProbeConfig,
    *,
    qgis_renderer: Callable[..., None] = render_qgis_vector_in_subprocess,
    diff_builder: Callable[..., ImageMetrics | None] = build_image_diff,
) -> dict[str, object]:
    manifest_path = config.baseline_manifest.expanduser().resolve()
    manifest = load_json_object(manifest_path)
    camera = camera_from_manifest(manifest)
    paths = build_style_adjustment_probe_paths(
        output_root=config.output_root.expanduser().resolve(),
        camera_name=REPORT_CAMERA_DIRECTORY,
        now=config.now,
    )
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    context = _build_probe_context(
        config=config,
        manifest_path=manifest_path,
        manifest=manifest,
        paths=paths,
        qgis_renderer=qgis_renderer,
        diff_builder=diff_builder,
    )
    variant_inputs: list[StyleAdjustmentVariant] = []
    control_name = "qgis-rerender-control"
    if config.include_rerender_control:
        if any(variant.name == control_name for variant in config.variants):
            raise ValueError(f"Variant name '{control_name}' is reserved for the rerender control.")
        variant_inputs.append(_rerender_control_variant())
    variant_inputs.extend(config.variants)

    variants: list[dict[str, object]] = []
    control_qgis_path: Path | None = None
    control_metrics: Mapping[str, object] | None = None
    control_crop_metrics: Sequence[Mapping[str, object]] = ()
    for variant in variant_inputs:
        variant_report = _render_variant_report(
            variant=variant,
            context=context,
            control_qgis_path=control_qgis_path,
            control_metrics=control_metrics,
            control_crop_metrics=control_crop_metrics,
        )
        if variant.name == control_name:
            variant_report["is_rerender_control"] = True
            control_qgis_path = _variant_directory(paths.run_dir, variant.name) / QGIS_RENDER_OUTPUT
            control_metrics = (
                variant_report.get("metrics") if isinstance(variant_report.get("metrics"), Mapping) else {}
            )
            control_crop_metrics = (
                variant_report.get("crop_metrics")
                if isinstance(variant_report.get("crop_metrics"), list)
                else ()
            )
        else:
            variant_report["is_rerender_control"] = False
        variants.append(variant_report)

    qgis_style_path = _manifest_output_path(manifest, manifest_path=manifest_path, key="qgis_preprocessed_style")
    generated_at = config.now or dt.datetime.now(dt.timezone.utc)
    report: dict[str, object] = {
        "generated": generated_at.isoformat(timespec="seconds"),
        "camera": dataclasses.asdict(camera),
        "rerender_control_variant": control_name if config.include_rerender_control else None,
        "inputs": {
            "baseline_manifest": _repo_relative(manifest_path),
            "browser_reference": _repo_relative(context.browser_reference_path),
            "baseline_qgis": _repo_relative(context.baseline_qgis_path),
            "qgis_preprocessed_style": _repo_relative(qgis_style_path),
        },
        "baseline": {
            "metrics": context.baseline_metrics,
            "crop_metrics": context.baseline_crop_metrics,
        },
        "crop_boxes": [list(crop_box) for crop_box in config.crop_boxes],
        "variants": variants,
    }
    paths.summary_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")  # NOSONAR
    paths.summary_md.write_text(render_markdown_summary(report), encoding="utf-8")
    return report


def _metric_delta_improves_both(delta: Mapping[str, object]) -> bool:
    mean_delta = _numeric_metric_value(delta, "normalized_mean_absolute_channel_delta")
    rms_delta = _numeric_metric_value(delta, "normalized_rms_channel_delta")
    return mean_delta is not None and mean_delta < 0 and rms_delta is not None and rms_delta < 0


def _metric_delta_worsens_both(delta: Mapping[str, object]) -> bool:
    mean_delta = _numeric_metric_value(delta, "normalized_mean_absolute_channel_delta")
    rms_delta = _numeric_metric_value(delta, "normalized_rms_channel_delta")
    return mean_delta is not None and mean_delta > 0 and rms_delta is not None and rms_delta > 0


def _crop_delta_improves_both(delta: Mapping[str, object]) -> bool:
    mean_delta = _numeric_metric_value(delta, "mean_absolute_channel_delta")
    rms_delta = _numeric_metric_value(delta, "rms_channel_delta")
    return mean_delta is not None and mean_delta < 0 and rms_delta is not None and rms_delta < 0


def _crop_delta_worsens_both(delta: Mapping[str, object]) -> bool:
    mean_delta = _numeric_metric_value(delta, "mean_absolute_channel_delta")
    rms_delta = _numeric_metric_value(delta, "rms_channel_delta")
    return mean_delta is not None and mean_delta > 0 and rms_delta is not None and rms_delta > 0


def _preferred_aggregate_delta(row: Mapping[str, object]) -> tuple[str | None, Mapping[str, object]]:
    control_delta = _mapping_value(row.get("metric_delta_vs_rerender_control"))
    if _has_aggregate_metric_pair(control_delta):
        return "rerender_control", control_delta
    baseline_delta = _mapping_value(row.get("metric_delta_vs_baseline"))
    if _has_aggregate_metric_pair(baseline_delta):
        return "baseline", baseline_delta
    return None, {}


def _preferred_crop_deltas(row: Mapping[str, object]) -> tuple[str | None, list[Mapping[str, object]]]:
    control_deltas = _list_of_mappings(row.get("crop_delta_vs_rerender_control"))
    if any(_has_crop_metric_pair(delta) for delta in control_deltas):
        return "rerender_control", control_deltas
    baseline_deltas = _list_of_mappings(row.get("crop_delta_vs_baseline"))
    if any(_has_crop_metric_pair(delta) for delta in baseline_deltas):
        return "baseline", baseline_deltas
    return None, []


def _numeric_metric_values(rows: Sequence[Mapping[str, object]], key: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = _numeric_metric_value(row, key)
        if value is not None:
            values.append(value)
    return values


def _numeric_metric_value(row: Mapping[str, object], key: str) -> float | None:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _has_aggregate_metric_pair(delta: Mapping[str, object]) -> bool:
    return all(_numeric_metric_value(delta, key) is not None for key in AGGREGATE_METRIC_KEYS)


def _has_crop_metric_pair(delta: Mapping[str, object]) -> bool:
    return all(_numeric_metric_value(delta, key) is not None for key in CROP_METRIC_KEYS[:2])


def _mean_value(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _metric_range(values: Sequence[float]) -> float | None:
    return max(values) - min(values) if values else None


def _aggregate_metric_rows(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    mean_delta_values = _numeric_metric_values(rows, "normalized_mean_absolute_channel_delta")
    rms_delta_values = _numeric_metric_values(rows, "normalized_rms_channel_delta")
    return {
        "runs": len(rows),
        "mean_delta_average": _mean_value(mean_delta_values),
        "mean_delta_min": min(mean_delta_values) if mean_delta_values else None,
        "mean_delta_max": max(mean_delta_values) if mean_delta_values else None,
        "mean_delta_range": _metric_range(mean_delta_values),
        "rms_delta_average": _mean_value(rms_delta_values),
        "rms_delta_min": min(rms_delta_values) if rms_delta_values else None,
        "rms_delta_max": max(rms_delta_values) if rms_delta_values else None,
        "rms_delta_range": _metric_range(rms_delta_values),
        "improving_runs": sum(1 for row in rows if _metric_delta_improves_both(row)),
        "worsening_runs": sum(1 for row in rows if _metric_delta_worsens_both(row)),
    }


def _aggregate_crop_metric_rows(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    mean_delta_values = _numeric_metric_values(rows, "mean_absolute_channel_delta")
    rms_delta_values = _numeric_metric_values(rows, "rms_channel_delta")
    luminance_delta_values = _numeric_metric_values(rows, "mean_luminance_delta")
    return {
        "runs": len(rows),
        "mean_delta_average": _mean_value(mean_delta_values),
        "mean_delta_range": _metric_range(mean_delta_values),
        "rms_delta_average": _mean_value(rms_delta_values),
        "rms_delta_range": _metric_range(rms_delta_values),
        "luminance_delta_average": _mean_value(luminance_delta_values),
        "luminance_delta_range": _metric_range(luminance_delta_values),
        "improving_runs": sum(1 for row in rows if _crop_delta_improves_both(row)),
        "worsening_runs": sum(1 for row in rows if _crop_delta_worsens_both(row)),
    }


def _aggregate_other_runs(aggregate: Mapping[str, object]) -> int:
    return int(aggregate["runs"]) - int(aggregate["improving_runs"]) - int(aggregate["worsening_runs"])


def _aggregate_crop_box(crop_boxes: object, crop_index: int) -> str:
    if not isinstance(crop_boxes, list) or crop_index >= len(crop_boxes):
        return ""
    return json.dumps(crop_boxes[crop_index])


def _aggregate_variant_name(variant: Mapping[str, object], control_variant_name: object) -> str | None:
    variant_name = variant.get("name")
    if not isinstance(variant_name, str) or not variant_name:
        return None
    if variant.get("is_rerender_control") is True or variant_name == control_variant_name:
        return None
    return variant_name


def _aggregate_variant_delta_entry(
    *,
    variant_name: str,
    camera_name: str,
    variant: Mapping[str, object],
) -> tuple[str, str, str, Mapping[str, object]] | None:
    delta_source, delta = _preferred_aggregate_delta(variant)
    if delta_source is None:
        return None
    return variant_name, camera_name, delta_source, delta


def _aggregate_variant_crop_entries(
    *,
    variant_name: str,
    camera_name: str,
    crop_boxes: object,
    variant: Mapping[str, object],
) -> list[tuple[str, str, str, int, str, Mapping[str, object]]]:
    crop_delta_source, crop_deltas = _preferred_crop_deltas(variant)
    if crop_delta_source is None:
        return []
    return [
        (
            variant_name,
            camera_name,
            crop_delta_source,
            crop_index + 1,
            _aggregate_crop_box(crop_boxes, crop_index),
            crop_delta,
        )
        for crop_index, crop_delta in enumerate(crop_deltas)
        if _has_crop_metric_pair(crop_delta)
    ]


def _aggregate_delta_entries(
    report_path_value: Path,
) -> tuple[
    str,
    list[tuple[str, str, str, Mapping[str, object]]],
    list[tuple[str, str, str, int, str, Mapping[str, object]]],
]:
    report_path = report_path_value.expanduser().resolve()
    report = load_json_object(report_path)
    camera = _mapping_value(report.get("camera"))
    camera_name = str(camera.get("name") or "(unknown camera)")
    control_variant_name = report.get("rerender_control_variant")
    crop_boxes = report.get("crop_boxes")
    entries: list[tuple[str, str, str, Mapping[str, object]]] = []
    crop_entries: list[tuple[str, str, str, int, str, Mapping[str, object]]] = []
    for variant in _list_of_mappings(report.get("variants")):
        variant_name = _aggregate_variant_name(variant, control_variant_name)
        if variant_name is None:
            continue
        entry = _aggregate_variant_delta_entry(
            variant_name=variant_name,
            camera_name=camera_name,
            variant=variant,
        )
        if entry is not None:
            entries.append(entry)
        crop_entries.extend(
            _aggregate_variant_crop_entries(
                variant_name=variant_name,
                camera_name=camera_name,
                crop_boxes=crop_boxes,
                variant=variant,
            )
        )
    return _repo_relative(report_path), entries, crop_entries


def _aggregate_camera_rows(
    grouped_rows: Mapping[tuple[str, str, str], Sequence[Mapping[str, object]]],
) -> list[dict[str, object]]:
    rows = []
    for variant_name, camera_name, delta_source in sorted(grouped_rows):
        aggregate = _aggregate_metric_rows(grouped_rows[(variant_name, camera_name, delta_source)])
        aggregate["variant"] = variant_name
        aggregate["camera"] = camera_name
        aggregate["delta_source"] = delta_source
        aggregate["other_runs"] = _aggregate_other_runs(aggregate)
        rows.append(aggregate)
    return rows


def _aggregate_variant_totals(
    grouped_totals: Mapping[tuple[str, str], Sequence[Mapping[str, object]]],
    total_cameras: Mapping[tuple[str, str], set[str]],
) -> list[dict[str, object]]:
    totals = []
    for variant_name, delta_source in sorted(grouped_totals):
        total_key = (variant_name, delta_source)
        aggregate = _aggregate_metric_rows(grouped_totals[total_key])
        aggregate["variant"] = variant_name
        aggregate["delta_source"] = delta_source
        aggregate["camera_count"] = len(total_cameras[total_key])
        aggregate["other_runs"] = _aggregate_other_runs(aggregate)
        totals.append(aggregate)
    return totals


def _aggregate_crop_rows(
    grouped_crop_rows: Mapping[tuple[str, str, str, int, str], Sequence[Mapping[str, object]]],
) -> list[dict[str, object]]:
    rows = []
    for variant_name, camera_name, delta_source, crop_index, crop_box in sorted(grouped_crop_rows):
        aggregate = _aggregate_crop_metric_rows(
            grouped_crop_rows[(variant_name, camera_name, delta_source, crop_index, crop_box)]
        )
        aggregate["variant"] = variant_name
        aggregate["camera"] = camera_name
        aggregate["delta_source"] = delta_source
        aggregate["crop"] = crop_index
        aggregate["crop_box"] = crop_box
        aggregate["other_runs"] = _aggregate_other_runs(aggregate)
        rows.append(aggregate)
    return rows


def build_style_adjustment_aggregate_report(
    report_paths: Sequence[Path],
    *,
    now: dt.datetime | None = None,
) -> dict[str, object]:
    grouped_rows: dict[tuple[str, str, str], list[Mapping[str, object]]] = {}
    grouped_totals: dict[tuple[str, str], list[Mapping[str, object]]] = {}
    grouped_crop_rows: dict[tuple[str, str, str, int, str], list[Mapping[str, object]]] = {}
    total_cameras: dict[tuple[str, str], set[str]] = {}
    input_paths: list[str] = []
    for report_path_value in report_paths:
        input_path, entries, crop_entries = _aggregate_delta_entries(report_path_value)
        input_paths.append(input_path)
        for variant_name, camera_name, delta_source, delta in entries:
            grouped_rows.setdefault((variant_name, camera_name, delta_source), []).append(delta)
            total_key = (variant_name, delta_source)
            grouped_totals.setdefault(total_key, []).append(delta)
            total_cameras.setdefault(total_key, set()).add(camera_name)
        for variant_name, camera_name, delta_source, crop_index, crop_box, crop_delta in crop_entries:
            grouped_crop_rows.setdefault(
                (variant_name, camera_name, delta_source, crop_index, crop_box),
                [],
            ).append(crop_delta)

    generated_at = now or dt.datetime.now(dt.timezone.utc)
    return {
        "generated": generated_at.isoformat(timespec="seconds"),
        "input_reports": input_paths,
        "rows": _aggregate_camera_rows(grouped_rows),
        "variant_totals": _aggregate_variant_totals(grouped_totals, total_cameras),
        "crop_rows": _aggregate_crop_rows(grouped_crop_rows),
    }


def _aggregate_summary_header_lines(report: Mapping[str, object]) -> list[str]:
    input_reports = [str(path) for path in report.get("input_reports") or []]
    lines = [
        "# Mapbox Outdoors style-adjustment aggregate",
        "",
        f"Generated: `{report.get('generated')}`",
        f"Input reports: `{len(input_reports)}`",
    ]
    if input_reports:
        lines.append("")
        lines.append("Inputs:")
        lines.extend(f"- `{path}`" for path in input_reports[:20])
        if len(input_reports) > 20:
            lines.append(f"- ... {len(input_reports) - 20} more")
    return lines


def _aggregate_row(row: Mapping[str, object]) -> str:
    return (
        f"| `{row.get('variant')}` | `{row.get('camera')}` | `{row.get('delta_source')}` | "
        f"{row.get('runs')} | "
        f"{_format_number(row.get('mean_delta_average'))} | "
        f"{_format_number(row.get('rms_delta_average'))} | "
        f"{_format_number(row.get('mean_delta_range'))} | "
        f"{_format_number(row.get('rms_delta_range'))} | "
        f"{row.get('improving_runs')} | {row.get('worsening_runs')} | {row.get('other_runs')} |"
    )


def _aggregate_total_row(row: Mapping[str, object]) -> str:
    return (
        f"| `{row.get('variant')}` | `{row.get('delta_source')}` | "
        f"{row.get('runs')} | {row.get('camera_count')} | "
        f"{_format_number(row.get('mean_delta_average'))} | "
        f"{_format_number(row.get('rms_delta_average'))} | "
        f"{_format_number(row.get('mean_delta_range'))} | "
        f"{_format_number(row.get('rms_delta_range'))} | "
        f"{row.get('improving_runs')} | {row.get('worsening_runs')} | {row.get('other_runs')} |"
    )


def _aggregate_crop_row(row: Mapping[str, object]) -> str:
    return (
        f"| `{row.get('variant')}` | `{row.get('camera')}` | `{row.get('delta_source')}` | "
        f"{row.get('crop')} | `{row.get('crop_box')}` | {row.get('runs')} | "
        f"{_format_number(row.get('mean_delta_average'))} | "
        f"{_format_number(row.get('rms_delta_average'))} | "
        f"{_format_number(row.get('luminance_delta_average'))} | "
        f"{_format_number(row.get('mean_delta_range'))} | "
        f"{_format_number(row.get('rms_delta_range'))} | "
        f"{row.get('improving_runs')} | {row.get('worsening_runs')} | {row.get('other_runs')} |"
    )


def _count_value(row: Mapping[str, object], key: str) -> int:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _runs_count(row: Mapping[str, object]) -> int:
    return _count_value(row, "runs")


def _improving_runs_count(row: Mapping[str, object]) -> int:
    return _count_value(row, "improving_runs")


def _worsening_runs_count(row: Mapping[str, object]) -> int:
    return _count_value(row, "worsening_runs")


def _is_all_improving(row: Mapping[str, object]) -> bool:
    runs = _runs_count(row)
    return runs > 0 and _improving_runs_count(row) == runs


def _is_mixed_signal(row: Mapping[str, object]) -> bool:
    return _improving_runs_count(row) > 0 and _worsening_runs_count(row) > 0


def _variant_signal_label(row: Mapping[str, object]) -> str:
    return (
        f"`{row.get('variant')}` (`{row.get('delta_source')}`, "
        f"{_improving_runs_count(row)}/{_runs_count(row)} improving, "
        f"{_worsening_runs_count(row)}/{_runs_count(row)} worsening)"
    )


def _crop_signal_label(row: Mapping[str, object]) -> str:
    return (
        f"`{row.get('variant')}` on `{row.get('camera')}` crop {row.get('crop')} "
        f"(`{row.get('delta_source')}`, {_improving_runs_count(row)}/{_runs_count(row)} improving, "
        f"{_worsening_runs_count(row)}/{_runs_count(row)} worsening)"
    )


def _metric_sort_value(row: Mapping[str, object]) -> float:
    mean_delta = _numeric_metric_value(row, "mean_delta_average") or 0.0
    rms_delta = _numeric_metric_value(row, "rms_delta_average") or 0.0
    return mean_delta + rms_delta


def _format_limited(labels: Sequence[str], *, limit: int = 5) -> str:
    if not labels:
        return "none"
    visible = list(labels[:limit])
    if len(labels) > limit:
        visible.append(f"... {len(labels) - limit} more")
    return ", ".join(visible)


def _aggregate_read_lines(
    *,
    totals: Sequence[Mapping[str, object]],
    crop_rows: Sequence[Mapping[str, object]],
) -> list[str]:
    all_improving_totals = [_variant_signal_label(row) for row in totals if _is_all_improving(row)]
    mixed_totals = [_variant_signal_label(row) for row in totals if _is_mixed_signal(row)]
    all_improving_crops = [
        _crop_signal_label(row)
        for row in sorted(
            (row for row in crop_rows if _is_all_improving(row)),
            key=_metric_sort_value,
        )
    ]
    mixed_crops = [_crop_signal_label(row) for row in crop_rows if _is_mixed_signal(row)]
    return [
        "",
        "## Read",
        "",
        f"- Whole-image all-improving variants: {_format_limited(all_improving_totals)}.",
        f"- Whole-image mixed-signal variants: {_format_limited(mixed_totals)}.",
        f"- Crop rows all-improving: {_format_limited(all_improving_crops)}.",
        f"- Crop rows mixed-signal: {_format_limited(mixed_crops)}.",
        "- Treat crop-only wins as diagnostic leads; promote a style change only after camera-matrix validation avoids regressions.",
    ]


def render_aggregate_markdown_summary(report: Mapping[str, object]) -> str:
    totals = _list_of_mappings(report.get("variant_totals"))
    rows = _list_of_mappings(report.get("rows"))
    crop_rows = _list_of_mappings(report.get("crop_rows"))
    lines = _aggregate_summary_header_lines(report)
    lines.extend([
        "",
        "## Variant totals",
        "",
        "| Variant | Delta source | Runs | Cameras | Mean delta avg | RMS delta avg | Mean delta range | RMS delta range | Improving | Worsening | Other |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    if totals:
        lines.extend(_aggregate_total_row(row) for row in totals)
    else:
        lines.append("| _none_ |  | 0 | 0 |  |  |  |  | 0 | 0 | 0 |")
    lines.extend([
        "",
        "## Aggregated variant movement",
        "",
        "| Variant | Camera | Delta source | Runs | Mean delta avg | RMS delta avg | Mean delta range | RMS delta range | Improving | Worsening | Other |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    if rows:
        lines.extend(_aggregate_row(row) for row in rows)
    else:
        lines.append("| _none_ |  |  | 0 |  |  |  |  | 0 | 0 | 0 |")
    lines.extend([
        "",
        "## Aggregated crop movement",
        "",
        "| Variant | Camera | Delta source | Crop | Box | Runs | Mean delta avg | RMS delta avg | Luminance delta avg | Mean delta range | RMS delta range | Improving | Worsening | Other |",
        "| --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    if crop_rows:
        lines.extend(_aggregate_crop_row(row) for row in crop_rows)
    else:
        lines.append("| _none_ |  |  | 0 |  | 0 |  |  |  |  |  | 0 | 0 | 0 |")
    lines.extend(_aggregate_read_lines(totals=totals, crop_rows=crop_rows))
    lines.extend([
        "",
        "## Key",
        "",
        "- Deltas are grouped by variant and camera, preferring rerender-control deltas when present.",
        "- Crop deltas use rerender-control deltas when present and fall back to baseline deltas.",
        "- Negative mean/RMS averages indicate the variant moved closer to the Mapbox GL reference on average.",
        "- Non-zero ranges or mixed improving/worsening counts indicate repeated-render or camera-matrix instability.",
        "",
    ])
    return "\n".join(lines)


def _summary_header_lines(report: Mapping[str, object]) -> list[str]:
    camera = _mapping_value(report.get("camera"))
    inputs = _mapping_value(report.get("inputs"))
    lines = [
        "# Mapbox Outdoors style-adjustment probe",
        "",
        f"Generated: `{report.get('generated')}`",
        f"Camera: `{camera.get('name')}`",
        "",
        "Inputs:",
    ]
    lines.extend(f"- {label}: `{path}`" for label, path in inputs.items())
    return lines


def _whole_image_row(row: Mapping[str, object]) -> str:
    metrics = _mapping_value(row.get("metrics"))
    baseline_delta = _mapping_value(row.get("metric_delta_vs_baseline"))
    control_delta = _mapping_value(row.get("metric_delta_vs_rerender_control"))
    return (
        f"| `{row.get('name')}` | "
        f"{_format_number(metrics.get('normalized_mean_absolute_channel_delta'))} | "
        f"{_format_number(metrics.get('normalized_rms_channel_delta'))} | "
        f"{_format_number(baseline_delta.get('normalized_mean_absolute_channel_delta'))} | "
        f"{_format_number(baseline_delta.get('normalized_rms_channel_delta'))} | "
        f"{_format_number(control_delta.get('normalized_mean_absolute_channel_delta'))} | "
        f"{_format_number(control_delta.get('normalized_rms_channel_delta'))} |"
    )


def _whole_image_lines(
    *,
    baseline_metrics: Mapping[str, object],
    variant_rows: Sequence[Mapping[str, object]],
) -> list[str]:
    return [
        "",
        "## Whole-image metrics",
        "",
        "| Variant | Mean abs delta | RMS delta | Mean delta vs baseline | RMS delta vs baseline | Mean delta vs control | RMS delta vs control |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        (
            "| `baseline` | "
            f"{_format_number(baseline_metrics.get('normalized_mean_absolute_channel_delta'))} | "
            f"{_format_number(baseline_metrics.get('normalized_rms_channel_delta'))} |  |  |  |  |"
        ),
        *[_whole_image_row(row) for row in variant_rows],
    ]


def _read_lines(
    *,
    variant_rows: Sequence[Mapping[str, object]],
    control_name: object,
) -> list[str]:
    baseline_improving = [
        f"`{row.get('name')}`"
        for row in variant_rows
        if row.get("name") != control_name
        and _metric_delta_improves_both(_mapping_value(row.get("metric_delta_vs_baseline")))
    ]
    control_improving = [
        f"`{row.get('name')}`"
        for row in variant_rows
        if row.get("name") != control_name
        and _metric_delta_improves_both(_mapping_value(row.get("metric_delta_vs_rerender_control")))
    ]
    return [
        "",
        "## Read",
        "",
        f"- Whole-image mean/RMS improving variants: {', '.join(baseline_improving) if baseline_improving else 'none'}.",
        f"- Control-adjusted whole-image mean/RMS improving variants: {', '.join(control_improving) if control_improving else 'none'}.",
        "- Negative mean/RMS deltas indicate the variant moved closer to the Mapbox GL reference.",
        "- This is diagnostic evidence only; validate promising variants across the all-camera matrix before changing production style preprocessing.",
        "",
    ]


def render_markdown_summary(report: Mapping[str, object]) -> str:
    variant_rows = _list_of_mappings(report.get("variants"))
    baseline_metrics = _mapping_value(_mapping_value(report.get("baseline")).get("metrics"))
    lines = _summary_header_lines(report)
    lines.extend(_whole_image_lines(baseline_metrics=baseline_metrics, variant_rows=variant_rows))
    _extend_crop_movement_lines(lines, report=report, variant_rows=variant_rows)
    lines.extend(_read_lines(variant_rows=variant_rows, control_name=report.get("rerender_control_variant")))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Render diagnostic QGIS style-adjustment variants from an existing Mapbox Outdoors "
            "comparison manifest and compare each variant with the baseline artifacts."
        ),
    )
    parser.add_argument(
        "--baseline-manifest",
        type=Path,
        help="Existing comparison manifest with Mapbox reference, QGIS render, and preprocessed style paths.",
    )
    parser.add_argument(
        "--variant-json",
        type=Path,
        help="JSON plan containing variants and per-layer paint/layout/zoom/filter adjustments.",
    )
    parser.add_argument(
        "--aggregate-report",
        action="append",
        type=Path,
        default=[],
        help="Existing style-adjustment-probe.json report to aggregate. Repeat for multiple reports.",
    )
    parser.add_argument(
        "--aggregate-output",
        type=Path,
        help="Optional Markdown output path for --aggregate-report mode. Defaults to stdout.",
    )
    parser.add_argument(
        "--crop-box",
        action="append",
        type=parse_crop_box,
        default=[],
        help="Optional crop box as x_min,y_min,x_max,y_max. Repeat for multiple crops.",
    )
    parser.add_argument(
        "--mapbox-token",
        help="Mapbox access token. Prefer MAPBOX_ACCESS_TOKEN to avoid shell history exposure.",
    )
    parser.add_argument(
        "--no-rerender-control",
        action="store_true",
        help="Skip the automatic same-style QGIS rerender control.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.aggregate_report:
        aggregate_report = build_style_adjustment_aggregate_report(tuple(args.aggregate_report))
        markdown_summary = render_aggregate_markdown_summary(aggregate_report)
        if args.aggregate_output is not None:
            output_path = args.aggregate_output.expanduser().resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(markdown_summary, encoding="utf-8")
            print(f"Aggregate summary: {_repo_relative(output_path)}")
        else:
            print(markdown_summary)
        return
    if args.baseline_manifest is None or args.variant_json is None:
        parser.error("--baseline-manifest and --variant-json are required unless --aggregate-report is provided.")
    token = resolve_mapbox_token(provided_token=args.mapbox_token)
    report = build_style_adjustment_probe_report(
        StyleAdjustmentProbeConfig(
            baseline_manifest=args.baseline_manifest,
            output_root=DEFAULT_OUTPUT_ROOT,
            variants=load_style_adjustment_variants(args.variant_json),
            crop_boxes=tuple(args.crop_box),
            token=token,
            include_rerender_control=not args.no_rerender_control,
        )
    )
    inputs = report.get("inputs")
    if isinstance(inputs, Mapping):
        print(f"Baseline manifest: {inputs.get('baseline_manifest')}")
    output_root = DEFAULT_OUTPUT_ROOT.expanduser().resolve()
    newest = max(
        (path for path in (output_root / REPORT_CAMERA_DIRECTORY).glob("*") if path.is_dir()),
        default=None,
    )
    if newest is not None:
        print(f"Run directory: {_repo_relative(newest)}")
        print(f"Summary: {_repo_relative(newest / SUMMARY_OUTPUT)}")


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    main()
