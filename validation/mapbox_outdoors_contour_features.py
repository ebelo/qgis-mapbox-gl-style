from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PARENT = REPO_ROOT.parent
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "debug" / "mapbox-outdoors-contour-features"
DEFAULT_MAPBOX_STYLE_OWNER = "mapbox"
DEFAULT_MAPBOX_STYLE_ID = "outdoors-v12"
DEFAULT_CAMERA_NAME = "chamonix-trails-z14-outdoors"
WEB_MERCATOR_HALF_WORLD = 20037508.342789244
MAX_WEB_MERCATOR_LATITUDE = 85.05112878
CONTOUR_SOURCE_LAYER = "contour"
CONTOUR_LABEL_INDICES = (5, 10)
SAMPLE_PROPERTY_KEYS = ("ele", "index", "class", "worldview", "level", "sizerank", "name")
MAX_SAMPLE_CANDIDATES = 12
MISSING_VALUE = "(missing)"
LINE_COMPATIBLE_GEOMETRY_TYPES = {"LineString", "MultiLineString"}
POLYGON_GEOMETRY_TYPES = {"Polygon", "MultiPolygon"}
LABEL_GEOMETRY_NO_CANDIDATES = "no_candidates"
LABEL_GEOMETRY_LINE_COMPATIBLE = "line_compatible"
LABEL_GEOMETRY_MIXED_WITH_LINES = "mixed_with_line_compatible"
LABEL_GEOMETRY_POLYGON_ONLY = "polygon_only"
LABEL_GEOMETRY_NO_LINE_COMPATIBLE = "no_line_compatible"
POLYGON_SHAPE_RECTANGULAR = "rectangular"
POLYGON_SHAPE_NON_RECTANGULAR = "non_rectangular"
POLYGON_SHAPE_UNSUPPORTED = "unsupported"
POLYGON_SHAPE_NO_POLYGON_CANDIDATES = "no_polygon_candidates"
POLYGON_SHAPE_RECTANGULAR_ONLY = "rectangular_only"
POLYGON_SHAPE_MIXED_RECTANGULAR = "mixed_rectangular"
POLYGON_SHAPE_NON_RECTANGULAR_ONLY = "non_rectangular_only"
POLYGON_SHAPE_UNSUPPORTED_ONLY = "unsupported_only"
POLYGON_SHAPE_MIXED_POLYGON_SHAPES = "mixed_polygon_shapes"
CANDIDATE_BOUNDARY_SEGMENT_STAT_KEYS = (
    "feature_count",
    "ring_count",
    "point_count",
    "segment_count",
    "axis_aligned_segment_count",
    "diagonal_segment_count",
    "bbox_edge_segment_count",
)

TileFetcher = Callable[[str], bytes]
TileDecoder = Callable[[bytes], dict[str, object]]


@dataclass(frozen=True)
class ContourFeaturePaths:
    run_dir: Path
    json_path: Path
    summary_path: Path


@dataclass(frozen=True)
class AllCameraContourFeaturePaths:
    run_dir: Path
    json_path: Path
    summary_path: Path


@dataclass(frozen=True)
class ContourFeatureConfig:
    token: str | None
    output_root: Path
    camera_name: str = DEFAULT_CAMERA_NAME
    style_owner: str = DEFAULT_MAPBOX_STYLE_OWNER
    style_id: str = DEFAULT_MAPBOX_STYLE_ID
    style_json_path: Path | None = None
    tile_zoom: int | None = None
    now: dt.datetime | None = None


def _ensure_package_parent_on_path() -> None:
    package_parent = str(PACKAGE_PARENT)
    if package_parent not in sys.path:
        sys.path.insert(0, package_parent)


def resolve_mapbox_token(*, provided_token: str | None, environ: dict[str, str] | None = None) -> str | None:
    env = os.environ if environ is None else environ
    return provided_token or env.get("MAPBOX_ACCESS_TOKEN") or env.get("QFIT_MAPBOX_ACCESS_TOKEN")


def load_style_definition(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected Mapbox style JSON object in {path}")
    return data


def _utc_timestamp(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_run_directory(
    *,
    output_root: Path,
    camera_name: str,
    now: dt.datetime | None = None,
) -> Path:
    return output_root / camera_name / _utc_timestamp(now)


def build_contour_feature_paths(run_dir: Path) -> ContourFeaturePaths:
    return ContourFeaturePaths(
        run_dir=run_dir,
        json_path=run_dir / "contour-features.json",
        summary_path=run_dir / "summary.md",
    )


def build_all_camera_run_directory(
    *,
    output_root: Path,
    now: dt.datetime | None = None,
) -> Path:
    return output_root / "all-cameras" / _utc_timestamp(now)


def build_all_camera_contour_feature_paths(run_dir: Path) -> AllCameraContourFeaturePaths:
    return AllCameraContourFeaturePaths(
        run_dir=run_dir,
        json_path=run_dir / "summary.json",
        summary_path=run_dir / "summary.md",
    )


def recommended_tile_zoom(camera_zoom: float) -> int:
    return max(0, min(22, int(round(camera_zoom))))


def web_mercator_to_lon_lat(x: float, y: float) -> tuple[float, float]:
    longitude = x / WEB_MERCATOR_HALF_WORLD * 180.0
    latitude = y / WEB_MERCATOR_HALF_WORLD * 180.0
    latitude = 180.0 / math.pi * (2.0 * math.atan(math.exp(latitude * math.pi / 180.0)) - math.pi / 2.0)
    return longitude, latitude


def lon_lat_to_tile(longitude: float, latitude: float, zoom: int) -> tuple[int, int]:
    clamped_latitude = max(-MAX_WEB_MERCATOR_LATITUDE, min(MAX_WEB_MERCATOR_LATITUDE, latitude))
    tile_count = 2**zoom
    x_float = (longitude + 180.0) / 360.0 * tile_count
    latitude_radians = math.radians(clamped_latitude)
    y_float = (
        1.0 - math.log(math.tan(latitude_radians) + 1.0 / math.cos(latitude_radians)) / math.pi
    ) / 2.0 * tile_count
    x = max(0, min(tile_count - 1, int(x_float)))
    y = max(0, min(tile_count - 1, int(y_float)))
    return x, y


def tile_bounds_for_web_mercator_extent(
    extent: tuple[float, float, float, float],
    zoom: int,
) -> dict[str, int]:
    min_x, min_y, max_x, max_y = extent
    west, north = web_mercator_to_lon_lat(min_x, max_y)
    east, south = web_mercator_to_lon_lat(max_x, min_y)
    tile_x_min, tile_y_top = lon_lat_to_tile(west, north, zoom)
    tile_x_max, tile_y_bottom = lon_lat_to_tile(east, south, zoom)
    return {
        "min_x": min(tile_x_min, tile_x_max),
        "max_x": max(tile_x_min, tile_x_max),
        "min_y": min(tile_y_top, tile_y_bottom),
        "max_y": max(tile_y_top, tile_y_bottom),
    }


def iter_tile_coordinates(tile_bounds: dict[str, int], zoom: int) -> Iterable[dict[str, int]]:
    for x in range(tile_bounds["min_x"], tile_bounds["max_x"] + 1):
        for y in range(tile_bounds["min_y"], tile_bounds["max_y"] + 1):
            yield {"z": zoom, "x": x, "y": y}


def _fetch_url_bytes(url: str) -> bytes:
    with urlopen(url, timeout=20) as response:  # noqa: S310
        return response.read()


def _default_tile_decoder(tile_bytes: bytes) -> dict[str, object]:
    try:
        from mapbox_vector_tile import decode  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - optional local diagnostic dependency
        raise RuntimeError(
            "Contour feature diagnostics require the optional mapbox_vector_tile package."
        ) from exc
    decoded = decode(tile_bytes)
    return decoded if isinstance(decoded, dict) else {}


def _tile_error_types() -> tuple[type[BaseException], ...]:
    errors: tuple[type[BaseException], ...] = (
        EOFError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        gzip.BadGzipFile,
    )
    try:  # pragma: no cover - depends on optional protobuf runtime details
        from google.protobuf.message import DecodeError  # type: ignore[import-not-found]
    except ImportError:
        return errors
    return (*errors, DecodeError)


_TILE_ERROR_TYPES = _tile_error_types()


def _decompressed_tile_bytes(tile_bytes: bytes) -> bytes:
    return gzip.decompress(tile_bytes) if tile_bytes.startswith(b"\x1f\x8b") else tile_bytes


def decode_vector_tile_bytes(tile_bytes: bytes, tile_decoder: TileDecoder) -> dict[str, object]:
    return tile_decoder(_decompressed_tile_bytes(tile_bytes))


def _decoded_layer_features(decoded_tile: dict[str, object], source_layer: str) -> list[dict[str, object]]:
    layer = decoded_tile.get(source_layer)
    if isinstance(layer, dict):
        features = layer.get("features")
    else:
        features = layer
    if not isinstance(features, list):
        return []
    return [feature for feature in features if isinstance(feature, dict)]


def _feature_properties(feature: dict[str, object]) -> dict[str, object]:
    properties = feature.get("properties")
    return properties if isinstance(properties, dict) else {}


def _geometry_type(feature: dict[str, object]) -> str:
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict):
        return MISSING_VALUE
    geometry_type = geometry.get("type")
    return geometry_type if isinstance(geometry_type, str) and geometry_type else MISSING_VALUE


def _is_coordinate_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_coordinate_pair(value: object) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and _is_coordinate_number(value[0])
        and _is_coordinate_number(value[1])
    )


def _coordinate_bounds(coordinate: list[object] | tuple[object, ...]) -> list[float]:
    x = float(coordinate[0])
    y = float(coordinate[1])
    return [x, y, x, y]


def _merge_bounds(left: list[float] | None, right: list[float] | None) -> list[float] | None:
    if left is None:
        return right
    if right is None:
        return left
    return [min(left[0], right[0]), min(left[1], right[1]), max(left[2], right[2]), max(left[3], right[3])]


def _coordinate_stats(coordinates: object) -> dict[str, object]:
    if _is_coordinate_pair(coordinates):
        return {"point_count": 1, "part_count": 1, "bounds": _coordinate_bounds(coordinates)}
    if not isinstance(coordinates, (list, tuple)) or not coordinates:
        return {"point_count": 0, "part_count": 0}
    if all(_is_coordinate_pair(coordinate) for coordinate in coordinates):
        bounds: list[float] | None = None
        for coordinate in coordinates:
            bounds = _merge_bounds(bounds, _coordinate_bounds(coordinate))
        return {"point_count": len(coordinates), "part_count": 1, "bounds": bounds}

    point_count = 0
    part_count = 0
    bounds = None
    for child in coordinates:
        child_stats = _coordinate_stats(child)
        point_count += int(child_stats.get("point_count") or 0)
        part_count += int(child_stats.get("part_count") or 0)
        child_bounds = child_stats.get("bounds")
        if isinstance(child_bounds, list):
            bounds = _merge_bounds(bounds, child_bounds)
    summary: dict[str, object] = {"point_count": point_count, "part_count": part_count}
    if bounds is not None:
        summary["bounds"] = bounds
    return summary


def _geometry_summary(feature: dict[str, object]) -> dict[str, object]:
    geometry = feature.get("geometry")
    summary: dict[str, object] = {"type": _geometry_type(feature)}
    if isinstance(geometry, dict):
        summary.update(_coordinate_stats(geometry.get("coordinates")))
    return summary


def _normalized_index(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def is_contour_label_candidate(properties: dict[str, object]) -> bool:
    return _normalized_index(properties.get("index")) in CONTOUR_LABEL_INDICES


def _count_indices(features: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for feature in features:
        index = _normalized_index(_feature_properties(feature).get("index"))
        key = str(index) if index is not None else MISSING_VALUE
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _count_geometry_types(features: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for feature in features:
        geometry_type = _geometry_type(feature)
        counts[geometry_type] = counts.get(geometry_type, 0) + 1
    return dict(sorted(counts.items()))


def _count_geometry_family(geometry_type_counts: dict[str, int], geometry_types: set[str]) -> int:
    return sum(count for geometry_type, count in geometry_type_counts.items() if geometry_type in geometry_types)


def _ring_is_axis_aligned_rectangle(coordinates: object) -> bool:
    if not isinstance(coordinates, (list, tuple)) or len(coordinates) < 5:
        return False
    if not all(_is_coordinate_pair(coordinate) for coordinate in coordinates):
        return False
    points = [(float(coordinate[0]), float(coordinate[1])) for coordinate in coordinates]
    if points[0] != points[-1]:
        return False
    ring_points = set(points[:-1])
    xs = [point[0] for point in ring_points]
    ys = [point[1] for point in ring_points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    if min_x == max_x or min_y == max_y:
        return False
    corners = {
        (min_x, min_y),
        (min_x, max_y),
        (max_x, min_y),
        (max_x, max_y),
    }
    return corners.issubset(ring_points) and all(
        x in {min_x, max_x} or y in {min_y, max_y} for x, y in ring_points
    )


def _polygon_coordinate_shape(coordinates: object) -> str:
    if not isinstance(coordinates, (list, tuple)) or len(coordinates) != 1:
        return POLYGON_SHAPE_UNSUPPORTED
    ring = coordinates[0]
    if not isinstance(ring, (list, tuple)):
        return POLYGON_SHAPE_UNSUPPORTED
    return (
        POLYGON_SHAPE_RECTANGULAR
        if _ring_is_axis_aligned_rectangle(ring)
        else POLYGON_SHAPE_NON_RECTANGULAR
    )


def _candidate_polygon_shape(feature: dict[str, object]) -> str | None:
    geometry_type = _geometry_type(feature)
    if geometry_type not in POLYGON_GEOMETRY_TYPES:
        return None
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict):
        return POLYGON_SHAPE_UNSUPPORTED
    coordinates = geometry.get("coordinates")
    if geometry_type == "Polygon":
        return _polygon_coordinate_shape(coordinates)
    if not isinstance(coordinates, (list, tuple)) or not coordinates:
        return POLYGON_SHAPE_UNSUPPORTED
    shapes = [_polygon_coordinate_shape(polygon) for polygon in coordinates]
    if all(shape == POLYGON_SHAPE_RECTANGULAR for shape in shapes):
        return POLYGON_SHAPE_RECTANGULAR
    if any(shape == POLYGON_SHAPE_NON_RECTANGULAR for shape in shapes):
        return POLYGON_SHAPE_NON_RECTANGULAR
    return POLYGON_SHAPE_UNSUPPORTED


def _count_candidate_polygon_shapes(features: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for feature in features:
        shape = _candidate_polygon_shape(feature)
        if shape is None:
            continue
        counts[shape] = counts.get(shape, 0) + 1
    return dict(sorted(counts.items()))


def _candidate_label_geometry(geometry_type_counts: dict[str, int]) -> dict[str, object]:
    total = sum(geometry_type_counts.values())
    line_compatible_count = _count_geometry_family(geometry_type_counts, LINE_COMPATIBLE_GEOMETRY_TYPES)
    polygon_count = _count_geometry_family(geometry_type_counts, POLYGON_GEOMETRY_TYPES)
    other_count = max(0, total - line_compatible_count - polygon_count)
    status = LABEL_GEOMETRY_NO_CANDIDATES
    if total and line_compatible_count == total:
        status = LABEL_GEOMETRY_LINE_COMPATIBLE
    elif line_compatible_count:
        status = LABEL_GEOMETRY_MIXED_WITH_LINES
    elif total and polygon_count == total:
        status = LABEL_GEOMETRY_POLYGON_ONLY
    elif total:
        status = LABEL_GEOMETRY_NO_LINE_COMPATIBLE
    return {
        "status": status,
        "candidate_count": total,
        "line_compatible_count": line_compatible_count,
        "polygon_count": polygon_count,
        "other_count": other_count,
    }


def _candidate_polygon_shape_summary(shape_counts: dict[str, int]) -> dict[str, object]:
    total = sum(shape_counts.values())
    rectangular_count = shape_counts.get(POLYGON_SHAPE_RECTANGULAR, 0)
    non_rectangular_count = shape_counts.get(POLYGON_SHAPE_NON_RECTANGULAR, 0)
    unsupported_count = shape_counts.get(POLYGON_SHAPE_UNSUPPORTED, 0)
    status = POLYGON_SHAPE_NO_POLYGON_CANDIDATES
    if total and rectangular_count == total:
        status = POLYGON_SHAPE_RECTANGULAR_ONLY
    elif rectangular_count:
        status = POLYGON_SHAPE_MIXED_RECTANGULAR
    elif total and non_rectangular_count == total:
        status = POLYGON_SHAPE_NON_RECTANGULAR_ONLY
    elif total and unsupported_count == total:
        status = POLYGON_SHAPE_UNSUPPORTED_ONLY
    elif total:
        status = POLYGON_SHAPE_MIXED_POLYGON_SHAPES
    return {
        "status": status,
        "polygon_candidate_count": total,
        "rectangular_count": rectangular_count,
        "non_rectangular_count": non_rectangular_count,
        "unsupported_count": unsupported_count,
    }


def _iter_polygon_rings(feature: dict[str, object]) -> Iterable[object]:
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict):
        return
    geometry_type = _geometry_type(feature)
    coordinates = geometry.get("coordinates")
    if geometry_type == "Polygon" and isinstance(coordinates, (list, tuple)):
        yield from coordinates
        return
    if geometry_type == "MultiPolygon" and isinstance(coordinates, (list, tuple)):
        for polygon in coordinates:
            if isinstance(polygon, (list, tuple)):
                yield from polygon


def _empty_candidate_boundary_segment_stats() -> dict[str, int]:
    return dict.fromkeys(CANDIDATE_BOUNDARY_SEGMENT_STAT_KEYS, 0)


def _points_from_ring(ring: object) -> list[tuple[float, float]]:
    if not isinstance(ring, (list, tuple)):
        return []
    return [
        (float(coordinate[0]), float(coordinate[1]))
        for coordinate in ring
        if _is_coordinate_pair(coordinate)
    ]


def _segment_is_bbox_edge(
    first: tuple[float, float],
    second: tuple[float, float],
    bounds: list[float] | None,
) -> bool:
    if bounds is None or len(bounds) != 4:
        return False
    min_x, min_y, max_x, max_y = bounds
    return (
        (first[0] == second[0] and first[0] in {min_x, max_x})
        or (first[1] == second[1] and first[1] in {min_y, max_y})
    )


def _candidate_polygon_boundary_segment_stats(feature: dict[str, object]) -> dict[str, int]:
    stats = _empty_candidate_boundary_segment_stats()
    if _candidate_polygon_shape(feature) is None:
        return stats
    geometry_summary = _geometry_summary(feature)
    bounds_value = geometry_summary.get("bounds")
    bounds = [float(value) for value in bounds_value] if isinstance(bounds_value, list) else None
    stats["feature_count"] = 1
    for ring in _iter_polygon_rings(feature):
        points = _points_from_ring(ring)
        if not points:
            continue
        stats["ring_count"] += 1
        stats["point_count"] += len(points)
        for first, second in zip(points, points[1:]):
            stats["segment_count"] += 1
            if first[0] == second[0] or first[1] == second[1]:
                stats["axis_aligned_segment_count"] += 1
            else:
                stats["diagonal_segment_count"] += 1
            if _segment_is_bbox_edge(first, second, bounds):
                stats["bbox_edge_segment_count"] += 1
    return stats


def _sum_candidate_polygon_boundary_segment_stats(features: list[dict[str, object]]) -> dict[str, dict[str, int]]:
    stats_by_shape: dict[str, dict[str, int]] = {}
    for feature in features:
        shape = _candidate_polygon_shape(feature)
        if shape is None:
            continue
        shape_stats = stats_by_shape.setdefault(shape, _empty_candidate_boundary_segment_stats())
        candidate_stats = _candidate_polygon_boundary_segment_stats(feature)
        for key in CANDIDATE_BOUNDARY_SEGMENT_STAT_KEYS:
            shape_stats[key] += candidate_stats[key]
    return dict(sorted(stats_by_shape.items()))


def _candidate_sample(tile: dict[str, int], feature: dict[str, object]) -> dict[str, object]:
    properties = _feature_properties(feature)
    sample = {key: properties[key] for key in SAMPLE_PROPERTY_KEYS if key in properties}
    sample["tile"] = tile
    sample["geometry"] = _geometry_summary(feature)
    polygon_shape = _candidate_polygon_shape(feature)
    if polygon_shape is not None:
        sample["polygon_shape"] = polygon_shape
        sample["boundary_segment_stats"] = _candidate_polygon_boundary_segment_stats(feature)
    sample["property_keys"] = sorted(str(key) for key in properties.keys())
    return sample


def _tile_url(tile_url_template: str, tile: dict[str, int]) -> str:
    return tile_url_template.format(z=tile["z"], x=tile["x"], y=tile["y"])


def contour_tile_record(
    *,
    tile: dict[str, int],
    tile_url_template: str,
    tile_fetcher: TileFetcher,
    tile_decoder: TileDecoder,
) -> dict[str, object]:
    try:
        tile_bytes = tile_fetcher(_tile_url(tile_url_template, tile))
        decoded = decode_vector_tile_bytes(tile_bytes, tile_decoder)
    except _TILE_ERROR_TYPES as exc:
        return {**tile, "status": "error", "error": type(exc).__name__, "message": str(exc)}
    features = _decoded_layer_features(decoded, CONTOUR_SOURCE_LAYER)
    candidates = [feature for feature in features if is_contour_label_candidate(_feature_properties(feature))]
    candidate_geometry_type_counts = _count_geometry_types(candidates)
    candidate_polygon_shape_counts = _count_candidate_polygon_shapes(candidates)
    candidate_polygon_boundary_segment_stats = _sum_candidate_polygon_boundary_segment_stats(candidates)
    return {
        **tile,
        "status": "decoded",
        "byte_count": len(tile_bytes),
        "contour_feature_count": len(features),
        "contour_label_candidate_count": len(candidates),
        "index_counts": _count_indices(features),
        "geometry_type_counts": _count_geometry_types(features),
        "candidate_geometry_type_counts": candidate_geometry_type_counts,
        "candidate_label_geometry": _candidate_label_geometry(candidate_geometry_type_counts),
        "candidate_polygon_shape_counts": candidate_polygon_shape_counts,
        "candidate_polygon_shape": _candidate_polygon_shape_summary(candidate_polygon_shape_counts),
        "candidate_polygon_boundary_segment_stats": candidate_polygon_boundary_segment_stats,
        "sample_candidates": [
            _candidate_sample(tile, feature) for feature in candidates[:MAX_SAMPLE_CANDIDATES]
        ],
    }


def _combined_record_counts(tile_records: list[dict[str, object]], count_key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for tile_record in tile_records:
        record_counts = tile_record.get(count_key)
        if not isinstance(record_counts, dict):
            continue
        for index, count in record_counts.items():
            if isinstance(count, int):
                counts[str(index)] = counts.get(str(index), 0) + count
    return dict(sorted(counts.items()))


def _combined_nested_record_counts(
    records: list[dict[str, object]],
    count_key: str,
) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for record in records:
        record_counts = record.get(count_key)
        if not isinstance(record_counts, dict):
            continue
        for group, group_counts in record_counts.items():
            if not isinstance(group_counts, dict):
                continue
            group_key = str(group)
            target_counts = counts.setdefault(group_key, {})
            for key, count in group_counts.items():
                if isinstance(count, int):
                    stat_key = str(key)
                    target_counts[stat_key] = target_counts.get(stat_key, 0) + count
    return dict(sorted((group, dict(sorted(group_counts.items()))) for group, group_counts in counts.items()))


def _combined_index_counts(tile_records: list[dict[str, object]]) -> dict[str, int]:
    return _combined_record_counts(tile_records, "index_counts")


def _combined_samples(tile_records: list[dict[str, object]]) -> list[dict[str, object]]:
    samples: list[dict[str, object]] = []
    for tile_record in tile_records:
        tile_samples = tile_record.get("sample_candidates")
        if isinstance(tile_samples, list):
            samples.extend(sample for sample in tile_samples if isinstance(sample, dict))
        if len(samples) >= MAX_SAMPLE_CANDIDATES:
            break
    return samples[:MAX_SAMPLE_CANDIDATES]


def _camera_by_name(camera_name: str):
    _ensure_package_parent_on_path()
    from qfit.validation.mapbox_outdoors_comparison import CAMERAS

    try:
        return CAMERAS[camera_name]
    except KeyError as exc:
        raise ValueError(f"Unknown comparison camera: {camera_name}") from exc


def _comparison_camera_names() -> list[str]:
    _ensure_package_parent_on_path()
    from qfit.validation.mapbox_outdoors_comparison import CAMERAS

    return list(CAMERAS.keys())


def _camera_extent(camera) -> tuple[float, float, float, float]:
    _ensure_package_parent_on_path()
    from qfit.validation.mapbox_outdoors_comparison import camera_extent_web_mercator

    return camera_extent_web_mercator(camera)


def _load_original_style(config: ContourFeatureConfig, style_fetcher) -> dict[str, object]:
    if config.style_json_path is not None:
        return load_style_definition(config.style_json_path)
    if not config.token:
        raise ValueError("A Mapbox token is required unless --style-json is provided.")
    return style_fetcher(config.token, config.style_owner, config.style_id)


def collect_contour_feature_report(
    config: ContourFeatureConfig,
    *,
    style_fetcher: Callable[[str, str, str], dict[str, object]] | None = None,
    tile_fetcher: TileFetcher | None = None,
    tile_decoder: TileDecoder | None = None,
) -> dict[str, object]:
    _ensure_package_parent_on_path()
    from qfit.mapbox_config import (
        build_mapbox_vector_tiles_url,
        extract_mapbox_vector_source_ids,
        fetch_mapbox_style_definition,
    )

    fetch_style = style_fetcher or fetch_mapbox_style_definition
    original_style = _load_original_style(config, fetch_style)
    tileset_ids = extract_mapbox_vector_source_ids(original_style)
    if not config.token:
        raise ValueError("A Mapbox token is required to fetch vector tiles.")
    camera = _camera_by_name(config.camera_name)
    tile_zoom = config.tile_zoom if config.tile_zoom is not None else recommended_tile_zoom(float(camera.zoom))
    tile_bounds = tile_bounds_for_web_mercator_extent(_camera_extent(camera), tile_zoom)
    tile_url_template = build_mapbox_vector_tiles_url(
        config.token,
        config.style_owner,
        config.style_id,
        tileset_ids=tileset_ids,
    )
    tile_records = [
        contour_tile_record(
            tile=tile,
            tile_url_template=tile_url_template,
            tile_fetcher=tile_fetcher or _fetch_url_bytes,
            tile_decoder=tile_decoder or _default_tile_decoder,
        )
        for tile in iter_tile_coordinates(tile_bounds, tile_zoom)
    ]
    decoded_tile_count = sum(1 for tile in tile_records if tile.get("status") == "decoded")
    generated = config.now or dt.datetime.now(dt.timezone.utc)
    candidate_geometry_type_counts = _combined_record_counts(tile_records, "candidate_geometry_type_counts")
    candidate_polygon_shape_counts = _combined_record_counts(tile_records, "candidate_polygon_shape_counts")
    candidate_polygon_boundary_segment_stats = _combined_nested_record_counts(
        tile_records,
        "candidate_polygon_boundary_segment_stats",
    )
    return {
        "style_owner": config.style_owner,
        "style_id": config.style_id,
        "generated": generated.isoformat(),
        "camera": {
            "name": camera.name,
            "longitude": camera.longitude,
            "latitude": camera.latitude,
            "zoom": camera.zoom,
            "width": camera.width,
            "height": camera.height,
        },
        "tile_zoom": tile_zoom,
        "tile_bounds": tile_bounds,
        "tileset_ids": tileset_ids,
        "tile_count": len(tile_records),
        "decoded_tile_count": decoded_tile_count,
        "failed_tile_count": len(tile_records) - decoded_tile_count,
        "contour_feature_count": sum(int(tile.get("contour_feature_count") or 0) for tile in tile_records),
        "contour_label_candidate_count": sum(
            int(tile.get("contour_label_candidate_count") or 0) for tile in tile_records
        ),
        "index_counts": _combined_index_counts(tile_records),
        "geometry_type_counts": _combined_record_counts(tile_records, "geometry_type_counts"),
        "candidate_geometry_type_counts": candidate_geometry_type_counts,
        "candidate_label_geometry": _candidate_label_geometry(candidate_geometry_type_counts),
        "candidate_polygon_shape_counts": candidate_polygon_shape_counts,
        "candidate_polygon_shape": _candidate_polygon_shape_summary(candidate_polygon_shape_counts),
        "candidate_polygon_boundary_segment_stats": candidate_polygon_boundary_segment_stats,
        "sample_candidates": _combined_samples(tile_records),
        "tiles": tile_records,
    }


def _all_camera_summary_counts(rows: list[dict[str, object]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(key)
        if isinstance(value, str) and value:
            counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _all_camera_row(report: dict[str, object]) -> dict[str, object]:
    camera = report.get("camera") if isinstance(report.get("camera"), dict) else {}
    candidate_geometry = (
        report.get("candidate_label_geometry")
        if isinstance(report.get("candidate_label_geometry"), dict)
        else {}
    )
    return {
        "status": "decoded",
        "camera": camera.get("name"),
        "camera_zoom": camera.get("zoom"),
        "tile_zoom": report.get("tile_zoom"),
        "tile_count": report.get("tile_count"),
        "decoded_tile_count": report.get("decoded_tile_count"),
        "failed_tile_count": report.get("failed_tile_count"),
        "contour_feature_count": report.get("contour_feature_count"),
        "contour_label_candidate_count": report.get("contour_label_candidate_count"),
        "candidate_label_geometry_status": candidate_geometry.get("status"),
        "candidate_geometry_type_counts": report.get("candidate_geometry_type_counts"),
        "candidate_polygon_shape_status": (
            report.get("candidate_polygon_shape", {}).get("status")
            if isinstance(report.get("candidate_polygon_shape"), dict)
            else None
        ),
        "candidate_polygon_shape_counts": report.get("candidate_polygon_shape_counts"),
        "candidate_polygon_boundary_segment_stats": report.get("candidate_polygon_boundary_segment_stats"),
    }


def _all_camera_error_row(camera_name: str, exc: BaseException) -> dict[str, object]:
    return {
        "status": "error",
        "camera": camera_name,
        "error": type(exc).__name__,
        "message": str(exc),
    }


def _all_camera_sum(rows: list[dict[str, object]], key: str) -> int:
    return sum(int(row.get(key) or 0) for row in rows)


def _config_for_camera(config: ContourFeatureConfig, camera_name: str) -> ContourFeatureConfig:
    return ContourFeatureConfig(
        token=config.token,
        output_root=config.output_root,
        camera_name=camera_name,
        style_owner=config.style_owner,
        style_id=config.style_id,
        style_json_path=config.style_json_path,
        tile_zoom=config.tile_zoom,
        now=config.now,
    )


def collect_all_camera_contour_feature_report(
    config: ContourFeatureConfig,
    *,
    style_fetcher: Callable[[str, str, str], dict[str, object]] | None = None,
    tile_fetcher: TileFetcher | None = None,
    tile_decoder: TileDecoder | None = None,
) -> dict[str, object]:
    _ensure_package_parent_on_path()
    from qfit.mapbox_config import fetch_mapbox_style_definition

    shared_style = _load_original_style(config, style_fetcher or fetch_mapbox_style_definition)
    if not config.token:
        raise ValueError("A Mapbox token is required to fetch vector tiles.")

    rows: list[dict[str, object]] = []
    for camera_name in _comparison_camera_names():
        try:
            report = collect_contour_feature_report(
                _config_for_camera(config, camera_name),
                style_fetcher=lambda _token, _owner, _style_id: shared_style,
                tile_fetcher=tile_fetcher,
                tile_decoder=tile_decoder,
            )
        except _TILE_ERROR_TYPES as exc:
            rows.append(_all_camera_error_row(camera_name, exc))
            continue
        rows.append(_all_camera_row(report))
    generated = config.now or dt.datetime.now(dt.timezone.utc)
    status_counts = _all_camera_summary_counts(rows, "status")
    return {
        "style_owner": config.style_owner,
        "style_id": config.style_id,
        "generated": generated.isoformat(),
        "camera_count": len(rows),
        "successful_camera_count": status_counts.get("decoded", 0),
        "failed_camera_count": status_counts.get("error", 0),
        "tile_count": _all_camera_sum(rows, "tile_count"),
        "decoded_tile_count": _all_camera_sum(rows, "decoded_tile_count"),
        "failed_tile_count": _all_camera_sum(rows, "failed_tile_count"),
        "contour_feature_count": _all_camera_sum(rows, "contour_feature_count"),
        "contour_label_candidate_count": _all_camera_sum(rows, "contour_label_candidate_count"),
        "candidate_label_geometry_statuses": _all_camera_summary_counts(
            rows,
            "candidate_label_geometry_status",
        ),
        "candidate_polygon_shape_statuses": _all_camera_summary_counts(
            rows,
            "candidate_polygon_shape_status",
        ),
        "candidate_polygon_boundary_segment_stats": _combined_nested_record_counts(
            rows,
            "candidate_polygon_boundary_segment_stats",
        ),
        "cameras": rows,
    }


def _markdown_value(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, dict) or isinstance(value, list):
        return json.dumps(value, sort_keys=True, separators=(",", ":")).replace("|", "\\|")
    return str(value).replace("|", "\\|")


def build_summary_markdown(report: dict[str, object]) -> str:
    camera = report.get("camera") if isinstance(report.get("camera"), dict) else {}
    lines = [
        f"# Mapbox Outdoors contour feature diagnostic - {camera.get('name')}",
        "",
        f"Generated: {report.get('generated')}",
        f"Style: {report.get('style_owner')}/{report.get('style_id')}",
        f"Tile zoom: {report.get('tile_zoom')}",
        f"Tiles: {report.get('decoded_tile_count')}/{report.get('tile_count')} decoded",
        f"Contour features: {report.get('contour_feature_count')}",
        f"Contour-label candidates (index 5/10): {report.get('contour_label_candidate_count')}",
        f"Index counts: {_markdown_value(report.get('index_counts'))}",
        f"Geometry types: {_markdown_value(report.get('geometry_type_counts'))}",
        f"Candidate geometry types: {_markdown_value(report.get('candidate_geometry_type_counts'))}",
        f"Candidate label geometry: {_markdown_value(report.get('candidate_label_geometry'))}",
        f"Candidate polygon shapes: {_markdown_value(report.get('candidate_polygon_shape'))}",
        f"Candidate polygon boundary segments: {_markdown_value(report.get('candidate_polygon_boundary_segment_stats'))}",
        "",
        "| z | x | y | Status | Contour features | Label candidates | Index counts | Geometry types | Candidate geometry types | Candidate polygon shapes | Candidate polygon boundary segments |",
        "| ---: | ---: | ---: | --- | ---: | ---: | --- | --- | --- | --- | --- |",
    ]
    tiles = report.get("tiles")
    tile_rows = tiles if isinstance(tiles, list) else []
    for tile in tile_rows:
        if not isinstance(tile, dict):
            continue
        lines.append(
            "| {z} | {x} | {y} | {status} | {feature_count} | {candidate_count} | {index_counts} | {geometry_counts} | {candidate_geometry_counts} | {candidate_polygon_shapes} | {candidate_boundary_segments} |".format(
                z=_markdown_value(tile.get("z")),
                x=_markdown_value(tile.get("x")),
                y=_markdown_value(tile.get("y")),
                status=_markdown_value(tile.get("status")),
                feature_count=_markdown_value(tile.get("contour_feature_count")),
                candidate_count=_markdown_value(tile.get("contour_label_candidate_count")),
                index_counts=_markdown_value(tile.get("index_counts")),
                geometry_counts=_markdown_value(tile.get("geometry_type_counts")),
                candidate_geometry_counts=_markdown_value(tile.get("candidate_geometry_type_counts")),
                candidate_polygon_shapes=_markdown_value(tile.get("candidate_polygon_shape_counts")),
                candidate_boundary_segments=_markdown_value(
                    tile.get("candidate_polygon_boundary_segment_stats")
                ),
            )
        )
    samples = report.get("sample_candidates")
    sample_rows = samples if isinstance(samples, list) else []
    if sample_rows:
        lines.extend(["", "## Sample contour-label candidates", ""])
        for sample in sample_rows:
            lines.append(f"- {_markdown_value(sample)}")
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, object], paths: ContourFeaturePaths) -> None:
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    paths.json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    paths.summary_path.write_text(build_summary_markdown(report), encoding="utf-8")


def build_all_camera_summary_markdown(report: dict[str, object]) -> str:
    cameras = report.get("cameras")
    camera_rows = cameras if isinstance(cameras, list) else []
    lines = [
        "# Mapbox Outdoors contour feature diagnostic - all cameras",
        "",
        f"Generated: {report.get('generated')}",
        f"Style: {report.get('style_owner')}/{report.get('style_id')}",
        f"Cameras: {report.get('camera_count')}",
        f"Camera statuses: {_markdown_value(_all_camera_summary_counts(camera_rows, 'status'))}",
        f"Tiles: {report.get('decoded_tile_count')}/{report.get('tile_count')} decoded",
        f"Contour features: {report.get('contour_feature_count')}",
        f"Contour-label candidates (index 5/10): {report.get('contour_label_candidate_count')}",
        f"Candidate label geometry statuses: {_markdown_value(report.get('candidate_label_geometry_statuses'))}",
        f"Candidate polygon shape statuses: {_markdown_value(report.get('candidate_polygon_shape_statuses'))}",
        f"Candidate polygon boundary segments: {_markdown_value(report.get('candidate_polygon_boundary_segment_stats'))}",
        "",
        "| Camera | Status | Camera zoom | Tile zoom | Tiles decoded | Contour features | Label candidates | Candidate label geometry | Candidate geometry types | Candidate polygon shape | Candidate polygon shapes | Candidate polygon boundary segments | Error |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- |",
    ]
    for camera in camera_rows:
        if not isinstance(camera, dict):
            continue
        lines.append(
            "| {camera} | {status} | {camera_zoom} | {tile_zoom} | {decoded}/{tile_count} | {feature_count} | {candidate_count} | {candidate_geometry} | {candidate_geometry_types} | {candidate_polygon_shape} | {candidate_polygon_shapes} | {candidate_boundary_segments} | {error} |".format(
                camera=_markdown_value(camera.get("camera")),
                status=_markdown_value(camera.get("status")),
                camera_zoom=_markdown_value(camera.get("camera_zoom")),
                tile_zoom=_markdown_value(camera.get("tile_zoom")),
                decoded=_markdown_value(camera.get("decoded_tile_count")),
                tile_count=_markdown_value(camera.get("tile_count")),
                feature_count=_markdown_value(camera.get("contour_feature_count")),
                candidate_count=_markdown_value(camera.get("contour_label_candidate_count")),
                candidate_geometry=_markdown_value(camera.get("candidate_label_geometry_status")),
                candidate_geometry_types=_markdown_value(camera.get("candidate_geometry_type_counts")),
                candidate_polygon_shape=_markdown_value(camera.get("candidate_polygon_shape_status")),
                candidate_polygon_shapes=_markdown_value(camera.get("candidate_polygon_shape_counts")),
                candidate_boundary_segments=_markdown_value(
                    camera.get("candidate_polygon_boundary_segment_stats")
                ),
                error=_markdown_value(camera.get("error")),
            )
        )
    return "\n".join(lines) + "\n"


def write_all_camera_report(report: dict[str, object], paths: AllCameraContourFeaturePaths) -> None:
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    paths.json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    paths.summary_path.write_text(build_all_camera_summary_markdown(report), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate Mapbox Outdoors contour vector-tile feature diagnostics.")
    parser.add_argument("camera", nargs="?")
    parser.add_argument(
        "--all-cameras",
        action="store_true",
        help="Run the diagnostic across the full Mapbox Outdoors comparison camera matrix.",
    )
    parser.add_argument("--style-json", type=Path, help="Read an already downloaded Mapbox style JSON file.")
    parser.add_argument("--style-owner", default=DEFAULT_MAPBOX_STYLE_OWNER)
    parser.add_argument("--style-id", default=DEFAULT_MAPBOX_STYLE_ID)
    parser.add_argument("--mapbox-token", help="Mapbox token. Prefer MAPBOX_ACCESS_TOKEN or QFIT_MAPBOX_ACCESS_TOKEN.")
    parser.add_argument("--tile-zoom", type=int, help="Override the integer vector-tile zoom to inspect.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.all_cameras and args.camera:
        parser.error("Do not pass a camera name with --all-cameras.")
    now = dt.datetime.now(dt.timezone.utc)
    config = ContourFeatureConfig(
        token=resolve_mapbox_token(provided_token=args.mapbox_token),
        output_root=args.output_root,
        camera_name=args.camera or DEFAULT_CAMERA_NAME,
        style_owner=args.style_owner,
        style_id=args.style_id,
        style_json_path=args.style_json,
        tile_zoom=args.tile_zoom,
        now=now,
    )
    if args.all_cameras:
        report = collect_all_camera_contour_feature_report(config)
        paths = build_all_camera_contour_feature_paths(
            build_all_camera_run_directory(output_root=config.output_root, now=config.now)
        )
        write_all_camera_report(report, paths)
        print(paths.summary_path)
        return

    report = collect_contour_feature_report(config)
    paths = build_contour_feature_paths(
        build_run_directory(output_root=config.output_root, camera_name=config.camera_name, now=config.now)
    )
    write_report(report, paths)
    print(paths.summary_path)


if __name__ == "__main__":  # pragma: no cover
    main()
