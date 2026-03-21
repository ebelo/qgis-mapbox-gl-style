def classFactory(iface):
    """Load qfit plugin class."""
    from .qfit_plugin import QfitPlugin
    return QfitPlugin(iface)
