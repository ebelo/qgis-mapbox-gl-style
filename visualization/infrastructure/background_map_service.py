import logging

logger = logging.getLogger(__name__)

from qgis.core import (
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsVectorTileLayer,
)

from ...mapbox_config import (
    BACKGROUND_LAYER_PREFIX,
    TILE_MODE_RASTER,
    TILE_MODE_VECTOR,
    MapboxSpriteResources,
    base_mapbox_style_layer_id_for_qfit,
    build_background_layer_name,
    build_vector_tile_layer_uri,
    build_xyz_layer_uri,
    extract_mapbox_vector_source_ids,
    fetch_mapbox_style_definition,
    fetch_mapbox_sprite_resources,
    resolve_background_style,
    simplify_mapbox_style_expressions,
    snap_web_mercator_bounds_to_native_zoom,
)

_WORKING_CRS = "EPSG:3857"
_LABEL_PRIORITIES = {
    "continent-label": 10,
    "country-label": 10,
    "state-label": 9,
    "settlement-major-label": 8,
    "settlement-minor-label": 6,
    "settlement-subdivision-label": 3,
    "water-point-label": 7,
    "water-line-label": 6,
    "natural-line-label": 4,
    "natural-point-label": 4,
    "poi-label": 5,
    "road-label": 4,
    "airport-label": 8,
}
_SETTLEMENT_LAYERS = {"settlement-major-label", "settlement-minor-label"}
_SWISS_MOTORWAY_SHIELD_PRIORITY = 6
_SWISS_MOTORWAY_SHIELD_Z11_STYLE_MARKER = "ch-motorway-icon-z11-plus"
_MAPBOX_SYMBOL_PIXEL_TO_MM = 25.4 / 96.0
_MAPBOX_DEFAULT_SYMBOL_SPACING_PX = 250.0
_ROAD_LABEL_LOW_ZOOM_SYMBOL_SPACING_PX = 150.0
_LINE_LABEL_REPEAT_DISTANCE_LAYERS = {
    "ferry-aerialway-label",
    "path-pedestrian-label",
}
# Representative-zoom values from mapbox_config's waterway-label spacing
# expression after qfit splits it into static QGIS zoom bands.  The z17+
# value is wider than Mapbox's raw 400 px spacing because QGIS repeats labels
# more densely along segmented waterways in the Zermatt z18 comparison.
_WATERWAY_LABEL_REPEAT_DISTANCE_PX_BY_STYLE_MARKER = {
    "z13-to-z15": 250.0,
    "z15-to-z17": 325.0,
    "z17-plus": 600.0,
}
_CONTOUR_LABEL_ELEVATION_FIELD_EXPRESSION = '"ele"'
_CONTOUR_LABEL_EXPRESSION = "concat(\"ele\", ' m')"


def _label_style_mapbox_layer_id(style) -> str:
    for accessor in ("styleName", "layerName"):
        try:
            layer_id = getattr(style, accessor)()
        except AttributeError:
            continue
        if isinstance(layer_id, str) and layer_id:
            return base_mapbox_style_layer_id_for_qfit(layer_id)
    return ""


def _label_style_name(style) -> str:
    try:
        style_name = style.styleName()
    except AttributeError:
        return ""
    return style_name if isinstance(style_name, str) else ""


def _label_priority(layer_name: str, style) -> int | None:
    if (
        layer_name == "road-number-shield"
        and _SWISS_MOTORWAY_SHIELD_Z11_STYLE_MARKER in _label_style_name(style)
    ):
        return _SWISS_MOTORWAY_SHIELD_PRIORITY
    return _LABEL_PRIORITIES.get(layer_name)


def _symbol_spacing_mm(pixels: float) -> float:
    return pixels * _MAPBOX_SYMBOL_PIXEL_TO_MM


def _label_repeat_distance(layer_name: str, style) -> float | None:
    style_name = _label_style_name(style)
    if layer_name == "road-label":
        if style_name in {"road-label-below-z12", "road-label-z12-to-z15"}:
            return _symbol_spacing_mm(_ROAD_LABEL_LOW_ZOOM_SYMBOL_SPACING_PX)
        return _symbol_spacing_mm(_MAPBOX_DEFAULT_SYMBOL_SPACING_PX)
    if layer_name in _LINE_LABEL_REPEAT_DISTANCE_LAYERS:
        return _symbol_spacing_mm(_MAPBOX_DEFAULT_SYMBOL_SPACING_PX)
    if layer_name != "waterway-label":
        return None

    for marker, pixels in _WATERWAY_LABEL_REPEAT_DISTANCE_PX_BY_STYLE_MARKER.items():
        if marker in style_name:
            return _symbol_spacing_mm(pixels)
    return _symbol_spacing_mm(_MAPBOX_DEFAULT_SYMBOL_SPACING_PX)


def _label_field_expression(layer_name: str, settings) -> str | None:
    if (
        layer_name == "contour-label"
        and getattr(settings, "fieldName", "") == _CONTOUR_LABEL_ELEVATION_FIELD_EXPRESSION
        and getattr(settings, "isExpression", False)
    ):
        return _CONTOUR_LABEL_EXPRESSION
    return None


def _needs_repeat_distance(settings) -> bool:
    repeat_distance = getattr(settings, "repeatDistance", 0.0)
    return not isinstance(repeat_distance, (int, float)) or repeat_distance <= 0


def _apply_settlement_label_priority(settings, qgs_property) -> None:
    dd_props = settings.dataDefinedProperties()
    dd_props.setProperty(
        87,
        qgs_property.fromExpression(
            "greatest(1, least(10, 10 - coalesce(to_int(\"symbolrank\"), to_int(\"sizerank\"), 8) + 1))"
        ),
    )
    settings.setDataDefinedProperties(dd_props)


def _apply_label_settings(
    settings,
    *,
    layer_name: str,
    priority: int | None,
    repeat_distance: float | None,
    field_expression: str | None,
    qgs_property,
    qgis,
) -> bool:
    changed = False
    if priority is not None:
        settings.priority = priority
        if layer_name in _SETTLEMENT_LAYERS:
            _apply_settlement_label_priority(settings, qgs_property)
        changed = True
    if repeat_distance is not None and _needs_repeat_distance(settings):
        settings.repeatDistance = repeat_distance
        settings.repeatDistanceUnit = qgis.RenderUnit.Millimeters
        changed = True
    if field_expression is not None and (
        getattr(settings, "fieldName", "") != field_expression
        or not getattr(settings, "isExpression", False)
    ):
        settings.fieldName = field_expression
        settings.isExpression = True
        changed = True
    return changed


def apply_mapbox_label_priority(labeling) -> None:
    try:
        from qgis.core import QgsProperty, Qgis  # noqa: PLC0415

        styles = list(labeling.styles())
        changed = False
        for style in styles:
            layer_name = _label_style_mapbox_layer_id(style)
            priority = _label_priority(layer_name, style)
            repeat_distance = _label_repeat_distance(layer_name, style)
            if priority is None and repeat_distance is None and layer_name != "contour-label":
                continue
            settings = style.labelSettings()
            if settings is None:
                continue
            field_expression = _label_field_expression(layer_name, settings)
            if priority is None and repeat_distance is None and field_expression is None:
                continue
            if _apply_label_settings(
                settings,
                layer_name=layer_name,
                priority=priority,
                repeat_distance=repeat_distance,
                field_expression=field_expression,
                qgs_property=QgsProperty,
                qgis=Qgis,
            ):
                style.setLabelSettings(settings)
                changed = True
        if changed:
            labeling.setStyles(styles)
    except (RuntimeError, AttributeError):
        logger.debug("Mapbox GL style application skipped", exc_info=True)


class BackgroundMapService:
    """Manages Mapbox background tile layers (raster and vector) in the QGIS project.

    Encapsulates all background-map concerns: layer creation, style application,
    layer-tree ordering, and tile-zoom snapping.  Stateless — operates exclusively
    on :func:`QgsProject.instance`.
    """

    def ensure_background_layer(
        self,
        enabled,
        preset_name,
        access_token,
        style_owner="",
        style_id="",
        tile_mode=TILE_MODE_RASTER,
    ):
        """Add (or replace) the background tile layer.

        Returns the new layer, or *None* when *enabled* is *False*.
        Raises :class:`RuntimeError` when the raster fallback also fails.
        """
        if not enabled:
            self._remove_background_layers()
            return None

        resolved_owner, resolved_style_id = resolve_background_style(preset_name, style_owner, style_id)
        display_name = build_background_layer_name(preset_name, resolved_owner, resolved_style_id)

        layer = None
        if tile_mode == TILE_MODE_VECTOR:
            try:
                style_definition = fetch_mapbox_style_definition(access_token, resolved_owner, resolved_style_id)
                simplified_style = simplify_mapbox_style_expressions(style_definition)
                sprite_resources = None
                try:
                    sprite_resources = fetch_mapbox_sprite_resources(
                        access_token,
                        resolved_owner,
                        resolved_style_id,
                        sprite_url=style_definition.get("sprite"),
                    )
                except (RuntimeError, KeyError, ValueError, OSError):
                    logger.debug("Mapbox sprite sheet unavailable for vector style conversion", exc_info=True)
                tileset_ids = extract_mapbox_vector_source_ids(style_definition)
                uri = build_vector_tile_layer_uri(
                    access_token,
                    resolved_owner,
                    resolved_style_id,
                    tileset_ids=tileset_ids,
                    include_style_url=False,
                )
                layer = QgsVectorTileLayer(uri, display_name)
                if not layer.isValid():
                    layer = None
                else:
                    self._apply_mapbox_gl_style(layer, simplified_style, sprite_resources=sprite_resources)
            except (RuntimeError, KeyError, ValueError, OSError):
                logger.warning("Vector tile layer creation failed, falling back to raster", exc_info=True)
                layer = None

        if layer is None:
            uri = build_xyz_layer_uri(access_token, resolved_owner, resolved_style_id)
            layer = QgsRasterLayer(uri, display_name, "wms")
            if not layer.isValid():
                raise RuntimeError("Could not load the selected Mapbox background layer into QGIS.")

        self._remove_background_layers()
        project = QgsProject.instance()
        project.addMapLayer(layer, False)
        project.layerTreeRoot().addLayer(layer)
        self.move_background_layers_to_bottom()
        return layer

    def move_background_layers_to_bottom(self):
        """Reorder layer tree so all background layers sit below other layers."""
        root = QgsProject.instance().layerTreeRoot()
        background_nodes = []
        other_nodes = []
        for child in list(root.children()):
            layer = child.layer() if hasattr(child, "layer") else None
            if layer is not None and layer.name().startswith(BACKGROUND_LAYER_PREFIX):
                background_nodes.append(child)
            else:
                other_nodes.append(child)

        desired_order = other_nodes + background_nodes
        if desired_order and desired_order != list(root.children()):
            root.reorderChildren(desired_order)

    def snap_extent_to_background_tile_zoom(self, extents, canvas):
        """Snap *extents* to the nearest native Mapbox tile zoom level."""
        if extents is None or extents.isEmpty():
            return extents
        if QgsProject.instance().crs().authid() != _WORKING_CRS:
            return extents
        if not self._has_raster_background_layer():
            return extents

        viewport_width_px = getattr(canvas, "width", lambda: 1024)()
        viewport_height_px = getattr(canvas, "height", lambda: 768)()
        snapped_bounds, _ = snap_web_mercator_bounds_to_native_zoom(
            (
                extents.xMinimum(),
                extents.yMinimum(),
                extents.xMaximum(),
                extents.yMaximum(),
            ),
            viewport_width_px,
            viewport_height_px,
        )
        return QgsRectangle(*snapped_bounds)

    def _remove_background_layers(self):
        project = QgsProject.instance()
        for layer in list(project.mapLayers().values()):
            if layer.name().startswith(BACKGROUND_LAYER_PREFIX):
                project.removeMapLayer(layer.id())

    def _has_raster_background_layer(self) -> bool:
        for layer in QgsProject.instance().mapLayers().values():
            if layer.name().startswith(BACKGROUND_LAYER_PREFIX) and isinstance(layer, QgsRasterLayer):
                return True
        return False

    def _apply_label_priority(self, labeling) -> None:
        apply_mapbox_label_priority(labeling)

    def _apply_sprite_resources_to_context(self, ctx: object, sprite_resources: MapboxSpriteResources | None) -> None:
        if sprite_resources is None:
            return
        try:
            from qgis.PyQt.QtGui import QImage  # noqa: PLC0415

            sprite_image = QImage()
            if not sprite_image.loadFromData(sprite_resources.image_bytes):
                logger.debug("Mapbox sprite sheet image could not be decoded for vector style conversion")
                return
            argb_format = getattr(QImage, "Format_ARGB32", None)
            if argb_format is not None:
                sprite_image = sprite_image.convertToFormat(argb_format)
            ctx.setSprites(sprite_image, sprite_resources.definitions)
        except (RuntimeError, ImportError, AttributeError, TypeError):
            logger.debug("Mapbox sprite sheet skipped for vector style conversion", exc_info=True)

    def _apply_mapbox_gl_style(
        self,
        layer: QgsVectorTileLayer,
        style_definition: dict,
        *,
        sprite_resources: MapboxSpriteResources | None = None,
    ) -> None:
        try:
            from qgis.core import (  # noqa: PLC0415
                QgsMapBoxGlStyleConversionContext,
                QgsMapBoxGlStyleConverter,
                Qgis,
            )

            ctx = QgsMapBoxGlStyleConversionContext()
            ctx.setTargetUnit(Qgis.RenderUnit.Millimeters)
            ctx.setPixelSizeConversionFactor(25.4 / 96.0)
            self._apply_sprite_resources_to_context(ctx, sprite_resources)
            converter = QgsMapBoxGlStyleConverter()
            result = converter.convert(style_definition, ctx)
            if result == QgsMapBoxGlStyleConverter.Success:
                renderer = converter.renderer()
                labeling = converter.labeling()
                if renderer is not None:
                    layer.setRenderer(renderer)
                if labeling is not None:
                    self._apply_label_priority(labeling)
                    layer.setLabeling(labeling)
                    layer.setLabelsEnabled(True)
        except (RuntimeError, ImportError):
            logger.debug("Extent transformation failed, using default renderer", exc_info=True)
