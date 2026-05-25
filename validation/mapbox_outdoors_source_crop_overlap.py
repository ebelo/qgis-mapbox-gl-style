from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import math
import operator as py_operator
import os
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
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
COMPARISON_OPERATORS = frozenset(("==", "!=", ">", ">=", "<", "<=", "in", "!in"))
BOOLEAN_OPERATORS = frozenset(("all", "any", "none", "!"))
GEOMETRY_TYPE_PROPERTY = "$geometry_type"
ZOOM_PROPERTY = "$zoom"
NUMERIC_COMPARISONS = {
    ">": py_operator.gt,
    ">=": py_operator.ge,
    "<": py_operator.lt,
    "<=": py_operator.le,
}
STYLE_LAYER_PAINT_KEYS = (
    "fill-color",
    "fill-opacity",
    "line-color",
    "line-opacity",
    "line-width",
)
MISSING_VALUE = "(missing)"
QGIS_RUNTIME_NOT_CAPTURED = "(not captured)"
_DROPPED_FILTER = object()

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


@dataclass
class _AggregateSourceLayerTotal:
    source_layer: str
    reports: set[str] = field(default_factory=set)
    cameras: set[str] = field(default_factory=set)
    class_coverage: Counter[str] = field(default_factory=Counter)
    overlap_feature_count: int = 0
    coverage_sum: float = 0.0
    max_coverage: float = 0.0
    zero_overlap_reports: int = 0


@dataclass
class _AggregateStyleLayerTotal:
    source_layer: str
    layer: str
    layer_type: str
    reports: set[str] = field(default_factory=set)
    cameras: set[str] = field(default_factory=set)
    feature_count: int = 0
    coverage_sum: float = 0.0
    max_coverage: float = 0.0


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


def _bbox_area(west: float, south: float, east: float, north: float) -> float:
    return max(0.0, east - west) * max(0.0, north - south)


def lon_lat_bounds_area(bounds: Mapping[str, float]) -> float:
    return _bbox_area(bounds["west"], bounds["south"], bounds["east"], bounds["north"])


def bbox_overlap_area(
    bbox: tuple[float, float, float, float] | None,
    bounds: Mapping[str, float],
) -> float:
    if bbox is None:
        return 0.0
    west, south, east, north = bbox
    return _bbox_area(
        max(west, bounds["west"]),
        max(south, bounds["south"]),
        min(east, bounds["east"]),
        min(north, bounds["north"]),
    )


def _feature_bbox_overlap_area(feature: Mapping[str, object], bounds: Mapping[str, float]) -> float:
    return bbox_overlap_area(feature_lon_lat_bbox(feature), bounds)


def _rounded_float(value: float) -> float:
    return round(value, 12)


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


def _property_area_counter_summary(
    property_areas: Mapping[str, Counter[str]],
    *,
    crop_area: float,
    limit: int | None = MAX_COUNT_VALUES,
) -> dict[str, dict[str, dict[str, float]]]:
    return {
        key: {
            value: {
                "overlap_bbox_area": _rounded_float(area),
                "crop_coverage_ratio": _rounded_float(area / crop_area) if crop_area else 0.0,
            }
            for value, area in areas.most_common(limit)
        }
        for key, areas in property_areas.items()
        if areas
    }


def _update_feature_property_areas(
    property_areas: dict[str, Counter[str]],
    feature: Mapping[str, object],
    area: float,
) -> None:
    if area <= 0.0:
        return
    feature_properties = _feature_properties(feature)
    for key in PROPERTY_COUNT_KEYS:
        value = _clean_count_value(feature_properties.get(key))
        if value != MISSING_VALUE:
            property_areas.setdefault(key, Counter()).update({value: area})


def _property_area_summary(
    features: Sequence[Mapping[str, object]],
    *,
    bounds: Mapping[str, float],
    crop_area: float,
) -> dict[str, dict[str, dict[str, float]]]:
    property_areas: dict[str, Counter[str]] = {}
    feature_areas = [(feature, _feature_bbox_overlap_area(feature, bounds)) for feature in features]
    for feature, area in feature_areas:
        _update_feature_property_areas(property_areas, feature, area)
    return _property_area_counter_summary(property_areas, crop_area=crop_area)


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


def _filter_property_names(expression: object) -> set[str]:
    if not isinstance(expression, list) or not expression:
        return set()
    operator = expression[0]
    operands = expression[1:]
    if operator in {"get", "has"} and operands:
        return {str(operands[0])}
    if operator == "!has":
        return set()
    if operator in COMPARISON_OPERATORS and len(expression) >= 3 and isinstance(expression[1], str):
        names = {GEOMETRY_TYPE_PROPERTY if expression[1] == "$type" else expression[1]}
        for operand in expression[2:]:
            names.update(_filter_property_names(operand))
        return names
    names: set[str] = set()
    for operand in operands:
        names.update(_filter_property_names(operand))
    return names


def _source_filter_property_names(expression: object) -> list[str]:
    return sorted(_filter_property_names(expression) - {GEOMETRY_TYPE_PROPERTY, ZOOM_PROPERTY})


def _missing_filter_property_counts(
    features: Sequence[Mapping[str, object]],
    properties: Sequence[str],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in properties:
        missing_count = sum(1 for feature in features if name not in _feature_properties(feature))
        if missing_count > 0:
            counts[name] = missing_count
    return counts


def _stripped_filter_operands(
    operands: Sequence[object],
    missing_properties: set[str],
) -> list[object]:
    stripped_operands = []
    for operand in operands:
        stripped_operand = _filter_without_missing_property_checks(operand, missing_properties)
        if stripped_operand is not _DROPPED_FILTER:
            stripped_operands.append(stripped_operand)
    return stripped_operands


def _filter_without_missing_boolean_checks(
    operator: object,
    operands: Sequence[object],
    missing_properties: set[str],
) -> object:
    stripped_operands = _stripped_filter_operands(operands, missing_properties)
    if not stripped_operands:
        return True
    if operator == "any" and len(stripped_operands) == 1:
        return stripped_operands[0]
    return [operator, *stripped_operands]


def _filter_without_missing_not_check(
    operands: Sequence[object],
    missing_properties: set[str],
) -> object:
    if not operands:
        return _DROPPED_FILTER
    stripped_operand = _filter_without_missing_property_checks(operands[0], missing_properties)
    return True if stripped_operand is _DROPPED_FILTER else ["!", stripped_operand]


def _filter_without_missing_result_expression(
    expression: object,
    missing_properties: set[str],
) -> object:
    stripped_expression = _filter_without_missing_property_checks(expression, missing_properties)
    return True if stripped_expression is _DROPPED_FILTER else stripped_expression


def _filter_without_missing_case_checks(
    expression: Sequence[object],
    missing_properties: set[str],
) -> object:
    if len(expression) < 4:
        return _DROPPED_FILTER
    stripped_expression: list[object] = ["case"]
    for index in range(1, len(expression) - 1, 2):
        stripped_condition = _filter_without_missing_property_checks(expression[index], missing_properties)
        if stripped_condition is _DROPPED_FILTER:
            continue
        stripped_expression.extend(
            [
                stripped_condition,
                _filter_without_missing_result_expression(expression[index + 1], missing_properties),
            ]
        )
    fallback = expression[-1]
    stripped_expression.append(_filter_without_missing_result_expression(fallback, missing_properties))
    return stripped_expression[1] if len(stripped_expression) == 2 else stripped_expression


def _filter_without_missing_match_checks(
    expression: Sequence[object],
    missing_properties: set[str],
) -> object:
    if len(expression) < 5:
        return _DROPPED_FILTER
    if _filter_property_names(expression[1]) & missing_properties:
        return True
    stripped_expression = ["match", expression[1], *expression[2:-1]]
    for index in range(3, len(stripped_expression), 2):
        stripped_expression[index] = _filter_without_missing_result_expression(
            stripped_expression[index],
            missing_properties,
        )
    stripped_expression.append(_filter_without_missing_result_expression(expression[-1], missing_properties))
    return stripped_expression


def _filter_without_missing_property_checks(
    expression: object,
    missing_properties: set[str],
) -> object:
    if not isinstance(expression, list) or not expression:
        return expression
    if not (_filter_property_names(expression) & missing_properties):
        return expression
    operator = expression[0]
    operands = expression[1:]
    if operator in {"all", "any", "none"}:
        return _filter_without_missing_boolean_checks(operator, operands, missing_properties)
    if operator == "!":
        return _filter_without_missing_not_check(operands, missing_properties)
    if operator == "case":
        return _filter_without_missing_case_checks(expression, missing_properties)
    if operator == "match":
        return _filter_without_missing_match_checks(expression, missing_properties)
    return _DROPPED_FILTER


def _feature_matches_candidate_filter_without_missing_checks(
    feature: Mapping[str, object],
    expression: object,
    missing_properties: set[str],
    *,
    camera_zoom: float,
) -> bool:
    context_properties = _feature_context_properties(feature, camera_zoom=camera_zoom)
    if _mapbox_filter_matches(expression, context_properties):
        return False
    candidate_filter = _filter_without_missing_property_checks(expression, missing_properties)
    if candidate_filter is _DROPPED_FILTER:
        candidate_filter = True
    return _mapbox_filter_matches(candidate_filter, context_properties)


def _update_candidate_feature_property_counts(
    counts: dict[str, Counter[str]],
    feature: Mapping[str, object],
) -> None:
    feature_properties = _feature_properties(feature)
    for key in PROPERTY_COUNT_KEYS:
        if key in feature_properties:
            counts.setdefault(key, Counter()).update({_clean_count_value(feature_properties[key]): 1})


def _candidate_missing_filter_property_summary(
    features: Sequence[Mapping[str, object]],
    properties: Sequence[str],
    expression: object,
    *,
    bounds: Mapping[str, float],
    crop_area: float,
    camera_zoom: float,
) -> tuple[dict[str, int], dict[str, dict[str, int]], dict[str, dict[str, dict[str, float]]]]:
    property_set = set(properties)
    counts: Counter[str] = Counter()
    candidate_property_counts: dict[str, Counter[str]] = {}
    candidate_property_areas: dict[str, Counter[str]] = {}
    for feature in features:
        missing_properties = property_set - set(_feature_properties(feature))
        if missing_properties and _feature_matches_candidate_filter_without_missing_checks(
            feature,
            expression,
            missing_properties,
            camera_zoom=camera_zoom,
        ):
            counts.update(missing_properties)
            _update_candidate_feature_property_counts(candidate_property_counts, feature)
            _update_feature_property_areas(
                candidate_property_areas,
                feature,
                _feature_bbox_overlap_area(feature, bounds),
            )
    return (
        {name: counts[name] for name in properties if counts[name] > 0},
        {
            key: dict(counter.most_common())
            for key, counter in candidate_property_counts.items()
            if counter
        },
        _property_area_counter_summary(candidate_property_areas, crop_area=crop_area, limit=None),
    )


def _numeric_value(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _mapbox_to_number_value(value: object) -> float | None:
    if value is None or value is False:
        return 0.0
    if value is True:
        return 1.0
    return _numeric_value(value)


def _mapbox_to_number_expression_value(operands: Sequence[object], properties: Mapping[str, object]) -> float | None:
    for operand in operands:
        converted = _mapbox_to_number_value(_mapbox_expression_value(operand, properties))
        if converted is not None:
            return converted
    return None


def _comparison_membership_contains(left: object, right: object) -> bool:
    if isinstance(right, str):
        return isinstance(left, str) and left in right
    haystack = right if isinstance(right, list) else [right]
    return left in haystack


def _comparison_result(operator: object, left: object, right: object) -> bool:
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    numeric_comparison = NUMERIC_COMPARISONS.get(operator)
    if numeric_comparison is not None:
        left_number = _numeric_value(left)
        right_number = _numeric_value(right)
        if left_number is None or right_number is None:
            return False
        return numeric_comparison(left_number, right_number)
    if operator in {"in", "!in"}:
        contains = _comparison_membership_contains(left, right)
        return contains if operator == "in" else not contains
    return False


def _mapbox_step_value(expression: Sequence[object], properties: Mapping[str, object]) -> object:
    if len(expression) < 4:
        return None
    input_number = _numeric_value(_mapbox_expression_value(expression[1], properties))
    result = expression[2]
    if input_number is None:
        return _mapbox_expression_value(result, properties)
    for index in range(3, len(expression) - 1, 2):
        stop = _numeric_value(_mapbox_expression_value(expression[index], properties))
        if stop is None or input_number < stop:
            break
        result = expression[index + 1]
    return _mapbox_expression_value(result, properties)


def _mapbox_simple_expression_value(
    operator: object,
    operands: Sequence[object],
    properties: Mapping[str, object],
) -> object:
    if operator == "get" and operands:
        return properties.get(str(operands[0]))
    if operator == "to-number" and operands:
        return _mapbox_to_number_expression_value(operands, properties)
    if operator == "literal" and operands:
        return operands[0]
    if operator == "has" and operands:
        return str(operands[0]) in properties
    if operator == "!has" and operands:
        return str(operands[0]) not in properties
    if operator == "geometry-type":
        return properties.get(GEOMETRY_TYPE_PROPERTY)
    if operator == "zoom":
        return properties.get(ZOOM_PROPERTY)
    return None


def _mapbox_match_value(expression: Sequence[object], properties: Mapping[str, object]) -> object:
    if len(expression) < 5:
        return None
    input_value = _mapbox_expression_value(expression[1], properties)
    fallback = expression[-1]
    for index in range(2, len(expression) - 1, 2):
        label = expression[index]
        labels = label if isinstance(label, list) else [label]
        if input_value in labels:
            return _mapbox_expression_value(expression[index + 1], properties)
    return _mapbox_expression_value(fallback, properties)


def _mapbox_case_value(expression: Sequence[object], properties: Mapping[str, object]) -> object:
    if len(expression) < 3:
        return None
    for index in range(1, len(expression) - 1, 2):
        if _mapbox_filter_matches(expression[index], properties):
            return _mapbox_expression_value(expression[index + 1], properties)
    return _mapbox_expression_value(expression[-1], properties)


def _mapbox_boolean_value(
    operator: object,
    operands: Sequence[object],
    properties: Mapping[str, object],
) -> object:
    if operator == "all":
        return all(_mapbox_filter_matches(item, properties) for item in operands)
    if operator == "any":
        return any(_mapbox_filter_matches(item, properties) for item in operands)
    if operator == "none":
        return not any(_mapbox_filter_matches(item, properties) for item in operands)
    if operator == "!":
        return not _mapbox_filter_matches(operands[0], properties) if operands else False
    return None


def _comparison_values(expression: Sequence[object], properties: Mapping[str, object]) -> tuple[object, object] | None:
    if len(expression) < 3:
        return None
    return (
        _mapbox_expression_value(expression[1], properties),
        _mapbox_expression_value(expression[2], properties),
    )


def _mapbox_expression_value(expression: object, properties: Mapping[str, object]) -> object:
    if not isinstance(expression, list) or not expression:
        return expression
    operator = expression[0]
    operands = expression[1:]
    if operator == "match":
        return _mapbox_match_value(expression, properties)
    if operator == "case":
        return _mapbox_case_value(expression, properties)
    if operator in BOOLEAN_OPERATORS:
        return _mapbox_boolean_value(operator, operands, properties)
    if operator in COMPARISON_OPERATORS:
        values = _comparison_values(expression, properties)
        return None if values is None else _comparison_result(operator, values[0], values[1])
    if operator == "step":
        return _mapbox_step_value(expression, properties)
    return _mapbox_simple_expression_value(operator, operands, properties)


def _legacy_filter_property_value(value: object, properties: Mapping[str, object]) -> object:
    if value == "$type":
        return properties.get(GEOMETRY_TYPE_PROPERTY)
    return properties.get(value) if isinstance(value, str) else _mapbox_expression_value(value, properties)


def _legacy_filter_values(expression: Sequence[object], properties: Mapping[str, object]) -> tuple[object, object] | None:
    if len(expression) < 3 or not isinstance(expression[1], str):
        return None
    if expression[0] in {"in", "!in"} and len(expression) > 3:
        return _legacy_filter_property_value(expression[1], properties), list(expression[2:])
    return _legacy_filter_property_value(expression[1], properties), expression[2]


def _mapbox_filter_matches(expression: object, properties: Mapping[str, object]) -> bool:
    if expression in (None, True):
        return True
    if expression is False:
        return False
    if not isinstance(expression, list) or not expression:
        return bool(expression)
    operator = expression[0]
    if operator in BOOLEAN_OPERATORS:
        return bool(_mapbox_expression_value(expression, properties))
    legacy_values = _legacy_filter_values(expression, properties)
    if legacy_values is not None:
        return _comparison_result(operator, legacy_values[0], legacy_values[1])
    values = _comparison_values(expression, properties) if operator in COMPARISON_OPERATORS else None
    if values is not None:
        return _comparison_result(operator, values[0], values[1])
    return bool(_mapbox_expression_value(expression, properties))


def _style_layer_active_at_zoom(layer: Mapping[str, object], zoom: float) -> bool:
    layout = layer.get("layout")
    if isinstance(layout, Mapping) and layout.get("visibility") == "none":
        return False
    minzoom = _numeric_value(layer.get("minzoom"))
    maxzoom = _numeric_value(layer.get("maxzoom"))
    if minzoom is not None and zoom < minzoom:
        return False
    return maxzoom is None or zoom < maxzoom


def _style_layer_paint_summary(layer: Mapping[str, object]) -> dict[str, object]:
    paint = layer.get("paint")
    if not isinstance(paint, dict):
        return {}
    return {key: paint[key] for key in STYLE_LAYER_PAINT_KEYS if key in paint}


def _feature_context_properties(feature: Mapping[str, object], *, camera_zoom: float) -> dict[str, object]:
    properties = dict(_feature_properties(feature))
    geometry = feature.get("geometry")
    if isinstance(geometry, dict) and isinstance(geometry.get("type"), str):
        properties[GEOMETRY_TYPE_PROPERTY] = geometry["type"]
    properties[ZOOM_PROPERTY] = camera_zoom
    return properties


def _matching_style_layers(
    *,
    feature: Mapping[str, object],
    source_layer: str,
    style_layers: Sequence[Mapping[str, object]],
    camera_zoom: float,
) -> Iterator[Mapping[str, object]]:
    properties = _feature_context_properties(feature, camera_zoom=camera_zoom)
    for layer in style_layers:
        if layer.get("source-layer") != source_layer:
            continue
        if not _style_layer_active_at_zoom(layer, camera_zoom):
            continue
        if _mapbox_filter_matches(layer.get("filter"), properties):
            yield layer


def _style_layer_match_summary(
    features: Sequence[Mapping[str, object]],
    *,
    bounds: Mapping[str, float],
    crop_area: float,
    source_layer: str,
    style_layers: Sequence[Mapping[str, object]],
    camera_zoom: float,
) -> dict[str, dict[str, object]]:
    matches: dict[str, dict[str, object]] = {}
    for feature in features:
        area = _feature_bbox_overlap_area(feature, bounds)
        for layer in _matching_style_layers(
            feature=feature,
            source_layer=source_layer,
            style_layers=style_layers,
            camera_zoom=camera_zoom,
        ):
            layer_id = str(layer.get("id") or MISSING_VALUE)
            match = matches.setdefault(
                layer_id,
                {
                    "layer": layer_id,
                    "type": layer.get("type"),
                    "feature_count": 0,
                    "overlap_bbox_area": 0.0,
                    "paint": _style_layer_paint_summary(layer),
                },
            )
            match["feature_count"] = int(match["feature_count"]) + 1
            match["overlap_bbox_area"] = float(match["overlap_bbox_area"]) + area
    return {
        layer_id: {
            **match,
            "overlap_bbox_area": _rounded_float(float(match["overlap_bbox_area"])),
            "crop_coverage_ratio": _rounded_float(float(match["overlap_bbox_area"]) / crop_area)
            if crop_area
            else 0.0,
        }
        for layer_id, match in sorted(
            matches.items(),
            key=lambda item: (-float(item[1]["overlap_bbox_area"]), str(item[0])),
        )[:MAX_COUNT_VALUES]
    }


def _style_layer_filter_property_summary(
    features: Sequence[Mapping[str, object]],
    *,
    bounds: Mapping[str, float],
    crop_area: float,
    source_layer: str,
    style_layers: Sequence[Mapping[str, object]],
    camera_zoom: float,
) -> dict[str, dict[str, object]]:
    summaries: dict[str, dict[str, object]] = {}
    for layer in style_layers:
        if layer.get("source-layer") != source_layer or not _style_layer_active_at_zoom(layer, camera_zoom):
            continue
        properties = _source_filter_property_names(layer.get("filter"))
        if not properties:
            continue
        layer_id = str(layer.get("id") or MISSING_VALUE)
        missing_counts = _missing_filter_property_counts(features, properties)
        missing_total = sum(missing_counts.values())
        if missing_total <= 0:
            continue
        candidate_missing_counts, candidate_property_counts, candidate_property_areas = (
            _candidate_missing_filter_property_summary(
                features,
                properties,
                layer.get("filter"),
                bounds=bounds,
                crop_area=crop_area,
                camera_zoom=camera_zoom,
            )
        )
        candidate_missing_total = sum(candidate_missing_counts.values())
        summaries[layer_id] = {
            "layer": layer_id,
            "filter_properties": properties,
            "missing_feature_counts": missing_counts,
            "missing_feature_total": missing_total,
            "candidate_missing_feature_counts": candidate_missing_counts,
            "candidate_missing_feature_total": candidate_missing_total,
            "candidate_property_counts": candidate_property_counts,
            "candidate_property_overlap_areas": candidate_property_areas,
            "overlap_feature_count": len(features),
            "matched_feature_count": sum(
                1
                for feature in features
                if _mapbox_filter_matches(
                    layer.get("filter"),
                    _feature_context_properties(feature, camera_zoom=camera_zoom),
                )
            ),
        }
    return dict(
        sorted(
            summaries.items(),
            key=lambda item: (
                -int(item[1]["candidate_missing_feature_total"]),
                -int(item[1]["missing_feature_total"]),
                -int(item[1]["overlap_feature_count"]),
                str(item[0]),
            ),
        )[:MAX_COUNT_VALUES]
    )


def source_layer_overlap_record(
    *,
    decoded_tiles: Sequence[Mapping[str, object]],
    bounds: Mapping[str, float],
    source_layer: str,
    style_layers: Sequence[Mapping[str, object]] = (),
    camera_zoom: float = 0.0,
) -> dict[str, object]:
    tile_features = [
        feature
        for decoded_tile in decoded_tiles
        for feature in _decoded_layer_features(decoded_tile, source_layer)
    ]
    overlapping_features = [
        feature for feature in tile_features if bbox_overlaps_lon_lat_bounds(feature_lon_lat_bbox(feature), bounds)
    ]
    crop_area = lon_lat_bounds_area(bounds)
    overlap_area = sum(_feature_bbox_overlap_area(feature, bounds) for feature in overlapping_features)
    return {
        "source_layer": source_layer,
        "tile_feature_count": len(tile_features),
        "overlap_feature_count": len(overlapping_features),
        "crop_bbox_area": _rounded_float(crop_area),
        "overlap_bbox_area": _rounded_float(overlap_area),
        "bbox_crop_coverage_ratio": _rounded_float(overlap_area / crop_area) if crop_area else 0.0,
        "property_counts": _property_count_summary(overlapping_features),
        "property_overlap_areas": _property_area_summary(
            overlapping_features,
            bounds=bounds,
            crop_area=crop_area,
        ),
        "qgis_style_layer_matches": _style_layer_match_summary(
            overlapping_features,
            bounds=bounds,
            crop_area=crop_area,
            source_layer=source_layer,
            style_layers=style_layers,
            camera_zoom=camera_zoom,
        ),
        "qgis_filter_property_requirements": _style_layer_filter_property_summary(
            overlapping_features,
            bounds=bounds,
            crop_area=crop_area,
            source_layer=source_layer,
            style_layers=style_layers,
            camera_zoom=camera_zoom,
        ),
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


def _iter_property_area_values(record: Mapping[str, object]) -> Iterator[tuple[str, str, float]]:
    property_overlap_areas = record.get("property_overlap_areas")
    if not isinstance(property_overlap_areas, dict):
        return
    for key, value_areas in property_overlap_areas.items():
        if not isinstance(value_areas, dict):
            continue
        for value, area_record in value_areas.items():
            if not isinstance(area_record, dict):
                continue
            overlap_area = area_record.get("overlap_bbox_area")
            if isinstance(overlap_area, (int, float)):
                yield str(key), str(value), float(overlap_area)


def _combined_property_areas(
    records: Sequence[Mapping[str, object]],
    *,
    crop_area: float,
) -> dict[str, dict[str, dict[str, float]]]:
    property_areas: dict[str, Counter[str]] = {}
    for record in records:
        for key, value, overlap_area in _iter_property_area_values(record):
            property_areas.setdefault(key, Counter()).update({value: overlap_area})
    return {
        key: {
            value: {
                "overlap_bbox_area": _rounded_float(area),
                "crop_coverage_ratio": _rounded_float(area / crop_area) if crop_area else 0.0,
            }
            for value, area in counter.most_common(MAX_COUNT_VALUES)
        }
        for key, counter in property_areas.items()
        if counter
    }


def _iter_style_layer_match_values(record: Mapping[str, object]) -> Iterator[tuple[str, int, float, object, object]]:
    style_layer_matches = record.get("qgis_style_layer_matches")
    if not isinstance(style_layer_matches, dict):
        return
    for layer_id, match in style_layer_matches.items():
        if not isinstance(match, dict):
            continue
        feature_count = match.get("feature_count")
        overlap_area = match.get("overlap_bbox_area")
        if isinstance(feature_count, int) and isinstance(overlap_area, (int, float)):
            yield str(layer_id), feature_count, float(overlap_area), match.get("type"), match.get("paint")


def _combined_style_layer_matches(
    records: Sequence[Mapping[str, object]],
    *,
    crop_area: float,
) -> dict[str, dict[str, object]]:
    matches: dict[str, dict[str, object]] = {}
    for record in records:
        for layer_id, feature_count, overlap_area, layer_type, paint in _iter_style_layer_match_values(record):
            match = matches.setdefault(
                layer_id,
                {
                    "layer": layer_id,
                    "type": layer_type,
                    "feature_count": 0,
                    "overlap_bbox_area": 0.0,
                    "paint": paint if isinstance(paint, dict) else {},
                },
            )
            match["feature_count"] = int(match["feature_count"]) + feature_count
            match["overlap_bbox_area"] = float(match["overlap_bbox_area"]) + overlap_area
    return {
        layer_id: {
            **match,
            "overlap_bbox_area": _rounded_float(float(match["overlap_bbox_area"])),
            "crop_coverage_ratio": _rounded_float(float(match["overlap_bbox_area"]) / crop_area)
            if crop_area
            else 0.0,
        }
        for layer_id, match in sorted(
            matches.items(),
            key=lambda item: (-float(item[1]["overlap_bbox_area"]), str(item[0])),
        )[:MAX_COUNT_VALUES]
    }


def _iter_filter_property_requirement_values(
    record: Mapping[str, object],
) -> Iterator[
    tuple[
        str,
        int,
        int,
        int,
        int,
        Mapping[str, object],
        Mapping[str, object],
        Mapping[str, object],
        Mapping[str, object],
        Sequence[object],
    ]
]:
    requirements = record.get("qgis_filter_property_requirements")
    if not isinstance(requirements, dict):
        return
    for layer_id, requirement in requirements.items():
        if not isinstance(requirement, dict):
            continue
        missing_total = requirement.get("missing_feature_total")
        overlap_count = requirement.get("overlap_feature_count")
        matched_count = requirement.get("matched_feature_count")
        candidate_missing_total = requirement.get("candidate_missing_feature_total", 0)
        missing_counts = requirement.get("missing_feature_counts")
        candidate_missing_counts = requirement.get("candidate_missing_feature_counts", {})
        candidate_property_counts = requirement.get("candidate_property_counts", {})
        candidate_property_areas = requirement.get("candidate_property_overlap_areas", {})
        properties = requirement.get("filter_properties")
        if (
            isinstance(missing_total, int)
            and isinstance(overlap_count, int)
            and isinstance(matched_count, int)
            and isinstance(candidate_missing_total, int)
            and isinstance(missing_counts, Mapping)
            and isinstance(candidate_missing_counts, Mapping)
            and isinstance(candidate_property_counts, Mapping)
            and isinstance(candidate_property_areas, Mapping)
            and isinstance(properties, Sequence)
            and not isinstance(properties, (str, bytes))
        ):
            yield (
                str(layer_id),
                missing_total,
                overlap_count,
                matched_count,
                candidate_missing_total,
                missing_counts,
                candidate_missing_counts,
                candidate_property_counts,
                candidate_property_areas,
                properties,
            )


def _new_combined_filter_property_requirement(
    layer_id: str,
    properties: Sequence[object],
) -> dict[str, object]:
    return {
        "layer": layer_id,
        "filter_properties": sorted(str(property_name) for property_name in properties),
        "missing_feature_counts": Counter(),
        "missing_feature_total": 0,
        "candidate_missing_feature_counts": Counter(),
        "candidate_missing_feature_total": 0,
        "candidate_property_counts": {},
        "candidate_property_overlap_areas": {},
        "overlap_feature_count": 0,
        "matched_feature_count": 0,
    }


def _update_counter_from_mapping(counter: object, values: Mapping[str, object]) -> None:
    if isinstance(counter, Counter):
        counter.update({str(name): int(count) for name, count in values.items()})


def _update_candidate_property_value_counts(
    combined_counts: object,
    candidate_property_counts: Mapping[str, object],
) -> None:
    if not isinstance(combined_counts, dict):
        return
    for key, value_counts in candidate_property_counts.items():
        if not isinstance(value_counts, Mapping):
            continue
        counter = combined_counts.setdefault(str(key), Counter())
        if isinstance(counter, Counter):
            counter.update({str(value): int(count) for value, count in value_counts.items()})


def _update_candidate_property_overlap_areas(
    combined_areas: object,
    candidate_property_areas: Mapping[str, object],
) -> None:
    if not isinstance(combined_areas, dict):
        return
    for key, value_areas in candidate_property_areas.items():
        if not isinstance(value_areas, Mapping):
            continue
        counter = combined_areas.setdefault(str(key), Counter())
        if not isinstance(counter, Counter):
            continue
        for value, area_record in value_areas.items():
            if not isinstance(area_record, Mapping):
                continue
            overlap_area = area_record.get("overlap_bbox_area")
            if isinstance(overlap_area, (int, float)) and not isinstance(overlap_area, bool):
                counter.update({str(value): float(overlap_area)})


def _update_combined_filter_property_requirement(
    requirement: dict[str, object],
    *,
    missing_total: int,
    overlap_count: int,
    matched_count: int,
    candidate_missing_total: int,
    missing_counts: Mapping[str, object],
    candidate_missing_counts: Mapping[str, object],
    candidate_property_counts: Mapping[str, object],
    candidate_property_areas: Mapping[str, object],
) -> None:
    _update_counter_from_mapping(requirement["missing_feature_counts"], missing_counts)
    _update_counter_from_mapping(requirement["candidate_missing_feature_counts"], candidate_missing_counts)
    _update_candidate_property_value_counts(requirement["candidate_property_counts"], candidate_property_counts)
    _update_candidate_property_overlap_areas(
        requirement["candidate_property_overlap_areas"],
        candidate_property_areas,
    )
    requirement["missing_feature_total"] = int(requirement["missing_feature_total"]) + missing_total
    requirement["candidate_missing_feature_total"] = (
        int(requirement["candidate_missing_feature_total"]) + candidate_missing_total
    )
    requirement["overlap_feature_count"] = int(requirement["overlap_feature_count"]) + overlap_count
    requirement["matched_feature_count"] = int(requirement["matched_feature_count"]) + matched_count


def _limited_candidate_missing_counts(
    requirement: Mapping[str, object],
    missing_counts: Mapping[str, object],
) -> dict[str, int]:
    candidate_counter = requirement["candidate_missing_feature_counts"]
    if not isinstance(candidate_counter, Counter):
        return {}
    return {
        property_name: int(candidate_counter[property_name])
        for property_name in missing_counts
        if int(candidate_counter[property_name]) > 0
    }


def _limited_candidate_property_counts(requirement: Mapping[str, object]) -> dict[str, dict[str, int]]:
    candidate_value_counts = requirement["candidate_property_counts"]
    if not isinstance(candidate_value_counts, dict):
        return {}
    return {
        key: dict(counter.most_common(MAX_COUNT_VALUES))
        for key, counter in candidate_value_counts.items()
        if isinstance(counter, Counter) and counter
    }


def _limited_candidate_property_overlap_areas(
    requirement: Mapping[str, object],
    *,
    crop_area: float,
) -> dict[str, dict[str, dict[str, float]]]:
    candidate_value_areas = requirement["candidate_property_overlap_areas"]
    if not isinstance(candidate_value_areas, dict):
        return {}
    return _property_area_counter_summary(
        {
            str(key): counter
            for key, counter in candidate_value_areas.items()
            if isinstance(counter, Counter)
        },
        crop_area=crop_area,
    )


def _combined_filter_property_requirement_record(
    requirement: dict[str, object],
    *,
    crop_area: float,
) -> dict[str, object]:
    missing_counts = (
        dict(requirement["missing_feature_counts"].most_common(MAX_COUNT_VALUES))
        if isinstance(requirement["missing_feature_counts"], Counter)
        else {}
    )
    return {
        **requirement,
        "missing_feature_counts": missing_counts,
        "candidate_missing_feature_counts": _limited_candidate_missing_counts(requirement, missing_counts),
        "candidate_property_counts": _limited_candidate_property_counts(requirement),
        "candidate_property_overlap_areas": _limited_candidate_property_overlap_areas(
            requirement,
            crop_area=crop_area,
        ),
    }


def _combined_filter_property_requirements(
    records: Sequence[Mapping[str, object]],
    *,
    crop_area: float = 0.0,
) -> dict[str, dict[str, object]]:
    requirements: dict[str, dict[str, object]] = {}
    for record in records:
        for (
            layer_id,
            missing_total,
            overlap_count,
            matched_count,
            candidate_missing_total,
            missing_counts,
            candidate_missing_counts,
            candidate_property_counts,
            candidate_property_areas,
            properties,
        ) in (
            _iter_filter_property_requirement_values(record)
        ):
            requirement = requirements.setdefault(
                layer_id,
                _new_combined_filter_property_requirement(layer_id, properties),
            )
            _update_combined_filter_property_requirement(
                requirement,
                missing_total=missing_total,
                overlap_count=overlap_count,
                matched_count=matched_count,
                candidate_missing_total=candidate_missing_total,
                missing_counts=missing_counts,
                candidate_missing_counts=candidate_missing_counts,
                candidate_property_counts=candidate_property_counts,
                candidate_property_areas=candidate_property_areas,
            )
    combined: dict[str, dict[str, object]] = {}
    for layer_id, requirement in sorted(
        requirements.items(),
        key=lambda item: (
            -int(item[1]["candidate_missing_feature_total"]),
            -int(item[1]["missing_feature_total"]),
            -int(item[1]["overlap_feature_count"]),
            str(item[0]),
        ),
    )[:MAX_COUNT_VALUES]:
        combined[layer_id] = _combined_filter_property_requirement_record(requirement, crop_area=crop_area)
    return combined


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
    crop_area = sum(float(record.get("crop_bbox_area") or 0.0) for record in records)
    overlap_area = sum(float(record.get("overlap_bbox_area") or 0.0) for record in records)
    return {
        "source_layer": source_layer,
        "tile_feature_count": sum(int(record.get("tile_feature_count") or 0) for record in records),
        "overlap_feature_count": sum(int(record.get("overlap_feature_count") or 0) for record in records),
        "crop_bbox_area": _rounded_float(crop_area),
        "overlap_bbox_area": _rounded_float(overlap_area),
        "bbox_crop_coverage_ratio": _rounded_float(overlap_area / crop_area) if crop_area else 0.0,
        "property_counts": _combined_property_counts(records),
        "property_overlap_areas": _combined_property_areas(records, crop_area=crop_area),
        "qgis_style_layer_matches": _combined_style_layer_matches(records, crop_area=crop_area),
        "qgis_filter_property_requirements": _combined_filter_property_requirements(records, crop_area=crop_area),
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
    style_layers = [
        layer for layer in style_definition.get("layers", []) if isinstance(layer, dict)
    ]
    tileset_ids = extract_mapbox_vector_source_ids(style_definition)
    tile_url_template = build_mapbox_vector_tiles_url(
        token,
        config.style_owner,
        config.style_id,
        tileset_ids=tileset_ids,
    )
    tile_zoom = config.tile_zoom if config.tile_zoom is not None else recommended_tile_zoom(float(camera["zoom"]))
    camera_zoom = float(camera["zoom"])
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
                        style_layers=style_layers,
                        camera_zoom=camera_zoom,
                    )
                    for source_layer in config.source_layers
                ],
            }
        )
    generated = config.now or dt.datetime.now(dt.timezone.utc)
    return {
        "generated": generated.astimezone(dt.timezone.utc).isoformat(),
        "camera": config.camera_name,
        "camera_zoom": camera_zoom,
        "tile_zoom": tile_zoom,
        "decoded_tile_count": len(decoded_tile_cache),
        "tileset_ids": tileset_ids,
        "visual_crop_json": _repo_relative_path(visual_crop_path),
        "comparison_summary_json": _repo_relative_path(comparison_summary_path),
        "comparison_summary_run": _comparison_summary_run_metadata(visual_crop_report),
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


def _format_coverage(value: object) -> str:
    return f"{float(value):.3f}" if isinstance(value, (int, float)) else "-"


def _format_property_coverage(record: Mapping[str, object], key: str) -> str:
    property_areas = record.get("property_overlap_areas")
    if not isinstance(property_areas, dict):
        return "-"
    value_areas = property_areas.get(key)
    if not isinstance(value_areas, dict) or not value_areas:
        return "-"
    return ", ".join(
        f"{value}={_format_coverage(area_record.get('crop_coverage_ratio', 0.0))}"
        for value, area_record in value_areas.items()
        if isinstance(area_record, dict)
    )


def _markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _format_style_layer_matches(record: Mapping[str, object]) -> str:
    matches = record.get("qgis_style_layer_matches")
    if not isinstance(matches, dict) or not matches:
        return "-"
    return ", ".join(
        f"{layer_id}={float(match.get('crop_coverage_ratio', 0.0)):.3f}"
        for layer_id, match in matches.items()
        if isinstance(match, dict)
    )


def _format_style_layer_paint(match: Mapping[str, object]) -> str:
    paint = match.get("paint")
    if not isinstance(paint, dict) or not paint:
        return "-"
    return "<br>".join(
        f"{key}={_markdown_cell(json.dumps(value, ensure_ascii=False, separators=(',', ':')))}"
        for key, value in paint.items()
    )


def _style_layer_paint_rows(source_layer_records: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for record in source_layer_records:
        source_layer = str(record.get("source_layer") or MISSING_VALUE)
        matches = record.get("qgis_style_layer_matches")
        if not isinstance(matches, dict):
            continue
        for layer_id, match in matches.items():
            if not isinstance(match, dict):
                continue
            rows.append(
                {
                    "source_layer": source_layer,
                    "layer": str(layer_id),
                    "type": str(match.get("type") or MISSING_VALUE),
                    "feature_count": int(match.get("feature_count") or 0),
                    "coverage": float(match.get("crop_coverage_ratio") or 0.0),
                    "paint": _format_style_layer_paint(match),
                }
            )
    return sorted(
        rows,
        key=lambda row: (str(row["source_layer"]), -float(row["coverage"]), str(row["layer"])),
    )


def _markdown_style_layer_paint_table(source_layer_records: Sequence[Mapping[str, object]]) -> list[str]:
    rows = _style_layer_paint_rows(source_layer_records)
    if not rows:
        return []
    lines = [
        "",
        "## QGIS Style-Layer Paint Coverage",
        "",
        (
            "Shows camera-zoom-active QGIS-preprocessed style layers that matched overlapping source "
            "features, with their literal paint controls, so crop attribution can distinguish owner "
            "coverage from color/opacity composition."
        ),
        "",
        "| Source layer | QGIS style layer | Type | Features | Bbox crop coverage | Paint |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ]
    lines.extend(
        "| `{source_layer}` | `{layer}` | `{type}` | {features} | {coverage:.3f} | {paint} |".format(
            source_layer=_markdown_cell(row["source_layer"]),
            layer=_markdown_cell(row["layer"]),
            type=_markdown_cell(row["type"]),
            features=row["feature_count"],
            coverage=float(row["coverage"]),
            paint=row["paint"],
        )
        for row in rows
    )
    return lines


def _format_candidate_property_counts(requirement: Mapping[str, object]) -> str:
    counts = requirement.get("candidate_property_counts")
    if not isinstance(counts, dict) or not counts:
        return ""
    formatted: list[str] = []
    for key in PROPERTY_COUNT_KEYS:
        value_counts = counts.get(key)
        if not isinstance(value_counts, dict) or not value_counts:
            continue
        values = ", ".join(
            f"{value}={int(count)}" for value, count in list(value_counts.items())[:MAX_COUNT_VALUES]
        )
        formatted.append(f"{key}: {values}")
    return "; ".join(formatted)


def _format_candidate_property_overlap_areas(requirement: Mapping[str, object]) -> str:
    areas = requirement.get("candidate_property_overlap_areas")
    if not isinstance(areas, dict) or not areas:
        return ""
    formatted: list[str] = []
    for key in PROPERTY_COUNT_KEYS:
        value_areas = areas.get(key)
        if not isinstance(value_areas, dict) or not value_areas:
            continue
        values = ", ".join(
            f"{value}={float(area_record.get('crop_coverage_ratio', 0.0)):.3f}"
            for value, area_record in list(value_areas.items())[:MAX_COUNT_VALUES]
            if isinstance(area_record, dict)
        )
        if values:
            formatted.append(f"{key}: {values}")
    return "; ".join(formatted)


def _format_filter_property_requirement(layer_id: str, requirement: Mapping[str, object]) -> str | None:
    overlap_count = int(requirement.get("overlap_feature_count") or 0)
    missing_counts = requirement.get("missing_feature_counts")
    if not isinstance(missing_counts, dict) or not missing_counts:
        return None
    candidate_counts = requirement.get("candidate_missing_feature_counts")
    candidate_counts = candidate_counts if isinstance(candidate_counts, dict) else {}
    counts = ", ".join(
        f"{property_name}={int(count)}/{overlap_count} candidate={int(candidate_counts.get(property_name) or 0)}"
        for property_name, count in missing_counts.items()
    )
    candidate_properties = _format_candidate_property_counts(requirement)
    candidate_suffix = f" candidates [{candidate_properties}]" if candidate_properties else ""
    candidate_areas = _format_candidate_property_overlap_areas(requirement)
    candidate_area_suffix = f" candidate coverage [{candidate_areas}]" if candidate_areas else ""
    return (
        f"{layer_id}: {counts}{candidate_suffix}{candidate_area_suffix} "
        f"(matched={int(requirement.get('matched_feature_count') or 0)})"
    )


def _format_filter_property_requirements(record: Mapping[str, object]) -> str:
    requirements = record.get("qgis_filter_property_requirements")
    if not isinstance(requirements, dict) or not requirements:
        return "-"
    formatted: list[str] = []
    for layer_id, requirement in requirements.items():
        if not isinstance(requirement, dict):
            continue
        formatted_requirement = _format_filter_property_requirement(str(layer_id), requirement)
        if formatted_requirement:
            formatted.append(formatted_requirement)
    return "; ".join(formatted) if formatted else "-"


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


def _comparison_summary_run_metadata(report: Mapping[str, object]) -> dict[str, object]:
    run = report.get("comparison_summary_run")
    if not isinstance(run, Mapping):
        return {}
    qgis_runtimes = run.get("qgis_runtimes")
    if not isinstance(qgis_runtimes, list):
        return {}
    runtime_labels = [str(runtime) for runtime in qgis_runtimes if runtime not in (None, "")]
    return {"qgis_runtimes": runtime_labels} if runtime_labels else {}


def _summary_qgis_runtime_label(report: Mapping[str, object]) -> str:
    run = report.get("comparison_summary_run")
    if not isinstance(run, Mapping):
        return ""
    qgis_runtimes = run.get("qgis_runtimes")
    if not isinstance(qgis_runtimes, list):
        return ""
    return " | ".join(str(runtime) for runtime in qgis_runtimes if runtime not in (None, ""))


def _joined_read_labels(labels: Iterable[str]) -> str:
    values = [label for label in labels if label]
    return ", ".join(values) if values else "-"


def _feature_count_label(value: object) -> str:
    count = int(value or 0)
    return f"{count} {'feature' if count == 1 else 'features'}"


def _source_overlap_read_labels(report: Mapping[str, object]) -> list[str]:
    records = [
        record
        for record in report.get("combined_source_layers", [])
        if isinstance(record, Mapping)
        and (
            float(record.get("bbox_crop_coverage_ratio") or 0.0) > 0
            or int(record.get("overlap_feature_count") or 0) > 0
        )
    ]
    records.sort(
        key=lambda record: (
            -float(record.get("bbox_crop_coverage_ratio") or 0.0),
            -int(record.get("overlap_feature_count") or 0),
            str(record.get("source_layer") or MISSING_VALUE),
        )
    )
    return [
        (
            f"{record.get('source_layer') or MISSING_VALUE}="
            f"{float(record.get('bbox_crop_coverage_ratio') or 0.0):.3f} "
            f"({_feature_count_label(record.get('overlap_feature_count'))})"
        )
        for record in records[:5]
    ]


def _style_layer_coverage_read_labels(report: Mapping[str, object]) -> list[str]:
    rows = _style_layer_paint_rows(
        [
            record
            for record in report.get("combined_source_layers", [])
            if isinstance(record, Mapping)
        ]
    )
    return [
        f"{row['layer']}={float(row['coverage']):.3f} ({row['source_layer']})"
        for row in sorted(rows, key=lambda row: (-float(row["coverage"]), str(row["layer"])))[:5]
    ]


def _zero_overlap_source_layer_labels(report: Mapping[str, object]) -> list[str]:
    return [
        str(record.get("source_layer") or MISSING_VALUE)
        for record in report.get("combined_source_layers", [])
        if isinstance(record, Mapping) and int(record.get("overlap_feature_count") or 0) == 0
    ][:5]


def _markdown_table_row(cells: Iterable[object]) -> str:
    return "| " + " | ".join(_markdown_cell(cell) for cell in cells) + " |"


def _summary_read_lines(report: Mapping[str, object]) -> list[str]:
    rows: list[list[str]] = []
    qgis_runtime_label = _summary_qgis_runtime_label(report)
    if qgis_runtime_label:
        rows.append(["QGIS runtimes", qgis_runtime_label])
    source_labels = _source_overlap_read_labels(report)
    if source_labels:
        rows.append(["Top source overlaps", _joined_read_labels(source_labels)])
    style_labels = _style_layer_coverage_read_labels(report)
    if style_labels:
        rows.append(["Top QGIS style-layer coverage", _joined_read_labels(style_labels)])
    zero_overlap_labels = _zero_overlap_source_layer_labels(report)
    if zero_overlap_labels:
        rows.append(["Zero-overlap source layers", _joined_read_labels(zero_overlap_labels)])
    if not rows:
        return []
    lines = [
        "",
        "## Report read",
        "",
        (
            "Condenses the runtime, strongest source-layer bbox overlaps, and strongest "
            "QGIS style-layer coverage before the full attribution tables."
        ),
        "",
        "| Signal | Read |",
        "| --- | --- |",
    ]
    lines.extend(_markdown_table_row(row) for row in rows)
    return lines


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
        (
            "Bbox coverage is a summed upper-bound attribution aid, not pixel ownership; ratios can exceed "
            "1.0 when feature bboxes overlap or line-feature bboxes span the same crop area."
        ),
        (
            "QGIS style-layer coverage evaluates camera-zoom-active filters from the QGIS-preprocessed "
            "style against the overlapping source features; it is still bbox attribution, not rendered pixels."
        ),
        (
            "QGIS filter missing props reports active style-layer filter properties that are absent from "
            "overlapping source features as missing/overlap feature counts. Candidate counts only include "
            "features that do not already match the full filter but still match the layer's other available "
            "filter predicates after missing-property checks are removed, with candidate bbox coverage ratios "
            "attributed by feature-property value."
        ),
    ]
    lines.extend(_summary_read_lines(report))
    lines.extend([
        "",
        "## Combined overlap by source layer",
        "",
        "| Source layer | Tile features | Overlap features | Bbox crop coverage | Classes | Class coverage | QGIS style-layer coverage | QGIS filter missing props | Types | Index values | Elevation range |",
        "| --- | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- | --- |",
    ])
    for record in report.get("combined_source_layers", []):
        if not isinstance(record, dict):
            continue
        lines.append(
            "| `{source_layer}` | {tile_features} | {overlap_features} | {coverage} | {classes} | {class_coverage} | {style_matches} | {filter_missing} | {types} | {indices} | {ele} |".format(
                source_layer=record["source_layer"],
                tile_features=record["tile_feature_count"],
                overlap_features=record["overlap_feature_count"],
                coverage=_format_coverage(record.get("bbox_crop_coverage_ratio")),
                classes=_format_property_counts(record, "class"),
                class_coverage=_format_property_coverage(record, "class"),
                style_matches=_format_style_layer_matches(record),
                filter_missing=_format_filter_property_requirements(record),
                types=_format_property_counts(record, "type"),
                indices=_format_property_counts(record, "index"),
                ele=_format_ele_range(record.get("ele_range")),
            )
        )
    lines.extend(
        _markdown_style_layer_paint_table(
            [
                record
                for record in report.get("combined_source_layers", [])
                if isinstance(record, dict)
            ]
        )
    )
    lines.extend(
        [
            "",
            "## Per-crop overlap",
            "",
            "| Crop | Box | Lon/lat bounds | Tiles | Source layer | Tile features | Overlap features | Bbox crop coverage | Classes | Class coverage | QGIS style-layer coverage | QGIS filter missing props | Types | Index values | Elevation range |",
            "| ---: | --- | --- | ---: | --- | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for crop in report.get("crops", []):
        if not isinstance(crop, dict):
            continue
        for source_layer_record in crop.get("source_layers", []):
            if not isinstance(source_layer_record, dict):
                continue
            lines.append(
                "| {crop} | {box} | {bounds} | {tile_count} | `{source_layer}` | {tile_features} | {overlap_features} | {coverage} | {classes} | {class_coverage} | {style_matches} | {filter_missing} | {types} | {indices} | {ele} |".format(
                    crop=crop["index"],
                    box=crop["box"],
                    bounds=_format_bounds(crop["lon_lat_bounds"]),
                    tile_count=len(crop.get("tiles", [])),
                    source_layer=source_layer_record["source_layer"],
                    tile_features=source_layer_record["tile_feature_count"],
                    overlap_features=source_layer_record["overlap_feature_count"],
                    coverage=_format_coverage(source_layer_record.get("bbox_crop_coverage_ratio")),
                    classes=_format_property_counts(source_layer_record, "class"),
                    class_coverage=_format_property_coverage(source_layer_record, "class"),
                    style_matches=_format_style_layer_matches(source_layer_record),
                    filter_missing=_format_filter_property_requirements(source_layer_record),
                    types=_format_property_counts(source_layer_record, "type"),
                    indices=_format_property_counts(source_layer_record, "index"),
                    ele=_format_ele_range(source_layer_record.get("ele_range")),
                )
            )
    return "\n".join(lines) + "\n"


def _list_of_mappings(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _int_value(value: object) -> int:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _float_value(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _report_qgis_runtimes(report: Mapping[str, object]) -> list[str]:
    run = report.get("comparison_summary_run")
    if not isinstance(run, Mapping):
        return []
    runtimes = run.get("qgis_runtimes")
    if not isinstance(runtimes, list):
        return []
    return [str(runtime) for runtime in runtimes if runtime not in (None, "")]


def _update_class_coverage(counter: Counter[str], record: Mapping[str, object]) -> None:
    property_areas = record.get("property_overlap_areas")
    if not isinstance(property_areas, Mapping):
        return
    class_areas = property_areas.get("class")
    if not isinstance(class_areas, Mapping):
        return
    for class_name, area_record in class_areas.items():
        if not isinstance(area_record, Mapping):
            continue
        coverage = _float_value(area_record.get("crop_coverage_ratio"))
        if coverage > 0.0:
            counter.update({str(class_name): coverage})


def _class_coverage_row(counter: Counter[str]) -> dict[str, float]:
    return {
        class_name: _rounded_float(coverage)
        for class_name, coverage in counter.most_common(MAX_COUNT_VALUES)
    }


def _update_source_layer_total(
    total: _AggregateSourceLayerTotal,
    *,
    record: Mapping[str, object],
    camera_name: str,
    input_report: str,
) -> None:
    coverage = _float_value(record.get("bbox_crop_coverage_ratio"))
    overlap_feature_count = _int_value(record.get("overlap_feature_count"))
    total.reports.add(input_report)
    total.cameras.add(camera_name)
    _update_class_coverage(total.class_coverage, record)
    total.overlap_feature_count += overlap_feature_count
    total.coverage_sum += coverage
    total.max_coverage = max(total.max_coverage, coverage)
    if overlap_feature_count == 0:
        total.zero_overlap_reports += 1


def _update_style_layer_totals(
    totals: dict[tuple[str, str], _AggregateStyleLayerTotal],
    *,
    record: Mapping[str, object],
    camera_name: str,
    input_report: str,
) -> None:
    source_layer = str(record.get("source_layer") or MISSING_VALUE)
    matches = record.get("qgis_style_layer_matches")
    if not isinstance(matches, Mapping):
        return
    for layer_id, match in matches.items():
        if not isinstance(match, Mapping):
            continue
        layer_key = str(layer_id)
        total = totals.setdefault(
            (source_layer, layer_key),
            _AggregateStyleLayerTotal(
                source_layer=source_layer,
                layer=layer_key,
                layer_type=str(match.get("type") or MISSING_VALUE),
            ),
        )
        coverage = _float_value(match.get("crop_coverage_ratio"))
        total.reports.add(input_report)
        total.cameras.add(camera_name)
        total.feature_count += _int_value(match.get("feature_count"))
        total.coverage_sum += coverage
        total.max_coverage = max(total.max_coverage, coverage)


def _camera_source_row(
    *,
    record: Mapping[str, object],
    camera_name: str,
    camera_zoom: object,
    input_report: str,
) -> dict[str, object]:
    return {
        "input_report": input_report,
        "camera": camera_name,
        "camera_zoom": _float_value(camera_zoom),
        "source_layer": str(record.get("source_layer") or MISSING_VALUE),
        "overlap_feature_count": _int_value(record.get("overlap_feature_count")),
        "bbox_crop_coverage_ratio": _float_value(record.get("bbox_crop_coverage_ratio")),
        "classes": _format_property_counts(record, "class"),
        "class_coverage": _format_property_coverage(record, "class"),
        "qgis_style_layer_coverage": _format_style_layer_matches(record),
    }


def _source_layer_total_row(total: _AggregateSourceLayerTotal) -> dict[str, object]:
    return {
        "source_layer": total.source_layer,
        "report_count": len(total.reports),
        "camera_count": len(total.cameras),
        "overlap_feature_count": total.overlap_feature_count,
        "coverage_sum": _rounded_float(total.coverage_sum),
        "max_bbox_crop_coverage_ratio": _rounded_float(total.max_coverage),
        "zero_overlap_reports": total.zero_overlap_reports,
        "class_coverage": _class_coverage_row(total.class_coverage),
        "cameras": sorted(total.cameras),
    }


def _style_layer_total_row(total: _AggregateStyleLayerTotal) -> dict[str, object]:
    return {
        "source_layer": total.source_layer,
        "layer": total.layer,
        "type": total.layer_type,
        "report_count": len(total.reports),
        "camera_count": len(total.cameras),
        "feature_count": total.feature_count,
        "coverage_sum": _rounded_float(total.coverage_sum),
        "max_coverage": _rounded_float(total.max_coverage),
        "cameras": sorted(total.cameras),
    }


def _aggregate_one_source_crop_report(
    report_path: Path,
    *,
    source_totals: dict[str, _AggregateSourceLayerTotal],
    style_totals: dict[tuple[str, str], _AggregateStyleLayerTotal],
    camera_rows: list[dict[str, object]],
    qgis_runtimes: set[str],
) -> str:
    resolved_path = report_path.expanduser().resolve()
    report = load_json_object(resolved_path)
    input_report = _repo_relative_path(resolved_path)
    camera_name = str(report.get("camera") or MISSING_VALUE)
    qgis_runtimes.update(_report_qgis_runtimes(report))
    for record in _list_of_mappings(report.get("combined_source_layers")):
        source_layer = str(record.get("source_layer") or MISSING_VALUE)
        total = source_totals.setdefault(source_layer, _AggregateSourceLayerTotal(source_layer=source_layer))
        _update_source_layer_total(
            total,
            record=record,
            camera_name=camera_name,
            input_report=input_report,
        )
        _update_style_layer_totals(
            style_totals,
            record=record,
            camera_name=camera_name,
            input_report=input_report,
        )
        camera_rows.append(
            _camera_source_row(
                record=record,
                camera_name=camera_name,
                camera_zoom=report.get("camera_zoom"),
                input_report=input_report,
            )
        )
    return input_report


def _sorted_source_layer_rows(totals: Mapping[str, _AggregateSourceLayerTotal]) -> list[dict[str, object]]:
    return sorted(
        (_source_layer_total_row(total) for total in totals.values()),
        key=lambda row: (
            -float(row["coverage_sum"]),
            -int(row["overlap_feature_count"]),
            str(row["source_layer"]),
        ),
    )


def _sorted_style_layer_rows(
    totals: Mapping[tuple[str, str], _AggregateStyleLayerTotal],
) -> list[dict[str, object]]:
    return sorted(
        (_style_layer_total_row(total) for total in totals.values()),
        key=lambda row: (
            -float(row["coverage_sum"]),
            -int(row["feature_count"]),
            str(row["source_layer"]),
            str(row["layer"]),
        ),
    )


def _deduplicated_report_paths(report_paths: Sequence[Path]) -> list[Path]:
    unique_paths: list[Path] = []
    seen_paths: set[Path] = set()
    for report_path in report_paths:
        resolved_path = report_path.expanduser().resolve()
        if resolved_path in seen_paths:
            continue
        seen_paths.add(resolved_path)
        unique_paths.append(resolved_path)
    return unique_paths


def build_source_crop_overlap_aggregate_report(
    report_paths: Sequence[Path],
    *,
    now: dt.datetime | None = None,
) -> dict[str, object]:
    if not report_paths:
        raise ValueError("At least one source/crop overlap report is required.")
    deduplicated_report_paths = _deduplicated_report_paths(report_paths)
    source_totals: dict[str, _AggregateSourceLayerTotal] = {}
    style_totals: dict[tuple[str, str], _AggregateStyleLayerTotal] = {}
    camera_rows: list[dict[str, object]] = []
    qgis_runtimes: set[str] = set()
    input_reports = [
        _aggregate_one_source_crop_report(
            report_path,
            source_totals=source_totals,
            style_totals=style_totals,
            camera_rows=camera_rows,
            qgis_runtimes=qgis_runtimes,
        )
        for report_path in deduplicated_report_paths
    ]
    generated = now or dt.datetime.now(dt.timezone.utc)
    return {
        "generated": generated.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
        "input_reports": input_reports,
        "qgis_runtimes": sorted(qgis_runtimes),
        "source_layer_rows": _sorted_source_layer_rows(source_totals),
        "style_layer_rows": _sorted_style_layer_rows(style_totals),
        "camera_source_rows": sorted(
            camera_rows,
            key=lambda row: (
                str(row["camera"]),
                -float(row["bbox_crop_coverage_ratio"]),
                str(row["source_layer"]),
            ),
        ),
    }


def _camera_list_label(value: object) -> str:
    if not isinstance(value, list) or not value:
        return "-"
    return ", ".join(f"`{camera}`" for camera in value)


def _format_class_coverage(value: object) -> str:
    if not isinstance(value, Mapping) or not value:
        return "-"
    return ", ".join(f"{class_name}={_format_coverage(coverage)}" for class_name, coverage in value.items())


def _aggregate_source_layer_row(row: Mapping[str, object]) -> str:
    return _markdown_table_row(
        [
            f"`{row.get('source_layer')}`",
            row.get("report_count"),
            row.get("camera_count"),
            row.get("overlap_feature_count"),
            _format_coverage(row.get("coverage_sum")),
            _format_coverage(row.get("max_bbox_crop_coverage_ratio")),
            row.get("zero_overlap_reports"),
            _format_class_coverage(row.get("class_coverage")),
            _camera_list_label(row.get("cameras")),
        ]
    )


def _aggregate_style_layer_row(row: Mapping[str, object]) -> str:
    return _markdown_table_row(
        [
            f"`{row.get('source_layer')}`",
            f"`{row.get('layer')}`",
            f"`{row.get('type')}`",
            row.get("report_count"),
            row.get("camera_count"),
            row.get("feature_count"),
            _format_coverage(row.get("coverage_sum")),
            _format_coverage(row.get("max_coverage")),
            _camera_list_label(row.get("cameras")),
        ]
    )


def _format_camera_zoom(value: object) -> str:
    return f"{float(value):g}" if isinstance(value, (int, float)) and not isinstance(value, bool) else "-"


def _aggregate_camera_source_row(row: Mapping[str, object]) -> str:
    return _markdown_table_row(
        [
            f"`{row.get('camera')}`",
            _format_camera_zoom(row.get("camera_zoom")),
            f"`{row.get('source_layer')}`",
            row.get("overlap_feature_count"),
            _format_coverage(row.get("bbox_crop_coverage_ratio")),
            row.get("classes") or "-",
            row.get("class_coverage") or "-",
            row.get("qgis_style_layer_coverage") or "-",
        ]
    )


def _aggregate_source_read_labels(rows: Sequence[Mapping[str, object]]) -> list[str]:
    return [
        (
            f"{row.get('source_layer') or MISSING_VALUE}={float(row.get('coverage_sum') or 0.0):.3f} "
            f"({_feature_count_label(row.get('overlap_feature_count'))}, {row.get('camera_count')} cameras)"
        )
        for row in rows
        if float(row.get("coverage_sum") or 0.0) > 0.0 or int(row.get("overlap_feature_count") or 0) > 0
    ][:5]


def _aggregate_style_read_labels(rows: Sequence[Mapping[str, object]]) -> list[str]:
    return [
        (
            f"{row.get('layer') or MISSING_VALUE}={float(row.get('coverage_sum') or 0.0):.3f} "
            f"({row.get('source_layer') or MISSING_VALUE}, {row.get('camera_count')} cameras)"
        )
        for row in rows
        if float(row.get("coverage_sum") or 0.0) > 0.0 or int(row.get("feature_count") or 0) > 0
    ][:5]


def _aggregate_class_read_labels(rows: Sequence[Mapping[str, object]]) -> list[str]:
    labels = []
    for row in rows:
        class_coverage = row.get("class_coverage")
        if not isinstance(class_coverage, Mapping) or not class_coverage:
            continue
        class_labels = ", ".join(
            f"{class_name}={_format_coverage(coverage)}"
            for class_name, coverage in list(class_coverage.items())[:3]
        )
        labels.append(f"{row.get('source_layer') or MISSING_VALUE}: {class_labels}")
    return labels[:5]


def _aggregate_zero_overlap_labels(rows: Sequence[Mapping[str, object]]) -> list[str]:
    return [
        str(row.get("source_layer") or MISSING_VALUE)
        for row in rows
        if int(row.get("report_count") or 0) > 0
        and int(row.get("zero_overlap_reports") or 0) == int(row.get("report_count") or 0)
    ][:5]


def _aggregate_read_lines(
    *,
    source_rows: Sequence[Mapping[str, object]],
    style_rows: Sequence[Mapping[str, object]],
) -> list[str]:
    return [
        "",
        "## Read",
        "",
        f"- Top source-layer bbox coverage sums: {_joined_read_labels(_aggregate_source_read_labels(source_rows))}.",
        f"- Top QGIS style-layer bbox coverage sums: {_joined_read_labels(_aggregate_style_read_labels(style_rows))}.",
        f"- Top source-layer class coverage sums: {_joined_read_labels(_aggregate_class_read_labels(source_rows))}.",
        (
            "- Source layers with zero overlap wherever requested: "
            f"{_joined_read_labels(_aggregate_zero_overlap_labels(source_rows))}."
        ),
        "- Treat aggregate coverage as bbox attribution across reports, not pixel ownership or a production styling recommendation.",
    ]


def render_aggregate_markdown_summary(report: Mapping[str, object]) -> str:
    input_reports = [str(path) for path in report.get("input_reports") or []]
    qgis_runtimes = [str(runtime) for runtime in report.get("qgis_runtimes") or []]
    source_rows = _list_of_mappings(report.get("source_layer_rows"))
    style_rows = _list_of_mappings(report.get("style_layer_rows"))
    camera_rows = _list_of_mappings(report.get("camera_source_rows"))
    lines = [
        "# Mapbox Outdoors source/crop overlap aggregate",
        "",
        f"Generated: `{report.get('generated')}`",
        f"Input reports: `{len(input_reports)}`",
        f"QGIS runtimes: `{', '.join(qgis_runtimes) if qgis_runtimes else QGIS_RUNTIME_NOT_CAPTURED}`",
    ]
    if input_reports:
        lines.extend(["", "Inputs:"])
        lines.extend(f"- `{path}`" for path in input_reports[:20])
        if len(input_reports) > 20:
            lines.append(f"- ... {len(input_reports) - 20} more")
    lines.extend(_aggregate_read_lines(source_rows=source_rows, style_rows=style_rows))
    lines.extend([
        "",
        "## Source layer totals",
        "",
        "| Source layer | Reports | Cameras | Overlap features | Coverage sum | Max coverage | Zero-overlap reports | Top class coverage | Cameras |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ])
    lines.extend(_aggregate_source_layer_row(row) for row in source_rows) if source_rows else lines.append("| _none_ | 0 | 0 | 0 | 0 | 0 | 0 | | |")
    lines.extend([
        "",
        "## QGIS style-layer coverage totals",
        "",
        "| Source layer | QGIS style layer | Type | Reports | Cameras | Features | Coverage sum | Max coverage | Cameras |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ])
    lines.extend(_aggregate_style_layer_row(row) for row in style_rows) if style_rows else lines.append("| _none_ | | | 0 | 0 | 0 | 0 | 0 | |")
    lines.extend([
        "",
        "## Per-camera source overlap",
        "",
        "| Camera | Zoom | Source layer | Overlap features | Bbox crop coverage | Classes | Class coverage | QGIS style-layer coverage |",
        "| --- | ---: | --- | ---: | ---: | --- | --- | --- |",
    ])
    lines.extend(_aggregate_camera_source_row(row) for row in camera_rows) if camera_rows else lines.append("| _none_ | | | 0 | 0 | | | |")
    lines.append("")
    return "\n".join(lines)


def write_report(report: Mapping[str, object], paths: SourceCropOverlapPaths) -> None:
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    paths.json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    paths.summary_path.write_text(build_summary_markdown(report), encoding="utf-8")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Count live Mapbox vector source features overlapping Mapbox Outdoors visual crop boxes.",
    )
    parser.add_argument("--visual-crop-json", type=Path)
    parser.add_argument("--camera", default=DEFAULT_CAMERA_NAME)
    parser.add_argument("--mapbox-token")
    parser.add_argument("--style-owner", default=DEFAULT_MAPBOX_STYLE_OWNER)
    parser.add_argument("--style-id", default=DEFAULT_MAPBOX_STYLE_ID)
    parser.add_argument("--tile-zoom", type=int)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--aggregate-report",
        action="append",
        type=Path,
        default=[],
        help="Existing source-crop-overlap.json report to aggregate. Repeat for multiple reports.",
    )
    parser.add_argument(
        "--aggregate-output",
        type=Path,
        help="Optional Markdown output path for --aggregate-report mode. Defaults to stdout.",
    )
    parser.add_argument(
        "--source-layer",
        action="append",
        dest="source_layers",
        help="Mapbox vector source-layer to count. Repeat to inspect multiple layers.",
    )
    args = parser.parse_args(argv)
    if args.aggregate_report:
        if args.visual_crop_json is not None:
            parser.error("--visual-crop-json cannot be combined with --aggregate-report.")
        return args
    if args.visual_crop_json is None:
        parser.error("--visual-crop-json is required unless --aggregate-report is provided.")
    return args


def _run_aggregate_mode(args: argparse.Namespace) -> int:
    aggregate_report = build_source_crop_overlap_aggregate_report(tuple(args.aggregate_report))
    markdown_summary = render_aggregate_markdown_summary(aggregate_report)
    if args.aggregate_output is not None:
        output_path = args.aggregate_output.expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown_summary, encoding="utf-8")
        print(f"Aggregate summary: {_repo_relative_path(output_path)}")
    else:
        print(markdown_summary)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.aggregate_report:
        return _run_aggregate_mode(args)
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
