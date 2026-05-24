from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

try:
    from .mapbox_outdoors_comparison import (
        CAMERAS,
        DEFAULT_QT_QPA_PLATFORM,
        DEFAULT_MAPBOX_STYLE_OWNER,
        DEFAULT_OUTPUT_ROOT as COMPARISON_DEFAULT_OUTPUT_ROOT,
        ImageMetrics,
        MapboxComparisonCamera,
        PACKAGE_PARENT,
        REPO_ROOT,
        build_image_diff,
        load_style_definition,
        redact_sensitive_text,
        resolve_mapbox_token,
    )
except ImportError:  # pragma: no cover - direct script execution
    from mapbox_outdoors_comparison import (  # type: ignore[no-redef]
        CAMERAS,
        DEFAULT_QT_QPA_PLATFORM,
        DEFAULT_MAPBOX_STYLE_OWNER,
        DEFAULT_OUTPUT_ROOT as COMPARISON_DEFAULT_OUTPUT_ROOT,
        ImageMetrics,
        MapboxComparisonCamera,
        PACKAGE_PARENT,
        REPO_ROOT,
        build_image_diff,
        load_style_definition,
        redact_sensitive_text,
        resolve_mapbox_token,
    )


DEFAULT_OUTPUT_ROOT = COMPARISON_DEFAULT_OUTPUT_ROOT.parent / "mapbox-outdoors-rendered-layer-mask"
STYLE_MASK_OUTPUT = "qgis-mask-style.json"
QGIS_RENDER_OUTPUT = "qgis-vector-render.png"
MAPBOX_DIFF_OUTPUT = "mapbox-gl-vs-qgis-diff.png"
QGIS_MOVEMENT_DIFF_OUTPUT = "qgis-vs-baseline-diff.png"
REPORT_CAMERA_DIRECTORY = "comparison-camera"
VARIANT_SPEC_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*=[^=]+$")
SAFE_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
LUMINANCE_WEIGHTS = (0.2126, 0.7152, 0.0722)


@dataclass(frozen=True)
class RenderedLayerMaskVariant:
    name: str
    layer_ids: tuple[str, ...]


@dataclass(frozen=True)
class RenderedLayerMaskConfig:
    baseline_manifest: Path
    output_root: Path
    variants: tuple[RenderedLayerMaskVariant, ...]
    token: str
    crop_boxes: tuple[tuple[int, int, int, int], ...] = ()
    include_rerender_control: bool = True
    now: dt.datetime | None = None


@dataclass(frozen=True)
class RenderedLayerMaskPaths:
    run_dir: Path
    summary_json: Path
    summary_md: Path


@dataclass(frozen=True)
class RenderedLayerMaskContext:
    run_dir: Path
    camera: MapboxComparisonCamera
    token: str
    base_style: Mapping[str, object]
    browser_reference_path: Path
    baseline_qgis_path: Path
    baseline_metrics: Mapping[str, object]
    baseline_crop_metrics: Sequence[Mapping[str, object]]
    crop_boxes: Sequence[tuple[int, int, int, int]]
    qgis_renderer: Callable[..., None]
    diff_builder: Callable[..., ImageMetrics | None]


def _utc_timestamp(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def parse_variant_spec(value: str) -> RenderedLayerMaskVariant:
    if not VARIANT_SPEC_RE.match(value):
        raise argparse.ArgumentTypeError(
            "Variants must use NAME=LAYER_ID[,LAYER_ID...] with a filesystem-safe name."
        )
    name, layer_text = value.split("=", 1)
    layer_ids = tuple(layer_id.strip() for layer_id in layer_text.split(",") if layer_id.strip())
    if not layer_ids:
        raise argparse.ArgumentTypeError(f"Variant '{name}' must include at least one layer id.")
    return RenderedLayerMaskVariant(name=name, layer_ids=layer_ids)


def parse_crop_box(value: str) -> tuple[int, int, int, int]:
    try:
        x_min, y_min, x_max, y_max = (int(part.strip()) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Crop boxes must use x_min,y_min,x_max,y_max.") from exc
    if x_min < 0 or y_min < 0 or x_max <= x_min or y_max <= y_min:
        raise argparse.ArgumentTypeError("Crop boxes must be positive non-empty image regions.")
    return x_min, y_min, x_max, y_max


def build_rendered_layer_mask_paths(
    *,
    output_root: Path,
    camera_name: str,
    now: dt.datetime | None = None,
) -> RenderedLayerMaskPaths:
    run_dir = output_root / safe_path_segment(camera_name) / _utc_timestamp(now)
    return RenderedLayerMaskPaths(
        run_dir=run_dir,
        summary_json=run_dir / "summary.json",
        summary_md=run_dir / "summary.md",
    )


def safe_path_segment(value: str) -> str:
    if not SAFE_PATH_SEGMENT_RE.match(value):
        raise ValueError(f"Unsafe path segment: {value!r}")
    return value


def load_json_object(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return loaded


def _resolve_artifact_path(path_text: object, *, manifest_path: Path) -> Path:
    if not isinstance(path_text, str) or not path_text:
        raise ValueError("Manifest is missing a required output path.")
    path = Path(path_text)
    if path.is_absolute():
        return path
    candidates = [
        manifest_path.parent / path,
        Path.cwd() / path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[-1].resolve()


def _manifest_output_path(
    manifest: Mapping[str, object],
    *,
    manifest_path: Path,
    key: str,
) -> Path:
    outputs = manifest.get("outputs")
    if not isinstance(outputs, Mapping):
        raise ValueError("Comparison manifest does not include outputs.")
    return _resolve_artifact_path(outputs.get(key), manifest_path=manifest_path)


def camera_from_manifest(manifest: Mapping[str, object]) -> MapboxComparisonCamera:
    camera = manifest.get("camera")
    if not isinstance(camera, Mapping):
        raise ValueError("Comparison manifest does not include a camera object.")
    name = camera.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("Comparison manifest camera does not include a name.")
    known = CAMERAS.get(name)
    if known is not None:
        return known
    return MapboxComparisonCamera(
        name=name,
        description=str(camera.get("description") or name),
        longitude=float(camera["longitude"]),
        latitude=float(camera["latitude"]),
        zoom=float(camera["zoom"]),
        width=int(camera.get("width", 1280)),
        height=int(camera.get("height", 900)),
        bearing=float(camera.get("bearing", 0.0)),
        pitch=float(camera.get("pitch", 0.0)),
        style_owner=str(camera.get("style_owner") or DEFAULT_MAPBOX_STYLE_OWNER),
        style_id=str(camera.get("style_id") or "outdoors-v12"),
    )


def _masked_paint_properties(layer_type: str) -> dict[str, object]:
    if layer_type == "fill":
        return {"fill-opacity": 0.0}
    if layer_type == "line":
        return {"line-opacity": 0.0}
    if layer_type == "background":
        return {"background-opacity": 0.0}
    if layer_type == "symbol":
        return {"icon-opacity": 0.0, "text-opacity": 0.0}
    if layer_type == "circle":
        return {"circle-opacity": 0.0, "circle-stroke-opacity": 0.0}
    if layer_type == "fill-extrusion":
        return {"fill-extrusion-opacity": 0.0}
    if layer_type == "raster":
        return {"raster-opacity": 0.0}
    return {"visibility": "none"}


def apply_transparent_layer_mask(
    style: Mapping[str, object],
    *,
    layer_ids: Sequence[str],
) -> tuple[dict[str, object], list[str], list[str]]:
    masked = deepcopy(dict(style))
    layers = masked.get("layers")
    if not isinstance(layers, list):
        raise ValueError("Mapbox style JSON must include a layers array.")

    target_ids = set(layer_ids)
    matched_ids: list[str] = []
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        layer_id = layer.get("id")
        if not isinstance(layer_id, str) or layer_id not in target_ids:
            continue
        matched_ids.append(layer_id)
        layer_type = str(layer.get("type") or "")
        if layer_type == "":
            continue
        paint = layer.setdefault("paint", {})
        if not isinstance(paint, dict):
            paint = {}
            layer["paint"] = paint
        paint.update(_masked_paint_properties(layer_type))

    missing_ids = [layer_id for layer_id in layer_ids if layer_id not in set(matched_ids)]
    return masked, matched_ids, missing_ids


def image_delta_metrics(
    *,
    reference_path: Path,
    candidate_path: Path,
    crop_box: tuple[int, int, int, int] | None = None,
) -> dict[str, object]:
    try:
        from PIL import Image, ImageChops, ImageStat  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on optional local toolchain
        raise RuntimeError("Rendered-layer mask metrics require Pillow.") from exc

    with Image.open(reference_path).convert("RGB") as reference_image:
        with Image.open(candidate_path).convert("RGB") as candidate_image:
            if reference_image.size != candidate_image.size:
                raise ValueError(
                    f"Cannot compare images with different sizes: "
                    f"{reference_image.size} vs {candidate_image.size}."
                )
            if crop_box is not None:
                reference_image = reference_image.crop(crop_box)
                candidate_image = candidate_image.crop(crop_box)
            diff = ImageChops.difference(reference_image, candidate_image)
            diff_stats = ImageStat.Stat(diff)
            channel_count = max(1, len(diff_stats.mean))
            mean_absolute_delta = sum(diff_stats.mean) / channel_count
            rms_delta = sum(diff_stats.rms) / channel_count
            changed_pixel_count = sum(1 for pixel in diff.getdata() if any(channel != 0 for channel in pixel))
            signed_deltas = [
                tuple(candidate_channel - reference_channel for candidate_channel, reference_channel in zip(c, r))
                for r, c in zip(reference_image.getdata(), candidate_image.getdata())
            ]
            pixel_count = max(1, reference_image.width * reference_image.height)
            mean_delta_rgb = [
                sum(delta[channel] for delta in signed_deltas) / pixel_count for channel in range(3)
            ]
            mean_luminance_delta = sum(
                mean_delta_rgb[channel] * LUMINANCE_WEIGHTS[channel] for channel in range(3)
            )
            return {
                "box": list(crop_box) if crop_box is not None else None,
                "pixel_count": reference_image.width * reference_image.height,
                "changed_pixel_count": changed_pixel_count,
                "changed_pixel_ratio": changed_pixel_count / pixel_count,
                "mean_delta_rgb": mean_delta_rgb,
                "mean_luminance_delta": mean_luminance_delta,
                "mean_absolute_channel_delta": mean_absolute_delta,
                "rms_channel_delta": rms_delta,
            }


def image_changed_bbox(*, reference_path: Path, candidate_path: Path) -> list[int] | None:
    try:
        from PIL import Image, ImageChops  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on optional local toolchain
        raise RuntimeError("Rendered-layer mask metrics require Pillow.") from exc

    with Image.open(reference_path).convert("RGB") as reference_image:
        with Image.open(candidate_path).convert("RGB") as candidate_image:
            if reference_image.size != candidate_image.size:
                raise ValueError(
                    f"Cannot compare images with different sizes: "
                    f"{reference_image.size} vs {candidate_image.size}."
                )
            bbox = ImageChops.difference(reference_image, candidate_image).getbbox()
    return list(bbox) if bbox is not None else None


def metric_delta(
    candidate: Mapping[str, object],
    baseline: Mapping[str, object],
    *,
    keys: Sequence[str],
) -> dict[str, float]:
    delta: dict[str, float] = {}
    for key in keys:
        candidate_value = candidate.get(key)
        baseline_value = baseline.get(key)
        if isinstance(candidate_value, (int, float)) and isinstance(baseline_value, (int, float)):
            delta[key] = float(candidate_value) - float(baseline_value)
    return delta


def _write_variant_style(path: Path, style: Mapping[str, object], *, token: str) -> None:
    path.write_text(redact_sensitive_text(json.dumps(style, indent=2), token) + "\n", encoding="utf-8")


def build_qgis_render_child_script() -> str:
    return f"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, {str(PACKAGE_PARENT)!r})

from qfit.validation.mapbox_outdoors_comparison import (  # noqa: E402
    MapboxComparisonCamera,
    load_style_definition,
    render_qgis_vector,
)

camera_path = Path(sys.argv[1])
style_path = Path(sys.argv[2])
output_path = Path(sys.argv[3])
camera = MapboxComparisonCamera(**json.loads(camera_path.read_text(encoding="utf-8")))
render_qgis_vector(
    camera=camera,
    token=os.environ["MAPBOX_ACCESS_TOKEN"],
    output_path=output_path,
    style_definition=load_style_definition(style_path),
)
""".strip()


def render_qgis_vector_in_subprocess(
    *,
    camera: MapboxComparisonCamera,
    token: str,
    output_path: Path,
    style_definition: Mapping[str, object],
    timeout_seconds: float = 240.0,
    **_ignored: object,
) -> None:
    with tempfile.TemporaryDirectory(prefix="qfit-mapbox-mask-render-") as tmpdir:
        tmp_path = Path(tmpdir)
        camera_path = tmp_path / "camera.json"
        style_path = tmp_path / "style.json"
        script_path = tmp_path / "render-qgis-style.py"
        camera_path.write_text(json.dumps(dataclasses.asdict(camera)), encoding="utf-8")
        style_path.write_text(json.dumps(style_definition), encoding="utf-8")
        script_path.write_text(build_qgis_render_child_script(), encoding="utf-8")
        env = os.environ.copy()
        env.setdefault("QT_QPA_PLATFORM", DEFAULT_QT_QPA_PLATFORM)
        env["MAPBOX_ACCESS_TOKEN"] = token
        completed = subprocess.run(
            [sys.executable, str(script_path), str(camera_path), str(style_path), str(output_path)],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    if completed.returncode != 0:
        detail = redact_sensitive_text((completed.stderr or completed.stdout).strip(), token)
        raise RuntimeError(
            f"QGIS mask render failed for {output_path} with exit code "
            f"{completed.returncode}: {detail}"
        )


def _variant_directory(run_dir: Path, variant_name: str) -> Path:
    return run_dir / variant_name


def _repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def _variant_report(
    *,
    variant: RenderedLayerMaskVariant,
    context: RenderedLayerMaskContext,
    control_qgis_path: Path | None = None,
    control_crop_metrics: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    variant_dir = _variant_directory(context.run_dir, variant.name)
    variant_dir.mkdir(parents=True, exist_ok=True)
    variant_style_path = variant_dir / STYLE_MASK_OUTPUT
    qgis_path = variant_dir / QGIS_RENDER_OUTPUT
    mapbox_diff_path = variant_dir / MAPBOX_DIFF_OUTPUT
    movement_diff_path = variant_dir / QGIS_MOVEMENT_DIFF_OUTPUT
    control_movement_diff_path = variant_dir / "qgis-vs-rerender-control-diff.png"
    variant_style, matched_ids, missing_ids = apply_transparent_layer_mask(
        context.base_style,
        layer_ids=variant.layer_ids,
    )
    _write_variant_style(variant_style_path, variant_style, token=context.token)
    context.qgis_renderer(
        camera=context.camera,
        token=context.token,
        output_path=qgis_path,
        style_definition=variant_style,
        qgis_preprocessed_style_path=None,
        qgis_label_styles_path=None,
    )
    metrics = context.diff_builder(
        reference_path=context.browser_reference_path,
        candidate_path=qgis_path,
        output_path=mapbox_diff_path,
    ) or {}
    qgis_movement_metrics = context.diff_builder(
        reference_path=context.baseline_qgis_path,
        candidate_path=qgis_path,
        output_path=movement_diff_path,
    ) or {}
    crop_metrics = [
        image_delta_metrics(
            reference_path=context.browser_reference_path,
            candidate_path=qgis_path,
            crop_box=crop_box,
        )
        for crop_box in context.crop_boxes
    ]
    crop_delta_vs_baseline = [
        metric_delta(
            crop_metrics[index],
            context.baseline_crop_metrics[index],
            keys=("mean_absolute_channel_delta", "rms_channel_delta", "mean_luminance_delta"),
        )
        for index in range(len(crop_metrics))
    ]
    render_changed = any(
        isinstance(qgis_movement_metrics.get(key), (int, float)) and qgis_movement_metrics[key] > 0
        for key in ("changed_pixel_count", "mean_absolute_channel_delta", "rms_channel_delta")
    )
    report: dict[str, object] = {
        "name": variant.name,
        "target_layer_ids": list(variant.layer_ids),
        "matched_layer_ids": matched_ids,
        "missing_layer_ids": missing_ids,
        "render_changed": render_changed,
        "directory": _repo_relative(variant_dir),
        "style_json": _repo_relative(variant_style_path),
        "qgis_vector_render": _repo_relative(qgis_path),
        "mapbox_diff": _repo_relative(mapbox_diff_path),
        "qgis_movement_diff": _repo_relative(movement_diff_path),
        "metrics": metrics,
        "metric_delta_vs_baseline": metric_delta(
            metrics,
            context.baseline_metrics,
            keys=(
                "changed_pixel_ratio",
                "normalized_mean_absolute_channel_delta",
                "normalized_rms_channel_delta",
            ),
        ),
        "qgis_movement_metrics": qgis_movement_metrics,
        "diff_bbox_vs_baseline_qgis": image_changed_bbox(
            reference_path=context.baseline_qgis_path,
            candidate_path=qgis_path,
        ),
        "crop_metrics": crop_metrics,
        "crop_delta_vs_baseline": crop_delta_vs_baseline,
    }
    if control_qgis_path is not None:
        report["qgis_movement_vs_rerender_control_metrics"] = context.diff_builder(
            reference_path=control_qgis_path,
            candidate_path=qgis_path,
            output_path=control_movement_diff_path,
        ) or {}
        report["qgis_movement_vs_rerender_control_diff"] = _repo_relative(control_movement_diff_path)
        report["diff_bbox_vs_rerender_control_qgis"] = image_changed_bbox(
            reference_path=control_qgis_path,
            candidate_path=qgis_path,
        )
        report["crop_delta_vs_rerender_control"] = [
            metric_delta(
                crop_metrics[index],
                control_crop_metrics[index],
                keys=("mean_absolute_channel_delta", "rms_channel_delta", "mean_luminance_delta"),
            )
            for index in range(len(crop_metrics))
        ]
    return report


def build_rendered_layer_mask_report(
    config: RenderedLayerMaskConfig,
    *,
    qgis_renderer: Callable[..., None] = render_qgis_vector_in_subprocess,
    diff_builder: Callable[..., ImageMetrics | None] = build_image_diff,
) -> dict[str, object]:
    manifest_path = config.baseline_manifest.expanduser().resolve()
    manifest = load_json_object(manifest_path)
    camera = camera_from_manifest(manifest)
    paths = build_rendered_layer_mask_paths(
        output_root=config.output_root.expanduser().resolve(),
        camera_name=REPORT_CAMERA_DIRECTORY,
        now=config.now,
    )
    paths.run_dir.mkdir(parents=True, exist_ok=True)
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
    crop_boxes = config.crop_boxes
    baseline_crop_metrics = [
        image_delta_metrics(
            reference_path=browser_reference_path,
            candidate_path=baseline_qgis_path,
            crop_box=crop_box,
        )
        for crop_box in crop_boxes
    ]
    variant_inputs: list[RenderedLayerMaskVariant] = []
    control_variant_name = "qgis-rerender-control"
    if config.include_rerender_control:
        if any(variant.name == control_variant_name for variant in config.variants):
            raise ValueError(f"Variant name '{control_variant_name}' is reserved for the rerender control.")
        variant_inputs.append(RenderedLayerMaskVariant(control_variant_name, ()))
    variant_inputs.extend(config.variants)

    variants: list[dict[str, object]] = []
    control_qgis_path: Path | None = None
    control_crop_metrics: Sequence[Mapping[str, object]] = ()
    context = RenderedLayerMaskContext(
        run_dir=paths.run_dir,
        camera=camera,
        token=config.token,
        base_style=base_style,
        browser_reference_path=browser_reference_path,
        baseline_qgis_path=baseline_qgis_path,
        baseline_metrics=baseline_metrics,
        baseline_crop_metrics=baseline_crop_metrics,
        crop_boxes=crop_boxes,
        qgis_renderer=qgis_renderer,
        diff_builder=diff_builder,
    )
    for variant in variant_inputs:
        variant_report = _variant_report(
            variant=variant,
            context=context,
            control_qgis_path=control_qgis_path,
            control_crop_metrics=control_crop_metrics,
        )
        if variant.name == control_variant_name:
            variant_report["is_rerender_control"] = True
            control_qgis_path = _variant_directory(paths.run_dir, variant.name) / QGIS_RENDER_OUTPUT
            control_crop_metrics = (
                variant_report.get("crop_metrics")
                if isinstance(variant_report.get("crop_metrics"), list)
                else ()
            )
        variants.append(variant_report)
    report: dict[str, object] = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "camera": dataclasses.asdict(camera),
        "rerender_control_variant": control_variant_name if config.include_rerender_control else None,
        "inputs": {
            "baseline_manifest": _repo_relative(manifest_path),
            "browser_reference": _repo_relative(browser_reference_path),
            "baseline_qgis": _repo_relative(baseline_qgis_path),
            "qgis_preprocessed_style": _repo_relative(qgis_style_path),
        },
        "baseline": {
            "metrics": baseline_metrics,
            "crop_metrics": baseline_crop_metrics,
        },
        "crop_boxes": [list(crop_box) for crop_box in crop_boxes],
        "variants": variants,
    }
    # The output path uses the configured output root, a constant camera directory,
    # and timestamp only; manifest data is serialized as report content, not a path.
    paths.summary_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")  # NOSONAR
    paths.summary_md.write_text(render_markdown_summary(report), encoding="utf-8")
    return report


def _format_number(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.9f}"
    if value is None:
        return ""
    return str(value)


def _crop_delta(variant: Mapping[str, object], *, crop_index: int, key: str) -> object:
    crop_deltas = variant.get("crop_delta_vs_baseline")
    if not isinstance(crop_deltas, list) or crop_index >= len(crop_deltas):
        return None
    crop_delta = crop_deltas[crop_index]
    if not isinstance(crop_delta, Mapping):
        return None
    return crop_delta.get(key)


def _has_control_adjusted_movement(variant: Mapping[str, object]) -> bool:
    metrics = variant.get("qgis_movement_vs_rerender_control_metrics")
    if not isinstance(metrics, Mapping):
        return bool(variant.get("render_changed"))
    for key in ("changed_pixel_count", "mean_absolute_channel_delta", "rms_channel_delta"):
        value = metrics.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return True
    return False


def _mapping_value(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _list_of_mappings(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _summary_header_lines(report: Mapping[str, object]) -> list[str]:
    camera = _mapping_value(report.get("camera"))
    inputs = _mapping_value(report.get("inputs"))
    lines = [
        "# Mapbox Outdoors rendered-layer mask probe",
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
    delta = _mapping_value(row.get("metric_delta_vs_baseline"))
    control_movement = _mapping_value(row.get("qgis_movement_vs_rerender_control_metrics"))
    return (
        f"| `{row.get('name')}` | {len(row.get('target_layer_ids') or [])} | "
        f"{'yes' if row.get('render_changed') else 'no'} | "
        f"{_format_number(metrics.get('normalized_mean_absolute_channel_delta'))} | "
        f"{_format_number(metrics.get('normalized_rms_channel_delta'))} | "
        f"{_format_number(delta.get('normalized_mean_absolute_channel_delta'))} | "
        f"{_format_number(delta.get('normalized_rms_channel_delta'))} | "
        f"{_format_number(control_movement.get('normalized_mean_absolute_channel_delta'))} | "
        f"{_format_number(control_movement.get('normalized_rms_channel_delta'))} |"
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
        "| Variant | Target layers | Render changed | Mean abs delta | RMS delta | Mean delta vs baseline | RMS delta vs baseline | QGIS mean vs control | QGIS RMS vs control |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        (
            "| `baseline` |  |  | "
            f"{_format_number(baseline_metrics.get('normalized_mean_absolute_channel_delta'))} | "
            f"{_format_number(baseline_metrics.get('normalized_rms_channel_delta'))} |  |  |  |  |"
        ),
        *[_whole_image_row(row) for row in variant_rows],
    ]


def _control_crop_delta(row: Mapping[str, object], *, crop_index: int) -> Mapping[str, object]:
    control_crop_delta = row.get("crop_delta_vs_rerender_control")
    if not isinstance(control_crop_delta, list) or crop_index >= len(control_crop_delta):
        return {}
    return _mapping_value(control_crop_delta[crop_index])


def _crop_box_label(crop_boxes: Sequence[object], *, crop_index: int) -> str:
    if crop_index >= len(crop_boxes):
        return ""
    crop_box = crop_boxes[crop_index]
    return f"`{crop_box}`"


def _crop_movement_row(row: Mapping[str, object], *, crop_index: int, crop_boxes: Sequence[object]) -> str:
    control_crop_delta = _control_crop_delta(row, crop_index=crop_index)
    return (
        f"| `{row.get('name')}` | {crop_index + 1} | {_crop_box_label(crop_boxes, crop_index=crop_index)} | "
        f"{_format_number(_crop_delta(row, crop_index=crop_index, key='mean_absolute_channel_delta'))} | "
        f"{_format_number(_crop_delta(row, crop_index=crop_index, key='rms_channel_delta'))} | "
        f"{_format_number(_crop_delta(row, crop_index=crop_index, key='mean_luminance_delta'))} | "
        f"{_format_number(control_crop_delta.get('mean_absolute_channel_delta'))} | "
        f"{_format_number(control_crop_delta.get('rms_channel_delta'))} |"
    )


def _crop_count(variant_rows: Sequence[Mapping[str, object]], crop_boxes: Sequence[object]) -> int:
    counts = [len(crop_boxes)]
    for row in variant_rows:
        crop_deltas = row.get("crop_delta_vs_baseline")
        if isinstance(crop_deltas, list):
            counts.append(len(crop_deltas))
        control_crop_deltas = row.get("crop_delta_vs_rerender_control")
        if isinstance(control_crop_deltas, list):
            counts.append(len(control_crop_deltas))
    return max(counts)


def _crop_movement_lines(
    variant_rows: Sequence[Mapping[str, object]],
    crop_boxes: Sequence[object] = (),
) -> list[str]:
    crop_count = _crop_count(variant_rows, crop_boxes)
    rows = [
        _crop_movement_row(row, crop_index=crop_index, crop_boxes=crop_boxes)
        for crop_index in range(crop_count)
        for row in variant_rows
    ]
    return [
        "",
        "## Crop movement",
        "",
        "| Variant | Crop | Box | Mean abs delta vs baseline | RMS delta vs baseline | Luminance delta vs baseline | Mean abs delta vs control | RMS delta vs control |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |",
        *rows,
    ]


def _extend_crop_movement_lines(
    lines: list[str],
    *,
    report: Mapping[str, object],
    variant_rows: Sequence[Mapping[str, object]],
) -> None:
    crop_boxes = report.get("crop_boxes")
    if isinstance(crop_boxes, list) and crop_boxes:
        lines.extend(_crop_movement_lines(variant_rows, crop_boxes))


def _read_lines(
    *,
    variant_rows: Sequence[Mapping[str, object]],
    control_name: object,
) -> list[str]:
    moving = [
        f"`{row.get('name')}`"
        for row in variant_rows
        if row.get("name") != control_name and _has_control_adjusted_movement(row)
    ]
    return [
        "",
        "## Read",
        "",
        f"- Control-adjusted render-moving variants: {', '.join(moving) if moving else 'none'}.",
        "- Use the rerender-control columns to separate style-mask movement from QGIS rerender noise.",
        "- This is diagnostic evidence only; use it to prove rendered-pixel ownership before changing production paint.",
        "",
    ]


def render_markdown_summary(report: Mapping[str, object]) -> str:
    variants = report.get("variants")
    variant_rows = _list_of_mappings(variants)
    baseline_metrics = _mapping_value(_mapping_value(report.get("baseline")).get("metrics"))
    lines = _summary_header_lines(report)
    lines.extend(_whole_image_lines(baseline_metrics=baseline_metrics, variant_rows=variant_rows))
    _extend_crop_movement_lines(lines, report=report, variant_rows=variant_rows)
    lines.extend(_read_lines(variant_rows=variant_rows, control_name=report.get("rerender_control_variant")))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Render QGIS-only transparent layer masks from an existing Mapbox Outdoors "
            "comparison manifest and compare each variant with the baseline artifacts."
        ),
    )
    parser.add_argument(
        "--baseline-manifest",
        required=True,
        type=Path,
        help="Existing comparison manifest with Mapbox reference, QGIS render, and preprocessed style paths.",
    )
    parser.add_argument(
        "--variant",
        action="append",
        type=parse_variant_spec,
        required=True,
        help="Mask variant as NAME=LAYER_ID[,LAYER_ID...]. Repeat for multiple variants.",
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


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    token = resolve_mapbox_token(provided_token=args.mapbox_token)
    report = build_rendered_layer_mask_report(
        RenderedLayerMaskConfig(
            baseline_manifest=args.baseline_manifest,
            output_root=DEFAULT_OUTPUT_ROOT,
            variants=tuple(args.variant),
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
        print(f"Summary: {_repo_relative(newest / 'summary.md')}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
