# QFIT

**QFIT — Explore fitness data spatially in QGIS**

QFIT is a QGIS plugin for importing and visualizing fitness activity data in a spatial workflow.

## Current MVP

The current implementation supports:
- Strava API connection using `client_id`, `client_secret`, and `refresh_token`
- downloading recent athlete activities from Strava
- normalizing them into a shared internal activity model
- writing activity lines and activity start points to a GeoPackage
- loading those layers directly into QGIS
- filtering by activity type, date range, and minimum distance
- applying visualization presets including line styling, activity-type coloring, and start-point heatmaps

## Planned next expansions

- detailed stream geometry instead of summary polylines
- provider adapters for FIT / GPX / TCX imports
- richer symbology and density workflows
- better auth flow and token management
- packaging and release automation

## Plugin structure

- `metadata.txt` — QGIS plugin metadata
- `__init__.py` — QGIS plugin entrypoint
- `qfit_plugin.py` — main plugin class
- `qfit_dockwidget.py` — dock widget UI logic
- `qfit_dockwidget_base.ui` — Qt Designer UI layout
- `strava_client.py` — Strava authentication and activity retrieval
- `models.py` — canonical activity model
- `polyline_utils.py` — encoded polyline decoding
- `gpkg_writer.py` — GeoPackage writing via QGIS APIs
- `layer_manager.py` — layer loading, filtering, and styling
- `docs/schema.md` — first-pass schema design

## How the MVP works

1. Enter Strava credentials in the QFIT dock
2. Choose how many pages of activities to fetch
3. Fetch activities from Strava
4. Choose an output `.gpkg` file
5. Write and load the result into QGIS
6. Apply filters and style presets

## Strava credentials

You need:
- `client_id`
- `client_secret`
- `refresh_token`

These are stored locally through QGIS settings for convenience.

## GeoPackage output

QFIT currently writes two layers:
- `activities` — line features for activity tracks
- `activity_starts` — point features for activity start locations

## Development notes

This is an MVP implementation intended to validate the end-to-end workflow before broadening provider support.

## License

TBD
