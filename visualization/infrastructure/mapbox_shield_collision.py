"""Keep low-zoom Outdoors shield sprites in the label collision lifecycle."""

import base64
import binascii
from functools import lru_cache
import re

from ...mapbox_config import _is_mapbox_outdoors_style

_INLINE_PREFIX = "base64:"
_INLINE_IMAGE = re.compile(r"base64:[A-Za-z0-9+/=]+")


@lru_cache(maxsize=256)
def _svg_wrapped_sprite(path: str) -> str:
    """Wrap the unchanged inline PNG in an SVG usable by PAL label backgrounds."""
    from qgis.PyQt.QtGui import QImage

    if not path.startswith(_INLINE_PREFIX):
        raise ValueError("Shield background requires an inline sprite")
    encoded = path[len(_INLINE_PREFIX):]
    try:
        data = base64.b64decode(encoded, validate=True)
    except binascii.Error as exc:
        raise ValueError("Invalid inline shield sprite") from exc
    image = QImage.fromData(data, "PNG")
    if image.isNull():
        raise ValueError("Invalid PNG shield sprite")
    width, height = image.width(), image.height()
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<image width="{width}" height="{height}" '
        f'xlink:href="data:image/png;base64,{encoded}"/></svg>'
    )
    return _INLINE_PREFIX + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def _background_sprite(marker):
    from qgis.core import QgsProperty, QgsSymbolLayer

    path_property = marker.dataDefinedProperties().property(QgsSymbolLayer.PropertyName)
    if path_property.isActive():
        expression = path_property.expressionString()
        if not expression or not _INLINE_IMAGE.search(expression):
            raise ValueError("Unsupported shield sprite selector")
        expression = _INLINE_IMAGE.sub(lambda match: _svg_wrapped_sprite(match.group()), expression)
        fallback = _svg_wrapped_sprite(marker.path()) if marker.path() else ""
        return fallback, QgsProperty.fromExpression(expression)
    return _svg_wrapped_sprite(marker.path()), QgsProperty()


def _coupled_settings(settings, symbol):
    from qgis.PyQt.QtCore import QSizeF
    from qgis.core import QgsPalLayerSettings, QgsSymbolLayer, QgsTextBackgroundSettings

    marker = symbol.symbolLayer(0)
    path, path_property = _background_sprite(marker)
    text_format = settings.format()
    background = text_format.background()
    background.setEnabled(True)
    background.setType(QgsTextBackgroundSettings.ShapeSVG)
    background.setSvgFile(path)
    background.setSizeType(QgsTextBackgroundSettings.SizeFixed)
    # ShapeSVG consumes only X; the wrapped sprite viewBox supplies its aspect
    # ratio (including for data-defined widths). Y is not a collision rectangle.
    background.setSize(QSizeF(symbol.size(), symbol.size()))
    background.setSizeUnit(symbol.sizeUnit())
    background.setSizeMapUnitScale(symbol.sizeMapUnitScale())
    background.setOpacity(symbol.opacity())
    text_format.setBackground(background)
    settings.setFormat(text_format)
    properties = settings.dataDefinedProperties()
    properties.setProperty(QgsPalLayerSettings.Property.ShapeSVGFile, path_property)
    width = marker.dataDefinedProperties().property(QgsSymbolLayer.PropertyWidth)
    if width.isActive():
        properties.setProperty(QgsPalLayerSettings.Property.ShapeSizeX, width)
    settings.setDataDefinedProperties(properties)
    return settings


def _supported_pair(label, renderer) -> bool:
    from qgis.core import Qgis

    if renderer is None or not label.isEnabled() or not renderer.isEnabled():
        return False
    if label.geometryType() != Qgis.GeometryType.Point:
        return False
    if (label.filterExpression(), label.minZoomLevel(), label.maxZoomLevel()) != (
        renderer.filterExpression(), renderer.minZoomLevel(), renderer.maxZoomLevel()
    ):
        return False
    symbol = renderer.symbol()
    settings = label.labelSettings()
    text_format = settings.format()
    background = text_format.background()
    return (
        symbol is not None
        and symbol.type() == Qgis.SymbolType.Marker
        and symbol.symbolLayerCount() == 1
        and symbol.symbolLayer(0).layerType() == "RasterMarker"
        and not background.enabled()
    )


def couple_outdoors_shield_backgrounds(renderer, labeling, style_definition: dict) -> int:
    """Hide each supported point shield together with its collision-suppressed text.

    QGIS converts Mapbox icons into independent renderer symbols. PAL suppresses
    colliding text, but cannot suppress those symbols. Move the existing raster
    pixels and per-feature size/path selectors to PAL's label background instead.
    Line shields, other presets and unsupported renderer/label pairs stay intact.
    """
    if not _is_mapbox_outdoors_style(style_definition):
        return 0
    render_styles = list(renderer.styles())
    labels = list(labeling.styles())
    by_name = {style.styleName(): style for style in render_styles}
    changed = 0
    for label in labels:
        name = label.styleName()
        if not name.startswith("road-number-shield-") or not name.endswith("-below-z11"):
            continue
        owner = by_name.get(name)
        if not _supported_pair(label, owner):
            continue
        try:
            settings = _coupled_settings(label.labelSettings(), owner.symbol())
        except ValueError:
            # Retain the existing icon when no lossless inline conversion exists.
            continue
        label.setLabelSettings(settings)
        owner.setEnabled(False)
        changed += 1
    if changed:
        renderer.setStyles(render_styles)
        labeling.setStyles(labels)
    return changed
