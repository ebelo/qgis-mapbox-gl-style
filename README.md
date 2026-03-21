# qfit

**qfit — Explore fitness data spatially in QGIS**

qfit is a QGIS plugin for importing and visualizing fitness activity data in a spatial workflow.

## Current MVP+

The current implementation supports:
- Strava API connection using `client_id`, `client_secret`, and `refresh_token`
- a built-in OAuth helper to open the Strava authorize page and exchange an authorization code for a refresh token
- downloading recent athlete activities from Strava
- date-bounded fetches based on the selected filter window
- optional detailed Strava track streams for higher-fidelity geometries
- local caching of detailed stream bundles to reduce repeated API calls
- a simple Strava rate-limit guard for detailed-stream enrichment
- upserting fetched activities into a canonical local GeoPackage registry
- rebuilding visible track and start-point layers from that registry
- optionally writing an `activity_points` analysis layer from detailed stream geometry
- attaching sampled stream metrics to `activity_points` when available, including time, distance, elevation, heart rate, cadence, power, speed, temperature, grade, and moving-state flags
- deriving absolute sampled timestamps for `activity_points` in UTC and local activity time when stream offsets are available
- wiring loaded qfit layers into QGIS temporal playback using local or UTC timestamps when available
- generating an `activity_atlas_pages` layer with print-ready page extents and labels for QGIS atlas layouts
- tuning atlas-page padding and minimum extent directly from the plugin before rebuilding publish layers
- loading those layers directly into QGIS
- adding an optional Mapbox background layer through saved plugin settings
- filtering by activity type, activity-name search, date range, minimum/maximum distance, and detailed-stream availability
- previewing fetched activities with a dock-side summary and sortable recent-activity list before loading layers
- applying visualization presets including lines, track points, heatmaps, and start-point views

## Current GeoPackage model

qfit now uses the GeoPackage as a local sync store plus visualization container.

Internal tables:
- `activity_registry` — canonical source of truth for synced activities
- `sync_state` — sync cursor / status metadata

Visible layers:
- `activity_tracks` — line layer for activity geometries
- `activity_starts` — start-point layer
- `activity_points` — optional sampled point layer derived from detailed streams, with per-point stream metrics and derived timestamps when available
- `activity_atlas_pages` — polygon layer of atlas/page extents with titles/subtitles for QGIS print layouts

## Planned next expansions

- provider adapters for FIT / GPX / TCX imports
- richer temporal styling / playback presets on top of the new QGIS temporal wiring
- PDF/layout automation on top of the new atlas-page layer
- richer symbology and density workflows
- better packaging and release automation
- repeatable integration tests inside a real QGIS environment

## Plugin structure

- `metadata.txt` — QGIS plugin metadata
- `__init__.py` — QGIS plugin entrypoint
- `qfit_plugin.py` — main plugin class
- `qfit_dockwidget.py` — dock widget UI logic
- `qfit_dockwidget_base.ui` — Qt Designer UI layout
- `strava_client.py` — Strava authentication and activity retrieval
- `models.py` — canonical activity model
- `polyline_utils.py` — encoded polyline decoding
- `time_utils.py` — ISO timestamp parsing / offset helpers
- `sync_repository.py` — canonical GeoPackage registry + sync metadata upserts
- `gpkg_writer.py` — derived GeoPackage layer rebuilds via QGIS APIs
- `activity_query.py` — reusable activity filtering, sorting, summary, preview, and subset-expression helpers
- `layer_manager.py` — layer loading, filtering, styling, and background-map wiring
- `mapbox_config.py` — background-map preset resolution and Mapbox XYZ URL helpers
- `temporal_config.py` — reusable temporal-playback field selection and expression helpers
- `qfit_cache.py` — local cache for detailed stream bundles
- `publish_atlas.py` — atlas/page extent planning helpers for QGIS print layouts
- `scripts/install_plugin.py` — install qfit into a local QGIS profile for testing
- `scripts/uninstall_plugin.py` — remove qfit from a local QGIS profile
- `docs/schema.md` — current schema design
- `docs/strava-setup.md` — Strava setup and OAuth notes
- `docs/qgis-testing.md` — local QGIS testing workflow

## How the current workflow works

1. Enter Strava credentials in the qfit dock
2. Use the built-in OAuth helper if you still need a refresh token
3. Choose how many pages of activities to fetch
4. Optionally enable detailed Strava track streams and set a limit
5. Optionally enable the `activity_points` layer and choose a point sampling stride
6. Optionally enable a Mapbox background map and choose a preset such as Outdoor, Light, Satellite, or a custom Winter style
7. Fetch activities from Strava
8. Choose an output `.gpkg` file
9. Review the fetched-activity summary / preview and refine the query if needed
10. Write + load the synced result into QGIS
11. Optionally tune atlas-page margin and minimum extent in the Publish / atlas section
12. Apply filters, style presets, temporal-playback mode, and background-map updates
13. Optionally use the loaded `qfit atlas pages` layer as a starting index layer for a QGIS print layout / atlas export

## Publish / atlas settings

qfit now exposes a small publish configuration block for the generated `activity_atlas_pages` layer.

Use it when you want to tune the eventual print-layout framing:
- `Page margin (%)` adds extra space around each activity extent
- `Minimum page extent (°)` keeps very short or compact activities readable in an atlas

These values are saved in QGIS settings and applied the next time you write/load the GeoPackage.

## Background map settings

qfit can also add a Mapbox background layer underneath the synced activity data.

Configure these values in the dock when you want a background basemap:
- enable the background-map toggle
- paste a Mapbox access token
- choose a preset such as `Outdoor`, `Light`, or `Satellite`
- for `Winter (custom style)` or `Custom`, provide the Mapbox style owner and style ID from your own Studio style

The built-in presets intentionally keep the configuration small and predictable. The Winter slot is just a convenience label for a custom winter-themed style if you have one.

## Strava credentials

You need:
- `client_id`
- `client_secret`
- `refresh_token`

qfit helps with the refresh-token step:
- open the Strava authorize page from inside the plugin
- paste the returned authorization code
- exchange it for a refresh token inside qfit

These values are currently stored locally through QGIS settings for convenience.

See also:
- `docs/strava-setup.md`

## Packaging

Build an installable plugin zip with:

```bash
python3 scripts/package_plugin.py
```

This writes a release-style archive to `dist/`.

## Testing

qfit now includes a lightweight standard-library unit test suite for the core,
QGIS-independent modules.

Run it with:

```bash
python3 -m unittest discover -s tests -v
```

The covered areas currently include:
- activity querying, sorting, summary formatting, and layer subset expression helpers
- atlas-page extent/label planning helpers for publish workflows
- temporal-playback field selection / expression helpers
- polyline decoding
- ISO time parsing/formatting helpers
- local stream-cache behavior
- Mapbox background preset/config resolution
- Strava normalization and helper logic
- sync repository hashing, upserts, and reload behavior

## Development notes

This project is now beyond the original scaffold/MVP stage and is moving toward a proper sync-oriented QGIS plugin architecture.

## License

TBD
