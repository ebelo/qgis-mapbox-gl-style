from __future__ import annotations

import argparse
import copy
import datetime as dt
import html
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PARENT = REPO_ROOT.parent
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "debug" / "mapbox-outdoors-style-audit"
DEFAULT_MAPBOX_STYLE_OWNER = "mapbox"
DEFAULT_MAPBOX_STYLE_ID = "outdoors-v12"
_DEFAULT_OUTPUT_STYLE_SLUG = "mapbox-outdoors-v12"
_MARKDOWN_THREE_COLUMN_COUNT_SEPARATOR = "| --- | --- | ---: |"
_MARKDOWN_LAYER_GROUP_LABEL = "Layer group"
_MARKDOWN_MESSAGE_LABEL = "Message"
_MARKDOWN_LAYER_LABEL = "Layer"
_MARKDOWN_WARNING_DELTA_FROM_QFIT_LABEL = "Warning count delta from qfit preprocessing"
_EXPRESSION_PROBE_ZOOM = 12.0
_SPRITE_CONTEXT_PROBE_KEY = "with_sprite_context_probe"
_SPRITE_CONTEXT_DEFINITION_COUNT_KEY = "sprite_definition_count"
_SPRITE_CONTEXT_IMAGE_LOADED_KEY = "sprite_image_loaded"
_SCALAR_LINE_OPACITY_PROBE_KEY = "with_scalar_line_opacity_probe"
_LINE_OPACITY_EXPRESSION_COUNT_KEY = "line_opacity_expression_count_replaced"
_LINE_OPACITY_SCALARIZATION_ROWS_KEY = "line_opacity_scalarization_rows"
_LITERAL_LINE_DASHARRAY_PROBE_KEY = "with_literal_line_dasharray_probe"
_LINE_DASHARRAY_EXPRESSION_COUNT_KEY = "line_dasharray_expression_count_replaced"
_SCALAR_SYMBOL_SPACING_PROBE_KEY = "with_scalar_symbol_spacing_probe"
_SYMBOL_SPACING_EXPRESSION_COUNT_KEY = "symbol_spacing_expression_count_replaced"
_SYMBOL_SPACING_REPLACED_LAYERS_KEY = "symbol_spacing_replaced_layers"
_ROAD_TRAIL_HIERARCHY_CANDIDATES_KEY = "road_trail_hierarchy_candidates"
_ROAD_TRAIL_HIERARCHY_CANDIDATES_BY_SOURCE_LAYER_KEY = "road_trail_hierarchy_candidates_by_source_layer"
_ROAD_TRAIL_HIERARCHY_CANDIDATES_BY_TYPE_KEY = "road_trail_hierarchy_candidates_by_type"
_ROAD_TRAIL_HIERARCHY_SIMPLIFIED_BY_PROPERTY_KEY = "road_trail_hierarchy_simplified_by_property"
_ROAD_TRAIL_HIERARCHY_QGIS_DEPENDENT_BY_PROPERTY_KEY = "road_trail_hierarchy_qgis_dependent_by_property"
_ROAD_TRAIL_CONTROL_PROPERTIES_KEY = "road_trail_control_properties"
_TERRAIN_LANDCOVER_CANDIDATES_KEY = "terrain_landcover_palette_candidates"
_TERRAIN_LANDCOVER_CANDIDATES_BY_SOURCE_LAYER_KEY = "terrain_landcover_palette_candidates_by_source_layer"
_TERRAIN_LANDCOVER_CANDIDATES_BY_TYPE_KEY = "terrain_landcover_palette_candidates_by_type"
_TERRAIN_LANDCOVER_SIMPLIFIED_BY_PROPERTY_KEY = "terrain_landcover_palette_simplified_by_property"
_TERRAIN_LANDCOVER_QGIS_DEPENDENT_BY_PROPERTY_KEY = "terrain_landcover_palette_qgis_dependent_by_property"
_TERRAIN_LANDCOVER_CONTROL_PROPERTIES_KEY = "terrain_landcover_palette_control_properties"
_QFIT_SIMPLIFIED_CONTROL_PROPERTIES_KEY = "qfit_simplified_control_properties"
_QGIS_DEPENDENT_CONTROL_PROPERTIES_KEY = "qgis_dependent_control_properties"
_PROPERTY_REMOVAL_IMPACT_PROBE_KEY = "property_removal_impact_probe"
_FILTER_PARSE_SUPPORT_PROBE_KEY = "filter_expression_parse_support_probe"
_FILTER_PARSE_UNSUPPORTED_EXPRESSION_MESSAGE = "Skipping unsupported expression"
_FILTER_PARSE_UNSUPPORTED_EXPRESSION_PART_MESSAGE = f"{_FILTER_PARSE_UNSUPPORTED_EXPRESSION_MESSAGE} part"
_FILTER_PARSE_UNSUPPORTED_MESSAGE_ORDER = (
    _FILTER_PARSE_UNSUPPORTED_EXPRESSION_PART_MESSAGE,
    _FILTER_PARSE_UNSUPPORTED_EXPRESSION_MESSAGE,
)
_FILTER_PARSE_UNSUPPORTED_MESSAGES = frozenset(_FILTER_PARSE_UNSUPPORTED_MESSAGE_ORDER)
_FILTER_PARSE_PART_PARENT_OPERATORS = frozenset({"all", "any", "none"})
_NO_OPERATOR_SIGNATURE = "(none)"
_ALL_ZOOMS_BAND = "all zooms"
_LINE_DASHARRAY_PROPERTY = "paint.line-dasharray"
_LABEL_DENSITY_CONTROL_PROPERTIES = (
    "layout.icon-allow-overlap",
    "layout.icon-ignore-placement",
    "layout.icon-optional",
    "layout.symbol-sort-key",
    "layout.symbol-spacing",
    "layout.text-allow-overlap",
    "layout.text-anchor",
    "layout.text-field",
    "layout.text-ignore-placement",
    "layout.text-justify",
    "layout.text-max-angle",
    "layout.text-offset",
    "layout.text-optional",
    "layout.text-padding",
    "layout.text-radial-offset",
    "layout.text-size",
    "layout.text-variable-anchor",
)
_ROAD_TRAIL_HIERARCHY_LAYER_TYPES = frozenset({"fill", "line"})
_ROAD_TRAIL_HIERARCHY_CONTROL_PROPERTIES = (
    "layout.line-cap",
    "layout.line-join",
    "layout.line-miter-limit",
    "layout.line-round-limit",
    "layout.line-sort-key",
    "paint.fill-color",
    "paint.fill-opacity",
    "paint.fill-outline-color",
    "paint.fill-pattern",
    "paint.line-blur",
    "paint.line-color",
    _LINE_DASHARRAY_PROPERTY,
    "paint.line-gap-width",
    "paint.line-offset",
    "paint.line-opacity",
    "paint.line-pattern",
    "paint.line-translate",
    "paint.line-width",
)
_TERRAIN_LANDCOVER_LAYER_TYPES = frozenset({"fill", "line"})
_TERRAIN_LANDCOVER_CONTROL_PROPERTIES = (
    "layout.line-cap",
    "layout.line-join",
    "layout.line-sort-key",
    "paint.fill-antialias",
    "paint.fill-color",
    "paint.fill-opacity",
    "paint.fill-outline-color",
    "paint.fill-pattern",
    "paint.fill-translate",
    "paint.fill-translate-anchor",
    "paint.line-blur",
    "paint.line-color",
    _LINE_DASHARRAY_PROPERTY,
    "paint.line-gap-width",
    "paint.line-offset",
    "paint.line-opacity",
    "paint.line-pattern",
    "paint.line-translate",
    "paint.line-width",
)

if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))

from qfit.mapbox_config import (  # noqa: E402
    MapboxSpriteResources,
    QGIS_TEXT_FONT_FALLBACK,
    fetch_mapbox_style_definition,
    fetch_mapbox_sprite_resources,
    simplify_mapbox_style_expressions,
)

_SYMBOLOGY_SECTIONS = ("paint", "layout")
_MAPBOX_SPRITE_PATTERN_LIMITATION = (
    "Mapbox sprite patterns are handed to QGIS and may not render without an equivalent local pattern."
)
_UNSUPPORTED_CUES: dict[tuple[str, str], str] = {
    ("layout", "icon-image"): (
        "Mapbox sprite references use qfit-supplied Mapbox sprites when available, "
        "but QGIS may still not interpret data-driven sprite expressions."
    ),
    ("layout", "text-font"): "Mapbox font stacks are handed to QGIS and may be substituted by locally available fonts.",
    ("paint", "fill-pattern"): _MAPBOX_SPRITE_PATTERN_LIMITATION,
    ("paint", "line-pattern"): _MAPBOX_SPRITE_PATTERN_LIMITATION,
    ("paint", "background-pattern"): _MAPBOX_SPRITE_PATTERN_LIMITATION,
}
_LAYER_GROUP_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("roads/trails", ("road", "street", "motorway", "path", "trail", "track", "bridge", "tunnel")),
    ("water", ("water", "river", "lake", "marine")),
    ("terrain/landcover", ("landcover", "landuse", "terrain", "hillshade", "contour", "park", "wood", "snow")),
    ("settlements/places", ("settlement", "place", "country", "state", "region", "locality")),
    ("pois/labels", ("poi", "label", "symbol", "airport", "station")),
    ("boundaries/buildings", ("boundary", "admin", "building")),
)
_ABSENT_VALUE = "absent"
_ABSENT = object()
_MAPBOX_EXPRESSION_OPERATORS = frozenset(
    {
        "!",
        "!=",
        "<",
        "<=",
        "==",
        ">",
        ">=",
        "%",
        "*",
        "+",
        "-",
        "/",
        "^",
        "abs",
        "accumulated",
        "acos",
        "all",
        "any",
        "array",
        "asin",
        "at",
        "at-interpolated",
        "atan",
        "boolean",
        "case",
        "ceil",
        "coalesce",
        "collator",
        "concat",
        "config",
        "cos",
        "distance",
        "distance-from-center",
        "downcase",
        "e",
        "feature-state",
        "floor",
        "format",
        "geometry-type",
        "get",
        "has",
        "heatmap-density",
        "hsl",
        "hsla",
        "id",
        "image",
        "in",
        "index-of",
        "interpolate",
        "interpolate-hcl",
        "interpolate-lab",
        "is-supported-script",
        "length",
        "let",
        "line-progress",
        "literal",
        "ln",
        "ln2",
        "log10",
        "log2",
        "match",
        "max",
        "measure-light",
        "min",
        "number-format",
        "number",
        "object",
        "pi",
        "pitch",
        "properties",
        "random",
        "resolved-locale",
        "rgb",
        "rgba",
        "round",
        "sin",
        "slice",
        "split",
        "sqrt",
        "step",
        "string",
        "tan",
        "to-boolean",
        "to-color",
        "to-hsla",
        "to-number",
        "to-rgba",
        "to-string",
        "typeof",
        "upcase",
        "var",
        "within",
        "worldview",
        "zoom",
    }
)
_LEGACY_MAPBOX_FILTER_OPERATORS = frozenset({"!has", "!in", "none"})
_MAPBOX_FILTER_OPERATORS = _MAPBOX_EXPRESSION_OPERATORS | _LEGACY_MAPBOX_FILTER_OPERATORS


@dataclass(frozen=True)
class StyleAuditConfig:
    style_owner: str = DEFAULT_MAPBOX_STYLE_OWNER
    style_id: str = DEFAULT_MAPBOX_STYLE_ID
    generated_at: dt.datetime | None = None
    include_qgis_converter_warnings: bool = False
    include_qgis_property_removal_impact: bool = False
    include_qgis_filter_parse_support: bool = False
    sprite_resources: MapboxSpriteResources | None = None


def _utc_timestamp(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def resolve_mapbox_token(*, provided_token: str | None, environ: dict[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    token = provided_token or env.get("MAPBOX_ACCESS_TOKEN") or env.get("QFIT_MAPBOX_ACCESS_TOKEN")
    if not token:
        raise ValueError(
            "Mapbox token required via --mapbox-token, MAPBOX_ACCESS_TOKEN, or QFIT_MAPBOX_ACCESS_TOKEN."
        )
    return token


def _compact_json(value: object, *, max_length: int = 220) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 1]}…"


def _style_label(*, owner: str, style_id: str) -> str:
    return f"{owner.strip() or DEFAULT_MAPBOX_STYLE_OWNER}/{style_id.strip() or DEFAULT_MAPBOX_STYLE_ID}"


def _layer_group(layer: dict[str, object]) -> str:
    layer_type = str(layer.get("type") or "").lower()
    if layer_type == "background":
        return "background"

    haystack = " ".join(
        str(layer.get(key) or "").lower()
        for key in ("id", "type", "source-layer", "source")
    )
    for group, needles in _LAYER_GROUP_RULES:
        if any(needle in haystack for needle in needles):
            return group
    return "other"


def _zoom_band(layer: dict[str, object]) -> str:
    minzoom = layer.get("minzoom")
    maxzoom = layer.get("maxzoom")
    if minzoom is None and maxzoom is None:
        return _ALL_ZOOMS_BAND
    if minzoom is None:
        return f"z<{maxzoom:g}" if isinstance(maxzoom, (int, float)) else f"z<{maxzoom}"
    if maxzoom is None:
        return f"z≥{minzoom:g}" if isinstance(minzoom, (int, float)) else f"z≥{minzoom}"
    if isinstance(minzoom, (int, float)) and isinstance(maxzoom, (int, float)):
        return f"z{minzoom:g}–z{maxzoom:g}"
    return f"z{minzoom}–z{maxzoom}"


def _section_properties(layer: dict[str, object], section: str) -> dict[str, object]:
    props = layer.get(section)
    if not isinstance(props, dict):
        return {}
    return dict(sorted(props.items(), key=lambda item: item[0]))


def _iter_symbology(layer: dict[str, object]) -> Iterable[tuple[str, str, object]]:
    for section in _SYMBOLOGY_SECTIONS:
        for prop, value in _section_properties(layer, section).items():
            yield section, prop, value


def _change_entry(*, property_name: str, original_value: object, simplified_value: object) -> dict[str, str]:
    return {
        "property": property_name,
        "from": _ABSENT_VALUE if original_value is _ABSENT else _compact_json(original_value),
        "to": _ABSENT_VALUE if simplified_value is _ABSENT else _compact_json(simplified_value),
    }


def _changed_properties(
    *,
    original_layer: dict[str, object],
    simplified_layer: dict[str, object] | None,
) -> list[dict[str, str]]:
    if simplified_layer is None:
        return []

    changes: list[dict[str, str]] = []
    for section in _SYMBOLOGY_SECTIONS:
        original_props = _section_properties(original_layer, section)
        simplified_props = _section_properties(simplified_layer, section)
        for prop in sorted(set(original_props) | set(simplified_props)):
            original_value = original_props.get(prop, _ABSENT)
            simplified_value = simplified_props.get(prop, _ABSENT)
            if simplified_value != original_value:
                changes.append(
                    _change_entry(
                        property_name=f"{section}.{prop}",
                        original_value=original_value,
                        simplified_value=simplified_value,
                    )
                )
    if original_layer.get("filter") != simplified_layer.get("filter"):
        changes.append(
            _change_entry(
                property_name="filter",
                original_value=original_layer.get("filter", _ABSENT),
                simplified_value=simplified_layer.get("filter", _ABSENT),
            )
        )
    return changes


def _preserved_properties(
    *,
    original_layer: dict[str, object],
    simplified_layer: dict[str, object] | None,
) -> list[str]:
    if simplified_layer is None:
        return []

    preserved: list[str] = []
    for section, prop, original_value in _iter_symbology(original_layer):
        simplified_props = _section_properties(simplified_layer, section)
        if simplified_props.get(prop) == original_value:
            preserved.append(f"{section}.{prop}")
    if (
        original_layer.get("filter") == simplified_layer.get("filter")
        and original_layer.get("filter") is not None
    ):
        preserved.append("filter")
    return preserved


def _is_supported_simple_text_field(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and value[0] == "get"
        and isinstance(value[1], str)
    )


def _is_literal_number_array(value: object) -> bool:
    return isinstance(value, list) and len(value) > 0 and all(isinstance(item, (int, float)) for item in value)


def _is_qgis_text_font_fallback(value: object) -> bool:
    return value == [QGIS_TEXT_FONT_FALLBACK]


def _is_hidden_by_qfit(simplified_layer: dict[str, object] | None) -> bool:
    if simplified_layer is None:
        return False
    return _section_properties(simplified_layer, "layout").get("visibility") == "none"


def _expression_operator_names(value: object) -> list[str]:
    operators: set[str] = set()

    def visit(candidate: object) -> None:
        if not isinstance(candidate, list) or not candidate:
            return
        operator = candidate[0]
        if isinstance(operator, str) and operator in _MAPBOX_EXPRESSION_OPERATORS:
            operators.add(operator)
            children = _mapbox_operator_children(operator, candidate)
        else:
            children = candidate
        for child in children:
            visit(child)

    visit(value)
    return sorted(operators)


def _unresolved_entry(*, property_name: str, value: object, reason: str) -> dict[str, object]:
    entry: dict[str, object] = {
        "property": property_name,
        "value": _compact_json(value),
        "reason": reason,
    }
    operators = _expression_operator_names(value)
    if operators:
        entry["expression_operators"] = operators
    return entry


def _unresolved_cues(layer: dict[str, object], simplified_layer: dict[str, object] | None) -> list[dict[str, object]]:
    if _is_hidden_by_qfit(simplified_layer):
        return []

    unresolved: list[dict[str, object]] = []
    comparison_layer = simplified_layer or layer
    filter_value = comparison_layer.get("filter")
    if isinstance(filter_value, list):
        unresolved.append(
            _unresolved_entry(
                property_name="filter",
                value=filter_value,
                reason="Mapbox filter expression is still handed to QGIS after qfit simplification; verify native support visually.",
            )
        )
    for section, prop, value in _iter_symbology(comparison_layer):
        if section == "layout" and prop == "text-font" and _is_qgis_text_font_fallback(value):
            continue
        reason = _UNSUPPORTED_CUES.get((section, prop))
        if reason is not None:
            unresolved.append(
                _unresolved_entry(property_name=f"{section}.{prop}", value=value, reason=reason)
            )
        elif isinstance(value, list) and not (
            (section == "layout" and prop == "text-field" and _is_supported_simple_text_field(value))
            or _is_literal_number_array(value)
        ):
            unresolved.append(
                _unresolved_entry(
                    property_name=f"{section}.{prop}",
                    value=value,
                    reason="Expression is still handed to QGIS after qfit simplification; verify native support visually.",
                )
            )
    return unresolved


def build_layer_audit(
    *,
    layer: dict[str, object],
    simplified_layer: dict[str, object] | None,
) -> dict[str, object]:
    return {
        "id": str(layer.get("id") or ""),
        "type": str(layer.get("type") or ""),
        "group": _layer_group(layer),
        "source": str(layer.get("source") or ""),
        "source_layer": str(layer.get("source-layer") or ""),
        "zoom_band": _zoom_band(layer),
        "filter": layer.get("filter"),
        "qgis_filter": (simplified_layer or layer).get("filter"),
        "paint": _section_properties(layer, "paint"),
        "layout": _section_properties(layer, "layout"),
        "qfit_preserves": _preserved_properties(original_layer=layer, simplified_layer=simplified_layer),
        "qfit_simplifies": _changed_properties(original_layer=layer, simplified_layer=simplified_layer),
        "qfit_unresolved": _unresolved_cues(layer, simplified_layer),
    }


def _property_count_summary(layers: list[dict[str, object]], key: str) -> list[dict[str, object]]:
    counts: Counter[str] = Counter()
    for layer in layers:
        counts.update(_iter_property_names(layer, key))
    return [
        {"property": property_name, "count": count}
        for property_name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _iter_property_names(layer: dict[str, object], key: str) -> Iterable[str]:
    values = layer.get(key)
    if not isinstance(values, list):
        return
    for item in values:
        if isinstance(item, str):
            yield item
        elif isinstance(item, dict) and isinstance(item.get("property"), str):
            yield item["property"]


def _property_group_count_summary(layers: list[dict[str, object]], key: str) -> list[dict[str, object]]:
    counts: Counter[tuple[str, str]] = Counter()
    for layer in layers:
        group = str(layer.get("group") or "other")
        counts.update((group, property_name) for property_name in _iter_property_names(layer, key))
    return [
        {"group": group, "property": property_name, "count": count}
        for (group, property_name), count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0][0], item[0][1]),
        )
    ]


def _iter_expression_operator_pairs(layer: dict[str, object]) -> Iterable[tuple[str, str]]:
    unresolved = layer.get("qfit_unresolved")
    if not isinstance(unresolved, list):
        return
    for item in unresolved:
        if not isinstance(item, dict) or not isinstance(item.get("property"), str):
            continue
        operators = item.get("expression_operators")
        if not isinstance(operators, list):
            continue
        yield from ((item["property"], operator) for operator in operators if isinstance(operator, str))


def _expression_operator_count_summary(layers: list[dict[str, object]]) -> list[dict[str, object]]:
    counts: Counter[tuple[str, str]] = Counter()
    for layer in layers:
        counts.update(_iter_expression_operator_pairs(layer))
    return [
        {"property": property_name, "operator": operator, "count": count}
        for (property_name, operator), count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0][0], item[0][1]),
        )
    ]


def _expression_operator_group_count_summary(layers: list[dict[str, object]]) -> list[dict[str, object]]:
    counts: Counter[tuple[str, str, str]] = Counter()
    for layer in layers:
        group = str(layer.get("group") or "other")
        counts.update(
            (group, property_name, operator)
            for property_name, operator in _iter_expression_operator_pairs(layer)
        )
    return [
        {"group": group, "property": property_name, "operator": operator, "count": count}
        for (group, property_name, operator), count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0][0], item[0][1], item[0][2]),
        )
    ]


def _iter_filter_expression_signature_keys(
    layers: list[dict[str, object]],
) -> Iterable[tuple[str, tuple[str, ...], str]]:
    for layer in layers:
        group = str(layer.get("group") or "other")
        layer_id = str(layer.get("id") or "")
        unresolved = layer.get("qfit_unresolved")
        if not isinstance(unresolved, list):
            continue
        for item in unresolved:
            if not isinstance(item, dict) or item.get("property") != "filter":
                continue
            operators = tuple(str(operator) for operator in item.get("expression_operators") or [])
            yield group, operators, layer_id


def _record_filter_signature_example(
    example_layers: dict[tuple[str, tuple[str, ...]], list[str]],
    key: tuple[str, tuple[str, ...]],
    layer_id: str,
) -> None:
    examples = example_layers.setdefault(key, [])
    if layer_id and len(examples) < 5:
        examples.append(layer_id)


def _filter_expression_signature_group_summary(layers: list[dict[str, object]]) -> list[dict[str, object]]:
    counts: Counter[tuple[str, tuple[str, ...]]] = Counter()
    example_layers: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    for group, operators, layer_id in _iter_filter_expression_signature_keys(layers):
        key = (group, operators)
        counts[key] += 1
        _record_filter_signature_example(example_layers, key, layer_id)
    return [
        {
            "group": group,
            "operators": list(operators),
            "operator_signature": ", ".join(operators) or _NO_OPERATOR_SIGNATURE,
            "count": count,
            "example_layers": example_layers[(group, operators)],
        }
        for (group, operators), count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0][0], ", ".join(item[0][1])),
        )
    ]


def _is_qfit_hidden_layer(layer: dict[str, object]) -> bool:
    changes = layer.get("qfit_simplifies")
    if not isinstance(changes, list):
        return False
    return any(
        isinstance(change, dict)
        and change.get("property") == "layout.visibility"
        and change.get("to") == '"none"'
        for change in changes
    )


def _is_source_hidden_layer(layer: dict[str, object]) -> bool:
    layout = layer.get("layout")
    return isinstance(layout, dict) and layout.get("visibility") == "none"


def _is_label_density_candidate_layer(layer: dict[str, object]) -> bool:
    layout = layer.get("layout")
    return (
        str(layer.get("type") or "") == "symbol"
        and isinstance(layout, dict)
        and "text-field" in layout
        and not _is_source_hidden_layer(layer)
        and not _is_qfit_hidden_layer(layer)
    )


def _qgis_filter_value(layer: dict[str, object]) -> object:
    return layer.get("qgis_filter") if "qgis_filter" in layer else layer.get("filter")


def _control_properties(
    layer: dict[str, object],
    property_names: tuple[str, ...],
    *,
    include_filter: bool,
) -> list[str]:
    controls: list[str] = []
    if include_filter and _qgis_filter_value(layer) is not None:
        controls.append("filter")
    sections = {
        section: layer.get(section) if isinstance(layer.get(section), dict) else {}
        for section in _SYMBOLOGY_SECTIONS
    }
    for property_name in property_names:
        if "." not in property_name:
            continue
        section, section_property = property_name.split(".", 1)
        section_values = sections.get(section)
        if isinstance(section_values, dict) and section_property in section_values:
            controls.append(property_name)
    return controls


def _label_density_control_properties(layer: dict[str, object]) -> list[str]:
    return _control_properties(layer, _LABEL_DENSITY_CONTROL_PROPERTIES, include_filter=True)


def _label_density_unresolved_controls(layer: dict[str, object]) -> list[str]:
    controls = set(_label_density_control_properties(layer))
    unresolved = layer.get("qfit_unresolved")
    if not isinstance(unresolved, list):
        return []
    return sorted(
        str(item.get("property"))
        for item in unresolved
        if isinstance(item, dict) and item.get("property") in controls
    )


def _is_road_trail_hierarchy_candidate_layer(layer: dict[str, object]) -> bool:
    return (
        str(layer.get("group") or "") == "roads/trails"
        and str(layer.get("type") or "") in _ROAD_TRAIL_HIERARCHY_LAYER_TYPES
        and not _is_source_hidden_layer(layer)
        and not _is_qfit_hidden_layer(layer)
    )


def _road_trail_hierarchy_control_properties(layer: dict[str, object]) -> list[str]:
    return _control_properties(layer, _ROAD_TRAIL_HIERARCHY_CONTROL_PROPERTIES, include_filter=True)


def _is_terrain_landcover_candidate_layer(layer: dict[str, object]) -> bool:
    return (
        str(layer.get("group") or "") == "terrain/landcover"
        and str(layer.get("type") or "") in _TERRAIN_LANDCOVER_LAYER_TYPES
        and not _is_source_hidden_layer(layer)
        and not _is_qfit_hidden_layer(layer)
    )


def _terrain_landcover_control_properties(layer: dict[str, object]) -> list[str]:
    return _control_properties(layer, _TERRAIN_LANDCOVER_CONTROL_PROPERTIES, include_filter=True)


def _qfit_simplified_control_properties(layer: dict[str, object], controls: set[str]) -> list[str]:
    simplified = layer.get("qfit_simplifies")
    if not isinstance(simplified, list):
        return []
    return sorted(
        str(item.get("property"))
        for item in simplified
        if isinstance(item, dict) and item.get("property") in controls
    )


def _qgis_dependent_control_properties(layer: dict[str, object], controls: set[str]) -> list[str]:
    unresolved = layer.get("qfit_unresolved")
    if not isinstance(unresolved, list):
        return []
    return sorted(
        str(item.get("property"))
        for item in unresolved
        if isinstance(item, dict) and item.get("property") in controls
    )


def _road_trail_hierarchy_candidate_rows(layers: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for layer in layers:
        if not _is_road_trail_hierarchy_candidate_layer(layer):
            continue
        controls = _road_trail_hierarchy_control_properties(layer)
        if not controls:
            continue
        control_set = set(controls)
        rows.append(
            {
                "layer": str(layer.get("id") or ""),
                "type": str(layer.get("type") or ""),
                "source_layer": str(layer.get("source_layer") or ""),
                "zoom_band": str(layer.get("zoom_band") or _ALL_ZOOMS_BAND),
                "filter_operator_signature": _operator_signature(_qgis_filter_value(layer)),
                _ROAD_TRAIL_CONTROL_PROPERTIES_KEY: controls,
                _QFIT_SIMPLIFIED_CONTROL_PROPERTIES_KEY: _qfit_simplified_control_properties(layer, control_set),
                _QGIS_DEPENDENT_CONTROL_PROPERTIES_KEY: _qgis_dependent_control_properties(layer, control_set),
            }
        )
    return sorted(rows, key=lambda row: (str(row["type"]), str(row["layer"])))


def _terrain_landcover_candidate_rows(layers: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for layer in layers:
        if not _is_terrain_landcover_candidate_layer(layer):
            continue
        controls = _terrain_landcover_control_properties(layer)
        if not controls:
            continue
        control_set = set(controls)
        rows.append(
            {
                "layer": str(layer.get("id") or ""),
                "type": str(layer.get("type") or ""),
                "source_layer": str(layer.get("source_layer") or ""),
                "zoom_band": str(layer.get("zoom_band") or _ALL_ZOOMS_BAND),
                "filter_operator_signature": _operator_signature(_qgis_filter_value(layer)),
                _TERRAIN_LANDCOVER_CONTROL_PROPERTIES_KEY: controls,
                _QFIT_SIMPLIFIED_CONTROL_PROPERTIES_KEY: _qfit_simplified_control_properties(
                    layer,
                    control_set,
                ),
                _QGIS_DEPENDENT_CONTROL_PROPERTIES_KEY: _qgis_dependent_control_properties(
                    layer,
                    control_set,
                ),
            }
        )
    return sorted(rows, key=lambda row: (str(row["type"]), str(row["layer"])))


def _label_density_candidate_rows(layers: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for layer in layers:
        if not _is_label_density_candidate_layer(layer):
            continue
        rows.append(
            {
                "layer": str(layer.get("id") or ""),
                "group": str(layer.get("group") or "other"),
                "source_layer": str(layer.get("source_layer") or ""),
                "zoom_band": str(layer.get("zoom_band") or _ALL_ZOOMS_BAND),
                "filter_operator_signature": _operator_signature(_qgis_filter_value(layer)),
                "label_control_properties": _label_density_control_properties(layer),
                _QGIS_DEPENDENT_CONTROL_PROPERTIES_KEY: _label_density_unresolved_controls(layer),
            }
        )
    return sorted(rows, key=lambda row: (str(row["group"]), str(row["layer"])))


def _warning_count_summary(warnings: list[str], *, by_layer: bool) -> list[dict[str, object]]:
    counts: Counter[str] = Counter()
    for warning in warnings:
        layer, separator, message = warning.partition(": ")
        if not separator:
            key = "" if by_layer else warning
        elif by_layer:
            key = layer
        else:
            key = message
        if key:
            counts[key] += 1
    key_name = "layer" if by_layer else "message"
    return [
        {key_name: key, "count": count}
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _qgis_warning_summary(warnings: list[str]) -> dict[str, object]:
    return {
        "count": len(warnings),
        "by_message": _warning_count_summary(warnings, by_layer=False),
        "by_layer": _warning_count_summary(warnings, by_layer=True),
        "warnings": warnings,
    }


def _warning_message_matches(warning: str, message: str) -> bool:
    message_after_layer = f": {message}"
    return (
        warning == message
        or warning.startswith(f"{message} ")
        or warning.endswith(message_after_layer)
        or f"{message_after_layer} " in warning
    )


def _warning_message(warning: str) -> str:
    for unsupported_message in _FILTER_PARSE_UNSUPPORTED_MESSAGE_ORDER:
        if _warning_message_matches(warning, unsupported_message):
            return unsupported_message
    _layer, separator, message = warning.rpartition(": ")
    return message if separator else warning


def _filter_parse_unsupported_warning_count(warnings: list[str]) -> int:
    return sum(1 for warning in warnings if _warning_message(warning) in _FILTER_PARSE_UNSUPPORTED_MESSAGES)


def _filter_parse_unsupported_message(warning: str) -> str:
    for unsupported_message in _FILTER_PARSE_UNSUPPORTED_MESSAGE_ORDER:
        marker = f": {unsupported_message}"
        marker_index = warning.rfind(marker)
        if marker_index >= 0:
            return warning[marker_index + 2 :]
        if warning == unsupported_message or warning.startswith(f"{unsupported_message} "):
            return warning
    return _warning_message(warning)


def _filter_parse_unsupported_message_summary(warnings: list[str]) -> list[dict[str, object]]:
    counts: Counter[str] = Counter()
    for warning in warnings:
        if _warning_message(warning) in _FILTER_PARSE_UNSUPPORTED_MESSAGES:
            counts[_filter_parse_unsupported_message(warning)] += 1
    return [
        {"message": message, "count": count}
        for message, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _minimal_filter_probe_layer(layer: dict[str, object], filter_value: object = _ABSENT) -> dict[str, object]:
    layer_type = str(layer.get("type") or "")
    probe_layer: dict[str, object] = {
        "id": str(layer.get("id") or "filter-probe"),
        "type": layer_type,
        "filter": filter_value if filter_value is not _ABSENT else layer.get("filter"),
    }
    for key in ("source", "source-layer", "minzoom", "maxzoom"):
        if key in layer:
            probe_layer[key] = layer[key]

    if layer_type == "fill":
        probe_layer["paint"] = {"fill-color": "#000000"}
    elif layer_type == "line":
        probe_layer["paint"] = {"line-color": "#000000", "line-width": 1}
    elif layer_type == "circle":
        probe_layer["paint"] = {"circle-color": "#000000", "circle-radius": 1}
    elif layer_type == "symbol":
        probe_layer["layout"] = {"text-field": "filter-probe", "text-size": 12}
        probe_layer["paint"] = {"text-color": "#000000"}
    else:
        # raster, fill-extrusion, heatmap, background, sky: no standard same-type
        # paint stub is available. The probe only treats unsupported-expression
        # messages as filter parser failures, so unrelated converter warnings from
        # uncommon layer types do not inflate unsupported filter counts.
        pass
    return probe_layer


def _filter_probe_style(style_definition: dict[str, object], layer: dict[str, object]) -> dict[str, object]:
    return {
        "version": style_definition.get("version", 8),
        "sources": copy.deepcopy(style_definition.get("sources", {})),
        "layers": [_minimal_filter_probe_layer(layer)],
    }


def _filter_part_probe_style(
    style_definition: dict[str, object],
    layer: dict[str, object],
    filter_part: object,
) -> dict[str, object]:
    probe_layer = _minimal_filter_probe_layer(layer, copy.deepcopy(filter_part))
    return {
        "version": style_definition.get("version", 8),
        "sources": copy.deepcopy(style_definition.get("sources", {})),
        "layers": [probe_layer],
    }


def _iter_direct_filter_parts(filter_value: list[object]) -> Iterable[tuple[int, str, list[object]]]:
    if not filter_value:
        return
    parent_operator = filter_value[0]
    if not isinstance(parent_operator, str) or parent_operator not in _FILTER_PARSE_PART_PARENT_OPERATORS:
        return
    for index, filter_part in enumerate(filter_value[1:], start=1):
        if isinstance(filter_part, list):
            yield index, parent_operator, filter_part


def _diagnostic_arithmetic_value(operator: str, values: list[object]) -> object:
    if not values or not all(isinstance(value, (int, float)) for value in values):
        return _ABSENT
    if operator == "+":
        return sum(values)
    if operator == "-":
        return -values[0] if len(values) == 1 else values[0] - sum(values[1:])
    if operator == "*":
        result = 1.0
        for value in values:
            result *= value
        return result
    if operator == "/" and len(values) == 2 and values[1] != 0:
        return values[0] / values[1]
    return _ABSENT


def _diagnostic_step_value_at_zoom(expression: list[object], zoom: float) -> object:
    if len(expression) < 4:
        return _ABSENT
    input_value = _diagnostic_filter_value_at_zoom(expression[1], zoom)
    if not isinstance(input_value, (int, float)):
        return _ABSENT
    selected_value = expression[2]
    for index in range(3, len(expression) - 1, 2):
        stop = expression[index]
        if not isinstance(stop, (int, float)) or input_value < stop:
            break
        selected_value = expression[index + 1]
    return _diagnostic_filter_value_at_zoom(selected_value, zoom)


def _diagnostic_interpolate_value_at_zoom(expression: list[object], zoom: float) -> object:
    if len(expression) < 6:
        return _ABSENT
    input_value = _diagnostic_filter_value_at_zoom(expression[2], zoom)
    if not isinstance(input_value, (int, float)):
        return _ABSENT
    stops = [
        (float(expression[index]), _diagnostic_filter_value_at_zoom(expression[index + 1], zoom))
        for index in range(3, len(expression) - 1, 2)
        if isinstance(expression[index], (int, float))
    ]
    if not stops:
        return _ABSENT
    if input_value <= stops[0][0]:
        return stops[0][1]
    for (lower_stop, lower_value), (upper_stop, upper_value) in zip(stops, stops[1:]):
        if input_value <= upper_stop:
            # Diagnostic-only: approximate in-range numeric interpolation linearly.
            # This is not a full Mapbox expression evaluator for exponential/cubic-bezier curves.
            outputs_are_numeric = all(isinstance(value, (int, float)) for value in (lower_value, upper_value))
            if outputs_are_numeric and upper_stop != lower_stop:
                fraction = (input_value - lower_stop) / (upper_stop - lower_stop)
                return lower_value + (upper_value - lower_value) * fraction
            return lower_value
    return stops[-1][1]


def _diagnostic_filter_value_at_zoom(value: object, zoom: float = _EXPRESSION_PROBE_ZOOM) -> object:
    if not isinstance(value, list) or not value:
        return value
    operator = value[0]
    if operator == "literal":
        return value
    if operator == "zoom" and len(value) == 1:
        return zoom
    if operator == "step":
        step_value = _diagnostic_step_value_at_zoom(value, zoom)
        if step_value is not _ABSENT:
            return step_value
    if operator == "interpolate":
        interpolate_value = _diagnostic_interpolate_value_at_zoom(value, zoom)
        if interpolate_value is not _ABSENT:
            return interpolate_value
    if isinstance(operator, str) and operator in {"+", "-", "*", "/"} and _diagnostic_value_depends_on_zoom(value):
        arithmetic_value = _diagnostic_arithmetic_value(
            operator,
            [_diagnostic_filter_value_at_zoom(item, zoom) for item in value[1:]],
        )
        if arithmetic_value is not _ABSENT:
            return arithmetic_value
    return [_diagnostic_filter_value_at_zoom(item, zoom) for item in value]


def _diagnostic_inverted_boolean_match_value(value: object) -> object:
    if not isinstance(value, list) or len(value) != 2 or value[0] != "!":
        return _ABSENT
    match_expression = value[1]
    if (
        not isinstance(match_expression, list)
        or len(match_expression) < 5
        or (len(match_expression) - 3) % 2 != 0
        or match_expression[0] != "match"
    ):
        return _ABSENT
    normalized = ["match", copy.deepcopy(match_expression[1])]
    for output_index in range(3, len(match_expression) - 1, 2):
        output_value = match_expression[output_index]
        if not isinstance(output_value, bool):
            return _ABSENT
        normalized.extend([copy.deepcopy(match_expression[output_index - 1]), not output_value])
    default_value = match_expression[-1]
    if not isinstance(default_value, bool):
        return _ABSENT
    normalized.append(not default_value)
    return normalized


def _diagnostic_simple_case_predicate_value(value: object) -> object:
    if not isinstance(value, list) or len(value) != 4 or value[0] != "case":
        return _ABSENT
    condition = _diagnostic_filter_parser_friendly_value(value[1], root=True)
    predicate = _diagnostic_filter_parser_friendly_value(value[2], root=True)
    default = value[3]
    if value[2] is True and default is False:
        return condition
    if value[2] is False and default is True:
        return ["!", condition]
    if default is True:
        return ["any", ["!", condition], predicate]
    if default is False:
        return ["all", condition, predicate]
    return _ABSENT


def _diagnostic_is_numeric_zero(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0


def _diagnostic_additive_identity_value(value: list[object], *, root: bool) -> object:
    if len(value) != 3:
        return _ABSENT
    left, right = value[1], value[2]
    if value[0] == "+":
        if _diagnostic_is_numeric_zero(left):
            return _diagnostic_filter_parser_friendly_value(right, root=root)
        if _diagnostic_is_numeric_zero(right):
            return _diagnostic_filter_parser_friendly_value(left, root=root)
    if value[0] == "-" and _diagnostic_is_numeric_zero(right):
        return _diagnostic_filter_parser_friendly_value(left, root=root)
    return _ABSENT


def _diagnostic_filter_parser_friendly_value(value: object, *, root: bool = True) -> object:
    if isinstance(value, bool):
        if root:
            return ["==", 1, 1 if value else 0]
        return value
    if not isinstance(value, list) or not value:
        return value
    operator = value[0]
    if operator == "literal":
        return value
    inverted_match = _diagnostic_inverted_boolean_match_value(value)
    if inverted_match is not _ABSENT:
        return inverted_match
    case_predicate = _diagnostic_simple_case_predicate_value(value)
    if case_predicate is not _ABSENT:
        return case_predicate
    if operator in {"+", "-"}:
        additive_identity = _diagnostic_additive_identity_value(value, root=root)
        if additive_identity is not _ABSENT:
            return additive_identity
    return [operator, *[_diagnostic_filter_parser_friendly_value(item, root=False) for item in value[1:]]]


def _match_expression_children(candidate: list[object]) -> Iterable[object]:
    if len(candidate) > 1:
        yield candidate[1]
    for output_index in range(3, len(candidate) - 1, 2):
        yield candidate[output_index]
    if len(candidate) > 2:
        yield candidate[-1]


def _mapbox_operator_children(operator: str, candidate: list[object]) -> Iterable[object]:
    if operator == "literal":
        return []
    if operator == "match":
        return _match_expression_children(candidate)
    return candidate[1:]


def _diagnostic_value_depends_on_zoom(value: object) -> bool:
    if not isinstance(value, list) or not value:
        return False
    operator = value[0]
    if operator == "zoom" and len(value) == 1:
        return True
    if isinstance(operator, str) and operator in _MAPBOX_FILTER_OPERATORS:
        children = _mapbox_operator_children(operator, value)
    else:
        children = value[1:] if isinstance(operator, str) else value
    return any(_diagnostic_value_depends_on_zoom(child) for child in children)


def _filter_operator_names(value: object) -> list[str]:
    operators: set[str] = set()

    def visit(candidate: object) -> None:
        if not isinstance(candidate, list) or not candidate:
            return
        operator = candidate[0]
        if isinstance(operator, str) and operator in _MAPBOX_FILTER_OPERATORS:
            operators.add(operator)
            children = _mapbox_operator_children(operator, candidate)
        else:
            children = candidate
        for child in children:
            visit(child)

    visit(value)
    return sorted(operators)


def _operator_signature(value: object) -> str:
    return ", ".join(_filter_operator_names(value)) or _NO_OPERATOR_SIGNATURE


def _count_rows_by_key(rows: list[dict[str, object]], key: str) -> list[dict[str, object]]:
    counts = Counter(str(row.get(key) or "") for row in rows)
    return [
        {key: name, "count": count}
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        if name
    ]


def _count_row_values(rows: list[dict[str, object]], key: str) -> list[dict[str, object]]:
    counts: Counter[str] = Counter()
    for row in rows:
        values = row.get(key)
        if not isinstance(values, list):
            continue
        counts.update(str(value) for value in values if value)
    return [
        {"property": name, "count": count}
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        if name
    ]


def _filter_parse_signature_summary(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    counts: Counter[tuple[str, str]] = Counter()
    examples: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        key = (str(row.get("group") or "other"), str(row.get("operator_signature") or _NO_OPERATOR_SIGNATURE))
        counts[key] += 1
        layer_id = str(row.get("layer") or "")
        if layer_id:
            examples.setdefault(key, [])
            if len(examples[key]) < 5:
                examples[key].append(layer_id)
    return [
        {
            "group": group,
            "operator_signature": signature,
            "count": count,
            "example_layers": examples.get((group, signature), []),
        }
        for (group, signature), count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0][0], item[0][1]),
        )
    ]


def _filter_parse_warning_message_summary(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    counts: Counter[str] = Counter()
    for row in rows:
        for summary in row.get("unsupported_warning_messages") or []:
            if isinstance(summary, dict):
                message = str(summary.get("message") or "")
                if message:
                    counts[message] += int(summary.get("count") or 0)
    return [
        {"message": message, "count": count}
        for message, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _iter_filter_probe_layers(style_definition: dict[str, object]) -> Iterable[tuple[dict[str, object], list[object]]]:
    layers = style_definition.get("layers")
    if not isinstance(layers, list):
        return
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        filter_value = layer.get("filter")
        if isinstance(filter_value, list):
            yield layer, filter_value


def _filter_parse_support_row(
    style_definition: dict[str, object],
    layer: dict[str, object],
    filter_value: list[object],
) -> dict[str, object]:
    warnings = _collect_qgis_converter_warnings(_filter_probe_style(style_definition, layer))
    unsupported_warning_count = _filter_parse_unsupported_warning_count(warnings)
    return {
        "layer": str(layer.get("id") or ""),
        "group": _layer_group(layer),
        "type": str(layer.get("type") or ""),
        "source_layer": str(layer.get("source-layer") or ""),
        "operator_signature": _operator_signature(filter_value),
        "unsupported_warning_count": unsupported_warning_count,
        "unsupported_warning_messages": _filter_parse_unsupported_message_summary(warnings),
        "supported_by_qgis_parser": unsupported_warning_count == 0,
        "warnings": warnings,
        "filter": filter_value,
    }


def _filter_parse_part_support_row(
    style_definition: dict[str, object],
    layer: dict[str, object],
    filter_part: list[object],
    *,
    parent_operator: str,
    part_index: int,
) -> dict[str, object]:
    warnings = _collect_qgis_converter_warnings(_filter_part_probe_style(style_definition, layer, filter_part))
    unsupported_warning_count = _filter_parse_unsupported_warning_count(warnings)
    return {
        "layer": str(layer.get("id") or ""),
        "group": _layer_group(layer),
        "type": str(layer.get("type") or ""),
        "source_layer": str(layer.get("source-layer") or ""),
        "parent_operator": parent_operator,
        "part_index": part_index,
        "operator_signature": _operator_signature(filter_part),
        "unsupported_warning_count": unsupported_warning_count,
        "unsupported_warning_messages": _filter_parse_unsupported_message_summary(warnings),
        "supported_by_qgis_parser": unsupported_warning_count == 0,
        "warnings": warnings,
        "filter": filter_part,
    }


def _filter_parse_part_support_rows(
    style_definition: dict[str, object],
    layer: dict[str, object],
    filter_value: list[object],
) -> list[dict[str, object]]:
    return [
        _filter_parse_part_support_row(
            style_definition,
            layer,
            filter_part,
            parent_operator=parent_operator,
            part_index=part_index,
        )
        for part_index, parent_operator, filter_part in _iter_direct_filter_parts(filter_value)
    ]


def _filter_parse_zoom_normalized_part_support_row(
    style_definition: dict[str, object],
    layer: dict[str, object],
    unsupported_part_row: dict[str, object],
) -> dict[str, object]:
    original_filter = unsupported_part_row.get("filter")
    normalized_filter = _diagnostic_filter_value_at_zoom(original_filter)
    warnings = _collect_qgis_converter_warnings(_filter_part_probe_style(style_definition, layer, normalized_filter))
    unsupported_warning_count = _filter_parse_unsupported_warning_count(warnings)
    return {
        "layer": str(unsupported_part_row.get("layer") or ""),
        "group": str(unsupported_part_row.get("group") or ""),
        "type": str(unsupported_part_row.get("type") or ""),
        "source_layer": str(unsupported_part_row.get("source_layer") or ""),
        "parent_operator": str(unsupported_part_row.get("parent_operator") or ""),
        "part_index": int(unsupported_part_row.get("part_index") or 0),
        "original_operator_signature": str(unsupported_part_row.get("operator_signature") or _NO_OPERATOR_SIGNATURE),
        "operator_signature": _operator_signature(normalized_filter),
        "changed_by_zoom_normalization": normalized_filter != original_filter,
        "unsupported_warning_count": unsupported_warning_count,
        "unsupported_warning_messages": _filter_parse_unsupported_message_summary(warnings),
        "supported_by_qgis_parser": unsupported_warning_count == 0,
        "warnings": warnings,
        "filter": normalized_filter,
        "original_filter": original_filter,
    }


def _filter_parse_zoom_normalized_part_support_rows(
    style_definition: dict[str, object],
    layers_by_id: dict[str, dict[str, object]],
    unsupported_part_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for unsupported_part_row in unsupported_part_rows:
        layer = layers_by_id.get(str(unsupported_part_row.get("layer") or ""))
        if layer is not None:
            rows.append(_filter_parse_zoom_normalized_part_support_row(style_definition, layer, unsupported_part_row))
    return rows


def _filter_parse_parser_friendly_part_support_row(
    style_definition: dict[str, object],
    layer: dict[str, object],
    zoom_normalized_part_row: dict[str, object],
) -> dict[str, object]:
    zoom_normalized_filter = zoom_normalized_part_row.get("filter")
    parser_friendly_filter = _diagnostic_filter_parser_friendly_value(zoom_normalized_filter)
    warnings = _collect_qgis_converter_warnings(
        _filter_part_probe_style(style_definition, layer, parser_friendly_filter)
    )
    unsupported_warning_count = _filter_parse_unsupported_warning_count(warnings)
    return {
        "layer": str(zoom_normalized_part_row.get("layer") or ""),
        "group": str(zoom_normalized_part_row.get("group") or ""),
        "type": str(zoom_normalized_part_row.get("type") or ""),
        "source_layer": str(zoom_normalized_part_row.get("source_layer") or ""),
        "parent_operator": str(zoom_normalized_part_row.get("parent_operator") or ""),
        "part_index": int(zoom_normalized_part_row.get("part_index") or 0),
        "original_operator_signature": str(
            zoom_normalized_part_row.get("original_operator_signature") or _NO_OPERATOR_SIGNATURE
        ),
        "zoom_normalized_operator_signature": str(
            zoom_normalized_part_row.get("operator_signature") or _NO_OPERATOR_SIGNATURE
        ),
        "operator_signature": _operator_signature(parser_friendly_filter),
        "changed_by_parser_friendly_normalization": parser_friendly_filter != zoom_normalized_filter,
        "unsupported_warning_count": unsupported_warning_count,
        "unsupported_warning_messages": _filter_parse_unsupported_message_summary(warnings),
        "supported_by_qgis_parser": unsupported_warning_count == 0,
        "warnings": warnings,
        "filter": parser_friendly_filter,
        "zoom_normalized_filter": zoom_normalized_filter,
        "original_filter": zoom_normalized_part_row.get("original_filter", zoom_normalized_filter),
    }


def _filter_parse_parser_friendly_part_support_rows(
    style_definition: dict[str, object],
    layers_by_id: dict[str, dict[str, object]],
    zoom_normalized_unsupported_part_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for zoom_normalized_part_row in zoom_normalized_unsupported_part_rows:
        layer = layers_by_id.get(str(zoom_normalized_part_row.get("layer") or ""))
        if layer is not None:
            rows.append(
                _filter_parse_parser_friendly_part_support_row(style_definition, layer, zoom_normalized_part_row)
            )
    return rows


def _filter_parse_parser_friendly_filter_support_row(
    style_definition: dict[str, object],
    layer: dict[str, object],
    unsupported_row: dict[str, object],
) -> dict[str, object]:
    original_filter = unsupported_row.get("filter")
    zoom_normalized_filter = _diagnostic_filter_value_at_zoom(original_filter)
    parser_friendly_filter = _diagnostic_filter_parser_friendly_value(zoom_normalized_filter)
    warnings = _collect_qgis_converter_warnings(
        _filter_part_probe_style(style_definition, layer, parser_friendly_filter)
    )
    unsupported_warning_count = _filter_parse_unsupported_warning_count(warnings)
    return {
        "layer": str(unsupported_row.get("layer") or ""),
        "group": str(unsupported_row.get("group") or ""),
        "type": str(unsupported_row.get("type") or ""),
        "source_layer": str(unsupported_row.get("source_layer") or ""),
        "original_operator_signature": str(unsupported_row.get("operator_signature") or _NO_OPERATOR_SIGNATURE),
        "zoom_normalized_operator_signature": _operator_signature(zoom_normalized_filter),
        "operator_signature": _operator_signature(parser_friendly_filter),
        "changed_by_zoom_normalization": zoom_normalized_filter != original_filter,
        "changed_by_parser_friendly_normalization": parser_friendly_filter != zoom_normalized_filter,
        "changed_by_zoom_or_parser_friendly_normalization": parser_friendly_filter != original_filter,
        "unsupported_warning_count": unsupported_warning_count,
        "unsupported_warning_messages": _filter_parse_unsupported_message_summary(warnings),
        "supported_by_qgis_parser": unsupported_warning_count == 0,
        "warnings": warnings,
        "filter": parser_friendly_filter,
        "zoom_normalized_filter": zoom_normalized_filter,
        "original_filter": original_filter,
    }


def _filter_parse_parser_friendly_filter_support_rows(
    style_definition: dict[str, object],
    layers_by_id: dict[str, dict[str, object]],
    unsupported_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for unsupported_row in unsupported_rows:
        layer = layers_by_id.get(str(unsupported_row.get("layer") or ""))
        if layer is not None:
            rows.append(_filter_parse_parser_friendly_filter_support_row(style_definition, layer, unsupported_row))
    return rows


def _filter_parse_row_is_unsupported(row: dict[str, object]) -> bool:
    return int(row.get("unsupported_warning_count") or 0) > 0


def _filter_parse_unsupported_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [row for row in rows if _filter_parse_row_is_unsupported(row)]


def _filter_parse_supported_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [row for row in rows if not _filter_parse_row_is_unsupported(row)]


def _filter_parse_changed_row_count(rows: list[dict[str, object]], key: str) -> int:
    return sum(1 for row in rows if bool(row.get(key)))


def _filter_parse_supported_part_sort_key(row: dict[str, object]) -> tuple[str, str, int]:
    return (
        str(row.get("group") or ""),
        str(row.get("layer") or ""),
        int(row.get("part_index") or 0),
    )


def _filter_parse_supported_filter_sort_key(row: dict[str, object]) -> tuple[str, str]:
    return (
        str(row.get("group") or ""),
        str(row.get("layer") or ""),
    )


def _filter_parse_unsupported_part_sort_key(row: dict[str, object]) -> tuple[int, str, str, int]:
    return (
        -int(row.get("unsupported_warning_count") or 0),
        str(row.get("group") or ""),
        str(row.get("layer") or ""),
        int(row.get("part_index") or 0),
    )


def _filter_parse_unsupported_layer_sort_key(row: dict[str, object]) -> tuple[int, str, str]:
    return (
        -int(row.get("unsupported_warning_count") or 0),
        str(row.get("group") or ""),
        str(row.get("layer") or ""),
    )


@dataclass(frozen=True)
class _FilterParseSupportReportRows:
    rows: list[dict[str, object]]
    part_rows: list[dict[str, object]]
    unsupported_rows: list[dict[str, object]]
    unsupported_part_rows: list[dict[str, object]]
    parser_friendly_filter_rows: list[dict[str, object]]
    parser_friendly_supported_filter_rows: list[dict[str, object]]
    parser_friendly_unsupported_filter_rows: list[dict[str, object]]
    zoom_normalized_part_rows: list[dict[str, object]]
    zoom_normalized_supported_part_rows: list[dict[str, object]]
    zoom_normalized_unsupported_part_rows: list[dict[str, object]]
    parser_friendly_part_rows: list[dict[str, object]]
    parser_friendly_supported_part_rows: list[dict[str, object]]
    parser_friendly_unsupported_part_rows: list[dict[str, object]]


def _collect_filter_parse_support_rows(
    style_definition: dict[str, object],
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, dict[str, object]]]:
    rows: list[dict[str, object]] = []
    part_rows: list[dict[str, object]] = []
    layers_by_id: dict[str, dict[str, object]] = {}
    for layer, filter_value in _iter_filter_probe_layers(style_definition):
        layers_by_id[str(layer.get("id") or "")] = layer
        row = _filter_parse_support_row(style_definition, layer, filter_value)
        rows.append(row)
        if _filter_parse_row_is_unsupported(row):
            part_rows.extend(_filter_parse_part_support_rows(style_definition, layer, filter_value))
    return rows, part_rows, layers_by_id


def _filter_parse_support_report_rows(style_definition: dict[str, object]) -> _FilterParseSupportReportRows:
    rows, part_rows, layers_by_id = _collect_filter_parse_support_rows(style_definition)
    unsupported_rows = _filter_parse_unsupported_rows(rows)
    unsupported_part_rows = _filter_parse_unsupported_rows(part_rows)
    parser_friendly_filter_rows = _filter_parse_parser_friendly_filter_support_rows(
        style_definition,
        layers_by_id,
        unsupported_rows,
    )
    parser_friendly_supported_filter_rows = _filter_parse_supported_rows(parser_friendly_filter_rows)
    parser_friendly_unsupported_filter_rows = _filter_parse_unsupported_rows(parser_friendly_filter_rows)
    zoom_normalized_part_rows = _filter_parse_zoom_normalized_part_support_rows(
        style_definition,
        layers_by_id,
        unsupported_part_rows,
    )
    zoom_normalized_supported_part_rows = _filter_parse_supported_rows(zoom_normalized_part_rows)
    zoom_normalized_unsupported_part_rows = _filter_parse_unsupported_rows(zoom_normalized_part_rows)
    parser_friendly_part_rows = _filter_parse_parser_friendly_part_support_rows(
        style_definition,
        layers_by_id,
        zoom_normalized_unsupported_part_rows,
    )
    parser_friendly_supported_part_rows = _filter_parse_supported_rows(parser_friendly_part_rows)
    parser_friendly_unsupported_part_rows = _filter_parse_unsupported_rows(parser_friendly_part_rows)
    return _FilterParseSupportReportRows(
        rows=rows,
        part_rows=part_rows,
        unsupported_rows=unsupported_rows,
        unsupported_part_rows=unsupported_part_rows,
        parser_friendly_filter_rows=parser_friendly_filter_rows,
        parser_friendly_supported_filter_rows=parser_friendly_supported_filter_rows,
        parser_friendly_unsupported_filter_rows=parser_friendly_unsupported_filter_rows,
        zoom_normalized_part_rows=zoom_normalized_part_rows,
        zoom_normalized_supported_part_rows=zoom_normalized_supported_part_rows,
        zoom_normalized_unsupported_part_rows=zoom_normalized_unsupported_part_rows,
        parser_friendly_part_rows=parser_friendly_part_rows,
        parser_friendly_supported_part_rows=parser_friendly_supported_part_rows,
        parser_friendly_unsupported_part_rows=parser_friendly_unsupported_part_rows,
    )


def _qgis_filter_parse_support_count_report(report_rows: _FilterParseSupportReportRows) -> dict[str, object]:
    return {
        "filter_expression_count": len(report_rows.rows),
        "qgis_parser_supported_count": len(report_rows.rows) - len(report_rows.unsupported_rows),
        "qgis_parser_unsupported_count": len(report_rows.unsupported_rows),
        "parser_friendly_filter_count": len(report_rows.parser_friendly_filter_rows),
        "parser_friendly_changed_filter_count": _filter_parse_changed_row_count(
            report_rows.parser_friendly_filter_rows,
            "changed_by_zoom_or_parser_friendly_normalization",
        ),
        "qgis_parser_supported_parser_friendly_filter_count": len(
            report_rows.parser_friendly_supported_filter_rows
        ),
        "qgis_parser_unsupported_parser_friendly_filter_count": len(
            report_rows.parser_friendly_unsupported_filter_rows
        ),
        "direct_filter_part_count": len(report_rows.part_rows),
        "qgis_parser_supported_part_count": len(report_rows.part_rows) - len(report_rows.unsupported_part_rows),
        "qgis_parser_unsupported_part_count": len(report_rows.unsupported_part_rows),
        "zoom_normalized_direct_part_count": len(report_rows.zoom_normalized_part_rows),
        "zoom_normalized_changed_direct_part_count": _filter_parse_changed_row_count(
            report_rows.zoom_normalized_part_rows,
            "changed_by_zoom_normalization",
        ),
        "qgis_parser_supported_zoom_normalized_part_count": len(
            report_rows.zoom_normalized_supported_part_rows
        ),
        "qgis_parser_unsupported_zoom_normalized_part_count": len(
            report_rows.zoom_normalized_unsupported_part_rows
        ),
        "parser_friendly_direct_part_count": len(report_rows.parser_friendly_part_rows),
        "parser_friendly_changed_direct_part_count": _filter_parse_changed_row_count(
            report_rows.parser_friendly_part_rows,
            "changed_by_parser_friendly_normalization",
        ),
        "qgis_parser_supported_parser_friendly_part_count": len(report_rows.parser_friendly_supported_part_rows),
        "qgis_parser_unsupported_parser_friendly_part_count": len(report_rows.parser_friendly_unsupported_part_rows),
    }


def _qgis_filter_parse_support_group_report(report_rows: _FilterParseSupportReportRows) -> dict[str, object]:
    return {
        "unsupported_by_layer_group": _count_rows_by_key(report_rows.unsupported_rows, "group"),
        "unsupported_by_warning_message": _filter_parse_warning_message_summary(report_rows.unsupported_rows),
        "unsupported_by_layer_group_and_operator_signature": _filter_parse_signature_summary(
            report_rows.unsupported_rows
        ),
        "parser_friendly_supported_filters_by_layer_group_and_operator_signature": _filter_parse_signature_summary(
            report_rows.parser_friendly_supported_filter_rows
        ),
        "parser_friendly_unsupported_filters_by_layer_group_and_operator_signature": _filter_parse_signature_summary(
            report_rows.parser_friendly_unsupported_filter_rows
        ),
        "unsupported_parts_by_layer_group_and_operator_signature": _filter_parse_signature_summary(
            report_rows.unsupported_part_rows
        ),
        "zoom_normalized_unsupported_parts_by_layer_group_and_operator_signature": _filter_parse_signature_summary(
            report_rows.zoom_normalized_unsupported_part_rows
        ),
        "parser_friendly_supported_parts_by_layer_group_and_operator_signature": _filter_parse_signature_summary(
            report_rows.parser_friendly_supported_part_rows
        ),
        "parser_friendly_unsupported_parts_by_layer_group_and_operator_signature": _filter_parse_signature_summary(
            report_rows.parser_friendly_unsupported_part_rows
        ),
    }


def _qgis_filter_parse_support_detail_report(report_rows: _FilterParseSupportReportRows) -> dict[str, object]:
    return {
        "parser_friendly_supported_filters": sorted(
            report_rows.parser_friendly_supported_filter_rows,
            key=_filter_parse_supported_filter_sort_key,
        ),
        "parser_friendly_unsupported_filters": sorted(
            report_rows.parser_friendly_unsupported_filter_rows,
            key=_filter_parse_unsupported_layer_sort_key,
        ),
        "zoom_normalized_supported_parts": sorted(
            report_rows.zoom_normalized_supported_part_rows,
            key=_filter_parse_supported_part_sort_key,
        ),
        "zoom_normalized_unsupported_parts": sorted(
            report_rows.zoom_normalized_unsupported_part_rows,
            key=_filter_parse_unsupported_part_sort_key,
        ),
        "parser_friendly_supported_parts": sorted(
            report_rows.parser_friendly_supported_part_rows,
            key=_filter_parse_supported_part_sort_key,
        ),
        "parser_friendly_unsupported_parts": sorted(
            report_rows.parser_friendly_unsupported_part_rows,
            key=_filter_parse_unsupported_part_sort_key,
        ),
        "unsupported_parts": sorted(report_rows.unsupported_part_rows, key=_filter_parse_unsupported_part_sort_key),
        "unsupported_layers": sorted(report_rows.unsupported_rows, key=_filter_parse_unsupported_layer_sort_key),
    }


def _qgis_filter_parse_support_report(style_definition: dict[str, object]) -> dict[str, object]:
    report_rows = _filter_parse_support_report_rows(style_definition)
    return {
        **_qgis_filter_parse_support_count_report(report_rows),
        **_qgis_filter_parse_support_group_report(report_rows),
        **_qgis_filter_parse_support_detail_report(report_rows),
    }


def _qgis_warning_summaries_by_layer(warnings: list[str]) -> dict[str, dict[str, object]]:
    grouped: dict[str, list[str]] = {}
    for warning in warnings:
        layer, separator, _message = warning.partition(": ")
        if not separator or not layer:
            continue
        grouped.setdefault(layer, []).append(warning)
    return {
        layer: {
            "count": len(layer_warnings),
            "by_message": _warning_count_summary(layer_warnings, by_layer=False),
            "warnings": layer_warnings,
        }
        for layer, layer_warnings in sorted(grouped.items())
    }


def _warning_group_count_summary(
    warnings: list[str],
    layer_groups: dict[str, str],
) -> list[dict[str, object]]:
    counts: Counter[str] = Counter()
    for warning in warnings:
        layer, separator, _message = warning.partition(": ")
        if not separator or not layer:
            continue
        counts[layer_groups.get(layer, "other")] += 1
    return [
        {"group": group, "count": count}
        for group, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _warning_group_message_count_summary(
    warnings: list[str],
    layer_groups: dict[str, str],
) -> list[dict[str, object]]:
    counts: Counter[tuple[str, str]] = Counter()
    for warning in warnings:
        layer, separator, message = warning.partition(": ")
        if not separator or not layer or not message:
            continue
        counts[(layer_groups.get(layer, "other"), message)] += 1
    return [
        {"group": group, "message": message, "count": count}
        for (group, message), count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0][0], item[0][1]),
        )
    ]


def _annotate_warning_summary_groups(
    summary: dict[str, object],
    layer_groups: dict[str, str],
) -> None:
    warnings = summary.get("warnings")
    if not isinstance(warnings, list):
        return
    warning_strings = [str(warning) for warning in warnings]
    summary["by_layer_group"] = _warning_group_count_summary(
        warning_strings,
        layer_groups,
    )
    summary["by_layer_group_and_message"] = _warning_group_message_count_summary(
        warning_strings,
        layer_groups,
    )


def _annotate_probe_warning_groups(
    probe: dict[str, object],
    qfit_summary: dict[str, object],
    layer_groups: dict[str, str],
) -> None:
    summary = probe.get("summary") if isinstance(probe.get("summary"), dict) else {}
    _annotate_warning_summary_groups(summary, layer_groups)
    reduced_from_qfit = probe.setdefault("reduced_from_qfit", {})
    if isinstance(reduced_from_qfit, dict):
        reduced_from_qfit["by_layer_group"] = _warning_reduction_summary(
            list(qfit_summary.get("by_layer_group") or []),
            list(summary.get("by_layer_group") or []),
            key="group",
        )


def _warning_layer_unresolved_property_summaries(
    warnings: list[str],
    layers: list[dict[str, object]],
    *,
    exclude_properties: set[str] | None = None,
    exclude_properties_by_layer: dict[str, set[str]] | None = None,
) -> dict[str, list[dict[str, object]]]:
    excluded = exclude_properties or set()
    excluded_by_layer = exclude_properties_by_layer or {}
    warning_layer_ids = {
        layer
        for warning in warnings
        for layer, separator, _message in [warning.partition(": ")]
        if separator and layer
    }
    property_counts: Counter[str] = Counter()
    group_property_counts: Counter[tuple[str, str]] = Counter()
    for layer in layers:
        layer_id = str(layer.get("id") or "")
        if layer_id not in warning_layer_ids:
            continue
        group = str(layer.get("group") or "other")
        layer_excluded = excluded | excluded_by_layer.get(layer_id, set())
        for property_name in _iter_property_names(layer, "qfit_unresolved"):
            if property_name in layer_excluded:
                continue
            property_counts[property_name] += 1
            group_property_counts[(group, property_name)] += 1
    return {
        "by_property": [
            {"property": property_name, "count": count}
            for property_name, count in sorted(
                property_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ],
        "by_layer_group_and_property": [
            {"group": group, "property": property_name, "count": count}
            for (group, property_name), count in sorted(
                group_property_counts.items(),
                key=lambda item: (-item[1], item[0][0], item[0][1]),
            )
        ],
    }


def _annotate_probe_remaining_warning_unresolved_properties(
    probe: dict[str, object],
    layers: list[dict[str, object]],
    *,
    exclude_properties: set[str] | None = None,
    exclude_properties_by_layer: dict[str, set[str]] | None = None,
) -> None:
    summary = probe.get("summary") if isinstance(probe.get("summary"), dict) else {}
    warnings = summary.get("warnings")
    if isinstance(warnings, list):
        probe["remaining_warning_layers_by_unresolved_property"] = _warning_layer_unresolved_property_summaries(
            [str(warning) for warning in warnings],
            layers,
            exclude_properties=exclude_properties,
            exclude_properties_by_layer=exclude_properties_by_layer,
        )


def _annotate_qgis_warning_group_summaries(
    layers: list[dict[str, object]],
    warning_report: dict[str, object],
) -> None:
    layer_groups = {str(layer.get("id") or ""): str(layer.get("group") or "other") for layer in layers}
    raw_summary = warning_report.get("raw") if isinstance(warning_report.get("raw"), dict) else {}
    qfit_summary = (
        warning_report.get("qfit_preprocessed")
        if isinstance(warning_report.get("qfit_preprocessed"), dict)
        else {}
    )
    _annotate_warning_summary_groups(raw_summary, layer_groups)
    _annotate_warning_summary_groups(qfit_summary, layer_groups)
    reduced = warning_report.setdefault("reduced_by_qfit", {})
    if isinstance(reduced, dict):
        reduced["by_layer_group"] = _warning_reduction_summary(
            list(raw_summary.get("by_layer_group") or []),
            list(qfit_summary.get("by_layer_group") or []),
            key="group",
        )
    filterless_probe = (
        warning_report.get("without_filters_probe")
        if isinstance(warning_report.get("without_filters_probe"), dict)
        else {}
    )
    _annotate_probe_warning_groups(filterless_probe, qfit_summary, layer_groups)
    _annotate_probe_remaining_warning_unresolved_properties(
        filterless_probe,
        layers,
        exclude_properties={"filter"},
    )
    icon_image_probe = (
        warning_report.get("without_icon_images_probe")
        if isinstance(warning_report.get("without_icon_images_probe"), dict)
        else {}
    )
    _annotate_probe_warning_groups(icon_image_probe, qfit_summary, layer_groups)
    line_opacity_probe = (
        warning_report.get(_SCALAR_LINE_OPACITY_PROBE_KEY)
        if isinstance(warning_report.get(_SCALAR_LINE_OPACITY_PROBE_KEY), dict)
        else {}
    )
    _annotate_probe_warning_groups(line_opacity_probe, qfit_summary, layer_groups)
    line_dasharray_probe = (
        warning_report.get(_LITERAL_LINE_DASHARRAY_PROBE_KEY)
        if isinstance(warning_report.get(_LITERAL_LINE_DASHARRAY_PROBE_KEY), dict)
        else {}
    )
    _annotate_probe_warning_groups(line_dasharray_probe, qfit_summary, layer_groups)
    _annotate_probe_remaining_warning_unresolved_properties(
        line_dasharray_probe,
        layers,
        exclude_properties={_LINE_DASHARRAY_PROPERTY},
    )
    symbol_spacing_probe = (
        warning_report.get(_SCALAR_SYMBOL_SPACING_PROBE_KEY)
        if isinstance(warning_report.get(_SCALAR_SYMBOL_SPACING_PROBE_KEY), dict)
        else {}
    )
    _annotate_probe_warning_groups(symbol_spacing_probe, qfit_summary, layer_groups)
    symbol_spacing_replaced_layers = {
        str(layer_id)
        for layer_id in symbol_spacing_probe.get(_SYMBOL_SPACING_REPLACED_LAYERS_KEY, [])
        if layer_id
    }
    _annotate_probe_remaining_warning_unresolved_properties(
        symbol_spacing_probe,
        layers,
        exclude_properties_by_layer={
            layer_id: {"layout.symbol-spacing"} for layer_id in symbol_spacing_replaced_layers
        },
    )
    sprite_context_probe = (
        warning_report.get(_SPRITE_CONTEXT_PROBE_KEY)
        if isinstance(warning_report.get(_SPRITE_CONTEXT_PROBE_KEY), dict)
        else {}
    )
    _annotate_probe_warning_groups(sprite_context_probe, qfit_summary, layer_groups)
    _annotate_probe_remaining_warning_unresolved_properties(sprite_context_probe, layers)


def _annotate_layers_with_qgis_warnings(
    layers: list[dict[str, object]],
    warning_report: dict[str, object],
) -> None:
    qfit_summary = warning_report.get("qfit_preprocessed")
    if not isinstance(qfit_summary, dict):
        return
    warnings = qfit_summary.get("warnings")
    if not isinstance(warnings, list):
        return
    warnings_by_layer = _qgis_warning_summaries_by_layer([str(warning) for warning in warnings])
    for layer in layers:
        layer_id = str(layer.get("id") or "")
        layer_warnings = warnings_by_layer.get(layer_id)
        if layer_warnings is not None:
            layer["qgis_converter_warnings"] = layer_warnings


def _warning_reduction_summary(
    raw_counts: list[dict[str, object]],
    qfit_counts: list[dict[str, object]],
    *,
    key: str,
) -> list[dict[str, object]]:
    raw_by_key = {str(item.get(key) or ""): int(item.get("count") or 0) for item in raw_counts}
    qfit_by_key = {str(item.get(key) or ""): int(item.get("count") or 0) for item in qfit_counts}
    reductions = []
    for name, raw_count in raw_by_key.items():
        if not name:
            continue
        qfit_count = qfit_by_key.get(name, 0)
        reduced_count = raw_count - qfit_count
        if reduced_count > 0:
            reductions.append(
                {
                    key: name,
                    "raw_count": raw_count,
                    "qfit_count": qfit_count,
                    "reduced_count": reduced_count,
                }
            )
    return sorted(reductions, key=lambda item: (-int(item["reduced_count"]), str(item[key])))


def _qgis_warning_reduction_report(
    raw_summary: dict[str, object],
    qfit_summary: dict[str, object],
) -> dict[str, object]:
    raw_by_message = list(raw_summary.get("by_message") or [])
    qfit_by_message = list(qfit_summary.get("by_message") or [])
    raw_by_layer = list(raw_summary.get("by_layer") or [])
    qfit_by_layer = list(qfit_summary.get("by_layer") or [])
    return {
        "by_message": _warning_reduction_summary(raw_by_message, qfit_by_message, key="message"),
        "by_layer": _warning_reduction_summary(raw_by_layer, qfit_by_layer, key="layer"),
    }


def _style_without_filters(style_definition: dict[str, object]) -> tuple[dict[str, object], int]:
    """Return a copy of a style with layer filters removed for converter diagnostics only."""
    style_without_filters = copy.deepcopy(style_definition)
    removed_count = 0
    layers = style_without_filters.get("layers")
    if not isinstance(layers, list):
        return style_without_filters, removed_count
    for layer in layers:
        if not isinstance(layer, dict) or "filter" not in layer:
            continue
        layer.pop("filter")
        removed_count += 1
    return style_without_filters, removed_count


def _style_without_icon_images(style_definition: dict[str, object]) -> tuple[dict[str, object], int]:
    """Return a copy of a style with symbol icons removed for converter diagnostics only."""
    style_without_icons = copy.deepcopy(style_definition)
    removed_count = 0
    layers = style_without_icons.get("layers")
    if not isinstance(layers, list):
        return style_without_icons, removed_count
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        layout = layer.get("layout")
        if not isinstance(layout, dict) or "icon-image" not in layout:
            continue
        layout.pop("icon-image")
        removed_count += 1
    return style_without_icons, removed_count


def _is_mapbox_expression(value: object) -> bool:
    return isinstance(value, list) and bool(_expression_operator_names(value))


def _iter_layer_expression_property_paths(layer: dict[str, object]) -> Iterable[str]:
    if _is_mapbox_expression(layer.get("filter")):
        yield "filter"
    for section_name in _SYMBOLOGY_SECTIONS:
        section = layer.get(section_name)
        if not isinstance(section, dict):
            continue
        for property_name, value in section.items():
            if _is_mapbox_expression(value):
                yield f"{section_name}.{property_name}"


def _removable_expression_property_paths(style_definition: dict[str, object]) -> list[str]:
    """Return qfit-preprocessed property paths worth removal-testing in converter diagnostics."""
    layers = style_definition.get("layers")
    if not isinstance(layers, list):
        return []
    return sorted(
        {
            property_path
            for layer in layers
            if isinstance(layer, dict)
            for property_path in _iter_layer_expression_property_paths(layer)
        }
    )


def _style_without_property_path(
    style_definition: dict[str, object],
    property_path: str,
) -> tuple[dict[str, object], int]:
    """Return a copy with one property path removed from every layer for diagnostics only."""
    stripped_style = copy.deepcopy(style_definition)
    removed_count = 0
    layers = stripped_style.get("layers")
    if not isinstance(layers, list):
        return stripped_style, removed_count

    if "." not in property_path:
        for layer in layers:
            if isinstance(layer, dict) and _is_mapbox_expression(layer.get(property_path)):
                layer.pop(property_path)
                removed_count += 1
        return stripped_style, removed_count

    section_name, property_name = property_path.split(".", 1)
    if section_name not in _SYMBOLOGY_SECTIONS:
        return stripped_style, removed_count
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        section = layer.get(section_name)
        if not isinstance(section, dict) or not _is_mapbox_expression(section.get(property_name)):
            continue
        section.pop(property_name)
        removed_count += 1
    return stripped_style, removed_count


def _property_value_for_layer_path(layer: dict[str, object], property_path: str) -> object:
    if "." not in property_path:
        return layer.get(property_path, _ABSENT)
    section_name, property_name = property_path.split(".", 1)
    section = layer.get(section_name)
    if isinstance(section, dict):
        return section.get(property_name, _ABSENT)
    return _ABSENT


def _expression_property_values_by_layer(
    style_definition: dict[str, object],
    property_path: str,
) -> dict[str, object]:
    layers = style_definition.get("layers")
    if not isinstance(layers, list):
        return {}
    values: dict[str, object] = {}
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        layer_id = str(layer.get("id") or "")
        value = _property_value_for_layer_path(layer, property_path)
        if layer_id and value is not _ABSENT and _is_mapbox_expression(value):
            values[layer_id] = value
    return values


def _layer_reductions_with_property_values(
    layer_reductions: list[dict[str, object]],
    property_values_by_layer: dict[str, object],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for layer_reduction in layer_reductions:
        row = dict(layer_reduction)
        layer_id = str(row.get("layer") or "")
        if layer_id in property_values_by_layer:
            row["property_value"] = property_values_by_layer[layer_id]
        rows.append(row)
    return rows


def _layer_groups_by_id(style_definition: dict[str, object]) -> dict[str, str]:
    layers = style_definition.get("layers")
    if not isinstance(layers, list):
        return {}
    return {
        str(layer.get("id") or ""): _layer_group(layer)
        for layer in layers
        if isinstance(layer, dict) and layer.get("id")
    }


def _warning_summary_layer_group_reductions(
    before_summary: dict[str, object],
    after_summary: dict[str, object],
    layer_groups: dict[str, str],
) -> list[dict[str, object]]:
    before_warnings = [str(warning) for warning in before_summary.get("warnings") or []]
    after_warnings = [str(warning) for warning in after_summary.get("warnings") or []]
    return _warning_reduction_summary(
        _warning_group_count_summary(before_warnings, layer_groups),
        _warning_group_count_summary(after_warnings, layer_groups),
        key="group",
    )


def _warning_count_for_message(summary: dict[str, object], message: str) -> int:
    for item in summary.get("by_message") or []:
        if item.get("message") == message:
            return int(item.get("count") or 0)
    return 0


def _qgis_property_removal_impact_report(
    qfit_preprocessed_style: dict[str, object],
    qfit_summary: dict[str, object],
    *,
    property_paths: list[str] | None = None,
) -> dict[str, object]:
    """Remove one expression property at a time to rank residual converter-warning causes."""
    qfit_warning_count = int(qfit_summary.get("count") or 0)
    qfit_skipping_count = _warning_count_for_message(qfit_summary, _FILTER_PARSE_UNSUPPORTED_EXPRESSION_MESSAGE)
    layer_groups = _layer_groups_by_id(qfit_preprocessed_style)
    rows: list[dict[str, object]] = []
    for property_path in property_paths or _removable_expression_property_paths(qfit_preprocessed_style):
        stripped_style, removed_count = _style_without_property_path(qfit_preprocessed_style, property_path)
        if removed_count <= 0:
            continue
        stripped_warnings = _collect_qgis_converter_warnings(stripped_style)
        stripped_summary = _qgis_warning_summary(stripped_warnings)
        stripped_warning_count = len(stripped_warnings)
        by_layer = _layer_reductions_with_property_values(
            _warning_reduction_summary(
                list(qfit_summary.get("by_layer") or []),
                list(stripped_summary.get("by_layer") or []),
                key="layer",
            ),
            _expression_property_values_by_layer(qfit_preprocessed_style, property_path),
        )
        rows.append(
            {
                "property": property_path,
                "property_count_removed": removed_count,
                "warning_count_after_removal": stripped_warning_count,
                "warning_count_delta_from_qfit": qfit_warning_count - stripped_warning_count,
                "skipping_unsupported_expression_delta": qfit_skipping_count
                - _warning_count_for_message(stripped_summary, _FILTER_PARSE_UNSUPPORTED_EXPRESSION_MESSAGE),
                "reduced_from_qfit": {
                    "by_message": _warning_reduction_summary(
                        list(qfit_summary.get("by_message") or []),
                        list(stripped_summary.get("by_message") or []),
                        key="message",
                    ),
                    "by_layer_group": _warning_summary_layer_group_reductions(
                        qfit_summary,
                        stripped_summary,
                        layer_groups,
                    ),
                    "by_layer": by_layer,
                },
            }
        )
    return {
        "candidate_property_count": len(rows),
        "by_property": sorted(
            rows,
            key=lambda row: (
                -int(row["warning_count_delta_from_qfit"]),
                str(row["property"]),
            ),
        ),
    }


def _clamp_opacity_value(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0.0, min(float(value), 1.0))


def _representative_interpolate_output(expr: list[object]) -> object | None:
    if len(expr) < 5:
        return None
    if expr[2] != ["zoom"]:
        return expr[4]
    stops: list[tuple[float, object]] = []
    for index in range(3, len(expr) - 1, 2):
        stop = expr[index]
        if isinstance(stop, bool) or not isinstance(stop, (int, float)):
            continue
        stops.append((float(stop), expr[index + 1]))
    if not stops:
        return None
    return min(stops, key=lambda stop: abs(stop[0] - _EXPRESSION_PROBE_ZOOM))[1]


def _representative_step_output(expr: list[object]) -> object | None:
    if len(expr) < 3:
        return None
    if expr[1] != ["zoom"]:
        return expr[2]
    value = expr[2]
    for index in range(3, len(expr) - 1, 2):
        stop = expr[index]
        if isinstance(stop, bool) or not isinstance(stop, (int, float)):
            continue
        if _EXPRESSION_PROBE_ZOOM < float(stop):
            break
        value = expr[index + 1]
    return value


def _extract_line_opacity_scalar(expr: object) -> float | None:
    scalar = _clamp_opacity_value(expr)
    if scalar is not None:
        return scalar
    if not isinstance(expr, list) or not expr:
        return None

    op = expr[0]
    if op == "interpolate":
        return _extract_line_opacity_scalar(_representative_interpolate_output(expr))
    if op == "step":
        return _extract_line_opacity_scalar(_representative_step_output(expr))
    if op == "match":
        return _extract_line_opacity_scalar(expr[-1]) if len(expr) >= 4 else None
    if op in {"case", "coalesce"}:
        for item in reversed(expr[1:]):
            scalar = _extract_line_opacity_scalar(item)
            if scalar is not None:
                return scalar
    return None


def _style_with_scalar_line_opacity_details(
    style_definition: dict[str, object],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Return a copy with line-opacity expressions scalarized plus diagnostic rows."""
    style_with_scalar_opacity = copy.deepcopy(style_definition)
    replacement_rows: list[dict[str, object]] = []
    layers = style_with_scalar_opacity.get("layers")
    if not isinstance(layers, list):
        return style_with_scalar_opacity, replacement_rows
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        paint = layer.get("paint")
        if not isinstance(paint, dict):
            continue
        line_opacity = paint.get("line-opacity")
        if not isinstance(line_opacity, list):
            continue
        scalar_opacity = _extract_line_opacity_scalar(line_opacity)
        if scalar_opacity is None:
            continue
        replacement_rows.append(
            {
                "layer": str(layer.get("id") or ""),
                "group": _layer_group(layer),
                "operator_signature": _operator_signature(line_opacity),
                "scalar_line_opacity": scalar_opacity,
                "line_opacity": line_opacity,
            }
        )
        paint["line-opacity"] = scalar_opacity
    replacement_rows.sort(key=lambda row: (str(row["group"]), str(row["layer"])))
    return style_with_scalar_opacity, replacement_rows


def _style_with_scalar_line_opacity(style_definition: dict[str, object]) -> tuple[dict[str, object], int]:
    """Return a copy with line-opacity expressions scalarized for converter diagnostics only."""
    style_with_scalar_opacity, replacement_rows = _style_with_scalar_line_opacity_details(style_definition)
    replaced_count = len(replacement_rows)
    return style_with_scalar_opacity, replaced_count


def _literal_line_dasharray(value: object) -> list[object] | None:
    if _is_literal_number_array(value):
        return list(value)
    if not isinstance(value, list) or not value:
        return None
    if value[0] == "literal" and len(value) == 2 and _is_literal_number_array(value[1]):
        return list(value[1])
    return None


def _first_line_dasharray_literal(candidates: Iterable[object]) -> list[object] | None:
    for candidate in candidates:
        literal_dasharray = _extract_line_dasharray_literal(candidate)
        if literal_dasharray is not None:
            return literal_dasharray
    return None


def _case_expression_output_candidates(expr: list[object]) -> list[object]:
    if len(expr) < 4:
        return []
    return [expr[-1], *(expr[index] for index in range(len(expr) - 2, 1, -2))]


def _extract_line_dasharray_literal(expr: object) -> list[object] | None:
    literal_dasharray = _literal_line_dasharray(expr)
    if literal_dasharray is not None:
        return literal_dasharray
    if not isinstance(expr, list) or not expr:
        return None

    op = expr[0]
    if op == "interpolate":
        return _extract_line_dasharray_literal(_representative_interpolate_output(expr))
    if op == "step":
        return _extract_line_dasharray_literal(_representative_step_output(expr))
    if op == "match":
        return _extract_line_dasharray_literal(expr[-1]) if len(expr) >= 5 else None
    if op == "case":
        return _first_line_dasharray_literal(_case_expression_output_candidates(expr))
    if op == "coalesce":
        return _first_line_dasharray_literal(reversed(expr[1:]))
    return None


def _style_with_literal_line_dasharray(style_definition: dict[str, object]) -> tuple[dict[str, object], int]:
    """Return a copy with line-dasharray expressions literalized for converter diagnostics only."""
    style_with_literal_dasharray = copy.deepcopy(style_definition)
    replaced_count = 0
    layers = style_with_literal_dasharray.get("layers")
    if not isinstance(layers, list):
        return style_with_literal_dasharray, replaced_count
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        paint = layer.get("paint")
        if not isinstance(paint, dict):
            continue
        line_dasharray = paint.get("line-dasharray")
        if not isinstance(line_dasharray, list) or _is_literal_number_array(line_dasharray):
            continue
        literal_dasharray = _extract_line_dasharray_literal(line_dasharray)
        if literal_dasharray is None:
            continue
        paint["line-dasharray"] = literal_dasharray
        replaced_count += 1
    return style_with_literal_dasharray, replaced_count


def _extract_symbol_spacing_scalar(expr: object) -> float | None:
    if isinstance(expr, bool):
        return None
    if isinstance(expr, (int, float)):
        return float(expr) if expr >= 0 else None
    if not isinstance(expr, list) or not expr:
        return None

    op = expr[0]
    if op == "interpolate":
        return _extract_symbol_spacing_scalar(_representative_interpolate_output(expr))
    if op == "step":
        return _extract_symbol_spacing_scalar(_representative_step_output(expr))
    if op == "match":
        return _extract_symbol_spacing_scalar(expr[-1]) if len(expr) >= 5 else None
    if op == "case":
        return _first_symbol_spacing_scalar(_case_expression_output_candidates(expr))
    if op == "coalesce":
        return _first_symbol_spacing_scalar(reversed(expr[1:]))
    return None


def _first_symbol_spacing_scalar(candidates: Iterable[object]) -> float | None:
    for candidate in candidates:
        scalar = _extract_symbol_spacing_scalar(candidate)
        if scalar is not None:
            return scalar
    return None


def _style_with_scalar_symbol_spacing_details(style_definition: dict[str, object]) -> tuple[dict[str, object], list[str]]:
    """Return a copy with symbol-spacing expressions scalarized for converter diagnostics only."""
    style_with_scalar_spacing = copy.deepcopy(style_definition)
    replaced_layer_ids: list[str] = []
    layers = style_with_scalar_spacing.get("layers")
    if not isinstance(layers, list):
        return style_with_scalar_spacing, replaced_layer_ids
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        layout = layer.get("layout")
        if not isinstance(layout, dict):
            continue
        symbol_spacing = layout.get("symbol-spacing")
        if not isinstance(symbol_spacing, list):
            continue
        scalar_spacing = _extract_symbol_spacing_scalar(symbol_spacing)
        if scalar_spacing is None:
            continue
        layout["symbol-spacing"] = scalar_spacing
        replaced_layer_ids.append(str(layer.get("id") or ""))
    return style_with_scalar_spacing, replaced_layer_ids


def _style_with_scalar_symbol_spacing(style_definition: dict[str, object]) -> tuple[dict[str, object], int]:
    style_with_scalar_spacing, replaced_layer_ids = _style_with_scalar_symbol_spacing_details(style_definition)
    return style_with_scalar_spacing, len(replaced_layer_ids)


def _qgis_conversion_context_with_sprites(sprite_resources: MapboxSpriteResources):
    from qgis.PyQt.QtGui import QImage  # noqa: PLC0415
    from qgis.core import QgsMapBoxGlStyleConversionContext, Qgis  # noqa: PLC0415

    ctx = QgsMapBoxGlStyleConversionContext()
    ctx.setTargetUnit(Qgis.RenderUnit.Millimeters)
    ctx.setPixelSizeConversionFactor(25.4 / 96.0)
    sprite_image = QImage()
    sprite_image_loaded = sprite_image.loadFromData(sprite_resources.image_bytes)
    if sprite_image_loaded:
        argb_format = getattr(QImage, "Format_ARGB32", None)
        if argb_format is not None:
            sprite_image = sprite_image.convertToFormat(argb_format)
        ctx.setSprites(sprite_image, sprite_resources.definitions)
    return ctx, sprite_image_loaded


def _collect_qgis_converter_warnings_with_sprite_context(
    style_definition: dict[str, object],
    sprite_resources: MapboxSpriteResources,
) -> tuple[list[str], bool]:
    from qgis.core import QgsMapBoxGlStyleConverter  # noqa: PLC0415

    converter = QgsMapBoxGlStyleConverter()
    sprite_context, sprite_image_loaded = _qgis_conversion_context_with_sprites(sprite_resources)
    converter.convert(style_definition, sprite_context)
    return list(converter.warnings()), sprite_image_loaded


def _collect_qgis_converter_warnings(
    style_definition: dict[str, object],
) -> list[str]:
    from qgis.core import QgsMapBoxGlStyleConverter  # noqa: PLC0415

    converter = QgsMapBoxGlStyleConverter()
    converter.convert(style_definition)
    return list(converter.warnings())


def _qgis_converter_warning_report(
    *,
    raw_style: dict[str, object],
    qfit_preprocessed_style: dict[str, object],
    sprite_resources: MapboxSpriteResources | None = None,
    include_property_removal_impact: bool = False,
    include_filter_parse_support: bool = False,
) -> dict[str, object]:
    # Converter-only audits do not render, but headless environments can still abort when Qt
    # defaults to xcb. Prefer offscreen unless the caller explicitly chose another platform.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qgis.core import QgsApplication  # noqa: PLC0415

    app = QgsApplication.instance()
    created_app = False
    if app is None:
        app = QgsApplication([], False)
        app.initQgis()
        created_app = True

    try:
        raw_warnings = _collect_qgis_converter_warnings(raw_style)
        qfit_warnings = _collect_qgis_converter_warnings(qfit_preprocessed_style)
        filterless_style, filter_count_removed = _style_without_filters(qfit_preprocessed_style)
        filterless_warnings = _collect_qgis_converter_warnings(filterless_style)
        iconless_style, icon_image_count_removed = _style_without_icon_images(qfit_preprocessed_style)
        iconless_warnings = _collect_qgis_converter_warnings(iconless_style)
        scalar_line_opacity_style, line_opacity_scalarization_rows = _style_with_scalar_line_opacity_details(
            qfit_preprocessed_style
        )
        scalar_line_opacity_warnings = _collect_qgis_converter_warnings(scalar_line_opacity_style)
        literal_line_dasharray_style, line_dasharray_expression_count_replaced = _style_with_literal_line_dasharray(
            qfit_preprocessed_style
        )
        literal_line_dasharray_warnings = _collect_qgis_converter_warnings(literal_line_dasharray_style)
        scalar_symbol_spacing_style, symbol_spacing_replaced_layers = _style_with_scalar_symbol_spacing_details(
            qfit_preprocessed_style
        )
        scalar_symbol_spacing_warnings = _collect_qgis_converter_warnings(scalar_symbol_spacing_style)
        property_removal_impact = None
        if include_property_removal_impact:
            property_removal_impact = _qgis_property_removal_impact_report(
                qfit_preprocessed_style,
                _qgis_warning_summary(qfit_warnings),
            )
        filter_parse_support = None
        if include_filter_parse_support:
            filter_parse_support = _qgis_filter_parse_support_report(qfit_preprocessed_style)
        sprite_image_loaded = None
        if sprite_resources is not None:
            sprite_context_warnings, sprite_image_loaded = _collect_qgis_converter_warnings_with_sprite_context(
                qfit_preprocessed_style,
                sprite_resources,
            )
        else:
            sprite_context_warnings = None
    finally:
        if created_app:
            app.exitQgis()
    raw_summary = _qgis_warning_summary(raw_warnings)
    qfit_summary = _qgis_warning_summary(qfit_warnings)
    filterless_summary = _qgis_warning_summary(filterless_warnings)
    iconless_summary = _qgis_warning_summary(iconless_warnings)
    scalar_line_opacity_summary = _qgis_warning_summary(scalar_line_opacity_warnings)
    literal_line_dasharray_summary = _qgis_warning_summary(literal_line_dasharray_warnings)
    scalar_symbol_spacing_summary = _qgis_warning_summary(scalar_symbol_spacing_warnings)
    report = {
        "raw": raw_summary,
        "qfit_preprocessed": qfit_summary,
        "warning_count_delta": len(raw_warnings) - len(qfit_warnings),
        "reduced_by_qfit": _qgis_warning_reduction_report(raw_summary, qfit_summary),
        "without_filters_probe": {
            "filter_count_removed": filter_count_removed,
            "summary": filterless_summary,
            "warning_count_delta_from_qfit": len(qfit_warnings) - len(filterless_warnings),
            "reduced_from_qfit": _qgis_warning_reduction_report(qfit_summary, filterless_summary),
        },
        "without_icon_images_probe": {
            "icon_image_count_removed": icon_image_count_removed,
            "summary": iconless_summary,
            "warning_count_delta_from_qfit": len(qfit_warnings) - len(iconless_warnings),
            "reduced_from_qfit": _qgis_warning_reduction_report(qfit_summary, iconless_summary),
        },
        _SCALAR_LINE_OPACITY_PROBE_KEY: {
            _LINE_OPACITY_EXPRESSION_COUNT_KEY: len(line_opacity_scalarization_rows),
            _LINE_OPACITY_SCALARIZATION_ROWS_KEY: line_opacity_scalarization_rows,
            "summary": scalar_line_opacity_summary,
            "warning_count_delta_from_qfit": len(qfit_warnings) - len(scalar_line_opacity_warnings),
            "reduced_from_qfit": _qgis_warning_reduction_report(qfit_summary, scalar_line_opacity_summary),
        },
        _LITERAL_LINE_DASHARRAY_PROBE_KEY: {
            _LINE_DASHARRAY_EXPRESSION_COUNT_KEY: line_dasharray_expression_count_replaced,
            "summary": literal_line_dasharray_summary,
            "warning_count_delta_from_qfit": len(qfit_warnings) - len(literal_line_dasharray_warnings),
            "reduced_from_qfit": _qgis_warning_reduction_report(qfit_summary, literal_line_dasharray_summary),
        },
        _SCALAR_SYMBOL_SPACING_PROBE_KEY: {
            _SYMBOL_SPACING_EXPRESSION_COUNT_KEY: len(symbol_spacing_replaced_layers),
            _SYMBOL_SPACING_REPLACED_LAYERS_KEY: symbol_spacing_replaced_layers,
            "summary": scalar_symbol_spacing_summary,
            "warning_count_delta_from_qfit": len(qfit_warnings) - len(scalar_symbol_spacing_warnings),
            "reduced_from_qfit": _qgis_warning_reduction_report(qfit_summary, scalar_symbol_spacing_summary),
        },
    }
    if property_removal_impact is not None:
        report[_PROPERTY_REMOVAL_IMPACT_PROBE_KEY] = property_removal_impact
    if filter_parse_support is not None:
        report[_FILTER_PARSE_SUPPORT_PROBE_KEY] = filter_parse_support
    if sprite_context_warnings is not None:
        sprite_context_summary = _qgis_warning_summary(sprite_context_warnings)
        report[_SPRITE_CONTEXT_PROBE_KEY] = {
            _SPRITE_CONTEXT_DEFINITION_COUNT_KEY: len(sprite_resources.definitions),
            _SPRITE_CONTEXT_IMAGE_LOADED_KEY: bool(sprite_image_loaded),
            "summary": sprite_context_summary,
            "warning_count_delta_from_qfit": len(qfit_warnings) - len(sprite_context_warnings),
            "reduced_from_qfit": _qgis_warning_reduction_report(qfit_summary, sprite_context_summary),
        }
    return report


def build_style_audit(
    style_definition: dict[str, object],
    *,
    config: StyleAuditConfig | None = None,
) -> dict[str, object]:
    resolved_config = config or StyleAuditConfig()
    style_copy = copy.deepcopy(style_definition)
    simplified_style = simplify_mapbox_style_expressions(style_copy)
    simplified_layers = {
        str(layer.get("id") or ""): layer
        for layer in simplified_style.get("layers", [])
        if isinstance(layer, dict)
    }
    layers = [
        build_layer_audit(layer=layer, simplified_layer=simplified_layers.get(str(layer.get("id") or "")))
        for layer in style_definition.get("layers", [])
        if isinstance(layer, dict)
    ]
    label_density_candidates = _label_density_candidate_rows(layers)
    road_trail_hierarchy_candidates = _road_trail_hierarchy_candidate_rows(layers)
    terrain_landcover_candidates = _terrain_landcover_candidate_rows(layers)
    generated_at = resolved_config.generated_at or dt.datetime.now(dt.timezone.utc)
    audit = {
        "style": {
            "owner": resolved_config.style_owner,
            "id": resolved_config.style_id,
            "label": _style_label(owner=resolved_config.style_owner, style_id=resolved_config.style_id),
        },
        "generated_at": generated_at.isoformat(),
        "layer_count": len(layers),
        "summary": {
            "qfit_simplifies_by_property": _property_count_summary(layers, "qfit_simplifies"),
            "qfit_simplifies_by_layer_group_and_property": _property_group_count_summary(
                layers,
                "qfit_simplifies",
            ),
            "qfit_unresolved_by_property": _property_count_summary(layers, "qfit_unresolved"),
            "qfit_unresolved_by_layer_group_and_property": _property_group_count_summary(
                layers,
                "qfit_unresolved",
            ),
            "qfit_unresolved_expression_operators_by_property": _expression_operator_count_summary(layers),
            "qfit_unresolved_expression_operators_by_layer_group_and_property": (
                _expression_operator_group_count_summary(layers)
            ),
            "qfit_unresolved_filter_expression_signatures_by_layer_group": (
                _filter_expression_signature_group_summary(layers)
            ),
            "label_density_candidates_by_layer_group": _count_rows_by_key(label_density_candidates, "group"),
            "label_density_candidates": label_density_candidates,
            _ROAD_TRAIL_HIERARCHY_CANDIDATES_BY_SOURCE_LAYER_KEY: _count_rows_by_key(
                road_trail_hierarchy_candidates,
                "source_layer",
            ),
            _ROAD_TRAIL_HIERARCHY_CANDIDATES_BY_TYPE_KEY: _count_rows_by_key(
                road_trail_hierarchy_candidates,
                "type",
            ),
            _ROAD_TRAIL_HIERARCHY_SIMPLIFIED_BY_PROPERTY_KEY: _count_row_values(
                road_trail_hierarchy_candidates,
                _QFIT_SIMPLIFIED_CONTROL_PROPERTIES_KEY,
            ),
            _ROAD_TRAIL_HIERARCHY_QGIS_DEPENDENT_BY_PROPERTY_KEY: _count_row_values(
                road_trail_hierarchy_candidates,
                _QGIS_DEPENDENT_CONTROL_PROPERTIES_KEY,
            ),
            _ROAD_TRAIL_HIERARCHY_CANDIDATES_KEY: road_trail_hierarchy_candidates,
            _TERRAIN_LANDCOVER_CANDIDATES_BY_SOURCE_LAYER_KEY: _count_rows_by_key(
                terrain_landcover_candidates,
                "source_layer",
            ),
            _TERRAIN_LANDCOVER_CANDIDATES_BY_TYPE_KEY: _count_rows_by_key(
                terrain_landcover_candidates,
                "type",
            ),
            _TERRAIN_LANDCOVER_SIMPLIFIED_BY_PROPERTY_KEY: _count_row_values(
                terrain_landcover_candidates,
                _QFIT_SIMPLIFIED_CONTROL_PROPERTIES_KEY,
            ),
            _TERRAIN_LANDCOVER_QGIS_DEPENDENT_BY_PROPERTY_KEY: _count_row_values(
                terrain_landcover_candidates,
                _QGIS_DEPENDENT_CONTROL_PROPERTIES_KEY,
            ),
            _TERRAIN_LANDCOVER_CANDIDATES_KEY: terrain_landcover_candidates,
        },
        "layers": layers,
    }
    if (
        resolved_config.include_qgis_converter_warnings
        or resolved_config.include_qgis_property_removal_impact
        or resolved_config.include_qgis_filter_parse_support
    ):
        warning_report = _qgis_converter_warning_report(
            raw_style=style_definition,
            qfit_preprocessed_style=simplified_style,
            sprite_resources=resolved_config.sprite_resources,
            include_property_removal_impact=resolved_config.include_qgis_property_removal_impact,
            include_filter_parse_support=resolved_config.include_qgis_filter_parse_support,
        )
        _annotate_qgis_warning_group_summaries(layers, warning_report)
        audit["qgis_converter_warnings"] = warning_report
        _annotate_layers_with_qgis_warnings(layers, warning_report)
    return audit


def _markdown_list(items: list[str], *, empty: str = "—") -> str:
    if not items:
        return empty
    return "<br>".join(items)


def _markdown_yes_no(value: object) -> str:
    return "yes" if value is True else "no"


def _markdown_change_list(changes: list[dict[str, str]], *, empty: str = "—") -> str:
    if not changes:
        return empty
    return "<br>".join(
        f"`{change['property']}`: `{change['from']}` → `{change['to']}`" for change in changes
    )


def _markdown_unresolved_list(unresolved: list[dict[str, object]], *, empty: str = "—") -> str:
    if not unresolved:
        return empty
    return "<br>".join(
        f"`{item['property']}`: {item['reason']}" for item in unresolved
    )


def _markdown_layer_qgis_warnings(layer_obj: dict[str, object]) -> str:
    report = layer_obj.get("qgis_converter_warnings")
    if not isinstance(report, dict) or not report.get("count"):
        return ""
    messages = report.get("by_message") if isinstance(report.get("by_message"), list) else []
    warning_parts = [f"QGIS converter warnings: {report.get('count', 0)}"]
    for item in messages[:3]:
        if not isinstance(item, dict):
            continue
        warning_parts.append(f"`{item.get('message', '')}` ({item.get('count', 0)})")
    return "<br>".join(warning_parts)


def _markdown_layer_unresolved(layer_obj: dict[str, object]) -> str:
    raw_unresolved = list(layer_obj.get("qfit_unresolved") or [])
    qgis_warnings = _markdown_layer_qgis_warnings(layer_obj)
    if not qgis_warnings:
        return _markdown_unresolved_list(raw_unresolved)
    if not raw_unresolved:
        return qgis_warnings
    return f"{_markdown_unresolved_list(raw_unresolved)}<br>{qgis_warnings}"


def _markdown_count_table(items: list[dict[str, object]], *, empty: str = "—") -> list[str]:
    if not items:
        return [empty, ""]
    lines = ["| Property | # Layers |", "| --- | ---: |"]
    for item in items:
        lines.append(f"| `{item.get('property', '')}` | {item.get('count', 0)} |")
    lines.append("")
    return lines


def _markdown_group_count_table(items: list[dict[str, object]], *, empty: str = "—") -> list[str]:
    if not items:
        return [empty, ""]
    lines = ["| Layer group | Property | # Layers |", _MARKDOWN_THREE_COLUMN_COUNT_SEPARATOR]
    for item in items:
        lines.append(
            "| `{group}` | `{property_name}` | {count} |".format(
                group=item.get("group", ""),
                property_name=item.get("property", ""),
                count=item.get("count", 0),
            )
        )
    lines.append("")
    return lines


def _markdown_expression_operator_table(items: list[dict[str, object]], *, empty: str = "—") -> list[str]:
    if not items:
        return [empty, ""]
    lines = ["| Property | Operator | # Layers |", _MARKDOWN_THREE_COLUMN_COUNT_SEPARATOR]
    for item in items:
        lines.append(
            "| `{property_name}` | `{operator}` | {count} |".format(
                property_name=item.get("property", ""),
                operator=item.get("operator", ""),
                count=item.get("count", 0),
            )
        )
    lines.append("")
    return lines


def _markdown_group_expression_operator_table(
    items: list[dict[str, object]],
    *,
    empty: str = "—",
) -> list[str]:
    if not items:
        return [empty, ""]
    lines = ["| Layer group | Property | Operator | # Layers |", "| --- | --- | --- | ---: |"]
    for item in items:
        lines.append(
            "| `{group}` | `{property_name}` | `{operator}` | {count} |".format(
                group=item.get("group", ""),
                property_name=item.get("property", ""),
                operator=item.get("operator", ""),
                count=item.get("count", 0),
            )
        )
    lines.append("")
    return lines


def _markdown_filter_signature_group_table(
    items: list[dict[str, object]],
    *,
    count_label: str = "# Layers",
    empty: str = "—",
) -> list[str]:
    if not items:
        return [empty, ""]
    lines = [
        f"| Layer group | Operators | {count_label} | Example layers |",
        "| --- | --- | ---: | --- |",
    ]
    for item in items:
        example_layers = item.get("example_layers") if isinstance(item.get("example_layers"), list) else []
        examples = ", ".join(f"`{layer_id}`" for layer_id in example_layers)
        lines.append(
            "| `{group}` | `{operators}` | {count} | {examples} |".format(
                group=item.get("group", ""),
                operators=item.get("operator_signature", ""),
                count=item.get("count", 0),
                examples=examples or "—",
            )
        )
    lines.append("")
    return lines


def _markdown_label_density_candidate_table(rows: list[dict[str, object]], *, empty: str = "—") -> list[str]:
    if not rows:
        return [empty, ""]
    lines = [
        "| Layer group | Layer | Source layer | Zoom | Filter operators | Label controls | QGIS-dependent controls |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| `{group}` | `{layer}` | `{source_layer}` | {zoom} | `{filter_operators}` | {controls} | {unresolved} |".format(
                group=row.get("group", ""),
                layer=row.get("layer", ""),
                source_layer=row.get("source_layer", ""),
                zoom=row.get("zoom_band", _ALL_ZOOMS_BAND),
                filter_operators=row.get("filter_operator_signature", _NO_OPERATOR_SIGNATURE),
                controls=_markdown_list(list(row.get("label_control_properties") or [])),
                unresolved=_markdown_list(list(row.get(_QGIS_DEPENDENT_CONTROL_PROPERTIES_KEY) or [])),
            )
        )
    lines.append("")
    return lines


def _markdown_road_trail_hierarchy_candidate_table(
    rows: list[dict[str, object]],
    *,
    empty: str = "—",
) -> list[str]:
    if not rows:
        return [empty, ""]
    lines = [
        (
            "| Layer | Type | Source layer | Zoom | Filter operators | Road/trail controls | "
            "Simplified/substituted by qfit | QGIS-dependent controls |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            (
                "| `{layer}` | `{layer_type}` | `{source_layer}` | {zoom} | `{filter_operators}` | "
                "{controls} | {simplified} | {unresolved} |"
            ).format(
                layer=row.get("layer", ""),
                layer_type=row.get("type", ""),
                source_layer=row.get("source_layer", ""),
                zoom=row.get("zoom_band", _ALL_ZOOMS_BAND),
                filter_operators=row.get("filter_operator_signature", _NO_OPERATOR_SIGNATURE),
                controls=_markdown_list(list(row.get(_ROAD_TRAIL_CONTROL_PROPERTIES_KEY) or [])),
                simplified=_markdown_list(list(row.get(_QFIT_SIMPLIFIED_CONTROL_PROPERTIES_KEY) or [])),
                unresolved=_markdown_list(list(row.get(_QGIS_DEPENDENT_CONTROL_PROPERTIES_KEY) or [])),
            )
        )
    lines.append("")
    return lines


def _markdown_terrain_landcover_candidate_table(
    rows: list[dict[str, object]],
    *,
    empty: str = "—",
) -> list[str]:
    if not rows:
        return [empty, ""]
    lines = [
        (
            "| Layer | Type | Source layer | Zoom | Filter operators | Terrain/landcover controls | "
            "Simplified/substituted by qfit | QGIS-dependent controls |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            (
                "| `{layer}` | `{layer_type}` | `{source_layer}` | {zoom} | `{filter_operators}` | "
                "{controls} | {simplified} | {unresolved} |"
            ).format(
                layer=row.get("layer", ""),
                layer_type=row.get("type", ""),
                source_layer=row.get("source_layer", ""),
                zoom=row.get("zoom_band", _ALL_ZOOMS_BAND),
                filter_operators=row.get("filter_operator_signature", _NO_OPERATOR_SIGNATURE),
                controls=_markdown_list(list(row.get(_TERRAIN_LANDCOVER_CONTROL_PROPERTIES_KEY) or [])),
                simplified=_markdown_list(list(row.get(_QFIT_SIMPLIFIED_CONTROL_PROPERTIES_KEY) or [])),
                unresolved=_markdown_list(list(row.get(_QGIS_DEPENDENT_CONTROL_PROPERTIES_KEY) or [])),
            )
        )
    lines.append("")
    return lines


def _markdown_named_count_table(
    items: list[dict[str, object]],
    *,
    key: str,
    label: str,
    empty: str = "—",
) -> list[str]:
    if not items:
        return [empty, ""]
    lines = [f"| {label} | Count |", "| --- | ---: |"]
    for item in items:
        lines.append(f"| `{item.get(key, '')}` | {item.get('count', 0)} |")
    lines.append("")
    return lines


def _markdown_group_message_count_table(
    items: list[dict[str, object]],
    *,
    empty: str = "—",
) -> list[str]:
    if not items:
        return [empty, ""]
    lines = ["| Layer group | Message | Count |", _MARKDOWN_THREE_COLUMN_COUNT_SEPARATOR]
    for item in items:
        lines.append(
            "| `{group}` | `{message}` | {count} |".format(
                group=item.get("group", ""),
                message=item.get("message", ""),
                count=item.get("count", 0),
            )
        )
    lines.append("")
    return lines


def _markdown_warning_reduction_table(
    items: list[dict[str, object]],
    *,
    key: str,
    label: str,
    before_label: str = "Raw",
    after_label: str = "After qfit",
    empty: str = "—",
) -> list[str]:
    if not items:
        return [empty, ""]
    lines = [f"| {label} | {before_label} | {after_label} | Reduced |", "| --- | ---: | ---: | ---: |"]
    for item in items:
        lines.append(
            "| `{name}` | {raw_count} | {qfit_count} | {reduced_count} |".format(
                name=item.get(key, ""),
                raw_count=item.get("raw_count", 0),
                qfit_count=item.get("qfit_count", 0),
                reduced_count=item.get("reduced_count", 0),
            )
        )
    lines.append("")
    return lines


def _markdown_filterless_probe(probe: object) -> list[str]:
    if not isinstance(probe, dict):
        return []
    summary = probe.get("summary") if isinstance(probe.get("summary"), dict) else {}
    reduced = probe.get("reduced_from_qfit") if isinstance(probe.get("reduced_from_qfit"), dict) else {}
    unresolved_property_summary = (
        probe.get("remaining_warning_layers_by_unresolved_property")
        if isinstance(probe.get("remaining_warning_layers_by_unresolved_property"), dict)
        else {}
    )
    lines = [
        "#### Diagnostic filter-removal probe",
        "",
        "This is not a rendering-safe qfit preprocessing mode; filters control feature inclusion.",
        "Use it only as an upper-bound signal for how much converter warning debt is tied to filters.",
        "",
        f"Filters removed in probe: {probe.get('filter_count_removed', 0)}",
        f"Warnings after removing filters: {summary.get('count', 0)}",
        f"{_MARKDOWN_WARNING_DELTA_FROM_QFIT_LABEL}: {probe.get('warning_count_delta_from_qfit', 0)}",
        "",
    ]
    by_message = list(reduced.get("by_message") or [])
    by_group = list(reduced.get("by_layer_group") or [])
    remaining_by_message = list(summary.get("by_message") or [])
    remaining_by_group = list(summary.get("by_layer_group") or [])
    remaining_by_group_message = list(summary.get("by_layer_group_and_message") or [])
    remaining_by_layer = list(summary.get("by_layer") or [])
    unresolved_by_property = list(unresolved_property_summary.get("by_property") or [])
    unresolved_by_group_property = list(unresolved_property_summary.get("by_layer_group_and_property") or [])
    if by_message:
        lines.extend(
            [
                "##### Probe reductions by message",
                "",
                *_markdown_warning_reduction_table(
                    by_message,
                    key="message",
                    label=_MARKDOWN_MESSAGE_LABEL,
                    before_label="Before probe",
                    after_label="Without filters",
                ),
            ]
        )
    if by_group:
        lines.extend(
            [
                "##### Probe reductions by layer group",
                "",
                *_markdown_warning_reduction_table(
                    by_group,
                    key="group",
                    label=_MARKDOWN_LAYER_GROUP_LABEL,
                    before_label="Before probe",
                    after_label="Without filters",
                ),
            ]
        )
    lines.extend(
        [
            "##### Remaining probe warnings by message",
            "",
            *_markdown_named_count_table(
                remaining_by_message,
                key="message",
                label=_MARKDOWN_MESSAGE_LABEL,
            ),
            "##### Remaining probe warnings by layer group",
            "",
            *_markdown_named_count_table(
                remaining_by_group,
                key="group",
                label=_MARKDOWN_LAYER_GROUP_LABEL,
            ),
            "##### Remaining probe warnings by layer group and message",
            "",
            *_markdown_group_message_count_table(remaining_by_group_message),
            "##### Remaining probe warnings by layer",
            "",
            *_markdown_named_count_table(
                remaining_by_layer,
                key="layer",
                label=_MARKDOWN_LAYER_LABEL,
            ),
            "##### Remaining probe warning layers by unresolved qfit property",
            "",
            *_markdown_count_table(unresolved_by_property),
            "##### Remaining probe warning layers by layer group and unresolved qfit property",
            "",
            *_markdown_group_count_table(unresolved_by_group_property),
        ]
    )
    return lines


def _markdown_icon_image_probe(probe: object) -> list[str]:
    if not isinstance(probe, dict):
        return []
    summary = probe.get("summary") if isinstance(probe.get("summary"), dict) else {}
    reduced = probe.get("reduced_from_qfit") if isinstance(probe.get("reduced_from_qfit"), dict) else {}
    lines = [
        "#### Diagnostic icon-image removal probe",
        "",
        "This is not a rendering-safe qfit preprocessing mode; icons and sprites carry feature meaning.",
        "Use it only to estimate how much converter warning debt is tied to Mapbox sprite/icon handling.",
        "",
        f"Icon images removed in probe: {probe.get('icon_image_count_removed', 0)}",
        f"Warnings after removing icon images: {summary.get('count', 0)}",
        f"{_MARKDOWN_WARNING_DELTA_FROM_QFIT_LABEL}: {probe.get('warning_count_delta_from_qfit', 0)}",
        "",
    ]
    by_message = list(reduced.get("by_message") or [])
    by_group = list(reduced.get("by_layer_group") or [])
    if by_message:
        lines.extend(
            [
                "##### Icon probe reductions by message",
                "",
                *_markdown_warning_reduction_table(
                    by_message,
                    key="message",
                    label=_MARKDOWN_MESSAGE_LABEL,
                    before_label="Before icon probe",
                    after_label="Without icon-image",
                ),
            ]
        )
    if by_group:
        lines.extend(
            [
                "##### Icon probe reductions by layer group",
                "",
                *_markdown_warning_reduction_table(
                    by_group,
                    key="group",
                    label=_MARKDOWN_LAYER_GROUP_LABEL,
                    before_label="Before icon probe",
                    after_label="Without icon-image",
                ),
            ]
        )
    lines.extend(
        [
            "##### Remaining icon probe warnings by message",
            "",
            *_markdown_named_count_table(
                list(summary.get("by_message") or []),
                key="message",
                label=_MARKDOWN_MESSAGE_LABEL,
            ),
            "##### Remaining icon probe warnings by layer group",
            "",
            *_markdown_named_count_table(
                list(summary.get("by_layer_group") or []),
                key="group",
                label=_MARKDOWN_LAYER_GROUP_LABEL,
            ),
            "##### Remaining icon probe warnings by layer group and message",
            "",
            *_markdown_group_message_count_table(list(summary.get("by_layer_group_and_message") or [])),
            "##### Remaining icon probe warnings by layer",
            "",
            *_markdown_named_count_table(
                list(summary.get("by_layer") or []),
                key="layer",
                label=_MARKDOWN_LAYER_LABEL,
            ),
        ]
    )
    return lines


@dataclass(frozen=True)
class _ProbeMarkdownConfig:
    title: str
    safety_note: str
    guidance: str
    count_key: str
    count_label: str
    warning_count_label: str
    section_label: str
    remaining_label: str
    before_label: str
    after_label: str
    include_unresolved_properties: bool = False


def _markdown_expression_property_probe(probe: object, *, config: _ProbeMarkdownConfig) -> list[str]:
    if not isinstance(probe, dict):
        return []
    summary = probe.get("summary") if isinstance(probe.get("summary"), dict) else {}
    reduced = probe.get("reduced_from_qfit") if isinstance(probe.get("reduced_from_qfit"), dict) else {}
    lines = [
        config.title,
        "",
        config.safety_note,
        config.guidance,
        "",
        f"{config.count_label}: {probe.get(config.count_key, 0)}",
        f"{config.warning_count_label}: {summary.get('count', 0)}",
        f"{_MARKDOWN_WARNING_DELTA_FROM_QFIT_LABEL}: {probe.get('warning_count_delta_from_qfit', 0)}",
        "",
    ]
    _extend_markdown_probe_reduction_sections(lines, reduced=reduced, config=config)
    lines.extend(_markdown_probe_remaining_warning_sections(summary, config=config))
    lines.extend(_markdown_probe_unresolved_property_sections(probe, config=config))
    return lines


def _markdown_line_opacity_scalarization_table(
    rows: list[dict[str, object]],
    *,
    limit: int = 25,
) -> list[str]:
    if not rows:
        return ["—"]
    lines = [
        "| Layer group | Layer | Original operators | Scalar opacity | Original expression |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for row in rows[:limit]:
        lines.append(
            "| `{group}` | `{layer}` | `{operators}` | {scalar:g} | <code>{expression}</code> |".format(
                group=html.escape(str(row.get("group") or "other")),
                layer=html.escape(str(row.get("layer") or "")),
                operators=html.escape(str(row.get("operator_signature") or _NO_OPERATOR_SIGNATURE)),
                scalar=float(row.get("scalar_line_opacity") or 0.0),
                expression=html.escape(_compact_json(row.get("line_opacity"))),
            )
        )
    if len(rows) > limit:
        lines.append(f"| … | … | … | … | {len(rows) - limit} more scalarized line-opacity expressions omitted |")
    lines.append("")
    return lines


def _insert_line_opacity_scalarization_details(
    lines: list[str],
    rows: list[dict[str, object]],
) -> list[str]:
    if not rows:
        return lines
    detail_lines = [
        "##### Scalar line-opacity replacements",
        "",
        *_markdown_line_opacity_scalarization_table(rows),
    ]
    for index, line in enumerate(lines):
        if line.startswith("##### Remaining line-opacity probe warnings"):
            return [*lines[:index], *detail_lines, *lines[index:]]
    return [*lines, *detail_lines]


def _extend_markdown_probe_reduction_sections(
    lines: list[str],
    *,
    reduced: dict[str, object],
    config: _ProbeMarkdownConfig,
) -> None:
    by_message = list(reduced.get("by_message") or [])
    by_group = list(reduced.get("by_layer_group") or [])
    if by_message:
        lines.extend(
            [
                f"##### {config.section_label} probe reductions by message",
                "",
                *_markdown_warning_reduction_table(
                    by_message,
                    key="message",
                    label=_MARKDOWN_MESSAGE_LABEL,
                    before_label=config.before_label,
                    after_label=config.after_label,
                ),
            ]
        )
    if by_group:
        lines.extend(
            [
                f"##### {config.section_label} probe reductions by layer group",
                "",
                *_markdown_warning_reduction_table(
                    by_group,
                    key="group",
                    label=_MARKDOWN_LAYER_GROUP_LABEL,
                    before_label=config.before_label,
                    after_label=config.after_label,
                ),
            ]
        )


def _markdown_probe_remaining_warning_sections(
    summary: dict[str, object],
    *,
    config: _ProbeMarkdownConfig,
) -> list[str]:
    return [
        f"##### Remaining {config.remaining_label} probe warnings by message",
        "",
        *_markdown_named_count_table(
            list(summary.get("by_message") or []),
            key="message",
            label=_MARKDOWN_MESSAGE_LABEL,
        ),
        f"##### Remaining {config.remaining_label} probe warnings by layer group",
        "",
        *_markdown_named_count_table(
            list(summary.get("by_layer_group") or []),
            key="group",
            label=_MARKDOWN_LAYER_GROUP_LABEL,
        ),
        f"##### Remaining {config.remaining_label} probe warnings by layer group and message",
        "",
        *_markdown_group_message_count_table(list(summary.get("by_layer_group_and_message") or [])),
        f"##### Remaining {config.remaining_label} probe warnings by layer",
        "",
        *_markdown_named_count_table(
            list(summary.get("by_layer") or []),
            key="layer",
            label=_MARKDOWN_LAYER_LABEL,
        ),
    ]


def _markdown_probe_unresolved_property_sections(
    probe: dict[str, object],
    *,
    config: _ProbeMarkdownConfig,
) -> list[str]:
    if not config.include_unresolved_properties:
        return []
    unresolved_property_summary = (
        probe.get("remaining_warning_layers_by_unresolved_property")
        if isinstance(probe.get("remaining_warning_layers_by_unresolved_property"), dict)
        else {}
    )
    unresolved_by_property = list(unresolved_property_summary.get("by_property") or [])
    unresolved_by_group_property = list(unresolved_property_summary.get("by_layer_group_and_property") or [])
    return [
        f"##### Remaining {config.remaining_label} probe warning layers by unresolved qfit property",
        "",
        *_markdown_count_table(unresolved_by_property),
        (
            f"##### Remaining {config.remaining_label} probe warning layers "
            "by layer group and unresolved qfit property"
        ),
        "",
        *_markdown_group_count_table(unresolved_by_group_property),
    ]


def _markdown_line_opacity_probe(probe: object) -> list[str]:
    lines = _markdown_expression_property_probe(
        probe,
        config=_ProbeMarkdownConfig(
            title="#### Diagnostic line-opacity scalarization probe",
            safety_note=(
                "This is not a rendering-safe qfit preprocessing mode; "
                "line opacity preserves zoom/data-driven cartographic emphasis."
            ),
            guidance=(
                "Use it only to estimate how much converter warning debt is tied to "
                "Mapbox line-opacity expressions."
            ),
            count_key=_LINE_OPACITY_EXPRESSION_COUNT_KEY,
            count_label="Line opacity expressions replaced in probe",
            warning_count_label="Warnings after scalar line opacity",
            section_label="Line-opacity",
            remaining_label="line-opacity",
            before_label="Before line-opacity probe",
            after_label="Scalar line-opacity",
        ),
    )
    rows = list(probe.get(_LINE_OPACITY_SCALARIZATION_ROWS_KEY) or []) if isinstance(probe, dict) else []
    return _insert_line_opacity_scalarization_details(lines, rows)


def _markdown_line_dasharray_probe(probe: object) -> list[str]:
    return _markdown_expression_property_probe(
        probe,
        config=_ProbeMarkdownConfig(
            title="#### Diagnostic line-dasharray literalization probe",
            safety_note=(
                "This is not a rendering-safe qfit preprocessing mode; "
                "line dash arrays preserve zoom/data-driven cartographic distinctions."
            ),
            guidance=(
                "Use it only to estimate how much converter warning debt is tied to "
                "Mapbox line-dasharray expressions."
            ),
            count_key=_LINE_DASHARRAY_EXPRESSION_COUNT_KEY,
            count_label="Line dasharray expressions replaced in probe",
            warning_count_label="Warnings after literal line dasharray",
            section_label="Line-dasharray",
            remaining_label="line-dasharray",
            before_label="Before line-dasharray probe",
            after_label="Literal line-dasharray",
            include_unresolved_properties=True,
        ),
    )


def _markdown_symbol_spacing_probe(probe: object) -> list[str]:
    return _markdown_expression_property_probe(
        probe,
        config=_ProbeMarkdownConfig(
            title="#### Diagnostic symbol-spacing scalarization probe",
            safety_note=(
                "This is not a rendering-safe qfit preprocessing mode; "
                "symbol spacing preserves zoom/data-driven label density."
            ),
            guidance=(
                "Use it only to estimate how much converter warning debt is tied to "
                "Mapbox symbol-spacing expressions."
            ),
            count_key=_SYMBOL_SPACING_EXPRESSION_COUNT_KEY,
            count_label="Symbol spacing expressions replaced in probe",
            warning_count_label="Warnings after scalar symbol spacing",
            section_label="Symbol-spacing",
            remaining_label="symbol-spacing",
            before_label="Before symbol-spacing probe",
            after_label="Scalar symbol-spacing",
            include_unresolved_properties=True,
        ),
    )


def _markdown_property_removal_impact_table(rows: list[dict[str, object]]) -> list[str]:
    if not rows:
        return ["—", ""]
    lines = [
        "| Property | Removed from layers | Warnings after removal | Warning delta | Skipping-expression delta |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| `{property}` | {removed} | {warnings} | {delta} | {skipping_delta} |".format(
                property=row.get("property", ""),
                removed=row.get("property_count_removed", 0),
                warnings=row.get("warning_count_after_removal", 0),
                delta=row.get("warning_count_delta_from_qfit", 0),
                skipping_delta=row.get("skipping_unsupported_expression_delta", 0),
            )
        )
    lines.append("")
    return lines


def _markdown_code_cell(text: str) -> str:
    escaped = html.escape(text, quote=False).replace("|", "&#124;")
    return f"<code>{escaped}</code>"


def _property_removal_impact_layer_reductions(
    rows: list[dict[str, object]],
    *,
    per_property_limit: int = 5,
    total_limit: int = 25,
) -> list[dict[str, object]]:
    if total_limit <= 0:
        return []
    layer_rows: list[dict[str, object]] = []
    for row in rows:
        if int(row.get("warning_count_delta_from_qfit") or 0) <= 0:
            continue
        reduced = row.get("reduced_from_qfit") if isinstance(row.get("reduced_from_qfit"), dict) else {}
        by_layer = list(reduced.get("by_layer") or [])
        for layer_reduction in by_layer[:per_property_limit]:
            layer_rows.append({"property": row.get("property", ""), **layer_reduction})
            if len(layer_rows) >= total_limit:
                return layer_rows
    return layer_rows


def _property_removal_impact_group_reductions(
    rows: list[dict[str, object]],
    *,
    per_property_limit: int = 5,
    total_limit: int = 25,
) -> list[dict[str, object]]:
    if total_limit <= 0:
        return []
    group_rows: list[dict[str, object]] = []
    for row in rows:
        if int(row.get("warning_count_delta_from_qfit") or 0) <= 0:
            continue
        reduced = row.get("reduced_from_qfit") if isinstance(row.get("reduced_from_qfit"), dict) else {}
        by_group = list(reduced.get("by_layer_group") or [])
        for group_reduction in by_group[:per_property_limit]:
            group_rows.append({"property": row.get("property", ""), **group_reduction})
            if len(group_rows) >= total_limit:
                return group_rows
    return group_rows


def _markdown_property_removal_impact_group_table(rows: list[dict[str, object]]) -> list[str]:
    if not rows:
        return []
    lines = [
        "##### Top warning reductions by property and layer group",
        "",
        "| Property | Layer group | Before removal | After removal | Reduced |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| `{property}` | `{group}` | {raw_count} | {qfit_count} | {reduced_count} |".format(
                property=row.get("property", ""),
                group=row.get("group", ""),
                raw_count=row.get("raw_count", 0),
                qfit_count=row.get("qfit_count", 0),
                reduced_count=row.get("reduced_count", 0),
            )
        )
    lines.append("")
    return lines


def _markdown_property_removal_impact_layer_table(rows: list[dict[str, object]]) -> list[str]:
    if not rows:
        return []
    lines = [
        "##### Top warning reductions by property and layer",
        "",
        "| Property | Layer | Expression | Before removal | After removal | Reduced |",
        "| --- | --- | --- | ---: | ---: | ---: |",
    ]
    for row in rows:
        expression = "—"
        if "property_value" in row:
            expression = _markdown_code_cell(_compact_json(row.get("property_value")))
        lines.append(
            "| `{property}` | `{layer}` | {expression} | {raw_count} | {qfit_count} | {reduced_count} |".format(
                property=row.get("property", ""),
                layer=row.get("layer", ""),
                expression=expression,
                raw_count=row.get("raw_count", 0),
                qfit_count=row.get("qfit_count", 0),
                reduced_count=row.get("reduced_count", 0),
            )
        )
    lines.append("")
    return lines


def _markdown_property_removal_impact_probe(probe: object) -> list[str]:
    if not isinstance(probe, dict):
        return []
    rows = list(probe.get("by_property") or [])
    return [
        "#### Diagnostic unresolved-property removal impact probe",
        "",
        (
            "This removes one remaining expression-bearing property at a time from the "
            "qfit-preprocessed style to rank converter-warning causes. It is not a "
            "rendering-safe qfit preprocessing mode."
        ),
        "",
        f"Candidate properties tested: {probe.get('candidate_property_count', 0)}",
        "",
        *_markdown_property_removal_impact_table(rows),
        *_markdown_property_removal_impact_group_table(_property_removal_impact_group_reductions(rows)),
        *_markdown_property_removal_impact_layer_table(_property_removal_impact_layer_reductions(rows)),
    ]


def _markdown_filter_parse_unsupported_layer_table(
    rows: list[dict[str, object]],
    *,
    limit: int = 25,
) -> list[str]:
    if not rows:
        return ["—", ""]
    shown_rows = rows[:limit]
    lines = [
        "| Layer | Group | Type/source-layer | Operators | Unsupported warnings | Filter |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for row in shown_rows:
        layer_type = str(row.get("type") or "")
        source_layer = str(row.get("source_layer") or "")
        type_source = f"{layer_type} / {source_layer}" if source_layer else layer_type
        lines.append(
            "| `{layer}` | `{group}` | `{type_source}` | `{operators}` | {warnings} | {filter_value} |".format(
                layer=row.get("layer", ""),
                group=row.get("group", ""),
                type_source=type_source,
                operators=row.get("operator_signature", ""),
                warnings=row.get("unsupported_warning_count", 0),
                filter_value=_markdown_code_cell(_compact_json(row.get("filter"))),
            )
        )
    if len(rows) > limit:
        lines.append(f"| … | … | … | … | … | {len(rows) - limit} more unsupported filter layers omitted |")
    lines.append("")
    return lines


def _markdown_filter_parse_parser_friendly_filter_table(
    rows: list[dict[str, object]],
    *,
    limit: int = 25,
) -> list[str]:
    if not rows:
        return ["—", ""]
    shown_rows = rows[:limit]
    lines = [
        "| Layer | Original operators | Zoom-normalized operators | Parser-friendly operators | Parser-friendly filter |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in shown_rows:
        lines.append(
            "| `{layer}` | `{original}` | `{zoom_normalized}` | `{parser_friendly}` | {filter_value} |".format(
                layer=row.get("layer", ""),
                original=row.get("original_operator_signature", ""),
                zoom_normalized=row.get("zoom_normalized_operator_signature", ""),
                parser_friendly=row.get("operator_signature", ""),
                filter_value=_markdown_code_cell(_compact_json(row.get("filter"))),
            )
        )
    if len(rows) > limit:
        lines.append(f"| … | … | … | … | {len(rows) - limit} more parser-friendly filters omitted |")
    lines.append("")
    return lines


def _markdown_filter_parse_parser_friendly_unsupported_filter_table(
    rows: list[dict[str, object]],
    *,
    limit: int = 25,
) -> list[str]:
    if not rows:
        return ["—", ""]
    shown_rows = rows[:limit]
    lines = [
        (
            "| Layer | Original operators | Zoom-normalized operators | Parser-friendly operators | "
            "Unsupported warnings | Messages | Parser-friendly filter |"
        ),
        "| --- | --- | --- | --- | ---: | --- | --- |",
    ]
    for row in shown_rows:
        lines.append(
            "| `{layer}` | `{original}` | `{zoom_normalized}` | `{parser_friendly}` | {warnings} | "
            "{messages} | {filter_value} |".format(
                layer=row.get("layer", ""),
                original=row.get("original_operator_signature", ""),
                zoom_normalized=row.get("zoom_normalized_operator_signature", ""),
                parser_friendly=row.get("operator_signature", ""),
                warnings=row.get("unsupported_warning_count", 0),
                messages=_markdown_code_cell(
                    ", ".join(
                        str(message_row.get("message") or "")
                        for message_row in row.get("unsupported_warning_messages", []) or []
                    )
                ),
                filter_value=_markdown_code_cell(_compact_json(row.get("filter"))),
            )
        )
    if len(rows) > limit:
        lines.append(f"| … | … | … | … | … | … | {len(rows) - limit} more rejected filters omitted |")
    lines.append("")
    return lines


def _markdown_filter_parse_unsupported_part_table(
    rows: list[dict[str, object]],
    *,
    limit: int = 25,
) -> list[str]:
    if not rows:
        return ["—", ""]
    shown_rows = rows[:limit]
    lines = [
        "| Layer | Parent | Part | Operators | Unsupported warnings | Filter part |",
        "| --- | --- | ---: | --- | ---: | --- |",
    ]
    for row in shown_rows:
        lines.append(
            "| `{layer}` | `{parent}` | {part} | `{operators}` | {warnings} | {filter_value} |".format(
                layer=row.get("layer", ""),
                parent=row.get("parent_operator", ""),
                part=row.get("part_index", 0),
                operators=row.get("operator_signature", ""),
                warnings=row.get("unsupported_warning_count", 0),
                filter_value=_markdown_code_cell(_compact_json(row.get("filter"))),
            )
        )
    if len(rows) > limit:
        lines.append(f"| … | … | … | … | … | {len(rows) - limit} more unsupported filter parts omitted |")
    lines.append("")
    return lines


def _markdown_filter_parse_zoom_normalized_part_table(
    rows: list[dict[str, object]],
    *,
    limit: int = 25,
) -> list[str]:
    if not rows:
        return ["—", ""]
    shown_rows = rows[:limit]
    lines = [
        "| Layer | Part | Original operators | Normalized operators | Normalized filter part |",
        "| --- | ---: | --- | --- | --- |",
    ]
    for row in shown_rows:
        lines.append(
            "| `{layer}` | {part} | `{original}` | `{normalized}` | {filter_value} |".format(
                layer=row.get("layer", ""),
                part=row.get("part_index", 0),
                original=row.get("original_operator_signature", ""),
                normalized=row.get("operator_signature", ""),
                filter_value=_markdown_code_cell(_compact_json(row.get("filter"))),
            )
        )
    if len(rows) > limit:
        lines.append(f"| … | … | … | … | {len(rows) - limit} more zoom-normalized parts omitted |")
    lines.append("")
    return lines


def _markdown_filter_parse_zoom_normalized_unsupported_part_table(
    rows: list[dict[str, object]],
    *,
    limit: int = 25,
) -> list[str]:
    if not rows:
        return ["—", ""]
    shown_rows = rows[:limit]
    lines = [
        "| Layer | Part | Original operators | Normalized operators | Unsupported warnings | Messages | Normalized filter part |",
        "| --- | ---: | --- | --- | ---: | --- | --- |",
    ]
    for row in shown_rows:
        lines.append(
            "| `{layer}` | {part} | `{original}` | `{normalized}` | {warnings} | {messages} | {filter_value} |".format(
                layer=row.get("layer", ""),
                part=row.get("part_index", 0),
                original=row.get("original_operator_signature", ""),
                normalized=row.get("operator_signature", ""),
                warnings=row.get("unsupported_warning_count", 0),
                messages=_markdown_code_cell(
                    ", ".join(
                        str(message_row.get("message") or "")
                        for message_row in row.get("unsupported_warning_messages", []) or []
                    )
                ),
                filter_value=_markdown_code_cell(_compact_json(row.get("filter"))),
            )
        )
    if len(rows) > limit:
        lines.append(f"| … | … | … | … | … | … | {len(rows) - limit} more rejected parts omitted |")
    lines.append("")
    return lines


def _markdown_filter_parse_parser_friendly_part_table(
    rows: list[dict[str, object]],
    *,
    limit: int = 25,
) -> list[str]:
    if not rows:
        return ["—", ""]
    shown_rows = rows[:limit]
    lines = [
        "| Layer | Part | Zoom-normalized operators | Parser-friendly operators | Parser-friendly filter part |",
        "| --- | ---: | --- | --- | --- |",
    ]
    for row in shown_rows:
        lines.append(
            "| `{layer}` | {part} | `{zoom_normalized}` | `{parser_friendly}` | {filter_value} |".format(
                layer=row.get("layer", ""),
                part=row.get("part_index", 0),
                zoom_normalized=row.get("zoom_normalized_operator_signature", ""),
                parser_friendly=row.get("operator_signature", ""),
                filter_value=_markdown_code_cell(_compact_json(row.get("filter"))),
            )
        )
    if len(rows) > limit:
        lines.append(f"| … | … | … | … | {len(rows) - limit} more parser-friendly parts omitted |")
    lines.append("")
    return lines


def _markdown_filter_parse_parser_friendly_unsupported_part_table(
    rows: list[dict[str, object]],
    *,
    limit: int = 25,
) -> list[str]:
    if not rows:
        return ["—", ""]
    shown_rows = rows[:limit]
    lines = [
        (
            "| Layer | Part | Zoom-normalized operators | Parser-friendly operators | Unsupported warnings | "
            "Messages | Parser-friendly filter part |"
        ),
        "| --- | ---: | --- | --- | ---: | --- | --- |",
    ]
    for row in shown_rows:
        lines.append(
            "| `{layer}` | {part} | `{zoom_normalized}` | `{parser_friendly}` | {warnings} | "
            "{messages} | {filter_value} |".format(
                layer=row.get("layer", ""),
                part=row.get("part_index", 0),
                zoom_normalized=row.get("zoom_normalized_operator_signature", ""),
                parser_friendly=row.get("operator_signature", ""),
                warnings=row.get("unsupported_warning_count", 0),
                messages=_markdown_code_cell(
                    ", ".join(
                        str(message_row.get("message") or "")
                        for message_row in row.get("unsupported_warning_messages", []) or []
                    )
                ),
                filter_value=_markdown_code_cell(_compact_json(row.get("filter"))),
            )
        )
    if len(rows) > limit:
        lines.append(f"| … | … | … | … | … | … | {len(rows) - limit} more rejected parts omitted |")
    lines.append("")
    return lines


def _markdown_filter_parse_warning_message_table(rows: list[dict[str, object]]) -> list[str]:
    return _markdown_named_count_table(rows, key="message", label=_MARKDOWN_MESSAGE_LABEL)


def _filter_parse_probe_rows(probe: dict[str, object], key: str) -> list[dict[str, object]]:
    return list(probe.get(key) or [])


def _markdown_filter_parse_support_count_lines(probe: dict[str, object]) -> list[str]:
    return [
        f"Filter expressions tested: {probe.get('filter_expression_count', 0)}",
        f"Accepted by the QGIS parser probe: {probe.get('qgis_parser_supported_count', 0)}",
        f"Rejected by the QGIS parser probe: {probe.get('qgis_parser_unsupported_count', 0)}",
        (
            "Rejected filters re-tested with zoom + parser-friendly simplifications: "
            f"{probe.get('parser_friendly_filter_count', 0)}"
        ),
        (
            "Changed by zoom + parser-friendly simplification: "
            f"{probe.get('parser_friendly_changed_filter_count', 0)}"
        ),
        (
            "Accepted after zoom + parser-friendly simplification: "
            f"{probe.get('qgis_parser_supported_parser_friendly_filter_count', 0)}"
        ),
        (
            "Still rejected after zoom + parser-friendly simplification: "
            f"{probe.get('qgis_parser_unsupported_parser_friendly_filter_count', 0)}"
        ),
        f"Direct parts tested from rejected boolean filters: {probe.get('direct_filter_part_count', 0)}",
        f"Rejected direct parts: {probe.get('qgis_parser_unsupported_part_count', 0)}",
        (
            "Unsupported direct parts re-tested after zoom-normalizing at "
            f"z{_EXPRESSION_PROBE_ZOOM:g}: {probe.get('zoom_normalized_direct_part_count', 0)}"
        ),
        f"Changed by zoom-normalization: {probe.get('zoom_normalized_changed_direct_part_count', 0)}",
        (
            "Accepted after zoom-normalization: "
            f"{probe.get('qgis_parser_supported_zoom_normalized_part_count', 0)}"
        ),
        (
            "Still rejected after zoom-normalization: "
            f"{probe.get('qgis_parser_unsupported_zoom_normalized_part_count', 0)}"
        ),
        (
            "Still-rejected parts re-tested with parser-friendly simplifications: "
            f"{probe.get('parser_friendly_direct_part_count', 0)}"
        ),
        f"Changed by parser-friendly simplification: {probe.get('parser_friendly_changed_direct_part_count', 0)}",
        (
            "Accepted after parser-friendly simplification: "
            f"{probe.get('qgis_parser_supported_parser_friendly_part_count', 0)}"
        ),
        (
            "Still rejected after parser-friendly simplification: "
            f"{probe.get('qgis_parser_unsupported_parser_friendly_part_count', 0)}"
        ),
    ]


def _markdown_filter_parse_support_summary_sections(probe: dict[str, object]) -> list[str]:
    return [
        "##### Unsupported filter probes by layer group",
        "",
        *_markdown_named_count_table(
            _filter_parse_probe_rows(probe, "unsupported_by_layer_group"),
            key="group",
            label=_MARKDOWN_LAYER_GROUP_LABEL,
        ),
        "##### Unsupported filter parser warnings by message",
        "",
        *_markdown_filter_parse_warning_message_table(
            _filter_parse_probe_rows(probe, "unsupported_by_warning_message")
        ),
        "##### Unsupported filter probes by layer group and operators",
        "",
        *_markdown_filter_signature_group_table(
            _filter_parse_probe_rows(probe, "unsupported_by_layer_group_and_operator_signature"),
            count_label="Unsupported filters",
        ),
        "##### Parser-friendly full filters accepted by layer group and operators",
        "",
        *_markdown_filter_signature_group_table(
            _filter_parse_probe_rows(probe, "parser_friendly_supported_filters_by_layer_group_and_operator_signature"),
            count_label="Accepted filters",
        ),
        "##### Parser-friendly full filters still rejected by layer group and operators",
        "",
        *_markdown_filter_signature_group_table(
            _filter_parse_probe_rows(probe, "parser_friendly_unsupported_filters_by_layer_group_and_operator_signature"),
            count_label="Still rejected filters",
        ),
        "##### Unsupported direct filter parts by layer group and operators",
        "",
        *_markdown_filter_signature_group_table(
            _filter_parse_probe_rows(probe, "unsupported_parts_by_layer_group_and_operator_signature"),
            count_label="Unsupported parts",
        ),
        "##### Zoom-normalized direct parts still rejected by layer group and operators",
        "",
        *_markdown_filter_signature_group_table(
            _filter_parse_probe_rows(probe, "zoom_normalized_unsupported_parts_by_layer_group_and_operator_signature"),
            count_label="Still rejected parts",
        ),
        "##### Parser-friendly direct parts accepted by layer group and operators",
        "",
        *_markdown_filter_signature_group_table(
            _filter_parse_probe_rows(probe, "parser_friendly_supported_parts_by_layer_group_and_operator_signature"),
            count_label="Accepted parts",
        ),
        "##### Parser-friendly direct parts still rejected by layer group and operators",
        "",
        *_markdown_filter_signature_group_table(
            _filter_parse_probe_rows(probe, "parser_friendly_unsupported_parts_by_layer_group_and_operator_signature"),
            count_label="Still rejected parts",
        ),
    ]


def _markdown_filter_parse_support_full_filter_sections(probe: dict[str, object]) -> list[str]:
    return [
        "##### Full filters accepted after zoom + parser-friendly simplification",
        "",
        (
            "This diagnostic applies the same fixed-z12 zoom substitution and parser-friendly simplifications to "
            "entire rejected filters. It remains evidence only, not a rendering-safe qfit preprocessing mode."
        ),
        "",
        *_markdown_filter_parse_parser_friendly_filter_table(
            _filter_parse_probe_rows(probe, "parser_friendly_supported_filters")
        ),
        "##### Full filters still rejected after zoom + parser-friendly simplification",
        "",
        *_markdown_filter_parse_parser_friendly_unsupported_filter_table(
            _filter_parse_probe_rows(probe, "parser_friendly_unsupported_filters")
        ),
    ]


def _markdown_filter_parse_support_direct_part_sections(probe: dict[str, object]) -> list[str]:
    return [
        "##### Direct filter parts accepted after zoom-normalization",
        "",
        (
            "This diagnostic evaluates zoom-driven `step`/`interpolate` filter fragments at "
            f"z{_EXPRESSION_PROBE_ZOOM:g}. It is evidence only, not a rendering-safe rewrite."
        ),
        "",
        *_markdown_filter_parse_zoom_normalized_part_table(
            _filter_parse_probe_rows(probe, "zoom_normalized_supported_parts")
        ),
        "##### Direct filter parts still rejected after zoom-normalization",
        "",
        (
            "These rows distinguish remaining parser gaps from parts that only needed the fixed "
            f"z{_EXPRESSION_PROBE_ZOOM:g} diagnostic zoom substituted."
        ),
        "",
        *_markdown_filter_parse_zoom_normalized_unsupported_part_table(
            _filter_parse_probe_rows(probe, "zoom_normalized_unsupported_parts")
        ),
        "##### Direct filter parts accepted after parser-friendly simplification",
        "",
        (
            "This diagnostic applies parser-friendly, semantics-preserving simplifications to the still-rejected "
            "zoom-normalized parts. It remains evidence only, not a rendering-safe qfit preprocessing mode."
        ),
        "",
        *_markdown_filter_parse_parser_friendly_part_table(
            _filter_parse_probe_rows(probe, "parser_friendly_supported_parts")
        ),
        "##### Direct filter parts still rejected after parser-friendly simplification",
        "",
        *_markdown_filter_parse_parser_friendly_unsupported_part_table(
            _filter_parse_probe_rows(probe, "parser_friendly_unsupported_parts")
        ),
        "##### Unsupported direct filter parts",
        "",
        *_markdown_filter_parse_unsupported_part_table(_filter_parse_probe_rows(probe, "unsupported_parts")),
        "##### Unsupported filter probe layers",
        "",
        *_markdown_filter_parse_unsupported_layer_table(_filter_parse_probe_rows(probe, "unsupported_layers")),
    ]


def _markdown_filter_parse_support_probe(probe: object) -> list[str]:
    if not isinstance(probe, dict):
        return []
    return [
        "#### Diagnostic filter parser support probe",
        "",
        (
            "This isolates each remaining qfit-preprocessed filter in a minimal same-type "
            "QGIS converter style to distinguish filters the QGIS parser accepts from "
            "filters that directly trigger unsupported-expression warnings. It is not a "
            "rendering-safe qfit preprocessing mode."
        ),
        "",
        *_markdown_filter_parse_support_count_lines(probe),
        "",
        *_markdown_filter_parse_support_summary_sections(probe),
        *_markdown_filter_parse_support_full_filter_sections(probe),
        *_markdown_filter_parse_support_direct_part_sections(probe),
    ]


def _markdown_sprite_context_probe(probe: object) -> list[str]:
    if not isinstance(probe, dict):
        return []
    summary = probe.get("summary") if isinstance(probe.get("summary"), dict) else {}
    reduced = probe.get("reduced_from_qfit") if isinstance(probe.get("reduced_from_qfit"), dict) else {}
    unresolved_property_summary = (
        probe.get("remaining_warning_layers_by_unresolved_property")
        if isinstance(probe.get("remaining_warning_layers_by_unresolved_property"), dict)
        else {}
    )
    lines = [
        "#### Runtime sprite context probe",
        "",
        "This mirrors qfit's sprite-aware vector styling path when Mapbox sprite resources are available.",
        "Use it to separate missing sprite resources from remaining unsupported data-driven sprite expressions.",
        "",
        f"Sprite definitions available in probe: {probe.get(_SPRITE_CONTEXT_DEFINITION_COUNT_KEY, 0)}",
        f"Sprite image loaded in probe: {_markdown_yes_no(probe.get(_SPRITE_CONTEXT_IMAGE_LOADED_KEY))}",
        f"Warnings with sprite context: {summary.get('count', 0)}",
        f"{_MARKDOWN_WARNING_DELTA_FROM_QFIT_LABEL}: {probe.get('warning_count_delta_from_qfit', 0)}",
        "",
    ]
    by_message = list(reduced.get("by_message") or [])
    by_group = list(reduced.get("by_layer_group") or [])
    unresolved_by_property = list(unresolved_property_summary.get("by_property") or [])
    unresolved_by_group_property = list(unresolved_property_summary.get("by_layer_group_and_property") or [])
    if by_message:
        lines.extend(
            [
                "##### Sprite context reductions by message",
                "",
                *_markdown_warning_reduction_table(
                    by_message,
                    key="message",
                    label=_MARKDOWN_MESSAGE_LABEL,
                    before_label="Before sprite context",
                    after_label="With sprite context",
                ),
            ]
        )
    if by_group:
        lines.extend(
            [
                "##### Sprite context reductions by layer group",
                "",
                *_markdown_warning_reduction_table(
                    by_group,
                    key="group",
                    label=_MARKDOWN_LAYER_GROUP_LABEL,
                    before_label="Before sprite context",
                    after_label="With sprite context",
                ),
            ]
        )
    lines.extend(
        [
            "##### Remaining sprite-context warnings by message",
            "",
            *_markdown_named_count_table(
                list(summary.get("by_message") or []),
                key="message",
                label=_MARKDOWN_MESSAGE_LABEL,
            ),
            "##### Remaining sprite-context warnings by layer group",
            "",
            *_markdown_named_count_table(
                list(summary.get("by_layer_group") or []),
                key="group",
                label=_MARKDOWN_LAYER_GROUP_LABEL,
            ),
            "##### Remaining sprite-context warnings by layer group and message",
            "",
            *_markdown_group_message_count_table(list(summary.get("by_layer_group_and_message") or [])),
            "##### Remaining sprite-context warnings by layer",
            "",
            *_markdown_named_count_table(
                list(summary.get("by_layer") or []),
                key="layer",
                label=_MARKDOWN_LAYER_LABEL,
            ),
            "##### Remaining sprite-context warning layers by unresolved qfit property",
            "",
            *_markdown_count_table(unresolved_by_property),
            "##### Remaining sprite-context warning layers by layer group and unresolved qfit property",
            "",
            *_markdown_group_count_table(unresolved_by_group_property),
        ]
    )
    return lines


def _markdown_qgis_converter_warnings(report: object) -> list[str]:
    if not isinstance(report, dict):
        return []
    raw = report.get("raw") if isinstance(report.get("raw"), dict) else {}
    qfit = report.get("qfit_preprocessed") if isinstance(report.get("qfit_preprocessed"), dict) else {}
    reduced = report.get("reduced_by_qfit") if isinstance(report.get("reduced_by_qfit"), dict) else {}
    reduced_by_message = list(reduced.get("by_message") or [])
    reduced_by_layer = list(reduced.get("by_layer") or [])
    reduced_by_group = list(reduced.get("by_layer_group") or [])
    filterless_probe = report.get("without_filters_probe")
    icon_image_probe = report.get("without_icon_images_probe")
    line_opacity_probe = report.get(_SCALAR_LINE_OPACITY_PROBE_KEY)
    line_dasharray_probe = report.get(_LITERAL_LINE_DASHARRAY_PROBE_KEY)
    symbol_spacing_probe = report.get(_SCALAR_SYMBOL_SPACING_PROBE_KEY)
    property_removal_impact_probe = report.get(_PROPERTY_REMOVAL_IMPACT_PROBE_KEY)
    filter_parse_support_probe = report.get(_FILTER_PARSE_SUPPORT_PROBE_KEY)
    sprite_context_probe = report.get(_SPRITE_CONTEXT_PROBE_KEY)
    lines = [
        "### QGIS converter warnings",
        "",
        f"Raw style warnings: {raw.get('count', 0)}",
        f"After qfit preprocessing: {qfit.get('count', 0)}",
        f"Warning count delta: {report.get('warning_count_delta', 0)}",
        "",
    ]
    if reduced_by_message:
        lines.extend(
            [
                "#### Warnings reduced by qfit preprocessing",
                "",
                *_markdown_warning_reduction_table(
                    reduced_by_message,
                    key="message",
                    label=_MARKDOWN_MESSAGE_LABEL,
                ),
            ]
        )
    if reduced_by_layer:
        lines.extend(
            [
                "#### Layers with fewer warnings after qfit preprocessing",
                "",
                *_markdown_warning_reduction_table(
                    reduced_by_layer,
                    key="layer",
                    label=_MARKDOWN_LAYER_LABEL,
                ),
            ]
        )
    if reduced_by_group:
        lines.extend(
            [
                "#### Layer groups with fewer warnings after qfit preprocessing",
                "",
                *_markdown_warning_reduction_table(
                    reduced_by_group,
                    key="group",
                    label=_MARKDOWN_LAYER_GROUP_LABEL,
                ),
            ]
        )
    lines.extend(
        [
            "#### Remaining warnings by message",
            "",
            *_markdown_named_count_table(
                list(qfit.get("by_message") or []),
                key="message",
                label=_MARKDOWN_MESSAGE_LABEL,
            ),
            "#### Remaining warnings by layer group",
            "",
            *_markdown_named_count_table(
                list(qfit.get("by_layer_group") or []),
                key="group",
                label=_MARKDOWN_LAYER_GROUP_LABEL,
            ),
            "#### Remaining warnings by layer group and message",
            "",
            *_markdown_group_message_count_table(list(qfit.get("by_layer_group_and_message") or [])),
            "#### Remaining warnings by layer",
            "",
            *_markdown_named_count_table(
                list(qfit.get("by_layer") or []),
                key="layer",
                label=_MARKDOWN_LAYER_LABEL,
            ),
            *_markdown_filterless_probe(filterless_probe),
            *_markdown_filter_parse_support_probe(filter_parse_support_probe),
            *_markdown_property_removal_impact_probe(property_removal_impact_probe),
            *_markdown_sprite_context_probe(sprite_context_probe),
            *_markdown_icon_image_probe(icon_image_probe),
            *_markdown_line_opacity_probe(line_opacity_probe),
            *_markdown_line_dasharray_probe(line_dasharray_probe),
            *_markdown_symbol_spacing_probe(symbol_spacing_probe),
        ]
    )
    return lines



def _summary_rows(summary: dict[str, object], key: str) -> list[object]:
    return list(summary.get(key) or [])


def _markdown_label_density_summary(summary: dict[str, object]) -> list[str]:
    return [
        "### Label density candidates",
        "",
        (
            "Visible symbol layers with text labels. Use this diagnostic with live screenshots before changing "
            "label thinning, rank, or collision-related preprocessing."
        ),
        "",
        *_markdown_named_count_table(
            _summary_rows(summary, "label_density_candidates_by_layer_group"),
            key="group",
            label=_MARKDOWN_LAYER_GROUP_LABEL,
        ),
        *_markdown_label_density_candidate_table(_summary_rows(summary, "label_density_candidates")),
    ]


def _markdown_road_trail_hierarchy_summary(summary: dict[str, object]) -> list[str]:
    return [
        "### Road/trail hierarchy candidates",
        "",
        (
            "Visible road/trail line and fill layers. Use this diagnostic with live screenshots before changing "
            "road, path, casing, dash, opacity, or class-hierarchy preprocessing."
        ),
        "",
        *_markdown_named_count_table(
            _summary_rows(summary, _ROAD_TRAIL_HIERARCHY_CANDIDATES_BY_SOURCE_LAYER_KEY),
            key="source_layer",
            label="Source layer",
        ),
        *_markdown_named_count_table(
            _summary_rows(summary, _ROAD_TRAIL_HIERARCHY_CANDIDATES_BY_TYPE_KEY),
            key="type",
            label="Layer type",
        ),
        "#### Road/trail hierarchy candidates simplified/substituted by qfit",
        "",
        *_markdown_count_table(_summary_rows(summary, _ROAD_TRAIL_HIERARCHY_SIMPLIFIED_BY_PROPERTY_KEY)),
        "#### Road/trail hierarchy candidates QGIS-dependent controls",
        "",
        *_markdown_count_table(_summary_rows(summary, _ROAD_TRAIL_HIERARCHY_QGIS_DEPENDENT_BY_PROPERTY_KEY)),
        *_markdown_road_trail_hierarchy_candidate_table(_summary_rows(summary, _ROAD_TRAIL_HIERARCHY_CANDIDATES_KEY)),
    ]


def _markdown_terrain_landcover_summary(summary: dict[str, object]) -> list[str]:
    return [
        "### Terrain/landcover palette candidates",
        "",
        (
            "Visible terrain/landcover fill and line layers. Use this diagnostic with live screenshots before "
            "changing landcover, landuse, contour, hillshade, park, or wetland color/opacity/pattern preprocessing."
        ),
        "",
        *_markdown_named_count_table(
            _summary_rows(summary, _TERRAIN_LANDCOVER_CANDIDATES_BY_SOURCE_LAYER_KEY),
            key="source_layer",
            label="Source layer",
        ),
        *_markdown_named_count_table(
            _summary_rows(summary, _TERRAIN_LANDCOVER_CANDIDATES_BY_TYPE_KEY),
            key="type",
            label="Layer type",
        ),
        "#### Terrain/landcover palette candidates simplified/substituted by qfit",
        "",
        *_markdown_count_table(_summary_rows(summary, _TERRAIN_LANDCOVER_SIMPLIFIED_BY_PROPERTY_KEY)),
        "#### Terrain/landcover palette candidates QGIS-dependent controls",
        "",
        *_markdown_count_table(_summary_rows(summary, _TERRAIN_LANDCOVER_QGIS_DEPENDENT_BY_PROPERTY_KEY)),
        *_markdown_terrain_landcover_candidate_table(_summary_rows(summary, _TERRAIN_LANDCOVER_CANDIDATES_KEY)),
    ]


def _markdown_summary(summary: dict[str, object], qgis_converter_warnings: object) -> list[str]:
    return [
        "## Summary",
        "",
        "### Simplified/substituted by qfit",
        "",
        *_markdown_count_table(_summary_rows(summary, "qfit_simplifies_by_property")),
        "### Simplified/substituted by qfit by layer group",
        "",
        *_markdown_group_count_table(_summary_rows(summary, "qfit_simplifies_by_layer_group_and_property")),
        "### QGIS-dependent / unresolved",
        "",
        *_markdown_count_table(_summary_rows(summary, "qfit_unresolved_by_property")),
        "### QGIS-dependent / unresolved by layer group",
        "",
        *_markdown_group_count_table(_summary_rows(summary, "qfit_unresolved_by_layer_group_and_property")),
        "### Unresolved expression operators",
        "",
        *_markdown_expression_operator_table(_summary_rows(summary, "qfit_unresolved_expression_operators_by_property")),
        "### Unresolved expression operators by layer group",
        "",
        *_markdown_group_expression_operator_table(
            _summary_rows(summary, "qfit_unresolved_expression_operators_by_layer_group_and_property")
        ),
        "### Unresolved filter expression signatures by layer group",
        "",
        *_markdown_filter_signature_group_table(
            _summary_rows(summary, "qfit_unresolved_filter_expression_signatures_by_layer_group")
        ),
        *_markdown_label_density_summary(summary),
        *_markdown_road_trail_hierarchy_summary(summary),
        *_markdown_terrain_landcover_summary(summary),
        *_markdown_qgis_converter_warnings(qgis_converter_warnings),
    ]

def _markdown_source_filter(layer_obj: dict[str, object]) -> str:
    source_parts = [part for part in (layer_obj.get("source"), layer_obj.get("source_layer")) if part]
    source_filter = " / ".join(str(part) for part in source_parts) or "—"
    if layer_obj.get("filter") is not None:
        source_filter += f"<br>`filter`: `{_compact_json(layer_obj.get('filter'))}`"
    return source_filter


def _markdown_layer_row(layer_obj: dict[str, object]) -> str:
    layer_label = f"`{layer_obj.get('id', '')}`<br>{layer_obj.get('type', '')}"
    return "| {layer} | {group} | {source_filter} | {zoom} | {preserved} | {simplified} | {unresolved} |".format(
        layer=layer_label,
        group=layer_obj.get("group", "other"),
        source_filter=_markdown_source_filter(layer_obj),
        zoom=layer_obj.get("zoom_band", _ALL_ZOOMS_BAND),
        preserved=_markdown_list(list(layer_obj.get("qfit_preserves") or [])),
        simplified=_markdown_change_list(list(layer_obj.get("qfit_simplifies") or [])),
        unresolved=_markdown_layer_unresolved(layer_obj),
    )


def build_audit_markdown(audit: dict[str, object]) -> str:
    style = audit["style"] if isinstance(audit.get("style"), dict) else {}
    layers = audit.get("layers") if isinstance(audit.get("layers"), list) else []
    summary = audit.get("summary") if isinstance(audit.get("summary"), dict) else {}
    lines = [
        f"# Mapbox Outdoors style audit — {style.get('label', 'mapbox/outdoors-v12')}",
        "",
        f"Generated: {audit.get('generated_at', '')}",
        f"Layers: {audit.get('layer_count', len(layers))}",
        "",
        "This developer audit compares the live Mapbox style rules with qfit's current QGIS preprocessing.",
        "Use it to choose the next visual-parity slice before making rendering-sensitive changes.",
        "",
        *_markdown_summary(summary, audit.get("qgis_converter_warnings")),
        "## Layers",
        "",
        "| Layer | Group | Source/filter | Zoom | Preserved | Simplified/substituted by qfit | QGIS-dependent / unresolved |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for layer_obj in layers:
        if not isinstance(layer_obj, dict):
            continue
        lines.append(_markdown_layer_row(layer_obj))
    lines.append("")
    return "\n".join(lines)


def _default_output_path(*, output_format: str) -> Path:
    extension = "md" if output_format == "markdown" else "json"
    return DEFAULT_OUTPUT_ROOT / _DEFAULT_OUTPUT_STYLE_SLUG / _utc_timestamp() / f"audit.{extension}"


def load_style_definition(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        raise ValueError(f"Mapbox style JSON must be an object: {path}")
    return loaded


def render_audit(audit: dict[str, object], *, output_format: str) -> str:
    if output_format == "json":
        return json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return build_audit_markdown(audit)


def _assert_default_output_path(output_path: Path) -> None:
    root = DEFAULT_OUTPUT_ROOT.resolve()
    parent = output_path.parent.resolve()
    if not parent.is_relative_to(root):
        raise ValueError(f"Audit output must stay under {DEFAULT_OUTPUT_ROOT}")


def write_audit(content: str, *, output_format: str) -> Path:
    output_path = _default_output_path(output_format=output_format)
    _assert_default_output_path(output_path)
    # Safe: output_path is built from DEFAULT_OUTPUT_ROOT and a constant Mapbox Outdoors slug.
    output_path.parent.mkdir(parents=True, exist_ok=True)  # NOSONAR
    output_path.write_text(content, encoding="utf-8")  # NOSONAR
    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a developer audit of Mapbox Outdoors style rules and qfit/QGIS preprocessing.",
    )
    parser.add_argument(
        "--style-json",
        type=Path,
        help="Read an already downloaded Mapbox style JSON file instead of fetching live style JSON.",
    )
    parser.add_argument("--style-owner", default=DEFAULT_MAPBOX_STYLE_OWNER)
    parser.add_argument("--style-id", default=DEFAULT_MAPBOX_STYLE_ID)
    parser.add_argument(
        "--mapbox-token",
        default=None,
        help="Mapbox token for live style fetch. Prefer MAPBOX_ACCESS_TOKEN or QFIT_MAPBOX_ACCESS_TOKEN.",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Audit output format. Defaults to markdown.",
    )
    parser.add_argument(
        "--include-qgis-converter-warnings",
        action="store_true",
        help=(
            "Also run QGIS' Mapbox GL style converter on the raw and qfit-preprocessed "
            "style to summarize native conversion warnings. Requires PyQGIS."
        ),
    )
    parser.add_argument(
        "--include-qgis-property-removal-impact",
        action="store_true",
        help=(
            "With QGIS converter warnings, also remove each remaining expression-bearing "
            "property in isolation to rank residual warning causes. Implies "
            "--include-qgis-converter-warnings."
        ),
    )
    parser.add_argument(
        "--include-qgis-filter-parse-support",
        action="store_true",
        help=(
            "With QGIS converter warnings, also isolate each remaining qfit-preprocessed "
            "filter expression in a minimal same-type style to identify filters the QGIS "
            "parser itself rejects. Implies --include-qgis-converter-warnings."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    token = None
    if args.style_json is not None:
        style_definition = load_style_definition(args.style_json)
        token = (
            args.mapbox_token
            or os.environ.get("MAPBOX_ACCESS_TOKEN")
            or os.environ.get("QFIT_MAPBOX_ACCESS_TOKEN")
        )
    else:
        token = resolve_mapbox_token(provided_token=args.mapbox_token)
        style_definition = fetch_mapbox_style_definition(token, args.style_owner, args.style_id)

    include_qgis_converter_warnings = (
        args.include_qgis_converter_warnings
        or args.include_qgis_property_removal_impact
        or args.include_qgis_filter_parse_support
    )

    sprite_resources = None
    sprite_url = style_definition.get("sprite") if isinstance(style_definition.get("sprite"), str) else None
    if include_qgis_converter_warnings and token:
        try:
            sprite_resources = fetch_mapbox_sprite_resources(
                token,
                args.style_owner,
                args.style_id,
                sprite_url=sprite_url,
            )
        except (RuntimeError, KeyError, ValueError, OSError):
            sprite_resources = None

    audit = build_style_audit(
        style_definition,
        config=StyleAuditConfig(
            style_owner=args.style_owner,
            style_id=args.style_id,
            include_qgis_converter_warnings=include_qgis_converter_warnings,
            include_qgis_property_removal_impact=args.include_qgis_property_removal_impact,
            include_qgis_filter_parse_support=args.include_qgis_filter_parse_support,
            sprite_resources=sprite_resources,
        ),
    )
    content = render_audit(audit, output_format=args.format)
    output_path = write_audit(content, output_format=args.format)
    print(output_path)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised manually
    raise SystemExit(main())
