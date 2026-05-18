from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from collections.abc import Callable, Iterable
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
MISSING_VALUE = "(missing)"
MAX_SAMPLE_FEATURES = 12
PEDESTRIAN_PATH_CLASSES = {"path", "pedestrian"}
SURFACE_STRUCTURES = {"none", "ford"}
LINE_GEOMETRY_TYPES = {"LineString", "MultiLineString"}
POLYGON_GEOMETRY_TYPES = {"Polygon", "MultiPolygon"}
LOW_ZOOM_PATH_EXCLUDED_TYPES = {"crossing", "sidewalk", "steps"}
HIGH_ZOOM_PATH_EXCLUDED_TYPES = {"steps"}
SAMPLE_PROPERTY_KEYS = (
    "class",
    "type",
    "structure",
    "layer",
    "name",
    "oneway",
    "surface",
)


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


def _count_by_property(features: Iterable[dict[str, object]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for feature in features:
        value = _feature_properties(feature).get(key, MISSING_VALUE)
        if isinstance(value, (dict, list)):
            value = json.dumps(value, sort_keys=True, separators=(",", ":"))
        normalized = str(value)
        counts[normalized] = counts.get(normalized, 0) + 1
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
    pedestrian_polygons = [feature for feature in road_features if is_pedestrian_polygon_candidate(feature)]
    pedestrian_lines = [feature for feature in road_features if is_pedestrian_line_candidate(feature)]
    path_lines = [feature for feature in road_features if is_path_line_candidate(feature, tile_zoom=tile.get("z"))]
    return {
        **tile,
        "status": "decoded",
        "byte_count": len(tile_bytes),
        "road_feature_count": len(road_features),
        "pedestrian_polygon_candidate_count": len(pedestrian_polygons),
        "pedestrian_line_candidate_count": len(pedestrian_lines),
        "path_line_candidate_count": len(path_lines),
        "road_geometry_type_counts": _count_geometry_types(road_features),
        "pedestrian_polygon_class_counts": _count_by_property(pedestrian_polygons, "class"),
        "pedestrian_polygon_type_counts": _count_by_property(pedestrian_polygons, "type"),
        "pedestrian_polygon_structure_counts": _count_by_property(pedestrian_polygons, "structure"),
        "pedestrian_line_type_counts": _count_by_property(pedestrian_lines, "type"),
        "path_line_type_counts": _count_by_property(path_lines, "type"),
        "sample_pedestrian_polygons": [
            _feature_sample(tile, feature) for feature in pedestrian_polygons[:MAX_SAMPLE_FEATURES]
        ],
        "sample_pedestrian_lines": [
            _feature_sample(tile, feature) for feature in pedestrian_lines[:MAX_SAMPLE_FEATURES]
        ],
        "sample_path_lines": [_feature_sample(tile, feature) for feature in path_lines[:MAX_SAMPLE_FEATURES]],
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
        "pedestrian_polygon_candidate_count": sum(
            int(tile.get("pedestrian_polygon_candidate_count") or 0) for tile in tile_records
        ),
        "pedestrian_line_candidate_count": sum(
            int(tile.get("pedestrian_line_candidate_count") or 0) for tile in tile_records
        ),
        "path_line_candidate_count": sum(int(tile.get("path_line_candidate_count") or 0) for tile in tile_records),
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
        "pedestrian_line_type_counts": _combined_record_counts(tile_records, "pedestrian_line_type_counts"),
        "path_line_type_counts": _combined_record_counts(tile_records, "path_line_type_counts"),
        "sample_pedestrian_polygons": _combined_samples(tile_records, "sample_pedestrian_polygons"),
        "sample_pedestrian_lines": _combined_samples(tile_records, "sample_pedestrian_lines"),
        "sample_path_lines": _combined_samples(tile_records, "sample_path_lines"),
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
    camera_reports = [
        collect_road_feature_report(
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
        for camera_name in names
    ]
    return {
        "style_owner": config.style_owner,
        "style_id": config.style_id,
        "generated": generated.isoformat(),
        "camera_count": len(camera_reports),
        "tile_count": _sum_reports(camera_reports, "tile_count"),
        "decoded_tile_count": _sum_reports(camera_reports, "decoded_tile_count"),
        "failed_tile_count": _sum_reports(camera_reports, "failed_tile_count"),
        "road_feature_count": _sum_reports(camera_reports, "road_feature_count"),
        "pedestrian_polygon_candidate_count": _sum_reports(
            camera_reports,
            "pedestrian_polygon_candidate_count",
        ),
        "pedestrian_line_candidate_count": _sum_reports(
            camera_reports,
            "pedestrian_line_candidate_count",
        ),
        "path_line_candidate_count": _sum_reports(camera_reports, "path_line_candidate_count"),
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
        "pedestrian_line_type_counts": _combined_record_counts(
            camera_reports,
            "pedestrian_line_type_counts",
        ),
        "path_line_type_counts": _combined_record_counts(camera_reports, "path_line_type_counts"),
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
        f"Road features: {report.get('road_feature_count')}",
        f"Pedestrian/path polygon candidates: {report.get('pedestrian_polygon_candidate_count')}",
        f"Pedestrian line candidates: {report.get('pedestrian_line_candidate_count')}",
        f"Path line candidates: {report.get('path_line_candidate_count')}",
        f"Pedestrian polygon type counts: {_markdown_value(report.get('pedestrian_polygon_type_counts'))}",
        f"Pedestrian line type counts: {_markdown_value(report.get('pedestrian_line_type_counts'))}",
        f"Path line type counts: {_markdown_value(report.get('path_line_type_counts'))}",
        "",
        "| z | x | y | Status | Road | Pedestrian/path polygons | Pedestrian lines | Path lines | Polygon types | Pedestrian line types | Path line types |",
        "| ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    tiles = report.get("tiles")
    tile_rows = tiles if isinstance(tiles, list) else []
    for tile in tile_rows:
        if not isinstance(tile, dict):
            continue
        lines.append(
            "| {z} | {x} | {y} | {status} | {road} | {polygons} | {pedestrian_lines} | {path_lines} | {polygon_types} | {pedestrian_types} | {path_types} |".format(
                z=_markdown_value(tile.get("z")),
                x=_markdown_value(tile.get("x")),
                y=_markdown_value(tile.get("y")),
                status=_markdown_value(tile.get("status")),
                road=_markdown_value(tile.get("road_feature_count")),
                polygons=_markdown_value(tile.get("pedestrian_polygon_candidate_count")),
                pedestrian_lines=_markdown_value(tile.get("pedestrian_line_candidate_count")),
                path_lines=_markdown_value(tile.get("path_line_candidate_count")),
                polygon_types=_markdown_value(tile.get("pedestrian_polygon_type_counts")),
                pedestrian_types=_markdown_value(tile.get("pedestrian_line_type_counts")),
                path_types=_markdown_value(tile.get("path_line_type_counts")),
            )
        )
    sample_sections = (
        ("Sample pedestrian/path polygon candidates", "sample_pedestrian_polygons"),
        ("Sample pedestrian line candidates", "sample_pedestrian_lines"),
        ("Sample path line candidates", "sample_path_lines"),
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
    lines = [
        "# Mapbox Outdoors road feature diagnostic - all cameras",
        "",
        f"Generated: {report.get('generated')}",
        f"Style: {report.get('style_owner')}/{report.get('style_id')}",
        f"Cameras: {report.get('camera_count')}",
        f"Tiles: {report.get('decoded_tile_count')}/{report.get('tile_count')} decoded",
        f"Road features: {report.get('road_feature_count')}",
        f"Pedestrian/path polygon candidates: {report.get('pedestrian_polygon_candidate_count')}",
        f"Pedestrian line candidates: {report.get('pedestrian_line_candidate_count')}",
        f"Path line candidates: {report.get('path_line_candidate_count')}",
        f"Pedestrian polygon type counts: {_markdown_value(report.get('pedestrian_polygon_type_counts'))}",
        f"Pedestrian line type counts: {_markdown_value(report.get('pedestrian_line_type_counts'))}",
        f"Path line type counts: {_markdown_value(report.get('path_line_type_counts'))}",
        "",
        "| Camera | Camera zoom | Tile zoom | Tiles | Road | Pedestrian/path polygons | Pedestrian lines | Path lines | Polygon types | Pedestrian line types | Path line types |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    camera_reports = report.get("cameras")
    rows = camera_reports if isinstance(camera_reports, list) else []
    for camera_report in rows:
        if not isinstance(camera_report, dict):
            continue
        camera = camera_report.get("camera") if isinstance(camera_report.get("camera"), dict) else {}
        lines.append(
            "| {camera_name} | {camera_zoom} | {tile_zoom} | {tiles} | {road} | {polygons} | {pedestrian_lines} | {path_lines} | {polygon_types} | {pedestrian_types} | {path_types} |".format(
                camera_name=_markdown_value(camera.get("name")),
                camera_zoom=_markdown_value(camera.get("zoom")),
                tile_zoom=_markdown_value(camera_report.get("tile_zoom")),
                tiles=(
                    f"{_markdown_value(camera_report.get('decoded_tile_count'))}/"
                    f"{_markdown_value(camera_report.get('tile_count'))}"
                ),
                road=_markdown_value(camera_report.get("road_feature_count")),
                polygons=_markdown_value(camera_report.get("pedestrian_polygon_candidate_count")),
                pedestrian_lines=_markdown_value(camera_report.get("pedestrian_line_candidate_count")),
                path_lines=_markdown_value(camera_report.get("path_line_candidate_count")),
                polygon_types=_markdown_value(camera_report.get("pedestrian_polygon_type_counts")),
                pedestrian_types=_markdown_value(camera_report.get("pedestrian_line_type_counts")),
                path_types=_markdown_value(camera_report.get("path_line_type_counts")),
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
