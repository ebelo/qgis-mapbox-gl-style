from __future__ import annotations

import argparse
import copy
import datetime as dt
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
_LITERAL_LINE_DASHARRAY_PROBE_KEY = "with_literal_line_dasharray_probe"
_LINE_DASHARRAY_EXPRESSION_COUNT_KEY = "line_dasharray_expression_count_replaced"

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


@dataclass(frozen=True)
class StyleAuditConfig:
    style_owner: str = DEFAULT_MAPBOX_STYLE_OWNER
    style_id: str = DEFAULT_MAPBOX_STYLE_ID
    generated_at: dt.datetime | None = None
    include_qgis_converter_warnings: bool = False
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
        return "all zooms"
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
            children = [] if operator == "literal" else candidate[1:]
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
            "operator_signature": ", ".join(operators) or "(none)",
            "count": count,
            "example_layers": example_layers[(group, operators)],
        }
        for (group, operators), count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0][0], ", ".join(item[0][1])),
        )
    ]


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
) -> dict[str, list[dict[str, object]]]:
    excluded = exclude_properties or set()
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
        for property_name in _iter_property_names(layer, "qfit_unresolved"):
            if property_name in excluded:
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
) -> None:
    summary = probe.get("summary") if isinstance(probe.get("summary"), dict) else {}
    warnings = summary.get("warnings")
    if isinstance(warnings, list):
        probe["remaining_warning_layers_by_unresolved_property"] = _warning_layer_unresolved_property_summaries(
            [str(warning) for warning in warnings],
            layers,
            exclude_properties=exclude_properties,
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
        exclude_properties={"paint.line-dasharray"},
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


def _style_with_scalar_line_opacity(style_definition: dict[str, object]) -> tuple[dict[str, object], int]:
    """Return a copy with line-opacity expressions scalarized for converter diagnostics only."""
    style_with_scalar_opacity = copy.deepcopy(style_definition)
    replaced_count = 0
    layers = style_with_scalar_opacity.get("layers")
    if not isinstance(layers, list):
        return style_with_scalar_opacity, replaced_count
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
        paint["line-opacity"] = scalar_opacity
        replaced_count += 1
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


def _case_line_dasharray_output_candidates(expr: list[object]) -> list[object]:
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
        return _first_line_dasharray_literal(_case_line_dasharray_output_candidates(expr))
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
        scalar_line_opacity_style, line_opacity_expression_count_replaced = _style_with_scalar_line_opacity(
            qfit_preprocessed_style
        )
        scalar_line_opacity_warnings = _collect_qgis_converter_warnings(scalar_line_opacity_style)
        literal_line_dasharray_style, line_dasharray_expression_count_replaced = _style_with_literal_line_dasharray(
            qfit_preprocessed_style
        )
        literal_line_dasharray_warnings = _collect_qgis_converter_warnings(literal_line_dasharray_style)
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
            _LINE_OPACITY_EXPRESSION_COUNT_KEY: line_opacity_expression_count_replaced,
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
    }
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
        },
        "layers": layers,
    }
    if resolved_config.include_qgis_converter_warnings:
        warning_report = _qgis_converter_warning_report(
            raw_style=style_definition,
            qfit_preprocessed_style=simplified_style,
            sprite_resources=resolved_config.sprite_resources,
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
    empty: str = "—",
) -> list[str]:
    if not items:
        return [empty, ""]
    lines = [
        "| Layer group | Operators | # Layers | Example layers |",
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


def _markdown_line_opacity_probe(probe: object) -> list[str]:
    if not isinstance(probe, dict):
        return []
    summary = probe.get("summary") if isinstance(probe.get("summary"), dict) else {}
    reduced = probe.get("reduced_from_qfit") if isinstance(probe.get("reduced_from_qfit"), dict) else {}
    lines = [
        "#### Diagnostic line-opacity scalarization probe",
        "",
        "This is not a rendering-safe qfit preprocessing mode; line opacity preserves zoom/data-driven cartographic emphasis.",
        "Use it only to estimate how much converter warning debt is tied to Mapbox line-opacity expressions.",
        "",
        f"Line opacity expressions replaced in probe: {probe.get(_LINE_OPACITY_EXPRESSION_COUNT_KEY, 0)}",
        f"Warnings after scalar line opacity: {summary.get('count', 0)}",
        f"{_MARKDOWN_WARNING_DELTA_FROM_QFIT_LABEL}: {probe.get('warning_count_delta_from_qfit', 0)}",
        "",
    ]
    by_message = list(reduced.get("by_message") or [])
    by_group = list(reduced.get("by_layer_group") or [])
    if by_message:
        lines.extend(
            [
                "##### Line-opacity probe reductions by message",
                "",
                *_markdown_warning_reduction_table(
                    by_message,
                    key="message",
                    label=_MARKDOWN_MESSAGE_LABEL,
                    before_label="Before line-opacity probe",
                    after_label="Scalar line-opacity",
                ),
            ]
        )
    if by_group:
        lines.extend(
            [
                "##### Line-opacity probe reductions by layer group",
                "",
                *_markdown_warning_reduction_table(
                    by_group,
                    key="group",
                    label=_MARKDOWN_LAYER_GROUP_LABEL,
                    before_label="Before line-opacity probe",
                    after_label="Scalar line-opacity",
                ),
            ]
        )
    lines.extend(
        [
            "##### Remaining line-opacity probe warnings by message",
            "",
            *_markdown_named_count_table(
                list(summary.get("by_message") or []),
                key="message",
                label=_MARKDOWN_MESSAGE_LABEL,
            ),
            "##### Remaining line-opacity probe warnings by layer group",
            "",
            *_markdown_named_count_table(
                list(summary.get("by_layer_group") or []),
                key="group",
                label=_MARKDOWN_LAYER_GROUP_LABEL,
            ),
            "##### Remaining line-opacity probe warnings by layer group and message",
            "",
            *_markdown_group_message_count_table(list(summary.get("by_layer_group_and_message") or [])),
            "##### Remaining line-opacity probe warnings by layer",
            "",
            *_markdown_named_count_table(
                list(summary.get("by_layer") or []),
                key="layer",
                label=_MARKDOWN_LAYER_LABEL,
            ),
        ]
    )
    return lines


def _markdown_line_dasharray_probe(probe: object) -> list[str]:
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
        "#### Diagnostic line-dasharray literalization probe",
        "",
        "This is not a rendering-safe qfit preprocessing mode; line dash arrays preserve zoom/data-driven cartographic distinctions.",
        "Use it only to estimate how much converter warning debt is tied to Mapbox line-dasharray expressions.",
        "",
        f"Line dasharray expressions replaced in probe: {probe.get(_LINE_DASHARRAY_EXPRESSION_COUNT_KEY, 0)}",
        f"Warnings after literal line dasharray: {summary.get('count', 0)}",
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
                "##### Line-dasharray probe reductions by message",
                "",
                *_markdown_warning_reduction_table(
                    by_message,
                    key="message",
                    label=_MARKDOWN_MESSAGE_LABEL,
                    before_label="Before line-dasharray probe",
                    after_label="Literal line-dasharray",
                ),
            ]
        )
    if by_group:
        lines.extend(
            [
                "##### Line-dasharray probe reductions by layer group",
                "",
                *_markdown_warning_reduction_table(
                    by_group,
                    key="group",
                    label=_MARKDOWN_LAYER_GROUP_LABEL,
                    before_label="Before line-dasharray probe",
                    after_label="Literal line-dasharray",
                ),
            ]
        )
    lines.extend(
        [
            "##### Remaining line-dasharray probe warnings by message",
            "",
            *_markdown_named_count_table(
                list(summary.get("by_message") or []),
                key="message",
                label=_MARKDOWN_MESSAGE_LABEL,
            ),
            "##### Remaining line-dasharray probe warnings by layer group",
            "",
            *_markdown_named_count_table(
                list(summary.get("by_layer_group") or []),
                key="group",
                label=_MARKDOWN_LAYER_GROUP_LABEL,
            ),
            "##### Remaining line-dasharray probe warnings by layer group and message",
            "",
            *_markdown_group_message_count_table(list(summary.get("by_layer_group_and_message") or [])),
            "##### Remaining line-dasharray probe warnings by layer",
            "",
            *_markdown_named_count_table(
                list(summary.get("by_layer") or []),
                key="layer",
                label=_MARKDOWN_LAYER_LABEL,
            ),
            "##### Remaining line-dasharray probe warning layers by unresolved qfit property",
            "",
            *_markdown_count_table(unresolved_by_property),
            "##### Remaining line-dasharray probe warning layers by layer group and unresolved qfit property",
            "",
            *_markdown_group_count_table(unresolved_by_group_property),
        ]
    )
    return lines


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
            *_markdown_sprite_context_probe(sprite_context_probe),
            *_markdown_icon_image_probe(icon_image_probe),
            *_markdown_line_opacity_probe(line_opacity_probe),
            *_markdown_line_dasharray_probe(line_dasharray_probe),
        ]
    )
    return lines


def _markdown_summary(summary: dict[str, object], qgis_converter_warnings: object) -> list[str]:
    return [
        "## Summary",
        "",
        "### Simplified/substituted by qfit",
        "",
        *_markdown_count_table(list(summary.get("qfit_simplifies_by_property") or [])),
        "### QGIS-dependent / unresolved",
        "",
        *_markdown_count_table(list(summary.get("qfit_unresolved_by_property") or [])),
        "### QGIS-dependent / unresolved by layer group",
        "",
        *_markdown_group_count_table(list(summary.get("qfit_unresolved_by_layer_group_and_property") or [])),
        "### Unresolved expression operators",
        "",
        *_markdown_expression_operator_table(
            list(summary.get("qfit_unresolved_expression_operators_by_property") or [])
        ),
        "### Unresolved expression operators by layer group",
        "",
        *_markdown_group_expression_operator_table(
            list(summary.get("qfit_unresolved_expression_operators_by_layer_group_and_property") or [])
        ),
        "### Unresolved filter expression signatures by layer group",
        "",
        *_markdown_filter_signature_group_table(
            list(summary.get("qfit_unresolved_filter_expression_signatures_by_layer_group") or [])
        ),
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
        zoom=layer_obj.get("zoom_band", "all zooms"),
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

    sprite_resources = None
    sprite_url = style_definition.get("sprite") if isinstance(style_definition.get("sprite"), str) else None
    if args.include_qgis_converter_warnings and token:
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
            include_qgis_converter_warnings=args.include_qgis_converter_warnings,
            sprite_resources=sprite_resources,
        ),
    )
    content = render_audit(audit, output_format=args.format)
    output_path = write_audit(content, output_format=args.format)
    print(output_path)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised manually
    raise SystemExit(main())
