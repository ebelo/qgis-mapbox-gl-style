def classFactory(iface):
    """Load the QGIS Mapbox GL Style plugin class."""
    from .plugin import QgisMapboxGlStylePlugin

    return QgisMapboxGlStylePlugin(iface)
