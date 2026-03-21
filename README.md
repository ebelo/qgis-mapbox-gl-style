# QFIT

**QFIT — Explore fitness data spatially in QGIS**

QFIT is a QGIS plugin for importing and visualizing fitness activity data in a spatial workflow.

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
- loading those layers directly into QGIS
- filtering by activity type, date range, and minimum distance
- applying visualization presets including lines, track points, heatmaps, and start-point views

## Current GeoPackage model

QFIT now uses the GeoPackage as a local sync store plus visualization container.

Internal tables:
- `activity_registry` — canonical source of truth for synced activities
- `sync_state` — sync cursor / status metadata

Visible layers:
- `activity_tracks` — line layer for activity geometries
- `activity_starts` — start-point layer
- `activity_points` — optional sampled point layer derived from detailed streams, with per-point stream metrics and derived timestamps when available

## Planned next expansions

- more explicit QGIS temporal integration and styling on top of the new point timestamps
- provider adapters for FIT / GPX / TCX imports
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
- `layer_manager.py` — layer loading, filtering, and styling
- `qfit_cache.py` — local cache for detailed stream bundles
- `scripts/install_plugin.py` — install QFIT into a local QGIS profile for testing
- `scripts/uninstall_plugin.py` — remove QFIT from a local QGIS profile
- `docs/schema.md` — current schema design
- `docs/strava-setup.md` — Strava setup and OAuth notes
- `docs/qgis-testing.md` — local QGIS testing workflow

## How the current workflow works

1. Enter Strava credentials in the QFIT dock
2. Use the built-in OAuth helper if you still need a refresh token
3. Choose how many pages of activities to fetch
4. Optionally enable detailed Strava track streams and set a limit
5. Optionally enable the `activity_points` layer and choose a point sampling stride
6. Fetch activities from Strava
7. Choose an output `.gpkg` file
8. Write + load the synced result into QGIS
9. Apply filters and style presets

## Strava credentials

You need:
- `client_id`
- `client_secret`
- `refresh_token`

QFIT helps with the refresh-token step:
- open the Strava authorize page from inside the plugin
- paste the returned authorization code
- exchange it for a refresh token inside QFIT

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

QFIT now includes a lightweight standard-library unit test suite for the core,
QGIS-independent modules.

Run it with:

```bash
python3 -m unittest discover -s tests -v
```

The covered areas currently include:
- polyline decoding
- ISO time parsing/formatting helpers
- local stream-cache behavior
- Strava normalization and helper logic
- sync repository hashing, upserts, and reload behavior

## Development notes

This project is now beyond the original scaffold/MVP stage and is moving toward a proper sync-oriented QGIS plugin architecture.

## License

TBD
