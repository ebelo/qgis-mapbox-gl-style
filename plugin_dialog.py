from __future__ import annotations

from dataclasses import dataclass

from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QVBoxLayout,
)

from .mapbox_config import TILE_MODE_VECTOR, TILE_MODES

SETTINGS_PREFIX = "qgis_mapbox_gl_style"
ENV_TOKEN_NAMES = (
    "QGIS_MAPBOX_GL_STYLE_MAPBOX_TOKEN",
    "MAPBOX_ACCESS_TOKEN",
    "QFIT_MAPBOX_ACCESS_TOKEN",
)


@dataclass(frozen=True)
class PluginSettings:
    access_token: str
    style_owner: str
    style_id: str
    tile_mode: str


def _setting_key(name: str) -> str:
    return f"{SETTINGS_PREFIX}/{name}"


def _read_setting(settings: QSettings, name: str, default: str = "") -> str:
    value = settings.value(_setting_key(name), default)
    return value if isinstance(value, str) else str(value or "")


def read_plugin_settings() -> PluginSettings:
    from .plugin import default_plugin_settings

    defaults = default_plugin_settings()
    settings = QSettings()
    token = _read_setting(settings, "mapbox_token", defaults.access_token).strip()
    return PluginSettings(
        access_token=token,
        style_owner=_read_setting(settings, "style_owner", defaults.style_owner).strip(),
        style_id=_read_setting(settings, "style_id", defaults.style_id).strip(),
        tile_mode=_read_setting(settings, "tile_mode", defaults.tile_mode).strip() or TILE_MODE_VECTOR,
    )


def write_plugin_settings(values: PluginSettings) -> None:
    settings = QSettings()
    settings.setValue(_setting_key("mapbox_token"), values.access_token.strip())
    settings.setValue(_setting_key("style_owner"), values.style_owner.strip() or "mapbox")
    settings.setValue(_setting_key("style_id"), values.style_id.strip() or "outdoors-v12")
    settings.setValue(
        _setting_key("tile_mode"),
        values.tile_mode if values.tile_mode in TILE_MODES else TILE_MODE_VECTOR,
    )


class PluginSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("QGIS Mapbox GL Style")

        self.token_input = QLineEdit(self)
        self.token_input.setEchoMode(QLineEdit.Password)
        self.token_input.setPlaceholderText("pk...")

        self.owner_input = QLineEdit(self)
        self.style_input = QLineEdit(self)

        self.tile_mode_input = QComboBox(self)
        self.tile_mode_input.addItems(TILE_MODES)

        form = QFormLayout()
        form.addRow("Mapbox access token", self.token_input)
        form.addRow("Style owner", self.owner_input)
        form.addRow("Style id", self.style_input)
        form.addRow("Tile mode", self.tile_mode_input)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def load_settings(self) -> None:
        values = read_plugin_settings()
        self.token_input.setText(values.access_token)
        self.owner_input.setText(values.style_owner)
        self.style_input.setText(values.style_id)
        index = self.tile_mode_input.findText(values.tile_mode)
        self.tile_mode_input.setCurrentIndex(index if index >= 0 else self.tile_mode_input.findText(TILE_MODE_VECTOR))

    def save_settings(self) -> None:
        write_plugin_settings(
            PluginSettings(
                access_token=self.token_input.text(),
                style_owner=self.owner_input.text(),
                style_id=self.style_input.text(),
                tile_mode=self.tile_mode_input.currentText(),
            )
        )
