from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PARENT = REPO_ROOT.parent
package_parent = str(PACKAGE_PARENT)
if package_parent not in sys.path:
    sys.path.insert(0, package_parent)

from qfit import mapbox_config
from qfit.validation.mapbox_outdoors_contour_features import (
    TileDecoder,
    TileFetcher,
    _camera_by_name,
    _camera_extent,
    _combined_record_counts,
    _decoded_layer_features,
    _default_tile_decoder,
    _ensure_package_parent_on_path,
    _feature_properties,
    _fetch_url_bytes,
    _geometry_summary,
    _geometry_type,
    _markdown_value,
    _merge_bounds,
    _TILE_ERROR_TYPES,
    build_run_directory,
    decode_vector_tile_bytes,
    iter_tile_coordinates,
    load_style_definition,
    recommended_tile_zoom,
    resolve_mapbox_token,
    tile_bounds_for_web_mercator_extent,
)

DEFAULT_OUTPUT_ROOT = REPO_ROOT / "debug" / "mapbox-outdoors-road-features"
DEFAULT_MAPBOX_STYLE_OWNER = "mapbox"
DEFAULT_MAPBOX_STYLE_ID = "outdoors-v12"
DEFAULT_CAMERA_NAME = "zermatt-trails-z18-outdoors"
ROAD_SOURCE_LAYER = "road"
MOTORWAY_JUNCTION_SOURCE_LAYER = "motorway_junction"
MISSING_VALUE = "(missing)"
MAX_SAMPLE_FEATURES = 12
PEDESTRIAN_PATH_CLASSES = {"path", "pedestrian"}
SURFACE_STRUCTURES = {"none", "ford"}
LINE_GEOMETRY_TYPES = {"LineString", "MultiLineString"}
POLYGON_GEOMETRY_TYPES = {"Polygon", "MultiPolygon"}
LOW_ZOOM_PATH_EXCLUDED_TYPES = {"crossing", "sidewalk", "steps"}
HIGH_ZOOM_PATH_EXCLUDED_TYPES = {"steps"}
STEP_STRUCTURES = {"none", "ford", "bridge", "tunnel"}
ONEWAY_ARROW_MIN_ZOOM = 16
ROAD_INTERSECTION_MIN_ZOOM = 15
LEVEL_CROSSING_MIN_ZOOM = 16
ROAD_NUMBER_SHIELD_MIN_ZOOM = 6
ROAD_NUMBER_SHIELD_MAX_REFLEN = 6
ROAD_NUMBER_SHIELD_EXCLUDED_CLASSES = {"pedestrian", "service"}
ROAD_NUMBER_SHIELD_LINE_LENGTH_MIN = 2500.0
ROAD_EXIT_SHIELD_MIN_ZOOM = 14
ROAD_EXIT_SHIELD_MAX_REFLEN = 9
ONEWAY_ARROW_BLUE_CLASSES = {
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "service",
    "street",
    "street_limited",
    "tertiary",
    "tertiary_link",
    "track",
}
ONEWAY_ARROW_WHITE_CLASSES = {"motorway", "motorway_link", "trunk", "trunk_link"}
ONEWAY_ARROW_CLASSES = ONEWAY_ARROW_BLUE_CLASSES | ONEWAY_ARROW_WHITE_CLASSES
ONEWAY_ARROW_STRUCTURES = {"none", "ford", "bridge", "tunnel"}
SAMPLE_PROPERTY_KEYS = (
    "class",
    "type",
    "structure",
    "layer",
    "ref",
    "reflen",
    "shield",
    "shield_beta",
    "name",
    "oneway",
    "surface",
)
ROAD_FEATURE_SIGNATURE_KEYS = ("class", "type", "surface", "structure", "layer")
ROAD_INTERSECTION_SIGNATURE_KEYS = ("class", "name")
LEVEL_CROSSING_SIGNATURE_KEYS = ("class", "structure", "layer")
ROAD_NUMBER_SHIELD_SIGNATURE_KEYS = ("class", "reflen", "shield", "shield_beta", "structure", "layer")
ROAD_EXIT_SHIELD_SIGNATURE_KEYS = ("ref", "reflen")


@dataclass(frozen=True)
class RoadFeaturePaths:
    run_dir: Path
    json_path: Path
    summary_path: Path


@dataclass(frozen=True)
class RoadFeatureConfig:
    token: str | None
    output_root: Path
    camera_name: str = DEFAULT_CAMERA_NAME
    style_owner: str = DEFAULT_MAPBOX_STYLE_OWNER
    style_id: str = DEFAULT_MAPBOX_STYLE_ID
    style_json_path: Path | None = None
    tile_zoom: int | None = None
    now: dt.datetime | None = None


def build_road_feature_paths(run_dir: Path) -> RoadFeaturePaths:
    return RoadFeaturePaths(
        run_dir=run_dir,
        json_path=run_dir / "road-features.json",
        summary_path=run_dir / "summary.md",
    )


def build_all_camera_road_feature_paths(run_dir: Path) -> RoadFeaturePaths:
    return RoadFeaturePaths(
        run_dir=run_dir,
        json_path=run_dir / "road-features.json",
        summary_path=run_dir / "summary.md",
    )


def _structure_is_surface(properties: dict[str, object]) -> bool:
    structure = properties.get("structure")
    return structure in SURFACE_STRUCTURES


def _layer_is_nonnegative(properties: dict[str, object]) -> bool:
    layer = properties.get("layer")
    return layer is None or (
        not isinstance(layer, bool) and isinstance(layer, (int, float)) and math.isfinite(float(layer)) and layer >= 0
    )


def is_pedestrian_polygon_candidate(feature: dict[str, object]) -> bool:
    properties = _feature_properties(feature)
    return (
        properties.get("class") in PEDESTRIAN_PATH_CLASSES
        and _structure_is_surface(properties)
        and _layer_is_nonnegative(properties)
        and _geometry_type(feature) in POLYGON_GEOMETRY_TYPES
    )


def is_pedestrian_line_candidate(feature: dict[str, object]) -> bool:
    properties = _feature_properties(feature)
    return (
        properties.get("class") == "pedestrian"
        and _structure_is_surface(properties)
        and _layer_is_nonnegative(properties)
        and _geometry_type(feature) in LINE_GEOMETRY_TYPES
    )


def is_path_line_candidate(feature: dict[str, object], *, tile_zoom: int | None = None) -> bool:
    properties = _feature_properties(feature)
    if tile_zoom is not None:
        excluded_types = LOW_ZOOM_PATH_EXCLUDED_TYPES if tile_zoom < 16 else HIGH_ZOOM_PATH_EXCLUDED_TYPES
        path_type = properties.get("type")
        if isinstance(path_type, str) and path_type in excluded_types:
            return False
    return (
        properties.get("class") == "path"
        and _structure_is_surface(properties)
        and _layer_is_nonnegative(properties)
        and _geometry_type(feature) in LINE_GEOMETRY_TYPES
    )


def is_step_line_candidate(feature: dict[str, object]) -> bool:
    properties = _feature_properties(feature)
    return (
        properties.get("type") == "steps"
        and properties.get("structure") in STEP_STRUCTURES
        and _geometry_type(feature) in LINE_GEOMETRY_TYPES
    )


def is_oneway_arrow_candidate(feature: dict[str, object], *, tile_zoom: int | None = None) -> bool:
    properties = _feature_properties(feature)
    return (
        tile_zoom is not None
        and tile_zoom >= ONEWAY_ARROW_MIN_ZOOM
        and properties.get("oneway") == "true"
        and properties.get("class") in ONEWAY_ARROW_CLASSES
        and properties.get("structure") in ONEWAY_ARROW_STRUCTURES
        and _geometry_type(feature) in LINE_GEOMETRY_TYPES
    )


def is_road_intersection_candidate(feature: dict[str, object], *, tile_zoom: int | None = None) -> bool:
    properties = _feature_properties(feature)
    return (
        tile_zoom is not None
        and tile_zoom >= ROAD_INTERSECTION_MIN_ZOOM
        and properties.get("class") == "intersection"
        and "name" in properties
        and _geometry_type(feature) == "Point"
    )


def is_level_crossing_candidate(feature: dict[str, object], *, tile_zoom: int | None = None) -> bool:
    properties = _feature_properties(feature)
    return (
        tile_zoom is not None
        and tile_zoom >= LEVEL_CROSSING_MIN_ZOOM
        and properties.get("class") == "level_crossing"
        and _geometry_type(feature) == "Point"
    )


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def is_road_number_shield_candidate(feature: dict[str, object], *, tile_zoom: int | None = None) -> bool:
    properties = _feature_properties(feature)
    reflen = _finite_number(properties.get("reflen"))
    if (
        tile_zoom is None
        or tile_zoom < ROAD_NUMBER_SHIELD_MIN_ZOOM
        or properties.get("class") in ROAD_NUMBER_SHIELD_EXCLUDED_CLASSES
        or reflen is None
        or reflen <= 0
        or reflen > ROAD_NUMBER_SHIELD_MAX_REFLEN
    ):
        return False
    if tile_zoom < 11:
        return _geometry_type(feature) == "Point"
    if _geometry_type(feature) not in LINE_GEOMETRY_TYPES:
        return False
    length = _finite_number(properties.get("len"))
    return length is not None and length > ROAD_NUMBER_SHIELD_LINE_LENGTH_MIN


def is_road_exit_shield_candidate(feature: dict[str, object], *, tile_zoom: int | None = None) -> bool:
    properties = _feature_properties(feature)
    reflen = _finite_number(properties.get("reflen"))
    return (
        tile_zoom is not None
        and tile_zoom >= ROAD_EXIT_SHIELD_MIN_ZOOM
        and reflen is not None
        and reflen > 0
        and reflen <= ROAD_EXIT_SHIELD_MAX_REFLEN
    )


def _property_count_label(value: object) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return str(value)


def _feature_signature(feature: dict[str, object], keys: Sequence[str]) -> str:
    properties = _feature_properties(feature)
    return "; ".join(
        f"{key}={_property_count_label(properties.get(key, MISSING_VALUE))}"
        for key in keys
    )


def _count_by_property(features: Iterable[dict[str, object]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for feature in features:
        normalized = _property_count_label(_feature_properties(feature).get(key, MISSING_VALUE))
        counts[normalized] = counts.get(normalized, 0) + 1
    return dict(sorted(counts.items()))


def _count_property_signatures(
    features: Iterable[dict[str, object]],
    keys: Sequence[str],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for feature in features:
        signature = _feature_signature(feature, keys)
        counts[signature] = counts.get(signature, 0) + 1
    return dict(sorted(counts.items()))


def _count_geometry_types(features: Iterable[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for feature in features:
        geometry_type = _geometry_type(feature)
        counts[geometry_type] = counts.get(geometry_type, 0) + 1
    return dict(sorted(counts.items()))


def _feature_sample(tile: dict[str, int], feature: dict[str, object]) -> dict[str, object]:
    properties = _feature_properties(feature)
    return {
        "tile": tile,
        "properties": {key: properties[key] for key in SAMPLE_PROPERTY_KEYS if key in properties},
        "geometry": _geometry_summary(feature),
        "property_keys": sorted(str(key) for key in properties.keys()),
    }


def _tile_url(tile_url_template: str, tile: dict[str, int]) -> str:
    return tile_url_template.format(z=tile["z"], x=tile["x"], y=tile["y"])


def road_tile_record(
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
    road_features = _decoded_layer_features(decoded, ROAD_SOURCE_LAYER)
    motorway_junction_features = _decoded_layer_features(decoded, MOTORWAY_JUNCTION_SOURCE_LAYER)
    pedestrian_polygons = [feature for feature in road_features if is_pedestrian_polygon_candidate(feature)]
    pedestrian_lines = [feature for feature in road_features if is_pedestrian_line_candidate(feature)]
    path_lines = [feature for feature in road_features if is_path_line_candidate(feature, tile_zoom=tile.get("z"))]
    step_lines = [feature for feature in road_features if is_step_line_candidate(feature)]
    oneway_arrow_lines = [
        feature for feature in road_features if is_oneway_arrow_candidate(feature, tile_zoom=tile.get("z"))
    ]
    road_intersections = [
        feature for feature in road_features if is_road_intersection_candidate(feature, tile_zoom=tile.get("z"))
    ]
    level_crossings = [
        feature for feature in road_features if is_level_crossing_candidate(feature, tile_zoom=tile.get("z"))
    ]
    road_number_shields = [
        feature for feature in road_features if is_road_number_shield_candidate(feature, tile_zoom=tile.get("z"))
    ]
    road_exit_shields = [
        feature
        for feature in motorway_junction_features
        if is_road_exit_shield_candidate(feature, tile_zoom=tile.get("z"))
    ]
    return {
        **tile,
        "status": "decoded",
        "byte_count": len(tile_bytes),
        "road_feature_count": len(road_features),
        "motorway_junction_feature_count": len(motorway_junction_features),
        "pedestrian_polygon_candidate_count": len(pedestrian_polygons),
        "pedestrian_line_candidate_count": len(pedestrian_lines),
        "path_line_candidate_count": len(path_lines),
        "step_line_candidate_count": len(step_lines),
        "oneway_arrow_candidate_count": len(oneway_arrow_lines),
        "road_intersection_candidate_count": len(road_intersections),
        "level_crossing_candidate_count": len(level_crossings),
        "road_number_shield_candidate_count": len(road_number_shields),
        "road_exit_shield_candidate_count": len(road_exit_shields),
        "road_geometry_type_counts": _count_geometry_types(road_features),
        "pedestrian_polygon_class_counts": _count_by_property(pedestrian_polygons, "class"),
        "pedestrian_polygon_type_counts": _count_by_property(pedestrian_polygons, "type"),
        "pedestrian_polygon_structure_counts": _count_by_property(pedestrian_polygons, "structure"),
        "pedestrian_polygon_layer_counts": _count_by_property(pedestrian_polygons, "layer"),
        "pedestrian_polygon_surface_counts": _count_by_property(pedestrian_polygons, "surface"),
        "pedestrian_polygon_signature_counts": _count_property_signatures(
            pedestrian_polygons,
            ROAD_FEATURE_SIGNATURE_KEYS,
        ),
        "pedestrian_line_type_counts": _count_by_property(pedestrian_lines, "type"),
        "pedestrian_line_structure_counts": _count_by_property(pedestrian_lines, "structure"),
        "pedestrian_line_layer_counts": _count_by_property(pedestrian_lines, "layer"),
        "pedestrian_line_surface_counts": _count_by_property(pedestrian_lines, "surface"),
        "pedestrian_line_signature_counts": _count_property_signatures(
            pedestrian_lines,
            ROAD_FEATURE_SIGNATURE_KEYS,
        ),
        "path_line_type_counts": _count_by_property(path_lines, "type"),
        "path_line_structure_counts": _count_by_property(path_lines, "structure"),
        "path_line_layer_counts": _count_by_property(path_lines, "layer"),
        "path_line_surface_counts": _count_by_property(path_lines, "surface"),
        "path_line_signature_counts": _count_property_signatures(path_lines, ROAD_FEATURE_SIGNATURE_KEYS),
        "step_line_structure_counts": _count_by_property(step_lines, "structure"),
        "step_line_layer_counts": _count_by_property(step_lines, "layer"),
        "step_line_surface_counts": _count_by_property(step_lines, "surface"),
        "step_line_signature_counts": _count_property_signatures(step_lines, ROAD_FEATURE_SIGNATURE_KEYS),
        "oneway_arrow_class_counts": _count_by_property(oneway_arrow_lines, "class"),
        "oneway_arrow_structure_counts": _count_by_property(oneway_arrow_lines, "structure"),
        "oneway_arrow_layer_counts": _count_by_property(oneway_arrow_lines, "layer"),
        "road_intersection_name_counts": _count_by_property(road_intersections, "name"),
        "road_intersection_signature_counts": _count_property_signatures(
            road_intersections,
            ROAD_INTERSECTION_SIGNATURE_KEYS,
        ),
        "level_crossing_structure_counts": _count_by_property(level_crossings, "structure"),
        "level_crossing_layer_counts": _count_by_property(level_crossings, "layer"),
        "level_crossing_signature_counts": _count_property_signatures(
            level_crossings,
            LEVEL_CROSSING_SIGNATURE_KEYS,
        ),
        "road_number_shield_class_counts": _count_by_property(road_number_shields, "class"),
        "road_number_shield_reflen_counts": _count_by_property(road_number_shields, "reflen"),
        "road_number_shield_structure_counts": _count_by_property(road_number_shields, "structure"),
        "road_number_shield_layer_counts": _count_by_property(road_number_shields, "layer"),
        "road_number_shield_signature_counts": _count_property_signatures(
            road_number_shields,
            ROAD_NUMBER_SHIELD_SIGNATURE_KEYS,
        ),
        "road_exit_shield_reflen_counts": _count_by_property(road_exit_shields, "reflen"),
        "road_exit_shield_signature_counts": _count_property_signatures(
            road_exit_shields,
            ROAD_EXIT_SHIELD_SIGNATURE_KEYS,
        ),
        "sample_pedestrian_polygons": [
            _feature_sample(tile, feature) for feature in pedestrian_polygons[:MAX_SAMPLE_FEATURES]
        ],
        "sample_pedestrian_lines": [
            _feature_sample(tile, feature) for feature in pedestrian_lines[:MAX_SAMPLE_FEATURES]
        ],
        "sample_path_lines": [_feature_sample(tile, feature) for feature in path_lines[:MAX_SAMPLE_FEATURES]],
        "sample_step_lines": [_feature_sample(tile, feature) for feature in step_lines[:MAX_SAMPLE_FEATURES]],
        "sample_oneway_arrow_lines": [
            _feature_sample(tile, feature) for feature in oneway_arrow_lines[:MAX_SAMPLE_FEATURES]
        ],
        "sample_road_intersections": [
            _feature_sample(tile, feature) for feature in road_intersections[:MAX_SAMPLE_FEATURES]
        ],
        "sample_level_crossings": [
            _feature_sample(tile, feature) for feature in level_crossings[:MAX_SAMPLE_FEATURES]
        ],
        "sample_road_number_shields": [
            _feature_sample(tile, feature) for feature in road_number_shields[:MAX_SAMPLE_FEATURES]
        ],
        "sample_road_exit_shields": [
            _feature_sample(tile, feature) for feature in road_exit_shields[:MAX_SAMPLE_FEATURES]
        ],
    }


def _combined_samples(tile_records: list[dict[str, object]], sample_key: str) -> list[dict[str, object]]:
    samples: list[dict[str, object]] = []
    for tile_record in tile_records:
        tile_samples = tile_record.get(sample_key)
        if isinstance(tile_samples, list):
            samples.extend(sample for sample in tile_samples if isinstance(sample, dict))
        if len(samples) >= MAX_SAMPLE_FEATURES:
            break
    return samples[:MAX_SAMPLE_FEATURES]


_SUMMARY_COUNT_FIELDS = (
    ("Road features", "road_feature_count"),
    ("Motorway junction features", "motorway_junction_feature_count"),
    ("Pedestrian/path polygon candidates", "pedestrian_polygon_candidate_count"),
    ("Pedestrian line candidates", "pedestrian_line_candidate_count"),
    ("Path line candidates", "path_line_candidate_count"),
    ("Step line candidates", "step_line_candidate_count"),
    ("One-way arrow candidates", "oneway_arrow_candidate_count"),
    ("Road intersection candidates", "road_intersection_candidate_count"),
    ("Level crossing candidates", "level_crossing_candidate_count"),
    ("Road number shield candidates", "road_number_shield_candidate_count"),
    ("Road exit shield candidates", "road_exit_shield_candidate_count"),
)
_SUMMARY_COUNT_MAP_FIELDS = (
    ("Pedestrian polygon type counts", "pedestrian_polygon_type_counts"),
    ("Pedestrian polygon structure counts", "pedestrian_polygon_structure_counts"),
    ("Pedestrian polygon layer counts", "pedestrian_polygon_layer_counts"),
    ("Pedestrian polygon surface counts", "pedestrian_polygon_surface_counts"),
    ("Pedestrian polygon signatures", "pedestrian_polygon_signature_counts"),
    ("Pedestrian line type counts", "pedestrian_line_type_counts"),
    ("Pedestrian line structure counts", "pedestrian_line_structure_counts"),
    ("Pedestrian line layer counts", "pedestrian_line_layer_counts"),
    ("Pedestrian line surface counts", "pedestrian_line_surface_counts"),
    ("Pedestrian line signatures", "pedestrian_line_signature_counts"),
    ("Path line type counts", "path_line_type_counts"),
    ("Path line structure counts", "path_line_structure_counts"),
    ("Path line layer counts", "path_line_layer_counts"),
    ("Path line surface counts", "path_line_surface_counts"),
    ("Path line signatures", "path_line_signature_counts"),
    ("Step line structure counts", "step_line_structure_counts"),
    ("Step line layer counts", "step_line_layer_counts"),
    ("Step line surface counts", "step_line_surface_counts"),
    ("Step line signatures", "step_line_signature_counts"),
    ("One-way arrow class counts", "oneway_arrow_class_counts"),
    ("One-way arrow structure counts", "oneway_arrow_structure_counts"),
    ("One-way arrow layer counts", "oneway_arrow_layer_counts"),
    ("Road intersection name counts", "road_intersection_name_counts"),
    ("Road intersection signatures", "road_intersection_signature_counts"),
    ("Level crossing structure counts", "level_crossing_structure_counts"),
    ("Level crossing layer counts", "level_crossing_layer_counts"),
    ("Level crossing signatures", "level_crossing_signature_counts"),
    ("Road number shield class counts", "road_number_shield_class_counts"),
    ("Road number shield reflen counts", "road_number_shield_reflen_counts"),
    ("Road number shield structure counts", "road_number_shield_structure_counts"),
    ("Road number shield layer counts", "road_number_shield_layer_counts"),
    ("Road number shield signatures", "road_number_shield_signature_counts"),
    ("Road exit shield reflen counts", "road_exit_shield_reflen_counts"),
    ("Road exit shield signatures", "road_exit_shield_signature_counts"),
)
_ROAD_FEATURE_TABLE_FIELDS = (
    ("Road", "road_feature_count", "---:"),
    ("Motorway junctions", "motorway_junction_feature_count", "---:"),
    ("Pedestrian/path polygons", "pedestrian_polygon_candidate_count", "---:"),
    ("Pedestrian lines", "pedestrian_line_candidate_count", "---:"),
    ("Path lines", "path_line_candidate_count", "---:"),
    ("Step lines", "step_line_candidate_count", "---:"),
    ("One-way arrows", "oneway_arrow_candidate_count", "---:"),
    ("Road intersections", "road_intersection_candidate_count", "---:"),
    ("Level crossings", "level_crossing_candidate_count", "---:"),
    ("Road number shields", "road_number_shield_candidate_count", "---:"),
    ("Road exit shields", "road_exit_shield_candidate_count", "---:"),
    ("Polygon types", "pedestrian_polygon_type_counts", "---"),
    ("Polygon structures", "pedestrian_polygon_structure_counts", "---"),
    ("Polygon layers", "pedestrian_polygon_layer_counts", "---"),
    ("Polygon surfaces", "pedestrian_polygon_surface_counts", "---"),
    ("Polygon signatures", "pedestrian_polygon_signature_counts", "---"),
    ("Pedestrian line types", "pedestrian_line_type_counts", "---"),
    ("Pedestrian line structures", "pedestrian_line_structure_counts", "---"),
    ("Pedestrian line layers", "pedestrian_line_layer_counts", "---"),
    ("Pedestrian line surfaces", "pedestrian_line_surface_counts", "---"),
    ("Pedestrian line signatures", "pedestrian_line_signature_counts", "---"),
    ("Path line types", "path_line_type_counts", "---"),
    ("Path line structures", "path_line_structure_counts", "---"),
    ("Path line layers", "path_line_layer_counts", "---"),
    ("Path line surfaces", "path_line_surface_counts", "---"),
    ("Path line signatures", "path_line_signature_counts", "---"),
    ("Step structures", "step_line_structure_counts", "---"),
    ("Step layers", "step_line_layer_counts", "---"),
    ("Step surfaces", "step_line_surface_counts", "---"),
    ("Step signatures", "step_line_signature_counts", "---"),
    ("One-way arrow classes", "oneway_arrow_class_counts", "---"),
    ("One-way arrow structures", "oneway_arrow_structure_counts", "---"),
    ("One-way arrow layers", "oneway_arrow_layer_counts", "---"),
    ("Road intersection names", "road_intersection_name_counts", "---"),
    ("Road intersection signatures", "road_intersection_signature_counts", "---"),
    ("Level crossing structures", "level_crossing_structure_counts", "---"),
    ("Level crossing layers", "level_crossing_layer_counts", "---"),
    ("Level crossing signatures", "level_crossing_signature_counts", "---"),
    ("Shield classes", "road_number_shield_class_counts", "---"),
    ("Shield reflens", "road_number_shield_reflen_counts", "---"),
    ("Shield structures", "road_number_shield_structure_counts", "---"),
    ("Shield layers", "road_number_shield_layer_counts", "---"),
    ("Shield signatures", "road_number_shield_signature_counts", "---"),
    ("Exit shield reflens", "road_exit_shield_reflen_counts", "---"),
    ("Exit shield signatures", "road_exit_shield_signature_counts", "---"),
)


def _feature_summary_lines(report: dict[str, object]) -> list[str]:
    count_lines = [f"{label}: {report.get(key)}" for label, key in _SUMMARY_COUNT_FIELDS]
    count_map_lines = [f"{label}: {_markdown_value(report.get(key))}" for label, key in _SUMMARY_COUNT_MAP_FIELDS]
    return [*count_lines, *count_map_lines]


def _markdown_table_header(prefix_columns: tuple[tuple[str, str], ...]) -> list[str]:
    labels = [label for label, _alignment in prefix_columns]
    labels.extend(label for label, _key, _alignment in _ROAD_FEATURE_TABLE_FIELDS)
    alignments = [alignment for _label, alignment in prefix_columns]
    alignments.extend(alignment for _label, _key, alignment in _ROAD_FEATURE_TABLE_FIELDS)
    return [
        "| " + " | ".join(labels) + " |",
        "| " + " | ".join(alignments) + " |",
    ]


def _road_feature_table_cells(record: dict[str, object]) -> list[str]:
    return [_markdown_value(record.get(key)) for _label, key, _alignment in _ROAD_FEATURE_TABLE_FIELDS]


def _markdown_table_row(cells: Iterable[object]) -> str:
    return "| " + " | ".join(_markdown_value(cell) for cell in cells) + " |"


def _load_original_style(config: RoadFeatureConfig, style_fetcher) -> dict[str, object]:
    if config.style_json_path is not None:
        return load_style_definition(config.style_json_path)
    if not config.token:
        raise ValueError("A Mapbox token is required unless --style-json is provided.")
    return style_fetcher(config.token, config.style_owner, config.style_id)


def _road_tileset_context(
    config: RoadFeatureConfig,
    style_fetcher: Callable[[str, str, str], dict[str, object]] | None,
    style_definition: dict[str, object] | None = None,
) -> tuple[list[str], str]:
    fetch_style = style_fetcher if style_fetcher is not None else mapbox_config.fetch_mapbox_style_definition
    resolved_style_definition = style_definition if style_definition is not None else _load_original_style(config, fetch_style)
    tileset_ids = mapbox_config.extract_mapbox_vector_source_ids(resolved_style_definition)
    if not config.token:
        raise ValueError("A Mapbox token is required to fetch vector tiles.")
    tile_url_template = mapbox_config.build_mapbox_vector_tiles_url(
        config.token,
        config.style_owner,
        config.style_id,
        tileset_ids=tileset_ids,
    )
    return tileset_ids, tile_url_template


def _all_camera_names() -> list[str]:
    _ensure_package_parent_on_path()
    from qfit.validation.mapbox_outdoors_comparison import CAMERAS

    return list(CAMERAS.keys())


def _sum_reports(reports: Iterable[dict[str, object]], key: str) -> int:
    total = 0
    for report in reports:
        value = report.get(key)
        if isinstance(value, int):
            total += value
    return total


def _all_camera_status_counts(rows: Iterable[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = row.get("status")
        if isinstance(status, str) and status:
            counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def _all_camera_row(report: dict[str, object]) -> dict[str, object]:
    camera = report.get("camera") if isinstance(report.get("camera"), dict) else {}
    row = {
        "status": "decoded",
        "camera": camera.get("name"),
        "camera_zoom": camera.get("zoom"),
        "tile_zoom": report.get("tile_zoom"),
        "tile_count": report.get("tile_count"),
        "decoded_tile_count": report.get("decoded_tile_count"),
        "failed_tile_count": report.get("failed_tile_count"),
        "road_geometry_type_counts": report.get("road_geometry_type_counts"),
        "pedestrian_polygon_class_counts": report.get("pedestrian_polygon_class_counts"),
    }
    for _label, key, _alignment in _ROAD_FEATURE_TABLE_FIELDS:
        row[key] = report.get(key)
    return row


def _all_camera_error_row(camera_name: str, exc: BaseException) -> dict[str, object]:
    return {
        "status": "error",
        "camera": camera_name,
        "error": type(exc).__name__,
        "message": str(exc),
    }


def collect_road_feature_report(
    config: RoadFeatureConfig,
    *,
    style_fetcher: Callable[[str, str, str], dict[str, object]] | None = None,
    style_definition: dict[str, object] | None = None,
    tile_fetcher: TileFetcher | None = None,
    tile_decoder: TileDecoder | None = None,
) -> dict[str, object]:
    _ensure_package_parent_on_path()
    tileset_ids, tile_url_template = _road_tileset_context(config, style_fetcher, style_definition)
    camera = _camera_by_name(config.camera_name)
    tile_zoom = config.tile_zoom if config.tile_zoom is not None else recommended_tile_zoom(float(camera.zoom))
    tile_bounds = tile_bounds_for_web_mercator_extent(_camera_extent(camera), tile_zoom)
    fetch_tile = tile_fetcher or _fetch_url_bytes
    decode_tile = tile_decoder or _default_tile_decoder
    tile_records = [
        road_tile_record(
            tile=tile,
            tile_url_template=tile_url_template,
            tile_fetcher=fetch_tile,
            tile_decoder=decode_tile,
        )
        for tile in iter_tile_coordinates(tile_bounds, tile_zoom)
    ]
    decoded_tile_count = sum(1 for tile in tile_records if tile.get("status") == "decoded")
    generated = config.now or dt.datetime.now(dt.timezone.utc)
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
        "road_feature_count": sum(int(tile.get("road_feature_count") or 0) for tile in tile_records),
        "motorway_junction_feature_count": sum(
            int(tile.get("motorway_junction_feature_count") or 0) for tile in tile_records
        ),
        "pedestrian_polygon_candidate_count": sum(
            int(tile.get("pedestrian_polygon_candidate_count") or 0) for tile in tile_records
        ),
        "pedestrian_line_candidate_count": sum(
            int(tile.get("pedestrian_line_candidate_count") or 0) for tile in tile_records
        ),
        "path_line_candidate_count": sum(int(tile.get("path_line_candidate_count") or 0) for tile in tile_records),
        "step_line_candidate_count": sum(int(tile.get("step_line_candidate_count") or 0) for tile in tile_records),
        "oneway_arrow_candidate_count": sum(
            int(tile.get("oneway_arrow_candidate_count") or 0) for tile in tile_records
        ),
        "road_intersection_candidate_count": sum(
            int(tile.get("road_intersection_candidate_count") or 0) for tile in tile_records
        ),
        "level_crossing_candidate_count": sum(
            int(tile.get("level_crossing_candidate_count") or 0) for tile in tile_records
        ),
        "road_number_shield_candidate_count": sum(
            int(tile.get("road_number_shield_candidate_count") or 0) for tile in tile_records
        ),
        "road_exit_shield_candidate_count": sum(
            int(tile.get("road_exit_shield_candidate_count") or 0) for tile in tile_records
        ),
        "road_geometry_type_counts": _combined_record_counts(tile_records, "road_geometry_type_counts"),
        "pedestrian_polygon_class_counts": _combined_record_counts(
            tile_records,
            "pedestrian_polygon_class_counts",
        ),
        "pedestrian_polygon_type_counts": _combined_record_counts(
            tile_records,
            "pedestrian_polygon_type_counts",
        ),
        "pedestrian_polygon_structure_counts": _combined_record_counts(
            tile_records,
            "pedestrian_polygon_structure_counts",
        ),
        "pedestrian_polygon_layer_counts": _combined_record_counts(
            tile_records,
            "pedestrian_polygon_layer_counts",
        ),
        "pedestrian_polygon_surface_counts": _combined_record_counts(
            tile_records,
            "pedestrian_polygon_surface_counts",
        ),
        "pedestrian_polygon_signature_counts": _combined_record_counts(
            tile_records,
            "pedestrian_polygon_signature_counts",
        ),
        "pedestrian_line_type_counts": _combined_record_counts(tile_records, "pedestrian_line_type_counts"),
        "pedestrian_line_structure_counts": _combined_record_counts(
            tile_records,
            "pedestrian_line_structure_counts",
        ),
        "pedestrian_line_layer_counts": _combined_record_counts(tile_records, "pedestrian_line_layer_counts"),
        "pedestrian_line_surface_counts": _combined_record_counts(tile_records, "pedestrian_line_surface_counts"),
        "pedestrian_line_signature_counts": _combined_record_counts(
            tile_records,
            "pedestrian_line_signature_counts",
        ),
        "path_line_type_counts": _combined_record_counts(tile_records, "path_line_type_counts"),
        "path_line_structure_counts": _combined_record_counts(tile_records, "path_line_structure_counts"),
        "path_line_layer_counts": _combined_record_counts(tile_records, "path_line_layer_counts"),
        "path_line_surface_counts": _combined_record_counts(tile_records, "path_line_surface_counts"),
        "path_line_signature_counts": _combined_record_counts(tile_records, "path_line_signature_counts"),
        "step_line_structure_counts": _combined_record_counts(tile_records, "step_line_structure_counts"),
        "step_line_layer_counts": _combined_record_counts(tile_records, "step_line_layer_counts"),
        "step_line_surface_counts": _combined_record_counts(tile_records, "step_line_surface_counts"),
        "step_line_signature_counts": _combined_record_counts(tile_records, "step_line_signature_counts"),
        "oneway_arrow_class_counts": _combined_record_counts(tile_records, "oneway_arrow_class_counts"),
        "oneway_arrow_structure_counts": _combined_record_counts(tile_records, "oneway_arrow_structure_counts"),
        "oneway_arrow_layer_counts": _combined_record_counts(tile_records, "oneway_arrow_layer_counts"),
        "road_intersection_name_counts": _combined_record_counts(tile_records, "road_intersection_name_counts"),
        "road_intersection_signature_counts": _combined_record_counts(
            tile_records,
            "road_intersection_signature_counts",
        ),
        "level_crossing_structure_counts": _combined_record_counts(tile_records, "level_crossing_structure_counts"),
        "level_crossing_layer_counts": _combined_record_counts(tile_records, "level_crossing_layer_counts"),
        "level_crossing_signature_counts": _combined_record_counts(
            tile_records,
            "level_crossing_signature_counts",
        ),
        "road_number_shield_class_counts": _combined_record_counts(tile_records, "road_number_shield_class_counts"),
        "road_number_shield_reflen_counts": _combined_record_counts(
            tile_records,
            "road_number_shield_reflen_counts",
        ),
        "road_number_shield_structure_counts": _combined_record_counts(
            tile_records,
            "road_number_shield_structure_counts",
        ),
        "road_number_shield_layer_counts": _combined_record_counts(tile_records, "road_number_shield_layer_counts"),
        "road_number_shield_signature_counts": _combined_record_counts(
            tile_records,
            "road_number_shield_signature_counts",
        ),
        "road_exit_shield_reflen_counts": _combined_record_counts(
            tile_records,
            "road_exit_shield_reflen_counts",
        ),
        "road_exit_shield_signature_counts": _combined_record_counts(
            tile_records,
            "road_exit_shield_signature_counts",
        ),
        "sample_pedestrian_polygons": _combined_samples(tile_records, "sample_pedestrian_polygons"),
        "sample_pedestrian_lines": _combined_samples(tile_records, "sample_pedestrian_lines"),
        "sample_path_lines": _combined_samples(tile_records, "sample_path_lines"),
        "sample_step_lines": _combined_samples(tile_records, "sample_step_lines"),
        "sample_oneway_arrow_lines": _combined_samples(tile_records, "sample_oneway_arrow_lines"),
        "sample_road_intersections": _combined_samples(tile_records, "sample_road_intersections"),
        "sample_level_crossings": _combined_samples(tile_records, "sample_level_crossings"),
        "sample_road_number_shields": _combined_samples(tile_records, "sample_road_number_shields"),
        "sample_road_exit_shields": _combined_samples(tile_records, "sample_road_exit_shields"),
        "tiles": tile_records,
    }


def collect_all_camera_road_feature_report(
    config: RoadFeatureConfig,
    *,
    camera_names: Iterable[str] | None = None,
    style_fetcher: Callable[[str, str, str], dict[str, object]] | None = None,
    tile_fetcher: TileFetcher | None = None,
    tile_decoder: TileDecoder | None = None,
) -> dict[str, object]:
    generated = config.now or dt.datetime.now(dt.timezone.utc)
    names = list(camera_names) if camera_names is not None else _all_camera_names()
    fetch_style = style_fetcher if style_fetcher is not None else mapbox_config.fetch_mapbox_style_definition
    style_definition = _load_original_style(config, fetch_style)
    camera_reports: list[dict[str, object]] = []
    for camera_name in names:
        try:
            report = collect_road_feature_report(
                RoadFeatureConfig(
                    token=config.token,
                    output_root=config.output_root,
                    camera_name=camera_name,
                    style_owner=config.style_owner,
                    style_id=config.style_id,
                    style_json_path=config.style_json_path,
                    tile_zoom=config.tile_zoom,
                    now=generated,
                ),
                style_fetcher=style_fetcher,
                style_definition=style_definition,
                tile_fetcher=tile_fetcher,
                tile_decoder=tile_decoder,
            )
        except _TILE_ERROR_TYPES as exc:
            camera_reports.append(_all_camera_error_row(camera_name, exc))
            continue
        camera_reports.append(_all_camera_row(report))
    status_counts = _all_camera_status_counts(camera_reports)
    return {
        "style_owner": config.style_owner,
        "style_id": config.style_id,
        "generated": generated.isoformat(),
        "camera_count": len(camera_reports),
        "successful_camera_count": status_counts.get("decoded", 0),
        "failed_camera_count": status_counts.get("error", 0),
        "tile_count": _sum_reports(camera_reports, "tile_count"),
        "decoded_tile_count": _sum_reports(camera_reports, "decoded_tile_count"),
        "failed_tile_count": _sum_reports(camera_reports, "failed_tile_count"),
        "road_feature_count": _sum_reports(camera_reports, "road_feature_count"),
        "motorway_junction_feature_count": _sum_reports(camera_reports, "motorway_junction_feature_count"),
        "pedestrian_polygon_candidate_count": _sum_reports(
            camera_reports,
            "pedestrian_polygon_candidate_count",
        ),
        "pedestrian_line_candidate_count": _sum_reports(
            camera_reports,
            "pedestrian_line_candidate_count",
        ),
        "path_line_candidate_count": _sum_reports(camera_reports, "path_line_candidate_count"),
        "step_line_candidate_count": _sum_reports(camera_reports, "step_line_candidate_count"),
        "oneway_arrow_candidate_count": _sum_reports(camera_reports, "oneway_arrow_candidate_count"),
        "road_intersection_candidate_count": _sum_reports(camera_reports, "road_intersection_candidate_count"),
        "level_crossing_candidate_count": _sum_reports(camera_reports, "level_crossing_candidate_count"),
        "road_number_shield_candidate_count": _sum_reports(camera_reports, "road_number_shield_candidate_count"),
        "road_exit_shield_candidate_count": _sum_reports(camera_reports, "road_exit_shield_candidate_count"),
        "road_geometry_type_counts": _combined_record_counts(camera_reports, "road_geometry_type_counts"),
        "pedestrian_polygon_class_counts": _combined_record_counts(
            camera_reports,
            "pedestrian_polygon_class_counts",
        ),
        "pedestrian_polygon_type_counts": _combined_record_counts(
            camera_reports,
            "pedestrian_polygon_type_counts",
        ),
        "pedestrian_polygon_structure_counts": _combined_record_counts(
            camera_reports,
            "pedestrian_polygon_structure_counts",
        ),
        "pedestrian_polygon_layer_counts": _combined_record_counts(
            camera_reports,
            "pedestrian_polygon_layer_counts",
        ),
        "pedestrian_polygon_surface_counts": _combined_record_counts(
            camera_reports,
            "pedestrian_polygon_surface_counts",
        ),
        "pedestrian_polygon_signature_counts": _combined_record_counts(
            camera_reports,
            "pedestrian_polygon_signature_counts",
        ),
        "pedestrian_line_type_counts": _combined_record_counts(
            camera_reports,
            "pedestrian_line_type_counts",
        ),
        "pedestrian_line_structure_counts": _combined_record_counts(
            camera_reports,
            "pedestrian_line_structure_counts",
        ),
        "pedestrian_line_layer_counts": _combined_record_counts(
            camera_reports,
            "pedestrian_line_layer_counts",
        ),
        "pedestrian_line_surface_counts": _combined_record_counts(
            camera_reports,
            "pedestrian_line_surface_counts",
        ),
        "pedestrian_line_signature_counts": _combined_record_counts(
            camera_reports,
            "pedestrian_line_signature_counts",
        ),
        "path_line_type_counts": _combined_record_counts(camera_reports, "path_line_type_counts"),
        "path_line_structure_counts": _combined_record_counts(camera_reports, "path_line_structure_counts"),
        "path_line_layer_counts": _combined_record_counts(camera_reports, "path_line_layer_counts"),
        "path_line_surface_counts": _combined_record_counts(camera_reports, "path_line_surface_counts"),
        "path_line_signature_counts": _combined_record_counts(camera_reports, "path_line_signature_counts"),
        "step_line_structure_counts": _combined_record_counts(camera_reports, "step_line_structure_counts"),
        "step_line_layer_counts": _combined_record_counts(camera_reports, "step_line_layer_counts"),
        "step_line_surface_counts": _combined_record_counts(camera_reports, "step_line_surface_counts"),
        "step_line_signature_counts": _combined_record_counts(camera_reports, "step_line_signature_counts"),
        "oneway_arrow_class_counts": _combined_record_counts(camera_reports, "oneway_arrow_class_counts"),
        "oneway_arrow_structure_counts": _combined_record_counts(camera_reports, "oneway_arrow_structure_counts"),
        "oneway_arrow_layer_counts": _combined_record_counts(camera_reports, "oneway_arrow_layer_counts"),
        "road_intersection_name_counts": _combined_record_counts(camera_reports, "road_intersection_name_counts"),
        "road_intersection_signature_counts": _combined_record_counts(
            camera_reports,
            "road_intersection_signature_counts",
        ),
        "level_crossing_structure_counts": _combined_record_counts(camera_reports, "level_crossing_structure_counts"),
        "level_crossing_layer_counts": _combined_record_counts(camera_reports, "level_crossing_layer_counts"),
        "level_crossing_signature_counts": _combined_record_counts(
            camera_reports,
            "level_crossing_signature_counts",
        ),
        "road_number_shield_class_counts": _combined_record_counts(
            camera_reports,
            "road_number_shield_class_counts",
        ),
        "road_number_shield_reflen_counts": _combined_record_counts(
            camera_reports,
            "road_number_shield_reflen_counts",
        ),
        "road_number_shield_structure_counts": _combined_record_counts(
            camera_reports,
            "road_number_shield_structure_counts",
        ),
        "road_number_shield_layer_counts": _combined_record_counts(
            camera_reports,
            "road_number_shield_layer_counts",
        ),
        "road_number_shield_signature_counts": _combined_record_counts(
            camera_reports,
            "road_number_shield_signature_counts",
        ),
        "road_exit_shield_reflen_counts": _combined_record_counts(
            camera_reports,
            "road_exit_shield_reflen_counts",
        ),
        "road_exit_shield_signature_counts": _combined_record_counts(
            camera_reports,
            "road_exit_shield_signature_counts",
        ),
        "cameras": camera_reports,
    }


def build_summary_markdown(report: dict[str, object]) -> str:
    camera = report.get("camera") if isinstance(report.get("camera"), dict) else {}
    lines = [
        f"# Mapbox Outdoors road feature diagnostic - {camera.get('name')}",
        "",
        f"Generated: {report.get('generated')}",
        f"Style: {report.get('style_owner')}/{report.get('style_id')}",
        f"Tile zoom: {report.get('tile_zoom')}",
        f"Tiles: {report.get('decoded_tile_count')}/{report.get('tile_count')} decoded",
        *_feature_summary_lines(report),
        "",
        *_markdown_table_header((("z", "---:"), ("x", "---:"), ("y", "---:"), ("Status", "---"))),
    ]
    tiles = report.get("tiles")
    tile_rows = tiles if isinstance(tiles, list) else []
    for tile in tile_rows:
        if not isinstance(tile, dict):
            continue
        lines.append(_markdown_table_row([tile.get("z"), tile.get("x"), tile.get("y"), tile.get("status"), *_road_feature_table_cells(tile)]))
    sample_sections = (
        ("Sample pedestrian/path polygon candidates", "sample_pedestrian_polygons"),
        ("Sample pedestrian line candidates", "sample_pedestrian_lines"),
        ("Sample path line candidates", "sample_path_lines"),
        ("Sample step line candidates", "sample_step_lines"),
        ("Sample one-way arrow candidates", "sample_oneway_arrow_lines"),
        ("Sample road intersection candidates", "sample_road_intersections"),
        ("Sample level crossing candidates", "sample_level_crossings"),
        ("Sample road number shield candidates", "sample_road_number_shields"),
        ("Sample road exit shield candidates", "sample_road_exit_shields"),
    )
    for heading, key in sample_sections:
        samples = report.get(key)
        sample_rows = samples if isinstance(samples, list) else []
        if sample_rows:
            lines.extend(["", f"## {heading}", ""])
            for sample in sample_rows:
                lines.append(f"- {_markdown_value(sample)}")
    return "\n".join(lines) + "\n"


def build_all_camera_summary_markdown(report: dict[str, object]) -> str:
    camera_reports = report.get("cameras")
    rows = camera_reports if isinstance(camera_reports, list) else []
    lines = [
        "# Mapbox Outdoors road feature diagnostic - all cameras",
        "",
        f"Generated: {report.get('generated')}",
        f"Style: {report.get('style_owner')}/{report.get('style_id')}",
        f"Cameras: {report.get('camera_count')}",
        f"Camera statuses: {_markdown_value(_all_camera_status_counts(rows))}",
        f"Tiles: {report.get('decoded_tile_count')}/{report.get('tile_count')} decoded",
        *_feature_summary_lines(report),
        "",
        *_markdown_table_header(
            (
                ("Camera", "---"),
                ("Status", "---"),
                ("Camera zoom", "---:"),
                ("Tile zoom", "---:"),
                ("Tiles", "---:"),
                ("Error", "---"),
            )
        ),
    ]
    for camera_report in rows:
        if not isinstance(camera_report, dict):
            continue
        tiles = (
            f"{_markdown_value(camera_report.get('decoded_tile_count'))}/"
            f"{_markdown_value(camera_report.get('tile_count'))}"
        )
        lines.append(
            _markdown_table_row(
                [
                    camera_report.get("camera"),
                    camera_report.get("status"),
                    camera_report.get("camera_zoom"),
                    camera_report.get("tile_zoom"),
                    tiles,
                    camera_report.get("error"),
                    *_road_feature_table_cells(camera_report),
                ]
            )
        )
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, object], paths: RoadFeaturePaths) -> None:
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    paths.json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    paths.summary_path.write_text(build_summary_markdown(report), encoding="utf-8")


def write_all_camera_report(report: dict[str, object], paths: RoadFeaturePaths) -> None:
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    paths.json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    paths.summary_path.write_text(build_all_camera_summary_markdown(report), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate Mapbox Outdoors road vector-tile feature diagnostics.")
    arguments = (
        (("camera",), {"nargs": "?"}),
        (("--all-cameras",), {"action": "store_true", "help": "Inspect every comparison harness camera."}),
        (("--style-json",), {"type": Path, "help": "Read an already downloaded Mapbox style JSON file."}),
        (("--style-owner",), {"default": DEFAULT_MAPBOX_STYLE_OWNER}),
        (("--style-id",), {"default": DEFAULT_MAPBOX_STYLE_ID}),
        (("--mapbox-token",), {"help": "Mapbox token. Prefer MAPBOX_ACCESS_TOKEN or QFIT_MAPBOX_ACCESS_TOKEN."}),
        (("--tile-zoom",), {"type": int, "help": "Override the integer vector-tile zoom to inspect."}),
        (("--output-root",), {"type": Path, "default": DEFAULT_OUTPUT_ROOT}),
    )
    for names, options in arguments:
        parser.add_argument(*names, **options)
    return parser


def _config_from_args(args: argparse.Namespace, now: dt.datetime) -> RoadFeatureConfig:
    config_kwargs = {
        "token": resolve_mapbox_token(provided_token=args.mapbox_token),
        "output_root": args.output_root,
        "camera_name": args.camera or DEFAULT_CAMERA_NAME,
        "style_owner": args.style_owner,
        "style_id": args.style_id,
        "style_json_path": args.style_json,
        "tile_zoom": args.tile_zoom,
        "now": now,
    }
    return RoadFeatureConfig(**config_kwargs)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.all_cameras and args.camera:
        print("error: pass either a single camera or --all-cameras, not both.", file=sys.stderr)
        return 2
    config = _config_from_args(args, dt.datetime.now(dt.timezone.utc))
    if args.all_cameras:
        report = collect_all_camera_road_feature_report(config)
        paths = build_all_camera_road_feature_paths(
            build_run_directory(output_root=config.output_root, camera_name="all-cameras", now=config.now)
        )
        write_all_camera_report(report, paths)
    else:
        report = collect_road_feature_report(config)
        paths = build_road_feature_paths(
            build_run_directory(output_root=config.output_root, camera_name=config.camera_name, now=config.now)
        )
        write_report(report, paths)
    print(paths.summary_path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
