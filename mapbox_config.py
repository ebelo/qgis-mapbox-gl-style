from __future__ import annotations

import json
from urllib.parse import quote, unquote
from urllib.request import urlopen


class MapboxConfigError(ValueError):
    """Raised when the configured Mapbox background settings are incomplete."""


BACKGROUND_LAYER_PREFIX = "qfit background"
DEFAULT_BACKGROUND_PRESET = "Outdoor"
DEFAULT_MAPBOX_TILE_SIZE = 512
DEFAULT_MAPBOX_RETINA = False
DEFAULT_MAPBOX_TILE_PIXEL_RATIO = 2
WEB_MERCATOR_WORLD_WIDTH_M = 40075016.685578488
DEFAULT_MAPBOX_MIN_ZOOM = 0
DEFAULT_MAPBOX_MAX_ZOOM = 22

_BACKGROUND_PRESETS = {
    "Outdoor": {
        "style_owner": "mapbox",
        "style_id": "outdoors-v12",
        "description": "Mapbox Outdoors",
        "requires_custom_style": False,
    },
    "Light": {
        "style_owner": "mapbox",
        "style_id": "light-v11",
        "description": "Mapbox Light",
        "requires_custom_style": False,
    },
    "Satellite": {
        "style_owner": "mapbox",
        "style_id": "satellite-streets-v12",
        "description": "Mapbox Satellite Streets",
        "requires_custom_style": False,
    },
    "Winter (custom style)": {
        "style_owner": "",
        "style_id": "",
        "description": "Your own winter-themed Mapbox Studio style",
        "requires_custom_style": True,
    },
    "Custom": {
        "style_owner": "",
        "style_id": "",
        "description": "Any Mapbox Studio style owner/style_id",
        "requires_custom_style": True,
    },
}


def background_preset_names() -> list[str]:
    return list(_BACKGROUND_PRESETS.keys())


def get_background_preset(name: str | None) -> dict[str, object]:
    return _BACKGROUND_PRESETS.get(name or DEFAULT_BACKGROUND_PRESET, _BACKGROUND_PRESETS[DEFAULT_BACKGROUND_PRESET])


def preset_requires_custom_style(name: str | None) -> bool:
    preset = get_background_preset(name)
    return bool(preset["requires_custom_style"])


def preset_defaults(name: str | None) -> tuple[str, str]:
    preset = get_background_preset(name)
    return str(preset["style_owner"]), str(preset["style_id"])


def resolve_background_style(
    preset_name: str | None,
    style_owner: str = "",
    style_id: str = "",
) -> tuple[str, str]:
    preset = get_background_preset(preset_name)
    if not preset_requires_custom_style(preset_name):
        return preset_defaults(preset_name)

    owner = style_owner.strip()
    resolved_style_id = style_id.strip()
    if not owner or not resolved_style_id:
        raise MapboxConfigError(
            "Enter a Mapbox style owner and style ID for the selected background preset."
        )
    return owner, resolved_style_id


def build_mapbox_tiles_url(
    access_token: str,
    style_owner: str,
    style_id: str,
    *,
    tile_size: int = DEFAULT_MAPBOX_TILE_SIZE,
    retina: bool = DEFAULT_MAPBOX_RETINA,
) -> str:
    token = access_token.strip()
    owner = style_owner.strip()
    resolved_style_id = style_id.strip()

    if not token:
        raise MapboxConfigError("Enter a Mapbox access token to load the selected background map.")
    if not owner or not resolved_style_id:
        raise MapboxConfigError("Enter a Mapbox style owner and style ID first.")

    retina_suffix = "@2x" if retina else ""
    return (
        "https://api.mapbox.com/styles/v1/{owner}/{style_id}/tiles/{tile_size}/{{z}}/{{x}}/{{y}}{retina}"
        "?access_token={token}"
    ).format(
        owner=quote(owner, safe=""),
        style_id=quote(resolved_style_id, safe=""),
        tile_size=tile_size,
        retina=retina_suffix,
        token=quote(token, safe=""),
    )


TILE_MODE_RASTER = "Raster"
TILE_MODE_VECTOR = "Vector"
TILE_MODES = [TILE_MODE_RASTER, TILE_MODE_VECTOR]


def build_xyz_layer_uri(access_token: str, style_owner: str, style_id: str) -> str:
    url = build_mapbox_tiles_url(access_token, style_owner, style_id)
    return "type=xyz&url={url}&zmin=0&zmax=22&tilePixelRatio={tile_pixel_ratio}".format(
        url=quote(url, safe=":/?{}=%@"),
        tile_pixel_ratio=DEFAULT_MAPBOX_TILE_PIXEL_RATIO,
    )


def native_web_mercator_resolution_for_zoom(
    zoom_level: int,
    *,
    tile_size: int = DEFAULT_MAPBOX_TILE_SIZE,
) -> float:
    normalized_zoom_level = max(DEFAULT_MAPBOX_MIN_ZOOM, int(zoom_level))
    return WEB_MERCATOR_WORLD_WIDTH_M / float(tile_size * (2 ** normalized_zoom_level))


def nearest_native_web_mercator_zoom_level(
    resolution_m_per_pixel: float,
    *,
    tile_size: int = DEFAULT_MAPBOX_TILE_SIZE,
    min_zoom: int = DEFAULT_MAPBOX_MIN_ZOOM,
    max_zoom: int = DEFAULT_MAPBOX_MAX_ZOOM,
) -> int:
    if resolution_m_per_pixel <= 0:
        return int(min_zoom)

    zoom_levels = range(int(min_zoom), int(max_zoom) + 1)
    return min(
        zoom_levels,
        key=lambda zoom_level: abs(
            native_web_mercator_resolution_for_zoom(zoom_level, tile_size=tile_size)
            - float(resolution_m_per_pixel)
        ),
    )


def snap_web_mercator_bounds_to_native_zoom(
    bounds: tuple[float, float, float, float],
    viewport_width_px: int,
    viewport_height_px: int,
    *,
    tile_size: int = DEFAULT_MAPBOX_TILE_SIZE,
    min_zoom: int = DEFAULT_MAPBOX_MIN_ZOOM,
    max_zoom: int = DEFAULT_MAPBOX_MAX_ZOOM,
) -> tuple[tuple[float, float, float, float], int]:
    min_x, min_y, max_x, max_y = bounds
    width_m = max(float(max_x) - float(min_x), 0.0)
    height_m = max(float(max_y) - float(min_y), 0.0)
    width_px = max(int(viewport_width_px or 0), 1)
    height_px = max(int(viewport_height_px or 0), 1)

    current_resolution = max(width_m / float(width_px), height_m / float(height_px), 0.0)
    snapped_zoom_level = nearest_native_web_mercator_zoom_level(
        current_resolution,
        tile_size=tile_size,
        min_zoom=min_zoom,
        max_zoom=max_zoom,
    )
    snapped_resolution = native_web_mercator_resolution_for_zoom(
        snapped_zoom_level,
        tile_size=tile_size,
    )

    center_x = (float(min_x) + float(max_x)) / 2.0
    center_y = (float(min_y) + float(max_y)) / 2.0
    snapped_width_m = snapped_resolution * float(width_px)
    snapped_height_m = snapped_resolution * float(height_px)
    snapped_bounds = (
        center_x - (snapped_width_m / 2.0),
        center_y - (snapped_height_m / 2.0),
        center_x + (snapped_width_m / 2.0),
        center_y + (snapped_height_m / 2.0),
    )
    return snapped_bounds, snapped_zoom_level


def _validated_mapbox_style_parts(access_token: str, style_owner: str, style_id: str) -> tuple[str, str, str]:
    token = access_token.strip()
    owner = style_owner.strip()
    resolved_style_id = style_id.strip()

    if not token:
        raise MapboxConfigError("Enter a Mapbox access token to load the selected background map.")
    if not owner or not resolved_style_id:
        raise MapboxConfigError("Enter a Mapbox style owner and style ID first.")
    return token, owner, resolved_style_id


def fetch_mapbox_style_definition(
    access_token: str,
    style_owner: str,
    style_id: str,
) -> dict[str, object]:
    """Fetch and parse the Mapbox style JSON for a style."""
    style_url = build_mapbox_style_json_url(access_token, style_owner, style_id)
    with urlopen(style_url, timeout=20) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


_REPRESENTATIVE_STYLE_ZOOM = 12.0


def _is_literal_color(value: object) -> bool:
    return isinstance(value, str) and (
        value.startswith("hsl") or value.startswith("#") or value.startswith("rgb")
    )


def _zoom_stops(expr: list[object]) -> list[tuple[float, object]]:
    """Return ``(zoom, value)`` pairs for zoom-based Mapbox expressions."""
    if len(expr) < 5 or expr[2] != ["zoom"]:
        return []
    stops: list[tuple[float, object]] = []
    for index in range(3, len(expr) - 1, 2):
        zoom = expr[index]
        if isinstance(zoom, (int, float)):
            stops.append((float(zoom), expr[index + 1]))
    return stops


def _nearest_zoom_stop_value(expr: list[object], target_zoom: float = _REPRESENTATIVE_STYLE_ZOOM) -> object | None:
    stops = _zoom_stops(expr)
    if not stops:
        return None
    return min(stops, key=lambda stop: abs(stop[0] - target_zoom))[1]


def _nearest_interpolate_output_value(
    expr: list[object],
    target_stop: float = _REPRESENTATIVE_STYLE_ZOOM,
) -> object | None:
    """Return a representative output value from any Mapbox interpolate expression."""
    if len(expr) < 5:
        return None
    stops: list[tuple[float, object]] = []
    for index in range(3, len(expr) - 1, 2):
        stop = expr[index]
        if isinstance(stop, (int, float)):
            stops.append((float(stop), expr[index + 1]))
    if not stops:
        return None
    return min(stops, key=lambda stop: abs(stop[0] - target_stop))[1]


def _step_zoom_value(expr: list[object], target_zoom: float = _REPRESENTATIVE_STYLE_ZOOM) -> object | None:
    """Evaluate a simple ``['step', ['zoom'], ...]`` expression at target zoom."""
    if len(expr) < 3 or expr[1] != ["zoom"]:
        return None
    value = expr[2]
    for index in range(3, len(expr) - 1, 2):
        threshold = expr[index]
        if not isinstance(threshold, (int, float)):
            continue
        if target_zoom < float(threshold):
            break
        value = expr[index + 1]
    return value


def _extract_fallback_color(expr: object) -> str | None:
    """Recursively extract a sensible literal color from a Mapbox expression.

    Mapbox uses expressions for dynamic colors. QGIS'
    ``QgsMapBoxGlStyleConverter`` can fall back to black when it cannot resolve
    them, so we collapse unsupported expressions to representative colors.

    For zoom expressions, choose the color closest to a mid-zoom instead of the
    last/max-zoom stop. That produces a closer default for the city/regional
    scales qfit usually displays while still avoiding converter black fallbacks.
    """
    if _is_literal_color(expr):
        return str(expr)
    if not isinstance(expr, list) or not expr:
        return None

    op = expr[0]
    if op == "interpolate":
        representative = _nearest_zoom_stop_value(expr)
        color = _extract_fallback_color(representative)
        if color is not None:
            return color
    if op == "step":
        representative = _step_zoom_value(expr)
        color = _extract_fallback_color(representative)
        if color is not None:
            return color

    # Walk backwards through list elements looking for a data/default fallback.
    fallback: str | None = None
    for item in reversed(expr):
        color = _extract_fallback_color(item)
        if color is not None:
            fallback = color
            break
    return fallback


def _extract_midrange_size(expr: object) -> float | None:
    """Extract a reasonable representative size from a Mapbox size expression.

    For zoom-interpolated sizes we pick the value nearest a mid-zoom (z12),
    falling back to a modest literal. This avoids collapsing all text and lines
    to either the low-zoom or high-zoom extreme.
    """
    if isinstance(expr, (int, float)):
        return float(expr)
    if not isinstance(expr, list) or not expr:
        return None
    op = expr[0]
    if op == "interpolate":
        representative = _nearest_zoom_stop_value(expr)
        value = _extract_midrange_size(representative)
        if value is not None:
            return value
        representative = _nearest_interpolate_output_value(expr)
        value = _extract_midrange_size(representative)
        if value is not None:
            return value
    if op == "step":
        representative = _step_zoom_value(expr)
        value = _extract_midrange_size(representative)
        if value is not None:
            return value
        default_val = _extract_midrange_size(expr[2]) if len(expr) >= 3 else None
        if default_val is not None:
            return default_val
    # Recurse for nested expressions, take first reasonable scalar
    for item in expr[1:]:
        val = _extract_midrange_size(item)
        if val is not None:
            return val
    return None


def _is_simple_text_field_reference(expr: object) -> bool:
    return isinstance(expr, list) and len(expr) == 2 and expr[0] == "get" and isinstance(expr[1], str)


def _first_text_field_reference_child(
    children: list[object],
    *,
    allow_concat: bool,
    allow_to_string: bool = True,
) -> object | None:
    for child in children:
        if isinstance(child, dict):
            continue
        reference = _first_simple_text_field_reference(
            child,
            allow_concat=allow_concat,
            allow_to_string=allow_to_string,
        )
        if reference is not None:
            return reference
    return None


def _text_field_reference_name(reference: object) -> str | None:
    if (
        isinstance(reference, list)
        and len(reference) == 2
        and reference[0] == "get"
        and isinstance(reference[1], str)
    ):
        return reference[1]
    return None


def _is_localized_name_reference(reference: object) -> bool:
    name = _text_field_reference_name(reference)
    return name is not None and name.startswith(("name_", "name:"))


def _prefer_generic_name_reference(references: list[object]) -> object | None:
    for index, reference in enumerate(references):
        if _text_field_reference_name(reference) == "name":
            if index == 0 or all(_is_localized_name_reference(item) for item in references[:index]):
                return reference
    return references[0] if references else None


def _text_field_references_from_children(
    children: list[object],
    *,
    allow_concat: bool,
    allow_to_string: bool,
) -> list[object]:
    references: list[object] = []
    for child in children:
        if isinstance(child, dict):
            continue
        reference = _first_simple_text_field_reference(
            child,
            allow_concat=allow_concat,
            allow_to_string=allow_to_string,
        )
        if reference is not None:
            references.append(reference)
    return references


def _first_coalesced_text_field_reference(expr: list[object]) -> object | None:
    references = _text_field_references_from_children(
        expr[1:],
        allow_concat=False,
        allow_to_string=False,
    )
    if references:
        return _prefer_generic_name_reference(references)
    return _prefer_generic_name_reference(
        _text_field_references_from_children(
            expr[1:],
            allow_concat=True,
            allow_to_string=True,
        )
    )


def _first_simple_text_field_reference(
    expr: object,
    *,
    allow_concat: bool = True,
    allow_to_string: bool = True,
) -> object | None:
    """Return the first direct ``['get', field]`` from text-oriented expressions."""
    if not isinstance(expr, list) or not expr:
        return None
    if _is_simple_text_field_reference(expr):
        return expr
    op = expr[0]
    if op == "coalesce":
        return _first_coalesced_text_field_reference(expr)
    if op == "concat" and not allow_concat:
        return None
    if op in {"concat", "format"}:
        return _first_text_field_reference_child(
            expr[1:],
            allow_concat=allow_concat,
            allow_to_string=allow_to_string,
        )
    if op == "to-string" and len(expr) >= 2:
        if not allow_to_string:
            return None
        return _first_simple_text_field_reference(
            expr[1],
            allow_concat=allow_concat,
            allow_to_string=allow_to_string,
        )
    return None


def _simplify_text_field(expr: object) -> object:
    """Simplify a Mapbox text-field expression to the first simple field reference.

    QGIS handles ``['get', 'name']`` but not richer Mapbox label expressions such
    as ``coalesce`` or ``format``. We extract the first useful ``['get', <field>]``
    reference so formatted labels still render with their primary label text.
    """
    if not isinstance(expr, list) or not expr:
        return expr
    op = expr[0]
    if op in {"coalesce", "concat", "format", "to-string"}:
        reference = _first_simple_text_field_reference(expr)
        if reference is not None:
            return reference
    if op == "step":
        # step expressions for text-field — find the first literal string fallback
        for item in expr[1:]:
            if isinstance(item, str) and item:
                return item
    return expr


def simplify_mapbox_style_expressions(style_definition: dict[str, object]) -> dict[str, object]:
    """Return a copy of a Mapbox style with expression-based colors replaced by
    literal fallback colors so QGIS' converter does not render them as black.

    Also simplifies ``text-field`` coalesce expressions to their first simple
    ``['get', field]`` reference so QGIS can resolve the label field name.

    Only color properties whose values are Mapbox expressions (lists) are
    simplified.  Literal strings (``hsl(...)``, ``#rrggbb``) are kept as-is.
    """
    import copy
    style = copy.deepcopy(style_definition)
    color_props = {
        "line-color", "fill-color", "fill-outline-color", "circle-color",
        "circle-stroke-color", "text-color", "text-halo-color",
        "icon-color", "icon-halo-color", "background-color",
        "fill-extrusion-color",
    }
    # Line width properties: expression values can reach 200–300 at max zoom;
    # QGIS may pick a large stop and produce QPen::setWidthF warnings.
    # We extract a z12-representative value and cap to a sane maximum.
    _WIDTH_PROPS = {"line-width", "line-gap-width", "line-offset"}
    _MAX_LINE_WIDTH_MM = 3.0  # ~11px at 96 DPI — sane max for cartographic lines
    # Per-layer-id text-size overrides to restore cartographic hierarchy.
    _TEXT_SIZE_OVERRIDES: dict[str, object] = {
        "natural-point-label": 9.0,
        "natural-line-label": 10.0,
        "poi-label": 9.0,
        "road-label": 10.0,
        "path-pedestrian-label": 9.0,
        "waterway-label": 10.0,
        "water-line-label": 11.0,
        "water-point-label": 12.0,
        "airport-label": 11.0,
        "settlement-subdivision-label": 8.0,
        "settlement-minor-label": 10.0,
        "settlement-major-label": 14.0,
        "state-label": 13.0,
        "country-label": 16.0,
        "continent-label": 16.0,
    }

    # Settlement layer label policy:
    # - settlement-major-label: only cities (type=city) — Geneva, Bern, Lyon, Lausanne
    # - settlement-minor-label: only towns (type=town) with filterrank<=2 — regional centres
    # - settlement-subdivision-label: suppress entirely
    # filterrank is available in tiles (verified z10: Cologny=3, Corsier=5, Geneva=1)
    # Filter by `type` only — filterrank is zoom-dependent and unreliable for
    # consistent cross-zoom filtering. `type` is stable across all zoom levels.
    # Mapbox Streets v8 settlement types: city, town, village, hamlet, suburb,
    # neighbourhood, quarter, borough
    _SETTLEMENT_FILTERS: dict[str, object] = {
        "settlement-major-label": ["match", ["get", "type"], ["city"], True, False],
        "settlement-minor-label": ["match", ["get", "type"], ["town"], True, False],
        "settlement-subdivision-label": None,
    }

    for layer in style.get("layers", []):
        layer_id = layer.get("id", "")

        # Suppress or filter settlement label layers
        settlement_filter = _SETTLEMENT_FILTERS.get(layer_id, "NOTSET")
        if settlement_filter != "NOTSET":
            if settlement_filter is None:
                layer["layout"] = layer.get("layout", {})
                layer["layout"]["visibility"] = "none"
            else:
                existing_filter = layer.get("filter")
                if existing_filter:
                    layer["filter"] = ["all", existing_filter, settlement_filter]
                else:
                    layer["filter"] = settlement_filter

        for section in ("paint", "layout"):
            props = layer.get(section)
            if not isinstance(props, dict):
                continue
            for prop in list(props.keys()):
                val = props[prop]
                if not isinstance(val, list):
                    continue
                if prop in color_props:
                    fallback = _extract_fallback_color(val)
                    if fallback is not None:
                        props[prop] = fallback
                elif prop in _WIDTH_PROPS:
                    width = _extract_midrange_size(val)
                    if width is not None:
                        # Convert px → mm (96 DPI) and clamp to sane range
                        width_mm = width * 25.4 / 96.0
                        props[prop] = max(0.1, min(width_mm, _MAX_LINE_WIDTH_MM))
                elif prop == "text-field":
                    props[prop] = _simplify_text_field(val)
                elif prop == "text-size":
                    override = _TEXT_SIZE_OVERRIDES.get(layer_id)
                    if override is not None:
                        props[prop] = override
                    else:
                        size = _extract_midrange_size(val)
                        if size is not None:
                            props[prop] = size
    return style


def extract_mapbox_vector_source_ids(style_definition: dict[str, object]) -> list[str]:
    """Extract vector tileset IDs from a Mapbox style definition.

    Supports sources declared like: {"url": "mapbox://tilesetA,tilesetB"}
    """
    sources = style_definition.get("sources") if isinstance(style_definition, dict) else None
    if not isinstance(sources, dict):
        raise MapboxConfigError("Mapbox style JSON does not declare any sources.")

    tileset_ids: list[str] = []
    for source in sources.values():
        if not isinstance(source, dict) or source.get("type") != "vector":
            continue
        url = str(source.get("url") or "").strip()
        if not url.startswith("mapbox://"):
            continue
        raw_ids = url.removeprefix("mapbox://")
        for tileset_id in raw_ids.split(","):
            normalized = tileset_id.strip()
            if normalized and normalized not in tileset_ids:
                tileset_ids.append(normalized)

    if not tileset_ids:
        raise MapboxConfigError("Mapbox style JSON does not expose a vector tileset source for QGIS.")
    return tileset_ids


def build_mapbox_vector_tiles_url(
    access_token: str,
    style_owner: str,
    style_id: str,
    *,
    tileset_ids: list[str] | tuple[str, ...] | None = None,
) -> str:
    """Return the Mapbox vector tile endpoint URL for a given style/source."""
    token, owner, resolved_style_id = _validated_mapbox_style_parts(access_token, style_owner, style_id)

    if tileset_ids:
        joined_tilesets = ",".join(quote(tileset_id.strip(), safe=".,-_") for tileset_id in tileset_ids if tileset_id.strip())
    else:
        joined_tilesets = "{owner}.{style_id}".format(
            owner=quote(owner, safe=""),
            style_id=quote(resolved_style_id, safe=""),
        )

    return (
        "https://api.mapbox.com/v4/{tilesets}/{{z}}/{{x}}/{{y}}.mvt"
        "?access_token={token}"
    ).format(
        tilesets=joined_tilesets,
        token=quote(token, safe=""),
    )


def build_mapbox_style_json_url(
    access_token: str,
    style_owner: str,
    style_id: str,
) -> str:
    """Return the Mapbox style JSON URL for use with QGIS vector tile styling."""
    token, owner, resolved_style_id = _validated_mapbox_style_parts(access_token, style_owner, style_id)

    return (
        "https://api.mapbox.com/styles/v1/{owner}/{style_id}?access_token={token}"
    ).format(
        owner=quote(owner, safe=""),
        style_id=quote(resolved_style_id, safe=""),
        token=quote(token, safe=""),
    )


def build_vector_tile_layer_uri(
    access_token: str,
    style_owner: str,
    style_id: str,
    *,
    tileset_ids: list[str] | tuple[str, ...] | None = None,
    include_style_url: bool = True,
) -> str:
    """Return a QGIS-compatible vector tile layer URI for a Mapbox style."""
    tiles_url = build_mapbox_vector_tiles_url(
        access_token,
        style_owner,
        style_id,
        tileset_ids=tileset_ids,
    )
    uri = "type=xyz&url={url}&zmin=0&zmax=22".format(
        url=quote(tiles_url, safe=":/?{}=%@,"),
    )
    if include_style_url:
        style_url = build_mapbox_style_json_url(access_token, style_owner, style_id)
        uri += "&styleUrl={style_url}".format(
            style_url=quote(style_url, safe=":/?{}=%@"),
        )
    return uri


def build_background_layer_name(preset_name: str | None, style_owner: str, style_id: str) -> str:
    label = (preset_name or DEFAULT_BACKGROUND_PRESET).strip() or DEFAULT_BACKGROUND_PRESET
    if label == "Custom":
        if style_owner and style_id:
            return f"{BACKGROUND_LAYER_PREFIX} — {style_owner}/{style_id}"
        return BACKGROUND_LAYER_PREFIX
    return f"{BACKGROUND_LAYER_PREFIX} — {label}"
