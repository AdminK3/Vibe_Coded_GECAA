# SPDX-License-Identifier: GPL-2.0-or-later
# GECAA QGIS plugin. Copyright 2026 Khoch3 KlimaKarten UG. GNU GPL v2 or later (see LICENSE).
"""Plugin entry: a toolbar action that toggles the GECAA dock widget."""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QAction

from .dock import GecaaDock


class GecaaPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dock = None
        self.action = None

    def initGui(self):  # noqa: N802
        self.action = QAction("GECAA Starkregen", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.triggered.connect(self.toggle)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&GECAA", self.action)

    def toggle(self, checked):
        if self.dock is None:
            self.dock = GecaaDock(self.iface)
            self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)
            self.dock.visibilityChanged.connect(self.action.setChecked)
        self.dock.setVisible(checked)

    def unload(self):
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        self.iface.removePluginMenu("&GECAA", self.action)
        self.iface.removeToolBarIcon(self.action)
