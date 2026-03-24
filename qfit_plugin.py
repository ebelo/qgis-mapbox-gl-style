import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QAction

from .qfit_dockwidget import QfitDockWidget


class QfitPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dockwidget = None

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        self.action = QAction(QIcon(icon_path), "qfit", self.iface.mainWindow())
        self.action.triggered.connect(self.show_dock)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&qfit", self.action)

    def unload(self):
        if self.action is not None:
            self.iface.removePluginMenu("&qfit", self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None

        if self.dockwidget is not None:
            self.iface.removeDockWidget(self.dockwidget)
            self.dockwidget.deleteLater()
            self.dockwidget = None

    def show_dock(self):
        if self.dockwidget is None:
            self.dockwidget = QfitDockWidget(self.iface)
            self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dockwidget)
        self.dockwidget.show()
        self.dockwidget.raise_()
