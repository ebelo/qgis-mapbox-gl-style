"""Resolve available open alternatives for the audited Outdoors DIN font stacks."""

from ...mapbox_config import _is_mapbox_outdoors_style

OPEN_FONT_FAMILY = "Barlow"
DIN_STYLES = {
    "DIN Pro Regular": "Regular",
    "DIN Pro Medium": "Medium",
    "DIN Pro Italic": "Italic",
    "DIN Pro Bold": "Bold",
}


def _source_layers(source_style):
    return [layer for layer in source_style.get("layers", []) if isinstance(layer, dict)]


def source_font_styles(source_style: dict) -> dict[str, str]:
    """Preserve source style distinctions before QGIS-safe simplification."""
    if not _is_mapbox_outdoors_style(source_style):
        return {}
    result = {}
    for layer in _source_layers(source_style):
        stack = layer.get("layout", {}).get("text-font")
        if not isinstance(stack, list) or not stack or not isinstance(stack[0], str):
            continue
        style = DIN_STYLES.get(stack[0])
        name = layer.get("id")
        if style is not None and isinstance(name, str) and name:
            result[name] = style
    return result


def _available_styles(styles):
    from qgis.PyQt.QtGui import QFont, QFontInfo

    available = set()
    for style in set(styles):
        candidate = QFont(OPEN_FONT_FAMILY)
        candidate.setStyleName(style)
        resolved = QFontInfo(candidate)
        if resolved.family() == OPEN_FONT_FAMILY and resolved.styleName() == style:
            available.add(style)
    return available


def apply_available_outdoors_fonts(labeling, source_style: dict) -> int:
    """Use verified Barlow faces when installed; retain existing fallback otherwise.

    Docker supplies all four faces. Desktop installations without them remain
    unchanged. Unknown stacks, custom styles and other presets are not retuned.
    """
    mapping = source_font_styles(source_style)
    if not mapping:
        return 0
    available = _available_styles(mapping.values())
    # Longest source ID wins so a generated zoom/filter suffix cannot match a
    # shorter source owner accidentally (e.g. road-label and road-label-extra).
    owners = sorted(
        (layer["id"] for layer in _source_layers(source_style) if isinstance(layer.get("id"), str)),
        key=len, reverse=True,
    )
    styles = list(labeling.styles())
    changed = 0
    for label in styles:
        name = label.styleName()
        owner = next((key for key in owners if name == key or name.startswith(key + "-")), None)
        if mapping.get(owner) not in available:
            continue
        settings = label.labelSettings()
        text_format = settings.format()
        font = text_format.font()
        # Qt 5 can retain the converter's family list even after setFamily().
        # Replace that list too, otherwise QFontInfo still resolves Noto Sans.
        font.setFamily(OPEN_FONT_FAMILY)
        font.setFamilies([OPEN_FONT_FAMILY, "Noto Sans"])
        font.setStyleName(mapping[owner])
        text_format.setFont(font)
        settings.setFormat(text_format)
        label.setLabelSettings(settings)
        changed += 1
    if changed:
        labeling.setStyles(styles)
    return changed
