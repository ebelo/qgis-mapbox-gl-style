# QFIT

**QFIT — Explore fitness data spatially in QGIS**

QFIT is a QGIS plugin for importing and visualizing fitness activity data in a spatial workflow.

## Vision

Start with Strava activity import via API, load activities into a GeoPackage, and provide interactive filtering and visualization inside QGIS.

Longer term, QFIT can expand to support additional sources and formats such as:
- FIT files
- GPX
- TCX
- Garmin exports
- COROS exports
- Suunto exports

## MVP goals

- Connect to Strava via API credentials
- Download user activities
- Store activities and track geometry in a GeoPackage
- Load resulting layers into QGIS
- Filter activities by:
  - type
  - date
  - distance
  - elapsed time
- Apply basic visualization presets

## Planned visualizations

- activity lines
- heatmap-style density layers
- start point layers
- clustered start points
- style presets by activity type

## Initial structure

- `metadata.txt` — QGIS plugin metadata
- `__init__.py` — QGIS plugin entrypoint
- `qfit_plugin.py` — main plugin class
- `qfit_dockwidget.py` — UI dock widget scaffold
- `qfit_dockwidget_base.ui` — Qt Designer UI layout
- `resources.qrc` / `resources.py` — Qt resources
- `strava_client.py` — API client scaffold
- `gpkg_writer.py` — GeoPackage writing scaffold

## MVP architecture

### Data flow
1. User configures Strava credentials
2. Plugin fetches activities from Strava
3. Activities are normalized into internal models
4. Data is written to a GeoPackage
5. GeoPackage layers are loaded into QGIS
6. User filters and styles the loaded layers

### Main components
- **Plugin UI**: toolbar action + dock widget
- **Strava client**: authentication and activity retrieval
- **Data writer**: GeoPackage persistence
- **Layer manager**: add/update QGIS layers
- **Styling engine**: apply rendering presets

## Development notes

This repository currently contains a clean scaffold intended to become a proper QGIS Python plugin.

## License

TBD
