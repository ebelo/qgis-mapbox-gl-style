from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PARENT = REPO_ROOT.parent
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "debug" / "mapbox-outdoors-label-settings"
DEFAULT_MAPBOX_STYLE_OWNER = "mapbox"
DEFAULT_MAPBOX_STYLE_ID = "outdoors-v12"
DEFAULT_QT_QPA_PLATFORM = "offscreen"
_BOOL_COUNT_KEY_MARKDOWN_VALUES = {"false": "no", "true": "yes"}
_SOURCE_LABEL_LAYOUT_PROPERTIES = (
    "symbol-placement",
    "symbol-spacing",
    "symbol-avoid-edges",
    "symbol-sort-key",
    "symbol-z-order",
    "icon-allow-overlap",
    "icon-anchor",
    "icon-ignore-placement",
    "icon-image",
    "icon-keep-upright",
    "icon-offset",
    "icon-optional",
    "icon-padding",
    "icon-pitch-alignment",
    "icon-rotate",
    "icon-rotation-alignment",
    "icon-size",
    "icon-text-fit",
    "icon-text-fit-padding",
    "text-field",
    "text-size",
    "text-font",
    "text-letter-spacing",
    "text-max-width",
    "text-max-angle",
    "text-allow-overlap",
    "text-ignore-placement",
    "text-optional",
    "text-keep-upright",
    "text-padding",
    "text-anchor",
    "text-justify",
    "text-offset",
    "text-radial-offset",
    "text-variable-anchor",
    "visibility",
)
_SOURCE_LABEL_PAINT_PROPERTIES = (
    "icon-color",
    "icon-halo-blur",
    "icon-halo-color",
    "icon-halo-width",
    "icon-opacity",
    "icon-translate",
    "icon-translate-anchor",
    "text-color",
    "text-halo-color",
    "text-halo-width",
    "text-halo-blur",
    "text-opacity",
    "text-translate",
)


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


def _style_layers(style_definition: dict[str, object]) -> list[dict[str, object]]:
    layers = style_definition.get("layers")
    if not isinstance(layers, list):
        return []
    return [layer for layer in layers if isinstance(layer, dict)]


def _style_layers_by_id(style_definition: dict[str, object]) -> dict[str, dict[str, object]]:
    return {
        str(layer.get("id")): layer
        for layer in _style_layers(style_definition)
        if isinstance(layer.get("id"), str)
    }


def _selected_section_properties(
    layer: dict[str, object],
    section: str,
    property_names: tuple[str, ...],
) -> dict[str, object]:
    values = layer.get(section)
    if not isinstance(values, dict):
        return {}
    return {property_name: values[property_name] for property_name in property_names if property_name in values}


def _source_label_layer_record(
    *,
    original_layer: dict[str, object],
    qfit_layer: dict[str, object] | None,
    style_name: str,
) -> dict[str, object]:
    return {
        "base_style_layer_id": str(original_layer.get("id") or ""),
        "style_name": style_name,
        "qfit_style_layer_id": str(qfit_layer.get("id") or "") if qfit_layer is not None else None,
        "source_layer": str(original_layer.get("source-layer") or ""),
        "minzoom": original_layer.get("minzoom"),
        "maxzoom": original_layer.get("maxzoom"),
        "qfit_minzoom": qfit_layer.get("minzoom") if qfit_layer is not None else None,
        "qfit_maxzoom": qfit_layer.get("maxzoom") if qfit_layer is not None else None,
        "filter": original_layer.get("filter"),
        "qfit_filter": qfit_layer.get("filter") if qfit_layer is not None else None,
        "layout": _selected_section_properties(
            original_layer,
            "layout",
            _SOURCE_LABEL_LAYOUT_PROPERTIES,
        ),
        "paint": _selected_section_properties(
            original_layer,
            "paint",
            _SOURCE_LABEL_PAINT_PROPERTIES,
        ),
        "qfit_layout": (
            _selected_section_properties(qfit_layer, "layout", _SOURCE_LABEL_LAYOUT_PROPERTIES)
            if qfit_layer is not None
            else {}
        ),
        "qfit_paint": (
            _selected_section_properties(qfit_layer, "paint", _SOURCE_LABEL_PAINT_PROPERTIES)
            if qfit_layer is not None
            else {}
        ),
    }


def source_label_layer_records(
    original_style: dict[str, object],
    qfit_style: dict[str, object],
    label_records: list[dict[str, object]],
) -> list[dict[str, object]]:
    _ensure_package_parent_on_path()
    from qfit.mapbox_config import base_mapbox_style_layer_id_for_qfit

    original_layers_by_id = _style_layers_by_id(original_style)
    qfit_layers = _style_layers(qfit_style)
    qfit_layers_by_id = _style_layers_by_id(qfit_style)
    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for label_record in label_records:
        style_name = str(label_record.get("style_name") or "")
        base_style_layer_id = str(label_record.get("base_style_layer_id") or "")
        if not base_style_layer_id:
            base_style_layer_id = base_mapbox_style_layer_id_for_qfit(style_name)
        key = (base_style_layer_id, style_name)
        if key in seen:
            continue
        seen.add(key)

        original_layer = original_layers_by_id.get(base_style_layer_id)
        if original_layer is None:
            continue
        qfit_layer = qfit_layers_by_id.get(style_name) or qfit_layers_by_id.get(base_style_layer_id)
        if qfit_layer is None:
            qfit_layer = next(
                (
                    layer
                    for layer in qfit_layers
                    if base_mapbox_style_layer_id_for_qfit(str(layer.get("id") or "")) == base_style_layer_id
                ),
                None,
            )
        rows.append(
            _source_label_layer_record(
                original_layer=original_layer,
                qfit_layer=qfit_layer,
                style_name=style_name,
            )
        )
    return sorted(rows, key=lambda row: (str(row.get("base_style_layer_id") or ""), str(row.get("style_name") or "")))


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
        "geometry_type": _enum_name(_method_value(style, "geometryType")),
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
        "geometry_generator": _settings_value(settings, "geometryGenerator"),
        "geometry_generator_enabled": _settings_value(settings, "geometryGeneratorEnabled"),
        "geometry_generator_type": _enum_name(_settings_value(settings, "geometryGeneratorType")),
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


def _summary_key(value: object) -> str:
    if value is None:
        return "(missing)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, list):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return str(value)


def _sorted_count_map(values: Iterable[object]) -> dict[str, int]:
    counts = Counter(_summary_key(value) for value in values)
    return {key: counts[key] for key in sorted(counts, key=lambda item: (-counts[item], item))}


def _zoom_range_value(minzoom: object, maxzoom: object, formatter: Callable[[object], str]) -> str:
    if minzoom is None and maxzoom is None:
        return "all"
    if maxzoom is None:
        return f"{formatter(minzoom)}+"
    if minzoom is None:
        return f"<{formatter(maxzoom)}"
    return f"{formatter(minzoom)} to {formatter(maxzoom)}"


def _zoom_range_key(minzoom: object, maxzoom: object) -> str:
    return _zoom_range_value(minzoom, maxzoom, _summary_key)


def _label_style_summary_rows(records: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in records:
        if not isinstance(record, dict):
            continue
        base_layer = str(record.get("base_style_layer_id") or record.get("style_name") or "")
        if base_layer:
            grouped[base_layer].append(record)

    rows: list[dict[str, object]] = []
    for base_layer, layer_records in grouped.items():
        rows.append(
            {
                "base_style_layer_id": base_layer,
                "count": len(layer_records),
                "source_layers": _sorted_count_map(row.get("source_layer") for row in layer_records),
                "geometry_types": _sorted_count_map(row.get("geometry_type") for row in layer_records),
                "priorities": _sorted_count_map(row.get("priority") for row in layer_records),
                "placements": _sorted_count_map(row.get("placement") for row in layer_records),
                "repeat_distances": _sorted_count_map(row.get("repeat_distance") for row in layer_records),
                "display_all": _sorted_count_map(row.get("display_all") for row in layer_records),
                "obstacle": _sorted_count_map(row.get("obstacle") for row in layer_records),
                "label_per_part": _sorted_count_map(row.get("label_per_part") for row in layer_records),
                "merge_lines": _sorted_count_map(row.get("merge_lines") for row in layer_records),
            }
        )
    return sorted(rows, key=lambda row: (-int(row["count"]), str(row["base_style_layer_id"])))


def _section_control(row: dict[str, object], section: str, key: str) -> object:
    values = row.get(section)
    return values.get(key) if isinstance(values, dict) else None


def _is_line_label_source_row(row: dict[str, object]) -> bool:
    return (
        _section_control(row, "qfit_layout", "symbol-placement") == "line"
        and _section_control(row, "qfit_layout", "icon-image") is None
    )


def _line_label_record_for_repeat(row: dict[str, object]) -> bool:
    placement = row.get("placement")
    return row.get("geometry_type") == "Line" or placement in {"Curved", "Line", "Horizontal"}


def _base_style_layer_id(row: dict[str, object]) -> str:
    return str(row.get("base_style_layer_id") or row.get("style_name") or "")


def _deduplicated_label_records(records: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    deduplicated: list[dict[str, object]] = []
    seen_record_ids: set[int] = set()
    for record in records:
        record_id = id(record)
        if record_id in seen_record_ids:
            continue
        seen_record_ids.add(record_id)
        deduplicated.append(record)
    return deduplicated


def _label_records_by_style(label_records: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    labels_by_style: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in label_records:
        style_name = str(record.get("style_name") or "")
        if style_name:
            labels_by_style[style_name].append(record)
    return labels_by_style


def _matched_label_records_for_source_row(
    row: dict[str, object],
    labels_by_style: dict[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    style_keys = {str(row.get("style_name") or ""), str(row.get("qfit_style_layer_id") or "")}
    return _deduplicated_label_records(
        record
        for style_key in style_keys
        for record in labels_by_style.get(style_key, [])
    )


def _line_label_source_groups(
    source_label_layers: list[dict[str, object]],
    labels_by_style: dict[str, list[dict[str, object]]],
) -> dict[str, list[tuple[dict[str, object], list[dict[str, object]]]]]:
    grouped: dict[str, list[tuple[dict[str, object], list[dict[str, object]]]]] = defaultdict(list)
    for row in source_label_layers:
        if isinstance(row, dict) and _is_line_label_source_row(row):
            base_layer = _base_style_layer_id(row)
            if base_layer:
                grouped[base_layer].append((row, _matched_label_records_for_source_row(row, labels_by_style)))
    return grouped


def _repeat_label_records(
    grouped_rows: list[tuple[dict[str, object], list[dict[str, object]]]],
) -> list[dict[str, object]]:
    return _deduplicated_label_records(
        record
        for _row, labels in grouped_rows
        for record in labels
        if _line_label_record_for_repeat(record)
    )


def _line_label_repeat_spacing_row(
    base_layer: str,
    source_rows: list[dict[str, object]],
    line_label_rows: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "base_style_layer_id": base_layer,
        "source_label_rows": len(source_rows),
        "converted_line_label_styles": len(line_label_rows),
        "missing_qfit_symbol_spacing": sum(
            1
            for row in source_rows
            if _section_control(row, "qfit_layout", "symbol-spacing") is None
        ),
        "source_symbol_spacings": _sorted_count_map(
            _section_control(row, "layout", "symbol-spacing") for row in source_rows
        ),
        "qfit_symbol_spacings": _sorted_count_map(
            _section_control(row, "qfit_layout", "symbol-spacing") for row in source_rows
        ),
        "repeat_distances": _sorted_count_map(row.get("repeat_distance") for row in line_label_rows),
        "placements": _sorted_count_map(row.get("placement") for row in line_label_rows),
        "style_names": _sorted_count_map(row.get("style_name") for row in source_rows),
        "zero_repeat_distance_count": sum(1 for row in line_label_rows if row.get("repeat_distance") == 0),
    }


def _line_label_repeat_spacing_rows(
    source_label_layers: list[dict[str, object]],
    label_records: list[dict[str, object]],
) -> list[dict[str, object]]:
    grouped = _line_label_source_groups(source_label_layers, _label_records_by_style(label_records))
    rows = []
    for base_layer, grouped_rows in grouped.items():
        source_rows = [row for row, _labels in grouped_rows]
        rows.append(_line_label_repeat_spacing_row(base_layer, source_rows, _repeat_label_records(grouped_rows)))
    return sorted(
        rows,
        key=lambda row: (
            -int(row["zero_repeat_distance_count"]),
            -int(row["missing_qfit_symbol_spacing"]),
            -int(row["converted_line_label_styles"]),
            str(row["base_style_layer_id"]),
        ),
    )


def _source_label_fanout_summary_rows(
    source_label_layers: list[dict[str, object]],
    label_records: list[dict[str, object]],
) -> list[dict[str, object]]:
    source_by_base: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in source_label_layers:
        if not isinstance(row, dict):
            continue
        base_layer = str(row.get("base_style_layer_id") or row.get("style_name") or "")
        if base_layer:
            source_by_base[base_layer].append(row)

    labels_by_base: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in label_records:
        if not isinstance(record, dict):
            continue
        base_layer = str(record.get("base_style_layer_id") or record.get("style_name") or "")
        if base_layer:
            labels_by_base[base_layer].append(record)

    rows: list[dict[str, object]] = []
    for base_layer in source_by_base.keys() | labels_by_base.keys():
        source_rows = source_by_base.get(base_layer, [])
        label_rows = labels_by_base.get(base_layer, [])
        qfit_layer_ids = {
            str(row.get("qfit_style_layer_id"))
            for row in source_rows
            if row.get("qfit_style_layer_id") is not None
        }
        rows.append(
            {
                "base_style_layer_id": base_layer,
                "source_label_rows": len(source_rows),
                "converted_label_styles": len(label_rows),
                "qfit_layer_count": len(qfit_layer_ids),
                "source_layers": _sorted_count_map(row.get("source_layer") for row in source_rows),
                "source_zooms": _sorted_count_map(
                    _zoom_range_key(row.get("minzoom"), row.get("maxzoom"))
                    for row in source_rows
                ),
                "qfit_zooms": _sorted_count_map(
                    _zoom_range_key(row.get("qfit_minzoom"), row.get("qfit_maxzoom"))
                    if row.get("qfit_style_layer_id") is not None
                    else "(missing)"
                    for row in source_rows
                ),
                "field_names": _sorted_count_map(row.get("field_name") for row in label_rows),
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            -int(row["converted_label_styles"]),
            -int(row["source_label_rows"]),
            str(row["base_style_layer_id"]),
        ),
    )


def _section_key_names(value: object) -> tuple[str, ...]:
    if not isinstance(value, dict):
        return ()
    return tuple(str(key) for key in value)


def _missing_section_keys(row: dict[str, object], source_section: str, qfit_section: str) -> tuple[str, ...]:
    source_keys = set(_section_key_names(row.get(source_section)))
    qfit_keys = set(_section_key_names(row.get(qfit_section)))
    return tuple(sorted(source_keys - qfit_keys))


def _section_key_values(rows: list[dict[str, object]], section: str) -> Iterable[str]:
    for row in rows:
        yield from _section_key_names(row.get(section))


def _missing_section_key_values(rows: list[dict[str, object]], source_section: str, qfit_section: str) -> Iterable[str]:
    for row in rows:
        yield from _missing_section_keys(row, source_section, qfit_section)


def _nested_contains_value(value: object, expected: object) -> bool:
    if value == expected:
        return True
    if isinstance(value, list):
        return any(_nested_contains_value(item, expected) for item in value)
    if isinstance(value, dict):
        return any(_nested_contains_value(item, expected) for item in value.values())
    return False


def _section_value(row: dict[str, object], section: str, key: str) -> object:
    source_section = row.get(section)
    source_values = source_section if isinstance(source_section, dict) else {}
    return source_values.get(key)


def _is_empty_icon_image_omission(row: dict[str, object], section: str, key: str) -> bool:
    return section == "layout" and key == "icon-image" and _section_value(row, section, key) == ""


def _is_settlement_sort_key_omission(row: dict[str, object], section: str, key: str) -> bool:
    base_layer = str(row.get("base_style_layer_id") or "")
    return (
        section == "layout"
        and key == "symbol-sort-key"
        and base_layer in {"settlement-major-label", "settlement-minor-label"}
        and _section_value(row, section, key) == ["get", "symbolrank"]
    )


def _source_icon_image(row: dict[str, object]) -> object:
    source_layout = row.get("layout")
    return source_layout.get("icon-image") if isinstance(source_layout, dict) else None


def _is_icon_opacity_without_qgis_icon(row: dict[str, object]) -> bool:
    qfit_layout = row.get("qfit_layout")
    return isinstance(qfit_layout, dict) and "icon-image" not in qfit_layout and _source_icon_image(row) in (None, "")


def _is_icon_visibility_split(row: dict[str, object]) -> bool:
    style_id = str(row.get("qfit_style_layer_id") or row.get("style_name") or "")
    return style_id.endswith(("-icon", "-text")) and _nested_contains_value(row.get("qfit_filter"), "sizerank")


def _is_text_split_icon_image_omission(row: dict[str, object], section: str, key: str) -> bool:
    style_id = str(row.get("qfit_style_layer_id") or row.get("style_name") or "")
    return (
        section == "layout"
        and key == "icon-image"
        and style_id.endswith("-text")
        and _nested_contains_value(row.get("qfit_filter"), "sizerank")
    )


def _zoom_step_outputs_at_or_before(value: object, zoom: float, expected: object) -> bool:
    if not isinstance(value, list) or len(value) < 4 or value[0] != "step" or value[1] != ["zoom"]:
        return False
    output = value[2]
    for index in range(3, len(value) - 1, 2):
        stop = value[index]
        if isinstance(stop, (int, float)) and stop <= zoom:
            output = value[index + 1]
    return output == expected


def _is_settlement_zoom_empty_icon_omission(row: dict[str, object], section: str, key: str) -> bool:
    if section != "layout" or key != "icon-image":
        return False
    base_layer = str(row.get("base_style_layer_id") or row.get("style_name") or "")
    if base_layer not in {"settlement-major-label", "settlement-minor-label"}:
        return False
    qfit_minzoom = row.get("qfit_minzoom")
    return isinstance(qfit_minzoom, (int, float)) and _zoom_step_outputs_at_or_before(
        _source_icon_image(row), qfit_minzoom, ""
    )


def _known_missing_control_reason(row: dict[str, object], section: str, key: str) -> str | None:
    if _is_empty_icon_image_omission(row, section, key):
        return "empty icon-image removed"
    if _is_settlement_sort_key_omission(row, section, key):
        return "settlement symbol-sort-key encoded by qfit split"
    if _is_text_split_icon_image_omission(row, section, key):
        return "icon-image encoded by label visibility split"
    if _is_settlement_zoom_empty_icon_omission(row, section, key):
        return "settlement icon-image empty at qfit zoom split"
    if section == "paint" and key == "icon-opacity":
        if _is_icon_opacity_without_qgis_icon(row):
            return "icon-opacity removed with no QGIS icon"
        if _is_icon_visibility_split(row):
            return "icon-opacity encoded by label visibility split"
    return None


def _known_missing_control_values(
    rows: list[dict[str, object]],
    source_section: str,
    qfit_section: str,
) -> Iterable[str]:
    for row in rows:
        for key in _missing_section_keys(row, source_section, qfit_section):
            if _known_missing_control_reason(row, source_section, key) is not None:
                yield f"{source_section}.{key}"


def _known_missing_control_reason_values(
    rows: list[dict[str, object]],
    source_section: str,
    qfit_section: str,
) -> Iterable[str]:
    for row in rows:
        for key in _missing_section_keys(row, source_section, qfit_section):
            reason = _known_missing_control_reason(row, source_section, key)
            if reason is not None:
                yield reason


def _unresolved_missing_control_values(
    rows: list[dict[str, object]],
    source_section: str,
    qfit_section: str,
) -> Iterable[str]:
    for row in rows:
        for key in _missing_section_keys(row, source_section, qfit_section):
            if _known_missing_control_reason(row, source_section, key) is None:
                yield f"{source_section}.{key}"


def _source_label_control_summary_rows(source_label_layers: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in source_label_layers:
        if not isinstance(row, dict):
            continue
        base_layer = str(row.get("base_style_layer_id") or row.get("style_name") or "")
        if base_layer:
            grouped[base_layer].append(row)

    rows: list[dict[str, object]] = []
    for base_layer, source_rows in grouped.items():
        missing_layout = _sorted_count_map(_missing_section_key_values(source_rows, "layout", "qfit_layout"))
        missing_paint = _sorted_count_map(_missing_section_key_values(source_rows, "paint", "qfit_paint"))
        rows.append(
            {
                "base_style_layer_id": base_layer,
                "source_label_rows": len(source_rows),
                "missing_control_count": sum(missing_layout.values()) + sum(missing_paint.values()),
                "source_layout_controls": _sorted_count_map(_section_key_values(source_rows, "layout")),
                "qfit_layout_controls": _sorted_count_map(_section_key_values(source_rows, "qfit_layout")),
                "missing_layout_controls": missing_layout,
                "source_paint_controls": _sorted_count_map(_section_key_values(source_rows, "paint")),
                "qfit_paint_controls": _sorted_count_map(_section_key_values(source_rows, "qfit_paint")),
                "missing_paint_controls": missing_paint,
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            -int(row["missing_control_count"]),
            -int(row["source_label_rows"]),
            str(row["base_style_layer_id"]),
        ),
    )


def _source_label_unresolved_control_summary_rows(source_label_layers: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in source_label_layers:
        if not isinstance(row, dict):
            continue
        base_layer = str(row.get("base_style_layer_id") or row.get("style_name") or "")
        if base_layer:
            grouped[base_layer].append(row)

    rows: list[dict[str, object]] = []
    for base_layer, source_rows in grouped.items():
        unresolved_controls = Counter(
            _unresolved_missing_control_values(source_rows, "layout", "qfit_layout")
        )
        unresolved_controls.update(_unresolved_missing_control_values(source_rows, "paint", "qfit_paint"))
        if not unresolved_controls:
            continue
        rows.append(
            {
                "base_style_layer_id": base_layer,
                "source_label_rows": len(source_rows),
                "unresolved_control_count": sum(unresolved_controls.values()),
                "unresolved_controls": dict(sorted(unresolved_controls.items(), key=lambda item: (-item[1], item[0]))),
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            -int(row["unresolved_control_count"]),
            -int(row["source_label_rows"]),
            str(row["base_style_layer_id"]),
        ),
    )


def _source_label_control_omission_summary_rows(source_label_layers: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in source_label_layers:
        if not isinstance(row, dict):
            continue
        base_layer = str(row.get("base_style_layer_id") or row.get("style_name") or "")
        if base_layer:
            grouped[base_layer].append(row)

    rows: list[dict[str, object]] = []
    for base_layer, source_rows in grouped.items():
        omitted_controls = Counter(
            _known_missing_control_values(source_rows, "layout", "qfit_layout")
        )
        omitted_controls.update(_known_missing_control_values(source_rows, "paint", "qfit_paint"))
        if not omitted_controls:
            continue
        rows.append(
            {
                "base_style_layer_id": base_layer,
                "source_label_rows": len(source_rows),
                "omitted_control_count": sum(omitted_controls.values()),
                "omitted_controls": dict(sorted(omitted_controls.items(), key=lambda item: (-item[1], item[0]))),
                "omission_reasons": _sorted_count_map(
                    tuple(_known_missing_control_reason_values(source_rows, "layout", "qfit_layout"))
                    + tuple(_known_missing_control_reason_values(source_rows, "paint", "qfit_paint"))
                ),
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            -int(row["omitted_control_count"]),
            -int(row["source_label_rows"]),
            str(row["base_style_layer_id"]),
        ),
    )


def _label_settings_report(
    *,
    config: LabelSettingsConfig,
    result: object,
    sprite_loaded: bool,
    sprite_count: int,
    records: list[dict[str, object]],
    source_label_layers: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    source_label_layer_rows = source_label_layers or []
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
        "label_style_summary_by_base_layer": _label_style_summary_rows(records),
        "line_label_repeat_spacing_by_base_layer": _line_label_repeat_spacing_rows(source_label_layer_rows, records),
        "source_label_fanout_by_base_layer": _source_label_fanout_summary_rows(source_label_layer_rows, records),
        "source_label_control_summary_by_base_layer": _source_label_control_summary_rows(source_label_layer_rows),
        "source_label_control_omission_summary_by_base_layer": _source_label_control_omission_summary_rows(
            source_label_layer_rows
        ),
        "source_label_unresolved_control_summary_by_base_layer": _source_label_unresolved_control_summary_rows(
            source_label_layer_rows
        ),
        "source_label_layer_count": len(source_label_layer_rows),
        "source_label_layers": source_label_layer_rows,
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
        source_label_layers = source_label_layer_records(original_style, qfit_style, records)
        return _label_settings_report(
            config=config,
            result=result,
            sprite_loaded=sprite_loaded,
            sprite_count=sprite_count,
            records=records,
            source_label_layers=source_label_layers,
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


def _geometry_generator_markdown_value(row: dict[str, object]) -> str:
    if row.get("geometry_generator_enabled") is False:
        return "no"
    generator = row.get("geometry_generator")
    if (
        row.get("geometry_generator_enabled") is None
        and row.get("geometry_generator_type") is None
        and (generator is None or generator == "")
    ):
        return "—"
    return _compound_markdown_value(
        row.get("geometry_generator_enabled"),
        row.get("geometry_generator_type"),
        row.get("geometry_generator"),
    )


def _json_markdown_value(value: object) -> str:
    if value is None or value == {} or value == []:
        return "—"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":")).replace("|", "\\|")
    return _markdown_value(value)


def _count_map_key_markdown_value(value: object) -> str:
    return _BOOL_COUNT_KEY_MARKDOWN_VALUES.get(str(value), _markdown_value(value))


def _count_map_markdown_value(value: object) -> str:
    if not isinstance(value, dict) or not value:
        return "—"
    return ", ".join(
        f"{_count_map_key_markdown_value(key)}={_markdown_value(count)}"
        for key, count in value.items()
    )


def _zoom_range_markdown_value(minzoom: object, maxzoom: object) -> str:
    return _zoom_range_value(minzoom, maxzoom, _markdown_value)


def _append_label_style_summary(lines: list[str], summary_rows: list[object]) -> None:
    if summary_rows:
        lines.extend(
            [
                "## Label style summary by base layer",
                "",
                "| Base layer | Count | Source layers | Geometry | Priorities | Placements | Repeat distances | Display all | Obstacle | Label/part | Merge lines |",
                "| --- | ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in summary_rows:
            if not isinstance(row, dict):
                continue
            lines.append(
                "| {base} | {count} | {source_layers} | {geometry} | {priorities} | {placements} | {repeat} | {display_all} | {obstacle} | {label_per_part} | {merge_lines} |".format(
                    base=_markdown_value(row.get("base_style_layer_id")),
                    count=_markdown_value(row.get("count")),
                    source_layers=_count_map_markdown_value(row.get("source_layers")),
                    geometry=_count_map_markdown_value(row.get("geometry_types")),
                    priorities=_count_map_markdown_value(row.get("priorities")),
                    placements=_count_map_markdown_value(row.get("placements")),
                    repeat=_count_map_markdown_value(row.get("repeat_distances")),
                    display_all=_count_map_markdown_value(row.get("display_all")),
                    obstacle=_count_map_markdown_value(row.get("obstacle")),
                    label_per_part=_count_map_markdown_value(row.get("label_per_part")),
                    merge_lines=_count_map_markdown_value(row.get("merge_lines")),
                )
            )
        lines.append("")


def _append_line_label_repeat_spacing_summary(lines: list[str], summary_rows: list[object]) -> None:
    if summary_rows:
        lines.extend(
            [
                "## Line label repeat spacing by base layer",
                "",
                "| Base layer | Source rows | Converted line styles | Missing QGIS symbol-spacing | Source symbol-spacing | QGIS symbol-spacing | QGIS repeat distances | QGIS placements | Styles |",
                "| --- | ---: | ---: | ---: | --- | --- | --- | --- | --- |",
            ]
        )
        for row in summary_rows:
            if not isinstance(row, dict):
                continue
            lines.append(
                "| {base} | {source_rows} | {converted} | {missing_spacing} | {source_spacing} | {qfit_spacing} | {repeat} | {placements} | {styles} |".format(
                    base=_markdown_value(row.get("base_style_layer_id")),
                    source_rows=_markdown_value(row.get("source_label_rows")),
                    converted=_markdown_value(row.get("converted_line_label_styles")),
                    missing_spacing=_markdown_value(row.get("missing_qfit_symbol_spacing")),
                    source_spacing=_count_map_markdown_value(row.get("source_symbol_spacings")),
                    qfit_spacing=_count_map_markdown_value(row.get("qfit_symbol_spacings")),
                    repeat=_count_map_markdown_value(row.get("repeat_distances")),
                    placements=_count_map_markdown_value(row.get("placements")),
                    styles=_count_map_markdown_value(row.get("style_names")),
                )
            )
        lines.append("")


def _append_source_label_fanout_summary(lines: list[str], summary_rows: list[object]) -> None:
    if summary_rows:
        lines.extend(
            [
                "## Source label fan-out by base layer",
                "",
                "| Base layer | Source rows | Converted styles | QGIS layers | Source layers | Source zooms | QGIS zooms | Fields |",
                "| --- | ---: | ---: | ---: | --- | --- | --- | --- |",
            ]
        )
        for row in summary_rows:
            if not isinstance(row, dict):
                continue
            lines.append(
                "| {base} | {source_rows} | {converted} | {qfit_layers} | {source_layers} | {source_zooms} | {qfit_zooms} | {fields} |".format(
                    base=_markdown_value(row.get("base_style_layer_id")),
                    source_rows=_markdown_value(row.get("source_label_rows")),
                    converted=_markdown_value(row.get("converted_label_styles")),
                    qfit_layers=_markdown_value(row.get("qfit_layer_count")),
                    source_layers=_count_map_markdown_value(row.get("source_layers")),
                    source_zooms=_count_map_markdown_value(row.get("source_zooms")),
                    qfit_zooms=_count_map_markdown_value(row.get("qfit_zooms")),
                    fields=_count_map_markdown_value(row.get("field_names")),
                )
            )
        lines.append("")


def _append_source_label_control_summary(lines: list[str], summary_rows: list[object]) -> None:
    if summary_rows:
        lines.extend(
            [
                "## Source label control coverage by base layer",
                "",
                "| Base layer | Source rows | Missing controls | Source layout | QGIS layout | Missing layout | Source paint | QGIS paint | Missing paint |",
                "| --- | ---: | ---: | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in summary_rows:
            if not isinstance(row, dict):
                continue
            lines.append(
                "| {base} | {source_rows} | {missing_count} | {source_layout} | {qfit_layout} | {missing_layout} | {source_paint} | {qfit_paint} | {missing_paint} |".format(
                    base=_markdown_value(row.get("base_style_layer_id")),
                    source_rows=_markdown_value(row.get("source_label_rows")),
                    missing_count=_markdown_value(row.get("missing_control_count")),
                    source_layout=_count_map_markdown_value(row.get("source_layout_controls")),
                    qfit_layout=_count_map_markdown_value(row.get("qfit_layout_controls")),
                    missing_layout=_count_map_markdown_value(row.get("missing_layout_controls")),
                    source_paint=_count_map_markdown_value(row.get("source_paint_controls")),
                    qfit_paint=_count_map_markdown_value(row.get("qfit_paint_controls")),
                    missing_paint=_count_map_markdown_value(row.get("missing_paint_controls")),
                )
            )
        lines.append("")


def _append_source_label_control_omission_summary(lines: list[str], summary_rows: list[object]) -> None:
    if summary_rows:
        lines.extend(
            [
                "## Known qfit label control omissions by base layer",
                "",
                "| Base layer | Source rows | Known omissions | Omitted controls | Reasons |",
                "| --- | ---: | ---: | --- | --- |",
            ]
        )
        for row in summary_rows:
            if not isinstance(row, dict):
                continue
            lines.append(
                "| {base} | {source_rows} | {omitted_count} | {omitted_controls} | {reasons} |".format(
                    base=_markdown_value(row.get("base_style_layer_id")),
                    source_rows=_markdown_value(row.get("source_label_rows")),
                    omitted_count=_markdown_value(row.get("omitted_control_count")),
                    omitted_controls=_count_map_markdown_value(row.get("omitted_controls")),
                    reasons=_count_map_markdown_value(row.get("omission_reasons")),
                )
            )
        lines.append("")


def _append_source_label_unresolved_control_summary(lines: list[str], summary_rows: list[object]) -> None:
    if summary_rows:
        lines.extend(
            [
                "## Unresolved label control gaps by base layer",
                "",
                "| Base layer | Source rows | Unresolved controls | Controls |",
                "| --- | ---: | ---: | --- |",
            ]
        )
        for row in summary_rows:
            if not isinstance(row, dict):
                continue
            lines.append(
                "| {base} | {source_rows} | {unresolved_count} | {unresolved_controls} |".format(
                    base=_markdown_value(row.get("base_style_layer_id")),
                    source_rows=_markdown_value(row.get("source_label_rows")),
                    unresolved_count=_markdown_value(row.get("unresolved_control_count")),
                    unresolved_controls=_count_map_markdown_value(row.get("unresolved_controls")),
                )
            )
        lines.append("")


def _append_converted_label_rows(lines: list[str], rows: list[object]) -> None:
    lines.extend(
        [
            "## Converted QGIS label styles",
            "",
            "| Base layer | Style | Source layer | Geometry | Field | Expr | Priority | Placement | Placement flags | Repeat distance | Repeat unit | Display all | Obstacle | Text size | Text color | Text opacity | Buffer | Buffer size | Buffer color | Buffer opacity | Label/part | Merge lines | Geometry generator | Curve angles | Overrun | Data-defined keys |",
            "| --- | --- | --- | --- | --- | --- | ---: | --- | ---: | ---: | --- | --- | --- | ---: | --- | ---: | --- | ---: | --- | ---: | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows:
        if not isinstance(row, dict):
            continue
        lines.append(
            "| {base} | {style} | {source} | {geometry} | {field} | {expr} | {priority} | {placement} | {placement_flags} | {repeat} | {unit} | {display_all} | {obstacle} | {text_size} | {text_color} | {text_opacity} | {buffer_enabled} | {buffer_size} | {buffer_color} | {buffer_opacity} | {label_per_part} | {merge_lines} | {generator} | {curve_angles} | {overrun} | {keys} |".format(
                base=_markdown_value(row.get("base_style_layer_id")),
                style=_markdown_value(row.get("style_name")),
                source=_markdown_value(row.get("source_layer")),
                geometry=_markdown_value(row.get("geometry_type")),
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
                generator=_geometry_generator_markdown_value(row),
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


def _append_source_label_rows(lines: list[str], report: dict[str, object], source_rows: list[object]) -> None:
    if source_rows:
        lines.extend(
            [
                "",
                "## Source Mapbox label controls",
                "",
                f"Source label layers: {report.get('source_label_layer_count', len(source_rows))}",
                "",
                "| Base layer | Style | QGIS layer | Source layer | Zoom | QGIS zoom | Filter | QGIS filter | Layout controls | Paint controls | QGIS layout | QGIS paint |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
    for row in source_rows:
        if not isinstance(row, dict):
            continue
        lines.append(
            "| {base} | {style} | {qfit_layer} | {source} | {zoom} | {qfit_zoom} | {filter} | {qfit_filter} | {layout} | {paint} | {qfit_layout} | {qfit_paint} |".format(
                base=_markdown_value(row.get("base_style_layer_id")),
                style=_markdown_value(row.get("style_name")),
                qfit_layer=_markdown_value(row.get("qfit_style_layer_id")),
                source=_markdown_value(row.get("source_layer")),
                zoom=_zoom_range_markdown_value(row.get("minzoom"), row.get("maxzoom")),
                qfit_zoom=(
                    _zoom_range_markdown_value(row.get("qfit_minzoom"), row.get("qfit_maxzoom"))
                    if row.get("qfit_style_layer_id") is not None
                    else "—"
                ),
                filter=_json_markdown_value(row.get("filter")),
                qfit_filter=_json_markdown_value(row.get("qfit_filter")),
                layout=_json_markdown_value(row.get("layout")),
                paint=_json_markdown_value(row.get("paint")),
                qfit_layout=_json_markdown_value(row.get("qfit_layout")),
                qfit_paint=_json_markdown_value(row.get("qfit_paint")),
            )
        )


def build_summary_markdown(report: dict[str, object]) -> str:
    labels = report.get("labels")
    rows = labels if isinstance(labels, list) else []
    label_summary = report.get("label_style_summary_by_base_layer")
    summary_rows = label_summary if isinstance(label_summary, list) else []
    line_repeat_summary = report.get("line_label_repeat_spacing_by_base_layer")
    line_repeat_rows = line_repeat_summary if isinstance(line_repeat_summary, list) else []
    source_fanout_summary = report.get("source_label_fanout_by_base_layer")
    source_fanout_rows = source_fanout_summary if isinstance(source_fanout_summary, list) else []
    source_control_summary = report.get("source_label_control_summary_by_base_layer")
    source_control_rows = source_control_summary if isinstance(source_control_summary, list) else []
    source_control_omission_summary = report.get("source_label_control_omission_summary_by_base_layer")
    source_control_omission_rows = (
        source_control_omission_summary if isinstance(source_control_omission_summary, list) else []
    )
    source_unresolved_control_summary = report.get("source_label_unresolved_control_summary_by_base_layer")
    source_unresolved_control_rows = (
        source_unresolved_control_summary if isinstance(source_unresolved_control_summary, list) else []
    )
    source_labels = report.get("source_label_layers")
    source_rows = source_labels if isinstance(source_labels, list) else []
    lines = [
        f"# Mapbox Outdoors QGIS label settings — {report.get('style_owner')}/{report.get('style_id')}",
        "",
        f"Generated: {report.get('generated')}",
        f"Converted label styles: {report.get('label_count', len(rows))}",
        f"Sprite context loaded: {_markdown_value(report.get('sprite_context_loaded'))}",
        f"Sprite definitions: {_markdown_value(report.get('sprite_definition_count'))}",
        "",
    ]
    _append_label_style_summary(lines, summary_rows)
    _append_line_label_repeat_spacing_summary(lines, line_repeat_rows)
    _append_source_label_fanout_summary(lines, source_fanout_rows)
    _append_source_label_control_summary(lines, source_control_rows)
    _append_source_label_control_omission_summary(lines, source_control_omission_rows)
    _append_source_label_unresolved_control_summary(lines, source_unresolved_control_rows)
    _append_converted_label_rows(lines, rows)
    _append_source_label_rows(lines, report, source_rows)
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
