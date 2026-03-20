def classFactory(iface):
    """Load QFIT plugin class."""
    from .qfit_plugin import QfitPlugin
    return QfitPlugin(iface)
