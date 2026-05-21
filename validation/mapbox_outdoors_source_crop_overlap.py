from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import math
import os
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PARENT = REPO_ROOT.parent
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "debug" / "mapbox-outdoors-source-crop-overlap"
DEFAULT_CAMERA_NAME = "zermatt-trails-z18-outdoors"
DEFAULT_MAPBOX_STYLE_OWNER = "mapbox"
DEFAULT_MAPBOX_STYLE_ID = "outdoors-v12"
DEFAULT_TILE_EXTENT = 4096
MAX_WEB_MERCATOR_LATITUDE = 85.05112878
DEFAULT_SOURCE_LAYERS = (
    "landuse",
    "landuse_overlay",
    "contour",
    "aeroway",
    "airport_label",
)
PROPERTY_COUNT_KEYS = ("class", "type", "index", "structure", "maki")
MAX_COUNT_VALUES = 8
MISSING_VALUE = "(missing)"

TileFetcher = Callable[[str], bytes]
TileDecoder = Callable[[bytes, Mapping[str, int]], dict[str, object]]


@dataclass(frozen=True)
class SourceCropOverlapConfig:
    token: str | None
    visual_crop_json_path: Path
    output_root: Path = DEFAULT_OUTPUT_ROOT
    camera_name: str = DEFAULT_CAMERA_NAME
    style_owner: str = DEFAULT_MAPBOX_STYLE_OWNER
    style_id: str = DEFAULT_MAPBOX_STYLE_ID
    source_layers: tuple[str, ...] = DEFAULT_SOURCE_LAYERS
    tile_zoom: int | None = None
    now: dt.datetime | None = None


@dataclass(frozen=True)
class SourceCropOverlapPaths:
    run_dir: Path
    json_path: Path
    summary_path: Path


def _ensure_package_parent_on_path() -> None:
    package_parent = str(PACKAGE_PARENT)
    if package_parent not in sys.path:
        sys.path.insert(0, package_parent)


def resolve_mapbox_token(*, provided_token: str | None, environ: Mapping[str, str] | None = None) -> str | None:
    env = os.environ if environ is None else environ
    return provided_token or env.get("MAPBOX_ACCESS_TOKEN") or env.get("QFIT_MAPBOX_ACCESS_TOKEN")


def _utc_timestamp(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_run_directory(
    *,
    output_root: Path,
    camera_name: str,
    now: dt.datetime | None = None,
) -> Path:
    return output_root / camera_name / _utc_timestamp(now)


def build_source_crop_overlap_paths(run_dir: Path) -> SourceCropOverlapPaths:
    return SourceCropOverlapPaths(
        run_dir=run_dir,
        json_path=run_dir / "source-crop-overlap.json",
        summary_path=run_dir / "summary.md",
    )


def load_json_object(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def _path_from_repo_or_absolute(value: object) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else REPO_ROOT / path


def _repo_relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _trusted_artifact_roots(*paths: Path) -> tuple[Path, ...]:
    roots = {(REPO_ROOT / "debug").resolve()}
    roots.update(path.resolve().parent for path in paths)
    return tuple(roots)


def _resolve_trusted_artifact_path(
    value: object,
    *,
    base_dir: Path,
    trusted_roots: Sequence[Path],
    description: str,
) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        candidate = path
    else:
        repo_candidate = REPO_ROOT / path
        candidate = repo_candidate if repo_candidate.exists() else base_dir / path
    # Safe: the resolved candidate is rejected unless it stays under a trusted artifact root.
    resolved = candidate.resolve()  # NOSONAR
    if any(resolved.is_relative_to(root) for root in trusted_roots):
        return resolved
    root_list = ", ".join(str(root) for root in trusted_roots)
    raise ValueError(f"{description} must stay under one of these roots: {root_list}")


def recommended_tile_zoom(camera_zoom: float) -> int:
    return max(0, min(22, int(round(camera_zoom))))


def _clamped_latitude(latitude: float) -> float:
    return max(-MAX_WEB_MERCATOR_LATITUDE, min(MAX_WEB_MERCATOR_LATITUDE, latitude))


def lon_lat_to_world_pixel(longitude: float, latitude: float, zoom: float) -> tuple[float, float]:
    world_size = 512.0 * (2.0**zoom)
    latitude_radians = math.radians(_clamped_latitude(latitude))
    return (
        (longitude + 180.0) / 360.0 * world_size,
        (
            1.0
            - math.log(math.tan(latitude_radians) + 1.0 / math.cos(latitude_radians)) / math.pi
        )
        / 2.0
        * world_size,
    )


def world_pixel_to_lon_lat(x: float, y: float, zoom: float) -> tuple[float, float]:
    world_size = 512.0 * (2.0**zoom)
    longitude = x / world_size * 360.0 - 180.0
    latitude = math.degrees(math.atan(math.sinh(math.pi - 2.0 * math.pi * y / world_size)))
    return longitude, latitude


def crop_box_lon_lat_bounds(
    *,
    camera: Mapping[str, object],
    box: Sequence[int],
) -> dict[str, float]:
    if len(box) != 4:
        raise ValueError(f"Expected crop box with four coordinates, got {box!r}")
    zoom = float(camera["zoom"])
    center_x, center_y = lon_lat_to_world_pixel(float(camera["longitude"]), float(camera["latitude"]), zoom)
    width = float(camera["width"])
    height = float(camera["height"])
    left, top, right, bottom = (float(coordinate) for coordinate in box)
    west, north = world_pixel_to_lon_lat(center_x + left - width / 2.0, center_y + top - height / 2.0, zoom)
    east, south = world_pixel_to_lon_lat(center_x + right - width / 2.0, center_y + bottom - height / 2.0, zoom)
    return {
        "west": min(west, east),
        "south": min(south, north),
        "east": max(west, east),
        "north": max(south, north),
    }


def lon_lat_to_tile(longitude: float, latitude: float, zoom: int) -> tuple[int, int]:
    tile_count = 2**zoom
    latitude_radians = math.radians(_clamped_latitude(latitude))
    x = int((longitude + 180.0) / 360.0 * tile_count)
    y = int(
        (
            1.0
            - math.log(math.tan(latitude_radians) + 1.0 / math.cos(latitude_radians)) / math.pi
        )
        / 2.0
        * tile_count
    )
    return max(0, min(tile_count - 1, x)), max(0, min(tile_count - 1, y))


def tiles_for_lon_lat_bounds(bounds: Mapping[str, float], zoom: int) -> list[dict[str, int]]:
    x_min, y_bottom = lon_lat_to_tile(bounds["west"], bounds["south"], zoom)
    x_max, y_top = lon_lat_to_tile(bounds["east"], bounds["north"], zoom)
    tiles: list[dict[str, int]] = []
    for x in range(min(x_min, x_max), max(x_min, x_max) + 1):
        for y in range(min(y_top, y_bottom), max(y_top, y_bottom) + 1):
            tiles.append({"z": zoom, "x": x, "y": y})
    return tiles


def _decompressed_tile_bytes(tile_bytes: bytes) -> bytes:
    return gzip.decompress(tile_bytes) if tile_bytes.startswith(b"\x1f\x8b") else tile_bytes


def _tile_coordinate_transformer(tile: Mapping[str, int], *, extent: int = DEFAULT_TILE_EXTENT):
    z = int(tile["z"])
    x_tile = int(tile["x"])
    y_tile = int(tile["y"])
    tile_count = 2**z

    def transform(x: float, y: float) -> tuple[float, float]:
        longitude = (x_tile + x / extent) / tile_count * 360.0 - 180.0
        latitude = math.degrees(
            math.atan(math.sinh(math.pi * (1.0 - 2.0 * (y_tile + y / extent) / tile_count)))
        )
        return longitude, latitude

    return transform


def _default_tile_decoder(tile_bytes: bytes, tile: Mapping[str, int]) -> dict[str, object]:
    try:
        from mapbox_vector_tile import decode  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - optional local diagnostic dependency
        raise RuntimeError(
            "Source/crop overlap diagnostics require the optional mapbox_vector_tile package."
        ) from exc
    decoded = decode(
        _decompressed_tile_bytes(tile_bytes),
        default_options={"y_coord_down": True, "transformer": _tile_coordinate_transformer(tile)},
    )
    return decoded if isinstance(decoded, dict) else {}


def _fetch_url_bytes(url: str) -> bytes:
    with urlopen(url, timeout=20) as response:  # noqa: S310
        return response.read()


def _decoded_layer_features(decoded_tile: Mapping[str, object], source_layer: str) -> list[dict[str, object]]:
    layer = decoded_tile.get(source_layer)
    features = layer.get("features") if isinstance(layer, dict) else layer
    if not isinstance(features, list):
        return []
    return [feature for feature in features if isinstance(feature, dict)]


def _feature_properties(feature: Mapping[str, object]) -> dict[str, object]:
    properties = feature.get("properties")
    return properties if isinstance(properties, dict) else {}


def _coordinate_pairs(value: object) -> Iterable[tuple[float, float]]:
    if not isinstance(value, (list, tuple)):
        return
    if (
        len(value) >= 2
        and isinstance(value[0], (int, float))
        and isinstance(value[1], (int, float))
        and not isinstance(value[0], bool)
        and not isinstance(value[1], bool)
    ):
        yield float(value[0]), float(value[1])
        return
    for item in value:
        yield from _coordinate_pairs(item)


def feature_lon_lat_bbox(feature: Mapping[str, object]) -> tuple[float, float, float, float] | None:
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict):
        return None
    points = list(_coordinate_pairs(geometry.get("coordinates")))
    if not points:
        return None
    longitudes = [point[0] for point in points]
    latitudes = [point[1] for point in points]
    return min(longitudes), min(latitudes), max(longitudes), max(latitudes)


def bbox_overlaps_lon_lat_bounds(
    bbox: tuple[float, float, float, float] | None,
    bounds: Mapping[str, float],
) -> bool:
    if bbox is None:
        return False
    west, south, east, north = bbox
    return east >= bounds["west"] and west <= bounds["east"] and north >= bounds["south"] and south <= bounds["north"]


def _clean_count_value(value: object) -> str:
    if value is None:
        return MISSING_VALUE
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f"{value:.3f}"
    if isinstance(value, (int, str)):
        return str(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"))[:80]


def _property_counts(features: Sequence[Mapping[str, object]], key: str) -> dict[str, int]:
    counts = Counter(_clean_count_value(_feature_properties(feature).get(key)) for feature in features)
    counts.pop(MISSING_VALUE, None)
    return dict(counts.most_common(MAX_COUNT_VALUES))


def _property_count_summary(features: Sequence[Mapping[str, object]]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for key in PROPERTY_COUNT_KEYS:
        counts = _property_counts(features, key)
        if counts:
            summary[key] = counts
    return summary


def _numeric_property_range(features: Sequence[Mapping[str, object]], key: str) -> dict[str, float] | None:
    values = [
        float(value)
        for feature in features
        for value in [_feature_properties(feature).get(key)]
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if not values:
        return None
    return {"min": min(values), "max": max(values)}


def _sample_feature_properties(features: Sequence[Mapping[str, object]], *, limit: int = 5) -> list[dict[str, object]]:
    samples: list[dict[str, object]] = []
    for feature in features[:limit]:
        properties = _feature_properties(feature)
        samples.append({key: properties[key] for key in PROPERTY_COUNT_KEYS if key in properties})
    return samples


def source_layer_overlap_record(
    *,
    decoded_tiles: Sequence[Mapping[str, object]],
    bounds: Mapping[str, float],
    source_layer: str,
) -> dict[str, object]:
    tile_features = [
        feature
        for decoded_tile in decoded_tiles
        for feature in _decoded_layer_features(decoded_tile, source_layer)
    ]
    overlapping_features = [
        feature for feature in tile_features if bbox_overlaps_lon_lat_bounds(feature_lon_lat_bbox(feature), bounds)
    ]
    return {
        "source_layer": source_layer,
        "tile_feature_count": len(tile_features),
        "overlap_feature_count": len(overlapping_features),
        "property_counts": _property_count_summary(overlapping_features),
        "ele_range": _numeric_property_range(overlapping_features, "ele"),
        "sample_properties": _sample_feature_properties(overlapping_features),
    }


def _comparison_summary_path_from_report(
    report: Mapping[str, object],
    *,
    visual_crop_path: Path,
    trusted_roots: Sequence[Path],
) -> Path:
    path = report.get("comparison_summary_json")
    if path is None:
        raise ValueError("Visual crop report does not include comparison_summary_json.")
    return _resolve_trusted_artifact_path(
        path,
        base_dir=visual_crop_path.parent,
        trusted_roots=trusted_roots,
        description="Comparison summary path",
    )


def _camera_manifest_path(
    *,
    comparison_summary: Mapping[str, object],
    camera_name: str,
    comparison_summary_path: Path,
    trusted_roots: Sequence[Path],
) -> Path:
    cameras = comparison_summary.get("cameras")
    if not isinstance(cameras, list):
        raise ValueError("Comparison summary does not include camera rows.")
    for row in cameras:
        if isinstance(row, dict) and row.get("camera") == camera_name and row.get("manifest"):
            return _resolve_trusted_artifact_path(
                row["manifest"],
                base_dir=comparison_summary_path.parent,
                trusted_roots=trusted_roots,
                description=f"Manifest path for camera {camera_name!r}",
            )
    raise ValueError(f"Comparison summary does not include a manifest for camera {camera_name!r}.")


def _crop_boxes_from_report(report: Mapping[str, object], camera_name: str) -> list[list[int]]:
    manual_crop_boxes = report.get("manual_crop_boxes")
    if isinstance(manual_crop_boxes, dict):
        boxes = manual_crop_boxes.get(camera_name)
        if isinstance(boxes, list) and boxes:
            return [[int(coordinate) for coordinate in box] for box in boxes if isinstance(box, list)]
    cameras = report.get("cameras")
    if isinstance(cameras, list):
        for row in cameras:
            if not isinstance(row, dict) or row.get("camera") != camera_name:
                continue
            crops = row.get("crops")
            if isinstance(crops, list):
                return [
                    [int(coordinate) for coordinate in crop["box"]]
                    for crop in crops
                    if isinstance(crop, dict) and isinstance(crop.get("box"), list)
                ]
    return []


def _validated_camera(manifest: Mapping[str, object]) -> dict[str, object]:
    camera = manifest.get("camera")
    if not isinstance(camera, dict):
        raise ValueError("Comparison manifest does not include camera metadata.")
    bearing = float(camera.get("bearing") or 0.0)
    pitch = float(camera.get("pitch") or 0.0)
    if not math.isclose(bearing, 0.0) or not math.isclose(pitch, 0.0):
        raise ValueError("Source/crop overlap diagnostics currently require bearing=0 and pitch=0.")
    return camera


def _style_path_from_manifest(
    manifest: Mapping[str, object],
    *,
    manifest_path: Path,
    trusted_roots: Sequence[Path],
) -> Path:
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict) or not outputs.get("qgis_preprocessed_style"):
        raise ValueError("Comparison manifest does not include qgis_preprocessed_style output.")
    return _resolve_trusted_artifact_path(
        outputs["qgis_preprocessed_style"],
        base_dir=manifest_path.parent,
        trusted_roots=trusted_roots,
        description="QGIS-preprocessed style path",
    )


def _format_tile_url(tile_url_template: str, tile: Mapping[str, int]) -> str:
    return tile_url_template.format(z=tile["z"], x=tile["x"], y=tile["y"])


def _crop_source_layer_records(crops: Sequence[Mapping[str, object]], source_layer: str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for crop in crops:
        source_layer_records = crop.get("source_layers", [])
        if not isinstance(source_layer_records, list):
            continue
        records.extend(
            record
            for record in source_layer_records
            if isinstance(record, dict) and record.get("source_layer") == source_layer
        )
    return records


def _combined_property_counts(records: Sequence[Mapping[str, object]]) -> dict[str, dict[str, int]]:
    property_counts: dict[str, Counter[str]] = {}
    for record in records:
        counts = record.get("property_counts")
        if not isinstance(counts, dict):
            continue
        for key, value_counts in counts.items():
            if isinstance(value_counts, dict):
                property_counts.setdefault(str(key), Counter()).update(
                    {str(value): int(count) for value, count in value_counts.items()}
                )
    return {
        key: dict(counter.most_common(MAX_COUNT_VALUES))
        for key, counter in property_counts.items()
        if counter
    }


def _combined_ele_range(records: Sequence[Mapping[str, object]]) -> dict[str, float] | None:
    values: list[float] = []
    for record in records:
        ele_range = record.get("ele_range")
        if not isinstance(ele_range, dict):
            continue
        values.extend(
            float(value)
            for value in (ele_range.get("min"), ele_range.get("max"))
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        )
    return {"min": min(values), "max": max(values)} if values else None


def _combined_source_layer_record(source_layer: str, records: Sequence[Mapping[str, object]]) -> dict[str, object]:
    return {
        "source_layer": source_layer,
        "tile_feature_count": sum(int(record.get("tile_feature_count") or 0) for record in records),
        "overlap_feature_count": sum(int(record.get("overlap_feature_count") or 0) for record in records),
        "property_counts": _combined_property_counts(records),
        "ele_range": _combined_ele_range(records),
    }


def _combined_source_layer_records(
    crops: Sequence[Mapping[str, object]],
    source_layers: Sequence[str],
) -> list[dict[str, object]]:
    return [
        _combined_source_layer_record(source_layer, _crop_source_layer_records(crops, source_layer))
        for source_layer in source_layers
    ]


def collect_source_crop_overlap_report(
    config: SourceCropOverlapConfig,
    *,
    tile_fetcher: TileFetcher | None = None,
    tile_decoder: TileDecoder | None = None,
) -> dict[str, object]:
    _ensure_package_parent_on_path()
    from qfit.mapbox_config import build_mapbox_vector_tiles_url, extract_mapbox_vector_source_ids

    token = resolve_mapbox_token(provided_token=config.token)
    if not token:
        raise ValueError("A Mapbox token is required to fetch vector tiles.")
    visual_crop_path = _path_from_repo_or_absolute(config.visual_crop_json_path)
    visual_crop_report = load_json_object(visual_crop_path)
    crop_boxes = _crop_boxes_from_report(visual_crop_report, config.camera_name)
    if not crop_boxes:
        raise ValueError(f"Visual crop report has no crop boxes for camera {config.camera_name!r}.")
    trusted_roots = _trusted_artifact_roots(visual_crop_path)
    comparison_summary_path = _comparison_summary_path_from_report(
        visual_crop_report,
        visual_crop_path=visual_crop_path,
        trusted_roots=trusted_roots,
    )
    comparison_summary = load_json_object(comparison_summary_path)
    trusted_roots = _trusted_artifact_roots(visual_crop_path, comparison_summary_path)
    manifest_path = _camera_manifest_path(
        comparison_summary=comparison_summary,
        camera_name=config.camera_name,
        comparison_summary_path=comparison_summary_path,
        trusted_roots=trusted_roots,
    )
    manifest = load_json_object(manifest_path)
    camera = _validated_camera(manifest)
    trusted_roots = _trusted_artifact_roots(visual_crop_path, comparison_summary_path, manifest_path)
    style_path = _style_path_from_manifest(
        manifest,
        manifest_path=manifest_path,
        trusted_roots=trusted_roots,
    )
    style_definition = load_json_object(style_path)
    tileset_ids = extract_mapbox_vector_source_ids(style_definition)
    tile_url_template = build_mapbox_vector_tiles_url(
        token,
        config.style_owner,
        config.style_id,
        tileset_ids=tileset_ids,
    )
    tile_zoom = config.tile_zoom if config.tile_zoom is not None else recommended_tile_zoom(float(camera["zoom"]))
    fetch_tile = tile_fetcher or _fetch_url_bytes
    decode_tile = tile_decoder or _default_tile_decoder
    decoded_tile_cache: dict[tuple[int, int, int], dict[str, object]] = {}
    crops: list[dict[str, object]] = []
    for index, box in enumerate(crop_boxes, start=1):
        bounds = crop_box_lon_lat_bounds(camera=camera, box=box)
        tiles = tiles_for_lon_lat_bounds(bounds, tile_zoom)
        decoded_tiles: list[Mapping[str, object]] = []
        for tile in tiles:
            cache_key = (tile["z"], tile["x"], tile["y"])
            if cache_key not in decoded_tile_cache:
                decoded_tile_cache[cache_key] = decode_tile(fetch_tile(_format_tile_url(tile_url_template, tile)), tile)
            decoded_tiles.append(decoded_tile_cache[cache_key])
        crops.append(
            {
                "index": index,
                "box": box,
                "lon_lat_bounds": bounds,
                "tiles": tiles,
                "source_layers": [
                    source_layer_overlap_record(
                        decoded_tiles=decoded_tiles,
                        bounds=bounds,
                        source_layer=source_layer,
                    )
                    for source_layer in config.source_layers
                ],
            }
        )
    generated = config.now or dt.datetime.now(dt.timezone.utc)
    return {
        "generated": generated.astimezone(dt.timezone.utc).isoformat(),
        "camera": config.camera_name,
        "camera_zoom": float(camera["zoom"]),
        "tile_zoom": tile_zoom,
        "decoded_tile_count": len(decoded_tile_cache),
        "tileset_ids": tileset_ids,
        "visual_crop_json": _repo_relative_path(visual_crop_path),
        "comparison_summary_json": _repo_relative_path(comparison_summary_path),
        "manifest": _repo_relative_path(manifest_path),
        "qgis_preprocessed_style": _repo_relative_path(style_path),
        "source_layers": list(config.source_layers),
        "crops": crops,
        "combined_source_layers": _combined_source_layer_records(crops, config.source_layers),
    }


def _format_counts(counts: object) -> str:
    if not isinstance(counts, dict) or not counts:
        return "-"
    return ", ".join(f"{key}={value}" for key, value in counts.items())


def _format_property_counts(record: Mapping[str, object], key: str) -> str:
    property_counts = record.get("property_counts")
    return _format_counts(property_counts.get(key) if isinstance(property_counts, dict) else None)


def _format_ele_range(value: object) -> str:
    if not isinstance(value, dict):
        return "-"
    minimum = value.get("min")
    maximum = value.get("max")
    if not isinstance(minimum, (int, float)) or not isinstance(maximum, (int, float)):
        return "-"
    return f"{minimum:g}-{maximum:g}"


def _format_bounds(bounds: Mapping[str, object]) -> str:
    return (
        f"{float(bounds['west']):.6f},{float(bounds['south']):.6f} "
        f"to {float(bounds['east']):.6f},{float(bounds['north']):.6f}"
    )


def build_summary_markdown(report: Mapping[str, object]) -> str:
    lines = [
        "# Mapbox Outdoors source/crop overlap",
        "",
        f"Generated: {report['generated']}",
        (
            f"Camera: `{report['camera']}` at z{float(report['camera_zoom']):g}; "
            f"tile z{report['tile_zoom']}; decoded tiles: {report['decoded_tile_count']}"
        ),
        f"Visual crop report: `{report['visual_crop_json']}`",
        f"QGIS-preprocessed style: `{report['qgis_preprocessed_style']}`",
        "",
        (
            "This diagnostic converts visual crop boxes to lon/lat bounds, fetches only the overlapping "
            "Mapbox vector tiles, and counts decoded source features whose transformed geometry bbox "
            "overlaps each crop. Token-bearing URLs are intentionally omitted."
        ),
        "",
        "## Combined overlap by source layer",
        "",
        "| Source layer | Tile features | Overlap features | Classes | Types | Index values | Elevation range |",
        "| --- | ---: | ---: | --- | --- | --- | --- |",
    ]
    for record in report.get("combined_source_layers", []):
        if not isinstance(record, dict):
            continue
        lines.append(
            "| `{source_layer}` | {tile_features} | {overlap_features} | {classes} | {types} | {indices} | {ele} |".format(
                source_layer=record["source_layer"],
                tile_features=record["tile_feature_count"],
                overlap_features=record["overlap_feature_count"],
                classes=_format_property_counts(record, "class"),
                types=_format_property_counts(record, "type"),
                indices=_format_property_counts(record, "index"),
                ele=_format_ele_range(record.get("ele_range")),
            )
        )
    lines.extend(
        [
            "",
            "## Per-crop overlap",
            "",
            "| Crop | Box | Lon/lat bounds | Tiles | Source layer | Tile features | Overlap features | Classes | Types | Index values | Elevation range |",
            "| ---: | --- | --- | ---: | --- | ---: | ---: | --- | --- | --- | --- |",
        ]
    )
    for crop in report.get("crops", []):
        if not isinstance(crop, dict):
            continue
        for source_layer_record in crop.get("source_layers", []):
            if not isinstance(source_layer_record, dict):
                continue
            lines.append(
                "| {crop} | {box} | {bounds} | {tile_count} | `{source_layer}` | {tile_features} | {overlap_features} | {classes} | {types} | {indices} | {ele} |".format(
                    crop=crop["index"],
                    box=crop["box"],
                    bounds=_format_bounds(crop["lon_lat_bounds"]),
                    tile_count=len(crop.get("tiles", [])),
                    source_layer=source_layer_record["source_layer"],
                    tile_features=source_layer_record["tile_feature_count"],
                    overlap_features=source_layer_record["overlap_feature_count"],
                    classes=_format_property_counts(source_layer_record, "class"),
                    types=_format_property_counts(source_layer_record, "type"),
                    indices=_format_property_counts(source_layer_record, "index"),
                    ele=_format_ele_range(source_layer_record.get("ele_range")),
                )
            )
    return "\n".join(lines) + "\n"


def write_report(report: Mapping[str, object], paths: SourceCropOverlapPaths) -> None:
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    paths.json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    paths.summary_path.write_text(build_summary_markdown(report), encoding="utf-8")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Count live Mapbox vector source features overlapping Mapbox Outdoors visual crop boxes.",
    )
    parser.add_argument("--visual-crop-json", required=True, type=Path)
    parser.add_argument("--camera", default=DEFAULT_CAMERA_NAME)
    parser.add_argument("--mapbox-token")
    parser.add_argument("--style-owner", default=DEFAULT_MAPBOX_STYLE_OWNER)
    parser.add_argument("--style-id", default=DEFAULT_MAPBOX_STYLE_ID)
    parser.add_argument("--tile-zoom", type=int)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--source-layer",
        action="append",
        dest="source_layers",
        help="Mapbox vector source-layer to count. Repeat to inspect multiple layers.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    source_layers = tuple(args.source_layers) if args.source_layers else DEFAULT_SOURCE_LAYERS
    config = SourceCropOverlapConfig(
        token=args.mapbox_token,
        visual_crop_json_path=args.visual_crop_json,
        output_root=args.output_root,
        camera_name=args.camera,
        style_owner=args.style_owner,
        style_id=args.style_id,
        source_layers=source_layers,
        tile_zoom=args.tile_zoom,
    )
    report = collect_source_crop_overlap_report(config)
    paths = build_source_crop_overlap_paths(
        build_run_directory(output_root=args.output_root, camera_name=args.camera, now=config.now)
    )
    write_report(report, paths)
    print(f"Wrote {paths.summary_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
