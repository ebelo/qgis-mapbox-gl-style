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
    build_background_layer_name,
    build_vector_tile_layer_uri,
    build_xyz_layer_uri,
    extract_mapbox_vector_source_ids,
    fetch_mapbox_style_definition,
    resolve_background_style,
    simplify_mapbox_style_expressions,
    snap_web_mercator_bounds_to_native_zoom,
)

_WORKING_CRS = "EPSG:3857"


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
                    self._apply_mapbox_gl_style(layer, simplified_style)
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
        _LAYER_PRIORITIES = {
            "continent-label": 10,
            "country-label": 10,
            "state-label": 9,
            "settlement-major-label": 8,
            "settlement-minor-label": 6,
            "settlement-subdivision-label": 3,
            "water-point-label": 7,
            "water-line-label": 6,
            "natural-line-label": 4,
            "natural-point-label": 2,
            "poi-label": 2,
            "road-label": 5,
            "airport-label": 5,
        }
        _SETTLEMENT_LAYERS = {"settlement-major-label", "settlement-minor-label"}

        try:
            from qgis.core import QgsProperty  # noqa: PLC0415

            for style in labeling.styles():
                layer_name = style.layerName()
                priority = _LAYER_PRIORITIES.get(layer_name)
                if priority is None:
                    continue
                settings = style.labelSettings()
                if settings is None:
                    continue
                settings.priority = priority
                if layer_name in _SETTLEMENT_LAYERS:
                    dd_props = settings.dataDefinedProperties()
                    dd_props.setProperty(
                        87,
                        QgsProperty.fromExpression(
                            "greatest(1, least(10, 10 - coalesce(to_int(\"sizerank\"), 8) + 1))"
                        ),
                    )
                    settings.setDataDefinedProperties(dd_props)
                style.setLabelSettings(settings)
        except (RuntimeError, AttributeError):
            logger.debug("Mapbox GL style application skipped", exc_info=True)

    def _apply_mapbox_gl_style(self, layer: QgsVectorTileLayer, style_definition: dict) -> None:
        try:
            from qgis.core import (  # noqa: PLC0415
                QgsMapBoxGlStyleConversionContext,
                QgsMapBoxGlStyleConverter,
                Qgis,
            )

            ctx = QgsMapBoxGlStyleConversionContext()
            ctx.setTargetUnit(Qgis.RenderUnit.Millimeters)
            ctx.setPixelSizeConversionFactor(25.4 / 96.0)
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
