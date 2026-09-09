"""Pure coverage for lossless sprite conversion and safe collision pairing."""
import base64
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import xml.etree.ElementTree as ET

import unittest

from tests import _path  # noqa: F401

from qfit.visualization.infrastructure import mapbox_shield_collision as collision



def pair():
    label, owner = MagicMock(), MagicMock()
    for item in [label, owner]:
        item.styleName.return_value = 'road-number-shield-3-known-icons-below-z11'
        item.filterExpression.return_value = 'same filter'
        item.minZoomLevel.return_value = 6
        item.maxZoomLevel.return_value = 10
        item.isEnabled.return_value = True
    label.geometryType.return_value = 0
    label.labelSettings.return_value.format.return_value.background.return_value.enabled.return_value = False
    symbol = owner.symbol.return_value
    symbol.type.return_value = 0
    symbol.symbolLayerCount.return_value = 1
    marker = symbol.symbolLayer.return_value
    marker.layerType.return_value = 'RasterMarker'
    marker.path.return_value = 'base64:eA=='
    marker.dataDefinedProperties.return_value.property.return_value.isActive.return_value = False
    return label, owner


def run_pair(label, owner, definition=None):
    renderer, labeling = MagicMock(), MagicMock()
    renderer.styles.return_value = [owner]
    labeling.styles.return_value = [label]
    result = collision.couple_outdoors_shield_backgrounds(
        renderer, labeling, definition or {'owner': 'mapbox', 'id': 'outdoors-v12'})
    return result, renderer, labeling



class ShieldCollisionTests(unittest.TestCase):
    def setUp(self):
        core = MagicMock()
        core.Qgis.GeometryType.Point = 0
        core.Qgis.SymbolType.Marker = 0
        core.QgsSymbolLayer.PropertyName = 2
        core.QgsSymbolLayer.PropertyWidth = 9
        core.QgsPalLayerSettings.Property.ShapeSVGFile = 48
        core.QgsPalLayerSettings.Property.ShapeSizeX = 50
        core.QgsProperty.fromExpression.side_effect = lambda expression: expression
        gui = MagicMock()
        image = gui.QImage.fromData.return_value
        image.isNull.return_value = False
        image.width.return_value = 12
        image.height.return_value = 8
        collision._svg_wrapped_sprite.cache_clear()
        module_patch = patch.dict('sys.modules', {'qgis.core': core, 'qgis.PyQt.QtGui': gui,
                                                'qgis.PyQt.QtCore': MagicMock()})
        module_patch.start()
        self.addCleanup(module_patch.stop)
        self.addCleanup(collision._svg_wrapped_sprite.cache_clear)
        self.api = SimpleNamespace(core=core, gui=gui, image=image)

    def test_wrapper_preserves_original_sprite_bytes(self):
        api = self.api
        encoded = base64.b64encode(b'original sprite bytes').decode()
        result = collision._svg_wrapped_sprite('base64:' + encoded)
        document = ET.fromstring(base64.b64decode(result.removeprefix('base64:')))
        assert document.attrib['viewBox'] == '0 0 12 8'
        assert next(iter(document)).attrib['{http://www.w3.org/1999/xlink}href'].endswith(encoded)
        assert collision._svg_wrapped_sprite('base64:' + encoded) == result
        api.gui.QImage.fromData.assert_called_once()

    def test_rejects_non_inline_or_invalid_sprite(self):
        for value in ['external-sprite.png', 'base64:invalid!']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                collision._svg_wrapped_sprite(value)

    def test_rejects_non_png(self):
        api = self.api
        api.image.isNull.return_value = True
        with self.assertRaises(ValueError):
            collision._svg_wrapped_sprite('base64:eA==')

    def test_static_and_feature_selected_sprites_couple_without_deleting_styles(self):
        api = self.api
        for dynamic in [False, True]:
            label, owner = pair()
            marker = owner.symbol.return_value.symbolLayer.return_value
            prop = marker.dataDefinedProperties.return_value.property.return_value
            prop.isActive.return_value = dynamic
            prop.expressionString.return_value = "CASE WHEN shield = 'red' THEN 'base64:eA==' ELSE '' END"
            result, renderer, labeling = run_pair(label, owner)
            assert result == 1
            owner.setEnabled.assert_called_once_with(False)
            label.setLabelSettings.assert_called_once_with(label.labelSettings.return_value)
            renderer.setStyles.assert_called_once_with([owner])
            labeling.setStyles.assert_called_once_with([label])
            if dynamic:
                expression = api.core.QgsProperty.fromExpression.call_args.args[0]
                assert "WHEN shield = 'red'" in expression
                assert 'base64:eA==' not in expression

    def test_unsupported_pairs_leave_existing_rendering_intact(self):
        for case in ['other-style', 'high-zoom', 'no-owner', 'disabled', 'line', 'filter', 'no-symbol', 'multilayer', 'non-raster', 'background', 'external-path', 'unsupported-selector']:
            with self.subTest(case=case):
                self._assert_unsupported_pair(case)

    def _assert_unsupported_pair(self, case):
        label, owner = pair()
        definition = {'owner': 'mapbox', 'id': 'outdoors-v12'}
        if case == 'other-style':
            definition['owner'] = 'custom-owner'
        if case == 'high-zoom':
            label.styleName.return_value = 'road-number-shield-3-known-icons-z11-plus'
        if case == 'no-owner':
            owner.styleName.return_value = 'different-owner'
        if case == 'disabled':
            label.isEnabled.return_value = False
        if case == 'line':
            label.geometryType.return_value = 1
        if case == 'filter':
            owner.filterExpression.return_value = 'different filter'
        if case == 'no-symbol':
            owner.symbol.return_value = None
        if case == 'multilayer':
            owner.symbol.return_value.symbolLayerCount.return_value = 2
        if case == 'non-raster':
            owner.symbol.return_value.symbolLayer.return_value.layerType.return_value = 'SimpleMarker'
        if case == 'background':
            label.labelSettings.return_value.format.return_value.background.return_value.enabled.return_value = True
        if case == 'external-path':
            owner.symbol.return_value.symbolLayer.return_value.path.return_value = 'external.png'
        if case == 'unsupported-selector':
            prop = owner.symbol.return_value.symbolLayer.return_value.dataDefinedProperties.return_value.property.return_value
            prop.isActive.return_value = True
            prop.expressionString.return_value = 'external_path_field'
        result, renderer, labeling = run_pair(label, owner, definition)
        assert result == 0
        owner.setEnabled.assert_not_called()
        label.setLabelSettings.assert_not_called()
        renderer.setStyles.assert_not_called()
        labeling.setStyles.assert_not_called()
