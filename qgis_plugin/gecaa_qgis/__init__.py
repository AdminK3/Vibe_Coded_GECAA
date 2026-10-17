# SPDX-License-Identifier: GPL-2.0-or-later
# GECAA QGIS plugin. Copyright 2026 Khoch3 KlimaKarten UG. GNU GPL v2 or later (see LICENSE).


def classFactory(iface):  # noqa: N802 (QGIS API name)
    from .plugin import GecaaPlugin
    return GecaaPlugin(iface)
