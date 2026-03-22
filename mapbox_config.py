from __future__ import annotations

from urllib.parse import quote


class MapboxConfigError(ValueError):
    """Raised when the configured Mapbox background settings are incomplete."""


BACKGROUND_LAYER_PREFIX = "qfit background"
DEFAULT_BACKGROUND_PRESET = "Outdoor"
DEFAULT_MAPBOX_TILE_SIZE = 512
DEFAULT_MAPBOX_RETINA = True
DEFAULT_MAPBOX_TILE_PIXEL_RATIO = 2

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


def build_xyz_layer_uri(access_token: str, style_owner: str, style_id: str) -> str:
    url = build_mapbox_tiles_url(access_token, style_owner, style_id)
    return "type=xyz&url={url}&zmin=0&zmax=22&tilePixelRatio={tile_pixel_ratio}".format(
        url=quote(url, safe=":/?{}=%@"),
        tile_pixel_ratio=DEFAULT_MAPBOX_TILE_PIXEL_RATIO,
    )


def build_background_layer_name(preset_name: str | None, style_owner: str, style_id: str) -> str:
    label = (preset_name or DEFAULT_BACKGROUND_PRESET).strip() or DEFAULT_BACKGROUND_PRESET
    if label == "Custom":
        if style_owner and style_id:
            return f"{BACKGROUND_LAYER_PREFIX} — {style_owner}/{style_id}"
        return BACKGROUND_LAYER_PREFIX
    return f"{BACKGROUND_LAYER_PREFIX} — {label}"
