from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PARENT = REPO_ROOT.parent
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "debug" / "mapbox-outdoors-label-settings"
DEFAULT_MAPBOX_STYLE_OWNER = "mapbox"
DEFAULT_MAPBOX_STYLE_ID = "outdoors-v12"
DEFAULT_QT_QPA_PLATFORM = "offscreen"


@dataclass(frozen=True)
class LabelSettingsPaths:
    run_dir: Path
    json_path: Path
    summary_path: Path


@dataclass(frozen=True)
class LabelSettingsConfig:
    token: str | None
    output_root: Path
    style_owner: str = DEFAULT_MAPBOX_STYLE_OWNER
    style_id: str = DEFAULT_MAPBOX_STYLE_ID
    style_json_path: Path | None = None
    include_sprite_context: bool = True
    now: dt.datetime | None = None


def _ensure_package_parent_on_path() -> None:
    package_parent = str(PACKAGE_PARENT)
    if package_parent not in sys.path:
        sys.path.insert(0, package_parent)


def _ensure_headless_qt_platform() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", DEFAULT_QT_QPA_PLATFORM)


def resolve_mapbox_token(*, provided_token: str | None, environ: dict[str, str] | None = None) -> str | None:
    env = os.environ if environ is None else environ
    return provided_token or env.get("MAPBOX_ACCESS_TOKEN") or env.get("QFIT_MAPBOX_ACCESS_TOKEN")


def load_style_definition(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected Mapbox style JSON object in {path}")
    return data


def _style_slug(style_owner: str, style_id: str) -> str:
    return f"{style_owner}-{style_id}".replace("/", "-")


def build_run_directory(
    *,
    output_root: Path,
    style_owner: str,
    style_id: str,
    now: dt.datetime | None = None,
) -> Path:
    timestamp = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    return output_root / _style_slug(style_owner, style_id) / timestamp.strftime("%Y%m%dT%H%M%SZ")


def build_label_settings_paths(run_dir: Path) -> LabelSettingsPaths:
    return LabelSettingsPaths(
        run_dir=run_dir,
        json_path=run_dir / "label-settings.json",
        summary_path=run_dir / "summary.md",
    )


def _method_value(obj: object, method_name: str) -> object:
    method = getattr(obj, method_name, None)
    if not callable(method):
        return None
    try:
        return method()
    except (AttributeError, RuntimeError, TypeError):
        return None


def _enum_name(value: object) -> object:
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    return str(value) if value is not None else None


def _settings_value(settings: object, name: str) -> object:
    try:
        value = getattr(settings, name)
    except (AttributeError, RuntimeError):
        return None
    return _enum_name(value) if name.endswith("Unit") or name == "placement" else value


def _color_name(value: object) -> object:
    name = _method_value(value, "name")
    return name if isinstance(name, str) else None


def _label_format_record(settings: object) -> dict[str, object]:
    text_format = _method_value(settings, "format")
    buffer = _method_value(text_format, "buffer") if text_format is not None else None
    text_color = _method_value(text_format, "color") if text_format is not None else None
    buffer_color = _method_value(buffer, "color") if buffer is not None else None
    return {
        "text_size": _method_value(text_format, "size") if text_format is not None else None,
        "text_size_unit": _enum_name(_method_value(text_format, "sizeUnit")) if text_format is not None else None,
        "text_color": _color_name(text_color),
        "text_opacity": _method_value(text_format, "opacity") if text_format is not None else None,
        "buffer_enabled": _method_value(buffer, "enabled") if buffer is not None else None,
        "buffer_size": _method_value(buffer, "size") if buffer is not None else None,
        "buffer_size_unit": _enum_name(_method_value(buffer, "sizeUnit")) if buffer is not None else None,
        "buffer_color": _color_name(buffer_color),
        "buffer_opacity": _method_value(buffer, "opacity") if buffer is not None else None,
    }


def _data_defined_property_keys(settings: object) -> list[object]:
    properties = _method_value(settings, "dataDefinedProperties")
    keys = _method_value(properties, "propertyKeys") if properties is not None else None
    if keys is None:
        return []
    try:
        return sorted(keys)
    except TypeError:
        return list(keys)


def label_settings_record(style: object, settings: object) -> dict[str, object]:
    _ensure_package_parent_on_path()
    from qfit.mapbox_config import base_mapbox_style_layer_id_for_qfit

    style_name = _method_value(style, "styleName")
    layer_name = _method_value(style, "layerName")
    style_name_text = style_name if isinstance(style_name, str) else ""
    return {
        "style_name": style_name_text,
        "base_style_layer_id": base_mapbox_style_layer_id_for_qfit(style_name_text),
        "source_layer": layer_name if isinstance(layer_name, str) else "",
        "field_name": _settings_value(settings, "fieldName"),
        "is_expression": _settings_value(settings, "isExpression"),
        "priority": _settings_value(settings, "priority"),
        "placement": _settings_value(settings, "placement"),
        "repeat_distance": _settings_value(settings, "repeatDistance"),
        "repeat_distance_unit": _settings_value(settings, "repeatDistanceUnit"),
        "display_all": _settings_value(settings, "displayAll"),
        "obstacle": _settings_value(settings, "obstacle"),
        "placement_flags": _settings_value(settings, "placementFlags"),
        "label_per_part": _settings_value(settings, "labelPerPart"),
        "merge_lines": _settings_value(settings, "mergeLines"),
        "max_curved_char_angle_in": _settings_value(settings, "maxCurvedCharAngleIn"),
        "max_curved_char_angle_out": _settings_value(settings, "maxCurvedCharAngleOut"),
        "overrun_distance": _settings_value(settings, "overrunDistance"),
        "overrun_distance_unit": _settings_value(settings, "overrunDistanceUnit"),
        **_label_format_record(settings),
        "data_defined_property_keys": _data_defined_property_keys(settings),
    }


def _iter_label_records(labeling: object) -> Iterable[dict[str, object]]:
    for style in _method_value(labeling, "styles") or []:
        settings = _method_value(style, "labelSettings")
        if settings is None:
            continue
        yield label_settings_record(style, settings)


def _apply_sprite_context(ctx: object, sprite_resources: object | None) -> bool:
    if sprite_resources is None:
        return False
    try:
        from qgis.PyQt.QtGui import QImage  # type: ignore[import-not-found]

        sprite_image = QImage()
        if not sprite_image.loadFromData(sprite_resources.image_bytes):
            return False
        argb_format = getattr(QImage, "Format_ARGB32", None)
        if argb_format is not None:
            sprite_image = sprite_image.convertToFormat(argb_format)
        ctx.setSprites(sprite_image, sprite_resources.definitions)
        return True
    except (AttributeError, ImportError, RuntimeError, TypeError):
        return False


def _ensure_qgis_application(qgs_application):
    app = qgs_application.instance()
    created_app = app is None
    if created_app:
        app = qgs_application([], False)
        app.initQgis()
    return app, created_app


def _load_original_style(config: LabelSettingsConfig, fetch_mapbox_style_definition) -> dict[str, object]:
    if config.style_json_path is not None:
        return load_style_definition(config.style_json_path)
    if not config.token:
        raise ValueError("A Mapbox token is required unless --style-json is provided.")
    return fetch_mapbox_style_definition(config.token, config.style_owner, config.style_id)


def _fetch_sprite_resources(config: LabelSettingsConfig, original_style: dict[str, object], fetch_mapbox_sprite_resources):
    if not config.include_sprite_context or not config.token:
        return None, 0
    try:
        resources = fetch_mapbox_sprite_resources(
            config.token,
            config.style_owner,
            config.style_id,
            sprite_url=original_style.get("sprite"),
        )
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        return None, 0
    return resources, len(resources.definitions)


def _convert_style_to_labeling(qfit_style: dict[str, object], sprite_resources: object | None, qgis_modules) -> tuple[object, object, bool]:
    conversion_context_cls, converter_cls, qgis_api = qgis_modules
    ctx = conversion_context_cls()
    ctx.setTargetUnit(qgis_api.RenderUnit.Millimeters)
    ctx.setPixelSizeConversionFactor(25.4 / 96.0)
    sprite_loaded = _apply_sprite_context(ctx, sprite_resources)

    converter = converter_cls()
    result = converter.convert(qfit_style, ctx)
    success_value = getattr(converter_cls, "Success", None)
    if success_value is not None and result != success_value:
        raise RuntimeError(f"QGIS Mapbox style conversion failed: {result}")
    return result, converter.labeling(), sprite_loaded


def _postprocessed_label_records(labeling: object | None, apply_label_priority) -> list[dict[str, object]]:
    if labeling is None:
        return []
    apply_label_priority(labeling)
    return sorted(
        _iter_label_records(labeling),
        key=lambda row: (str(row.get("base_style_layer_id") or ""), str(row.get("style_name") or "")),
    )


def _label_settings_report(
    *,
    config: LabelSettingsConfig,
    result: object,
    sprite_loaded: bool,
    sprite_count: int,
    records: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "style_owner": config.style_owner,
        "style_id": config.style_id,
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(),
        "qgis_converter_result": result,
        "sprite_context_requested": config.include_sprite_context,
        "sprite_context_loaded": sprite_loaded,
        "sprite_definition_count": sprite_count,
        "label_count": len(records),
        "labels": records,
    }


def collect_label_settings(config: LabelSettingsConfig) -> dict[str, object]:
    _ensure_package_parent_on_path()
    _ensure_headless_qt_platform()
    try:
        from qgis.core import (  # type: ignore[import-not-found]
            QgsApplication,
            QgsMapBoxGlStyleConversionContext,
            QgsMapBoxGlStyleConverter,
            Qgis,
        )
    except ImportError as exc:  # pragma: no cover - depends on optional PyQGIS runtime
        raise RuntimeError("QGIS label settings diagnostics require PyQGIS.") from exc

    from qfit.mapbox_config import (
        fetch_mapbox_sprite_resources,
        fetch_mapbox_style_definition,
        simplify_mapbox_style_expressions,
    )
    from qfit.visualization.infrastructure.background_map_service import apply_mapbox_label_priority

    app, created_app = _ensure_qgis_application(QgsApplication)
    try:
        original_style = _load_original_style(config, fetch_mapbox_style_definition)
        qfit_style = simplify_mapbox_style_expressions(original_style)
        sprite_resources, sprite_count = _fetch_sprite_resources(config, original_style, fetch_mapbox_sprite_resources)
        result, labeling, sprite_loaded = _convert_style_to_labeling(
            qfit_style,
            sprite_resources,
            (QgsMapBoxGlStyleConversionContext, QgsMapBoxGlStyleConverter, Qgis),
        )
        records = _postprocessed_label_records(labeling, apply_mapbox_label_priority)
        return _label_settings_report(
            config=config,
            result=result,
            sprite_loaded=sprite_loaded,
            sprite_count=sprite_count,
            records=records,
        )
    finally:
        if created_app:
            app.exitQgis()


def _markdown_value(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, list):
        return ", ".join(str(item).replace("|", "\\|") for item in value) if value else "—"
    return str(value).replace("|", "\\|")


def _compound_markdown_value(*values: object, separator: str = " ") -> str:
    rendered_values = [_markdown_value(value) for value in values]
    if all(value == "—" for value in rendered_values):
        return "—"
    return separator.join(rendered_values)


def build_summary_markdown(report: dict[str, object]) -> str:
    labels = report.get("labels")
    rows = labels if isinstance(labels, list) else []
    lines = [
        f"# Mapbox Outdoors QGIS label settings — {report.get('style_owner')}/{report.get('style_id')}",
        "",
        f"Generated: {report.get('generated')}",
        f"Converted label styles: {report.get('label_count', len(rows))}",
        f"Sprite context loaded: {_markdown_value(report.get('sprite_context_loaded'))}",
        f"Sprite definitions: {_markdown_value(report.get('sprite_definition_count'))}",
        "",
        "| Base layer | Style | Source layer | Field | Expr | Priority | Placement | Placement flags | Repeat distance | Repeat unit | Display all | Obstacle | Text size | Text color | Text opacity | Buffer | Buffer size | Buffer color | Buffer opacity | Label/part | Merge lines | Curve angles | Overrun | Data-defined keys |",
        "| --- | --- | --- | --- | --- | ---: | --- | ---: | ---: | --- | --- | --- | ---: | --- | ---: | --- | ---: | --- | ---: | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        if not isinstance(row, dict):
            continue
        lines.append(
            "| {base} | {style} | {source} | {field} | {expr} | {priority} | {placement} | {placement_flags} | {repeat} | {unit} | {display_all} | {obstacle} | {text_size} | {text_color} | {text_opacity} | {buffer_enabled} | {buffer_size} | {buffer_color} | {buffer_opacity} | {label_per_part} | {merge_lines} | {curve_angles} | {overrun} | {keys} |".format(
                base=_markdown_value(row.get("base_style_layer_id")),
                style=_markdown_value(row.get("style_name")),
                source=_markdown_value(row.get("source_layer")),
                field=_markdown_value(row.get("field_name")),
                expr=_markdown_value(row.get("is_expression")),
                priority=_markdown_value(row.get("priority")),
                placement=_markdown_value(row.get("placement")),
                placement_flags=_markdown_value(row.get("placement_flags")),
                repeat=_markdown_value(row.get("repeat_distance")),
                unit=_markdown_value(row.get("repeat_distance_unit")),
                display_all=_markdown_value(row.get("display_all")),
                obstacle=_markdown_value(row.get("obstacle")),
                text_size=_compound_markdown_value(row.get("text_size"), row.get("text_size_unit")),
                text_color=_markdown_value(row.get("text_color")),
                text_opacity=_markdown_value(row.get("text_opacity")),
                buffer_enabled=_markdown_value(row.get("buffer_enabled")),
                buffer_size=_compound_markdown_value(row.get("buffer_size"), row.get("buffer_size_unit")),
                buffer_color=_markdown_value(row.get("buffer_color")),
                buffer_opacity=_markdown_value(row.get("buffer_opacity")),
                label_per_part=_markdown_value(row.get("label_per_part")),
                merge_lines=_markdown_value(row.get("merge_lines")),
                curve_angles=_compound_markdown_value(
                    row.get("max_curved_char_angle_in"),
                    row.get("max_curved_char_angle_out"),
                    separator="/",
                ),
                overrun=_compound_markdown_value(
                    row.get("overrun_distance"),
                    row.get("overrun_distance_unit"),
                ),
                keys=_markdown_value(row.get("data_defined_property_keys")),
            )
        )
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, object], paths: LabelSettingsPaths) -> None:
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    paths.json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    paths.summary_path.write_text(build_summary_markdown(report), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate QGIS converted-label settings diagnostics.")
    parser.add_argument("--style-json", type=Path, help="Read an already downloaded Mapbox style JSON file.")
    parser.add_argument("--style-owner", default=DEFAULT_MAPBOX_STYLE_OWNER)
    parser.add_argument("--style-id", default=DEFAULT_MAPBOX_STYLE_ID)
    parser.add_argument("--mapbox-token", help="Mapbox token. Prefer MAPBOX_ACCESS_TOKEN or QFIT_MAPBOX_ACCESS_TOKEN.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--no-sprite-context", action="store_true", help="Do not attach Mapbox sprite resources.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    token = resolve_mapbox_token(provided_token=args.mapbox_token)
    config = LabelSettingsConfig(
        token=token,
        output_root=args.output_root,
        style_owner=args.style_owner,
        style_id=args.style_id,
        style_json_path=args.style_json,
        include_sprite_context=not args.no_sprite_context,
    )
    paths = build_label_settings_paths(
        build_run_directory(
            output_root=config.output_root,
            style_owner=config.style_owner,
            style_id=config.style_id,
            now=config.now,
        )
    )
    report = collect_label_settings(config)
    write_report(report, paths)
    print(paths.summary_path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
