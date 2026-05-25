from __future__ import annotations


def qgis_runtime_snapshot(qgis_api: object) -> dict[str, object]:
    return {
        "qgis_version": getattr(qgis_api, "QGIS_VERSION", None),
        "qgis_version_int": getattr(qgis_api, "QGIS_VERSION_INT", None),
        "qgis_release_name": getattr(qgis_api, "QGIS_RELEASE_NAME", None),
    }


def format_qgis_runtime_label(value: object, *, missing_label: str) -> str:
    if not isinstance(value, dict):
        return missing_label
    qgis_version = value.get("qgis_version")
    if qgis_version:
        return str(qgis_version)
    qgis_version_int = value.get("qgis_version_int")
    if qgis_version_int is not None:
        return str(qgis_version_int)
    qgis_release_name = value.get("qgis_release_name")
    if qgis_release_name:
        return str(qgis_release_name)
    return missing_label
