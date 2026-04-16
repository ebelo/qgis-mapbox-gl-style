import atexit
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SHARED_QGIS_APP = None


def get_shared_qgis_app(QgsApplication):
    global _SHARED_QGIS_APP
    if _SHARED_QGIS_APP is None:
        QgsApplication.setPrefixPath("/usr", True)
        _SHARED_QGIS_APP = QgsApplication([], False)
        _SHARED_QGIS_APP.initQgis()
    return _SHARED_QGIS_APP


def _cleanup_shared_qgis_app():
    global _SHARED_QGIS_APP
    if _SHARED_QGIS_APP is None:
        return

    try:
        from qgis.core import QgsProject

        QgsProject.instance().clear()
    except Exception:
        pass

    try:
        _SHARED_QGIS_APP.exitQgis()
    except Exception:
        pass

    _SHARED_QGIS_APP = None


atexit.register(_cleanup_shared_qgis_app)
