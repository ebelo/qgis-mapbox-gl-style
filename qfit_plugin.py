import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QAction

from .qfit_config_dialog import QfitConfigDialog
from .qfit_dockwidget import QfitDockWidget

MENU_NAME = "&qfit"


class QfitPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.activities_action = None
        self.config_action = None
        self.dockwidget = None
        self._config_dialog = None

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        icon = QIcon(icon_path)

        self.activities_action = QAction(icon, "Activities", self.iface.mainWindow())
        self.activities_action.triggered.connect(self.show_dock)
        self.iface.addToolBarIcon(self.activities_action)
        self.iface.addPluginToMenu(MENU_NAME, self.activities_action)

        self.config_action = QAction(icon, "Configuration", self.iface.mainWindow())
        self.config_action.triggered.connect(self.show_config)
        self.iface.addPluginToMenu(MENU_NAME, self.config_action)

    def unload(self):
        if self.activities_action is not None:
            self.iface.removePluginMenu(MENU_NAME, self.activities_action)
            self.iface.removeToolBarIcon(self.activities_action)
            self.activities_action = None

        if self.config_action is not None:
            self.iface.removePluginMenu(MENU_NAME, self.config_action)
            self.config_action = None

        if self.dockwidget is not None:
            self.iface.removeDockWidget(self.dockwidget)
            self.dockwidget.deleteLater()
            self.dockwidget = None

        if self._config_dialog is not None:
            self._config_dialog.close()
            self._config_dialog.deleteLater()
            self._config_dialog = None

    def show_dock(self):
        if self.dockwidget is None:
            self.dockwidget = QfitDockWidget(self.iface, parent=self.iface.mainWindow())
            self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dockwidget)
        self.dockwidget.show()
        self.dockwidget.raise_()

    def show_config(self):
        if self._config_dialog is None:
            self._config_dialog = QfitConfigDialog(parent=self.iface.mainWindow())
        self._config_dialog.show()
        self._config_dialog.raise_()
        self._config_dialog.activateWindow()
