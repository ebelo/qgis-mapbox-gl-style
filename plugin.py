from __future__ import annotations

import os
from pathlib import Path

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox
from qgis.core import QgsMessageLog, Qgis

from .mapbox_config import DEFAULT_BACKGROUND_PRESET, TILE_MODE_VECTOR
from .plugin_dialog import (
    ENV_TOKEN_NAMES,
    PluginSettings,
    PluginSettingsDialog,
    read_plugin_settings,
)
from .visualization.infrastructure.background_map_service import BackgroundMapService

MENU_NAME = "&QGIS Mapbox GL Style"
MESSAGE_TAG = "QGIS Mapbox GL Style"


class QgisMapboxGlStylePlugin:
    def __init__(self, iface):
        self.iface = iface
        self.load_action = None
        self.settings_action = None
        self._settings_dialog = None
        self._background_maps = BackgroundMapService()

    def initGui(self):
        icon = QIcon(str(Path(__file__).with_name("icon.png")))

        self.load_action = QAction(icon, "Load Mapbox Outdoors", self.iface.mainWindow())
        self.load_action.triggered.connect(self.load_outdoors)
        self.iface.addToolBarIcon(self.load_action)
        self.iface.addPluginToMenu(MENU_NAME, self.load_action)

        self.settings_action = QAction(icon, "Settings", self.iface.mainWindow())
        self.settings_action.triggered.connect(self.show_settings)
        self.iface.addPluginToMenu(MENU_NAME, self.settings_action)

    def unload(self):
        if self.load_action is not None:
            self.iface.removePluginMenu(MENU_NAME, self.load_action)
            self.iface.removeToolBarIcon(self.load_action)
            self.load_action = None

        if self.settings_action is not None:
            self.iface.removePluginMenu(MENU_NAME, self.settings_action)
            self.settings_action = None

        if self._settings_dialog is not None:
            self._settings_dialog.close()
            self._settings_dialog.deleteLater()
            self._settings_dialog = None

    def show_settings(self) -> bool:
        dialog = self._settings_dialog
        if dialog is None:
            dialog = PluginSettingsDialog(parent=self.iface.mainWindow())
            self._settings_dialog = dialog
        dialog.load_settings()
        if dialog.exec_() != dialog.Accepted:
            return False
        dialog.save_settings()
        return True

    def load_outdoors(self) -> None:
        settings = read_plugin_settings()
        if not settings.access_token:
            if not self.show_settings():
                return
            settings = read_plugin_settings()

        if not settings.access_token:
            env_names = ", ".join(ENV_TOKEN_NAMES)
            QMessageBox.warning(
                self.iface.mainWindow(),
                "Mapbox token required",
                f"Enter a Mapbox access token in Settings or set one of: {env_names}.",
            )
            return

        try:
            layer = self._background_maps.ensure_background_layer(
                True,
                DEFAULT_BACKGROUND_PRESET,
                settings.access_token,
                settings.style_owner,
                settings.style_id,
                settings.tile_mode,
            )
        except Exception as exc:  # pragma: no cover - exercised inside QGIS.
            QgsMessageLog.logMessage(str(exc), MESSAGE_TAG, Qgis.Critical)
            self.iface.messageBar().pushCritical(MESSAGE_TAG, str(exc))
            return

        if layer is not None:
            canvas = self.iface.mapCanvas()
            canvas.refresh()
            self.iface.messageBar().pushSuccess(MESSAGE_TAG, f"Loaded {layer.name()}.")


def token_from_environment() -> str:
    for name in ENV_TOKEN_NAMES:
        token = os.environ.get(name, "").strip()
        if token:
            return token
    return ""


def default_plugin_settings() -> PluginSettings:
    return PluginSettings(
        access_token=token_from_environment(),
        style_owner="mapbox",
        style_id="outdoors-v12",
        tile_mode=TILE_MODE_VECTOR,
    )
