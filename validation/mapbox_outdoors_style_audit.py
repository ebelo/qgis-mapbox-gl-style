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

if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))

from qfit.mapbox_config import (  # noqa: E402
    fetch_mapbox_style_definition,
    simplify_mapbox_style_expressions,
)

_SYMBOLOGY_SECTIONS = ("paint", "layout")
_MAPBOX_SPRITE_PATTERN_LIMITATION = (
    "Mapbox sprite patterns are handed to QGIS and may not render without an equivalent local pattern."
)
_UNSUPPORTED_CUES: dict[tuple[str, str], str] = {
    ("layout", "icon-image"): "Mapbox sprite references are handed to QGIS, but native vector mode cannot consume Mapbox sprites directly.",
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


@dataclass(frozen=True)
class StyleAuditConfig:
    style_owner: str = DEFAULT_MAPBOX_STYLE_OWNER
    style_id: str = DEFAULT_MAPBOX_STYLE_ID
    generated_at: dt.datetime | None = None
    include_qgis_converter_warnings: bool = False


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


def _is_hidden_by_qfit(simplified_layer: dict[str, object] | None) -> bool:
    if simplified_layer is None:
        return False
    return _section_properties(simplified_layer, "layout").get("visibility") == "none"


def _unresolved_cues(layer: dict[str, object], simplified_layer: dict[str, object] | None) -> list[dict[str, str]]:
    if _is_hidden_by_qfit(simplified_layer):
        return []

    unresolved: list[dict[str, str]] = []
    comparison_layer = simplified_layer or layer
    for section, prop, value in _iter_symbology(comparison_layer):
        reason = _UNSUPPORTED_CUES.get((section, prop))
        if reason is not None:
            unresolved.append(
                {
                    "property": f"{section}.{prop}",
                    "value": _compact_json(value),
                    "reason": reason,
                }
            )
        elif isinstance(value, list) and not (
            (section == "layout" and prop == "text-field" and _is_supported_simple_text_field(value))
            or _is_literal_number_array(value)
        ):
            unresolved.append(
                {
                    "property": f"{section}.{prop}",
                    "value": _compact_json(value),
                    "reason": "Expression is still handed to QGIS after qfit simplification; verify native support visually.",
                }
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
        values = layer.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, str):
                counts[item] += 1
            elif isinstance(item, dict) and isinstance(item.get("property"), str):
                counts[item["property"]] += 1
    return [
        {"property": property_name, "count": count}
        for property_name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
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


def _collect_qgis_converter_warnings(style_definition: dict[str, object]) -> list[str]:
    from qgis.core import QgsMapBoxGlStyleConverter  # noqa: PLC0415

    converter = QgsMapBoxGlStyleConverter()
    converter.convert(style_definition)
    return list(converter.warnings())


def _qgis_converter_warning_report(
    *,
    raw_style: dict[str, object],
    qfit_preprocessed_style: dict[str, object],
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
    finally:
        if created_app:
            app.exitQgis()
    raw_summary = _qgis_warning_summary(raw_warnings)
    qfit_summary = _qgis_warning_summary(qfit_warnings)
    return {
        "raw": raw_summary,
        "qfit_preprocessed": qfit_summary,
        "warning_count_delta": len(raw_warnings) - len(qfit_warnings),
        "reduced_by_qfit": _qgis_warning_reduction_report(raw_summary, qfit_summary),
    }


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
        },
        "layers": layers,
    }
    if resolved_config.include_qgis_converter_warnings:
        audit["qgis_converter_warnings"] = _qgis_converter_warning_report(
            raw_style=style_definition,
            qfit_preprocessed_style=simplified_style,
        )
    return audit


def _markdown_list(items: list[str], *, empty: str = "—") -> str:
    if not items:
        return empty
    return "<br>".join(items)


def _markdown_change_list(changes: list[dict[str, str]], *, empty: str = "—") -> str:
    if not changes:
        return empty
    return "<br>".join(
        f"`{change['property']}`: `{change['from']}` → `{change['to']}`" for change in changes
    )


def _markdown_unresolved_list(unresolved: list[dict[str, str]], *, empty: str = "—") -> str:
    if not unresolved:
        return empty
    return "<br>".join(
        f"`{item['property']}`: {item['reason']}" for item in unresolved
    )


def _markdown_count_table(items: list[dict[str, object]], *, empty: str = "—") -> list[str]:
    if not items:
        return [empty, ""]
    lines = ["| Property | # Layers |", "| --- | ---: |"]
    for item in items:
        lines.append(f"| `{item.get('property', '')}` | {item.get('count', 0)} |")
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


def _markdown_warning_reduction_table(
    items: list[dict[str, object]],
    *,
    key: str,
    label: str,
    empty: str = "—",
) -> list[str]:
    if not items:
        return [empty, ""]
    lines = [f"| {label} | Raw | After qfit | Reduced |", "| --- | ---: | ---: | ---: |"]
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


def _markdown_qgis_converter_warnings(report: object) -> list[str]:
    if not isinstance(report, dict):
        return []
    raw = report.get("raw") if isinstance(report.get("raw"), dict) else {}
    qfit = report.get("qfit_preprocessed") if isinstance(report.get("qfit_preprocessed"), dict) else {}
    reduced = report.get("reduced_by_qfit") if isinstance(report.get("reduced_by_qfit"), dict) else {}
    reduced_by_message = list(reduced.get("by_message") or [])
    reduced_by_layer = list(reduced.get("by_layer") or [])
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
                *_markdown_warning_reduction_table(reduced_by_message, key="message", label="Message"),
            ]
        )
    if reduced_by_layer:
        lines.extend(
            [
                "#### Layers with fewer warnings after qfit preprocessing",
                "",
                *_markdown_warning_reduction_table(reduced_by_layer, key="layer", label="Layer"),
            ]
        )
    lines.extend(
        [
            "#### Remaining warnings by message",
            "",
            *_markdown_named_count_table(list(qfit.get("by_message") or []), key="message", label="Message"),
            "#### Remaining warnings by layer",
            "",
            *_markdown_named_count_table(list(qfit.get("by_layer") or []), key="layer", label="Layer"),
        ]
    )
    return lines


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
        "## Summary",
        "",
        "### Simplified/substituted by qfit",
        "",
        *_markdown_count_table(list(summary.get("qfit_simplifies_by_property") or [])),
        "### QGIS-dependent / unresolved",
        "",
        *_markdown_count_table(list(summary.get("qfit_unresolved_by_property") or [])),
        *_markdown_qgis_converter_warnings(audit.get("qgis_converter_warnings")),
        "## Layers",
        "",
        "| Layer | Group | Source/filter | Zoom | Preserved | Simplified/substituted by qfit | QGIS-dependent / unresolved |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for layer_obj in layers:
        if not isinstance(layer_obj, dict):
            continue
        source_parts = [part for part in (layer_obj.get("source"), layer_obj.get("source_layer")) if part]
        source_filter = " / ".join(str(part) for part in source_parts) or "—"
        if layer_obj.get("filter") is not None:
            source_filter += f"<br>`filter`: `{_compact_json(layer_obj.get('filter'))}`"
        layer_label = f"`{layer_obj.get('id', '')}`<br>{layer_obj.get('type', '')}"
        lines.append(
            "| {layer} | {group} | {source_filter} | {zoom} | {preserved} | {simplified} | {unresolved} |".format(
                layer=layer_label,
                group=layer_obj.get("group", "other"),
                source_filter=source_filter,
                zoom=layer_obj.get("zoom_band", "all zooms"),
                preserved=_markdown_list(list(layer_obj.get("qfit_preserves") or [])),
                simplified=_markdown_change_list(list(layer_obj.get("qfit_simplifies") or [])),
                unresolved=_markdown_unresolved_list(list(layer_obj.get("qfit_unresolved") or [])),
            )
        )
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

    if args.style_json is not None:
        style_definition = load_style_definition(args.style_json)
    else:
        token = resolve_mapbox_token(provided_token=args.mapbox_token)
        style_definition = fetch_mapbox_style_definition(token, args.style_owner, args.style_id)

    audit = build_style_audit(
        style_definition,
        config=StyleAuditConfig(
            style_owner=args.style_owner,
            style_id=args.style_id,
            include_qgis_converter_warnings=args.include_qgis_converter_warnings,
        ),
    )
    content = render_audit(audit, output_format=args.format)
    output_path = write_audit(content, output_format=args.format)
    print(output_path)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised manually
    raise SystemExit(main())
