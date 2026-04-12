# qfit

**qfit — Explore fitness data spatially in QGIS**

qfit is a QGIS plugin for importing and visualizing fitness activity data in a spatial workflow.

## Current MVP+

The current implementation supports:
- Strava API connection using `client_id`, `client_secret`, and `refresh_token`
- a built-in OAuth helper to open the Strava authorize page and exchange an authorization code for a refresh token
- downloading all athlete activities from Strava (full sync)
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
- tuning atlas-page padding, minimum extent, and target aspect ratio directly from the plugin before rebuilding publish layers, with the built-in PDF exporter defaulting that ratio to its real map frame so tracks stay inside the visible extent
- generating atlas pages in a stable chronological order with page numbers and TOC-friendly labels for QGIS layouts
- adding Web Mercator-ready atlas metadata (`center_x_3857`, `center_y_3857`, `extent_width_m`, `extent_height_m`) for layout work in EPSG:3857
- precomputing route-profile-ready atlas metadata (`profile_available`, sampled profile distance, min/max elevation, relief, gain/loss, and layout-friendly labels) when detailed stream metrics are available
- exposing publish-friendly detail labels on atlas pages (`page_toc_label`, `page_average_speed_label`, `page_average_pace_label`, `page_elevation_gain_label`, `page_stats_summary`, `page_profile_summary`) so layouts can show activity stats with less expression boilerplate
- stamping atlas-document / cover-ready summary fields (`document_activity_count`, `document_date_range_label`, `document_total_distance_label`, `document_total_duration_label`, `document_total_elevation_gain_label`, `document_activity_types_label`, `document_cover_summary`) onto every atlas page so QGIS layouts can reuse them directly
- writing an `atlas_document_summary` helper table with the atlas-wide totals and labels as a single row for cover/TOC layouts that prefer a dedicated document summary source
- writing an `atlas_cover_highlights` helper table with one ordered cover-metric row per highlight so QGIS cover pages can bind simple label/value cards without custom expressions
- writing an `atlas_page_detail_items` helper table with ordered per-page label/value rows so activity detail blocks can use layout tables or repeated labels without expression boilerplate
- writing an `atlas_toc_entries` helper table with one row per atlas page so QGIS table-of-contents layouts can bind to a clean non-spatial TOC source instead of the atlas polygons
- writing an `atlas_profile_samples` helper table with one row per sampled distance/elevation point so QGIS layouts can build route-profile charts from atlas-ready per-page data
- loading those layers directly into QGIS with EPSG:3857 as the working project/map CRS
- adding an optional Mapbox background layer through saved plugin settings, with an explicit background-map Load button and basemap ordering kept below the activity layers
- filtering by activity type, activity-name search, date range, minimum/maximum distance, and detailed-stream availability
- previewing fetched activities with a dock-side summary and sortable recent-activity list before loading layers
- applying visualization presets including lines, track points, heatmaps, and start-point views
- surfacing reusable contextual help in the dock with clearer labels, consistent tooltips, inline helper text, and lightweight `?` affordances on the most confusing controls
- exporting a per-activity PDF atlas from the dock with a single "Generate Atlas PDF" button, using a programmatic QgsPrintLayout with atlas coverage from `activity_atlas_pages`, showing activity title, date, stats summary, and a map frame centred on each activity extent; export runs off the main thread via QgsTask so the QGIS UI stays responsive

## Current GeoPackage model

qfit now uses the GeoPackage as a local sync store plus visualization container.

Internal tables:
- `activity_registry` — canonical source of truth for synced activities
- `sync_state` — sync cursor / status metadata

Visible layers:
- `activity_tracks` — line layer for activity geometries
- `activity_starts` — start-point layer
- `activity_points` — optional sampled point layer derived from detailed streams, with per-point stream metrics and derived timestamps when available
- `activity_atlas_pages` — polygon layer of atlas/page extents with titles/subtitles plus page numbers and TOC-friendly labels for QGIS print layouts
- `atlas_document_summary` — single-row helper table with atlas-wide totals/labels for cover and table-of-contents layouts
- `atlas_cover_highlights` — ordered label/value helper rows for cover-page metric cards or summary lists
- `atlas_page_detail_items` — ordered per-page label/value helper rows for activity detail panels in print layouts
- `atlas_toc_entries` — one-row-per-page helper table with TOC-ready labels for print-layout tables and cover/contents compositions
- `atlas_profile_samples` — one-row-per-profile-point helper table with sampled distance/elevation values for route-profile diagrams in print layouts

## Planned next expansions

- provider adapters for FIT / GPX / TCX imports
- richer temporal styling / playback presets on top of the new QGIS temporal wiring
- richer PDF atlas options: table of contents page, inline elevation profile charts
- richer symbology and density workflows
- better packaging and release automation
- broader scripted integration coverage inside a real QGIS environment

## Architecture

qfit is being evolved as a **modular monolith** with pragmatic **ports-and-adapters** boundaries.

In practice, that means:
- keep qfit as one plugin/package
- prefer feature/workflow ownership over a flat pile of technical modules
- keep `QfitDockWidget` focused on UI glue
- move orchestration into controllers/services/use cases
- keep provider-neutral logic easier to test than QGIS-heavy adapters
- introduce ports/gateways only when they clarify workflows or reduce coupling

See `docs/architecture.md` for the contributor-facing boundary rules and placement guide.

## Plugin structure

- `metadata.txt` — QGIS plugin metadata
- `__init__.py` — QGIS plugin entrypoint
- `qfit_plugin.py` — main plugin class
- `qfit_dockwidget.py` — dock widget UI logic
- `qfit_dockwidget_base.ui` — Qt Designer UI layout
- `providers/domain/` — provider-neutral provider contracts (`provider.py`)
- `providers/infrastructure/` — provider adapters such as the Strava client/provider implementation
- `provider.py` / `strava_client.py` / `strava_provider.py` — compatibility import shims for the provider feature package
- `activities/domain/` — provider-neutral activity core (`models.py`, `activity_classification.py`, `activity_query.py`)
- `models.py` / `activity_query.py` / `activity_classification.py` — compatibility import shims for the activity domain core
- `polyline_utils.py` — encoded polyline decoding
- `time_utils.py` — ISO timestamp parsing / offset helpers
- `activity_storage.py` — small activity storage port plus the GeoPackage-backed adapter
- `sync_repository.py` — GeoPackage registry persistence and sync metadata upserts
- `gpkg_writer.py` — derived GeoPackage layer rebuilds via QGIS APIs, depending on the storage port/adapter seam
- `layer_manager.py` — layer loading, filtering, styling, and background-map wiring
- `visualization/map_style.py` — semantic activity-color mapping and basemap-aware line-style rules
- `mapbox_config.py` — background-map preset resolution and Mapbox XYZ URL helpers
- `visualization/application/temporal_config.py` — reusable temporal-playback field selection and expression helpers
- `qfit_cache.py` — local cache for detailed stream bundles
- `publish_atlas.py` — atlas/page extent planning helpers for QGIS print layouts
- `atlas_export_task.py` — QgsTask-based PDF atlas export (programmatic QgsPrintLayout + QgsLayoutExporter)
- `atlas_export_controller.py` — atlas export orchestration extracted from the dock widget
- `visualization/application/background_map_controller.py` — background map wiring and basemap orchestration
- `ui/contextual_help.py` — reusable contextual help entries for dock widget controls
- `activities/application/fetch_task.py` — QgsTask wrapper for background Strava fetching
- `activities/application/load_workflow.py` / `visualization/application/visual_apply.py` / `atlas/export_service.py` — workflow services with explicit request/result dataclasses to keep dock-widget calls structured during the architecture migration
- `load_workflow.py` / `visual_apply.py` — compatibility import shims for migrated workflow modules
- `settings_port.py` — small application-facing settings access contract
- `settings_service.py` — QGIS-backed settings adapter implementing that contract
- `sync_controller.py` — fetch/sync orchestration bridging the dock widget and Strava client
- `scripts/install_plugin.py` — install qfit into a local QGIS profile for testing
- `scripts/uninstall_plugin.py` — remove qfit from a local QGIS profile
- `scripts/package_plugin.py` — build a release-style plugin archive
- `docs/schema.md` — current schema design
- `docs/strava-setup.md` — Strava setup and OAuth notes
- `docs/qgis-testing.md` — local QGIS testing workflow
- `docs/map-style-guide.md` — semantic color palette and basemap-aware style rules

## How the current workflow works

The dock is organized around the main qfit workflow. Orchestration is split across dedicated controllers/services owned by feature packages: `SyncController` handles fetch/sync logic, `visualization.application.BackgroundMapController` manages basemap wiring, `visualization.application.VisualApplyService` applies styling/filter workflows, and `AtlasExportController` drives PDF atlas export — keeping the dock widget focused on UI wiring.

1. **Connect** — enter your Strava app credentials and use the built-in OAuth helper if you still need a refresh token
2. **Fetch activities** — choose paging limits and any activity filters you want to use for previewing; the fetch runs in a background `QgsTask` via `StravaFetchTask` so the QGIS UI stays responsive; the fetch always performs a full sync, and date filters apply only to the preview and loaded layers
3. Optionally enable detailed Strava track streams; qfit keeps the detailed-track limit hidden until that mode is turned on
4. Click **Fetch activities** to preview what qfit will work with before anything is written to disk
5. **Store data** — choose an output `.gpkg` file and optionally enable sampled `activity_points` generation for analysis
6. Click **Store and load layers** to sync the fetched result into the GeoPackage and load the full qfit layers into QGIS without auto-applying dock subset filters to the layer tables
7. **Visualize** — optionally enable a Mapbox basemap, choose a preset such as Outdoor, Light, Satellite, or a custom Winter style, then click **Load basemap** when you want to add or refresh it
8. Use **Apply current filters to loaded layers** only when you want the current dock query to become a real QGIS layer subset on already-loaded layers
9. **Analyze** — switch **Temporal timestamps** to `Local activity time` or `UTC time` when you want the loaded layers wired into the QGIS temporal controller
10. **Publish / atlas** — expand the collapsed publish section only when you want to tune atlas-page margin, minimum extent, or target aspect ratio for print layouts
11. Optionally use the loaded `qfit atlas pages` layer as a starting index layer for a QGIS print layout / atlas export, using its built-in `page_number`, `page_name`, `page_date`, `page_toc_label`, `page_distance_label`, `page_duration_label`, `page_average_speed_label`, `page_average_pace_label`, `page_elevation_gain_label`, `page_stats_summary`, `page_profile_summary`, `document_activity_count`, `document_date_range_label`, `document_total_distance_label`, `document_total_duration_label`, `document_total_elevation_gain_label`, `document_activity_types_label`, `document_cover_summary`, `sport_type`, `total_elevation_gain_m`, `center_x_3857`, `center_y_3857`, `extent_width_m`, `extent_height_m`, `profile_available`, `profile_distance_m`, `profile_distance_label`, `profile_altitude_range_label`, `profile_relief_m`, `profile_elevation_gain_m`, `profile_elevation_gain_label`, and `profile_elevation_loss_label` fields for layout text, conditional profile frames, cover/TOC summaries, or Web Mercator layout logic
12. If you want a single atlas-wide record for a cover page or table-of-contents layout, read the `atlas_document_summary` table from the GeoPackage and reuse its `activity_count`, `date_range_label`, `total_distance_label`, `total_duration_label`, `total_elevation_gain_label`, `activity_types_label`, and `cover_summary` fields directly
13. If you want a simple cover-page metric grid or highlight list, bind a layout table or labels to the ordered `atlas_cover_highlights` rows (`highlight_label`, `highlight_value`) instead of rebuilding those strings in expressions
14. If you want a reusable per-page activity detail block, bind a layout table or repeated labels to `atlas_page_detail_items` filtered by `page_number` or `page_sort_key`; it exposes ordered `detail_label` / `detail_value` rows for stat cards and side panels
15. If you want a clean per-page table source for a QGIS contents page, use the `atlas_toc_entries` table and bind a layout table or labels to its `page_number`, `page_number_label`, `page_toc_label`, `toc_entry_label`, `page_stats_summary`, and `page_profile_summary` fields instead of reading those values from the atlas polygons
16. If you want an atlas-ready route-profile data source, use the `atlas_profile_samples` table and chart or filter it by `page_number` / `page_sort_key`; it exposes ordered `distance_m`, `distance_label`, `altitude_m`, `profile_point_index`, `profile_point_ratio`, and `profile_distance_m` values for each page

## Publish / atlas settings

qfit now exposes a small publish configuration block for the generated `activity_atlas_pages` layer.

The resulting atlas-page layer is intentionally more layout-ready than a raw extent index:
- pages are ordered chronologically with a stable `page_number`
- `page_sort_key` gives QGIS a deterministic sort field for atlas or TOC tables
- `page_date`, `page_toc_label`, `page_distance_label`, `page_duration_label`, `page_average_speed_label`, `page_average_pace_label`, `page_elevation_gain_label`, `page_stats_summary`, and `page_profile_summary` reduce the need for layout expressions
- document-level summary fields (`document_activity_count`, `document_date_range_label`, `document_total_distance_label`, `document_total_duration_label`, `document_total_elevation_gain_label`, `document_activity_types_label`, `document_cover_summary`) are still repeated on every atlas page for simple per-page layout expressions
- raw `sport_type` and numeric `total_elevation_gain_m` are also preserved on each atlas page so export-time subset summaries can recompute canonical activity-type labels and climbing totals from the actually rendered atlas subset
- the GeoPackage now also includes an `atlas_document_summary` table with a single atlas-wide summary row when you prefer a dedicated cover/TOC data source
- the new `atlas_cover_highlights` table gives cover layouts an ordered label/value row source for simple metric cards or summary lists without custom expressions
- the new `atlas_page_detail_items` table gives per-activity layouts an ordered label/value row source for detail sidebars and stat grids without per-layout expression boilerplate
- the new `atlas_toc_entries` table gives TOC layouts a clean non-spatial row source with page-number labels and preformatted entry text, so simple contents pages do not need to read from atlas polygons or rebuild numbering logic in expressions
- the new `atlas_profile_samples` table gives route-profile layouts a clean per-page chart source with ordered sampled distance/elevation rows, so profile diagrams do not need to scrape nested JSON or activity points directly
- `center_x_3857`, `center_y_3857`, `extent_width_m`, and `extent_height_m` make it easier to drive Web Mercator-oriented layout logic now that qfit uses EPSG:3857 as the working QGIS projection choice
- `profile_available`, `profile_distance_m`, `profile_distance_label`, `profile_altitude_range_label`, `profile_relief_m`, `profile_elevation_gain_m`, `profile_elevation_gain_label`, `profile_elevation_loss_m`, and `profile_elevation_loss_label` make it easier to conditionally show route-profile panels in layouts when sampled altitude/distance stream data exists, without repeating basic label formatting in QGIS expressions

Use it when you want to tune the eventual print-layout framing:
- `Atlas margin around route (%)` adds extra space around each activity extent
- `Minimum atlas extent (°)` keeps very short or compact activities readable in an atlas
- `Target page aspect ratio` optionally expands each atlas extent in Web Mercator so it better matches a fixed layout frame (for example, square pages or wider landscape compositions)

The dock now adds inline help and `?` affordances to the most confusing settings, especially detailed-track limits, point sampling, temporal timestamps, basemap setup, and the difference between writing/loading layers versus applying subset filters.

These values are saved in QGIS settings and applied the next time you write/load the GeoPackage.

## Background map settings

qfit can also add a Mapbox background layer underneath the synced activity data.

Configure these values in the dock when you want a background basemap:
- enable the background-map toggle
- paste a Mapbox access token
- choose a preset such as `Outdoor`, `Light`, or `Satellite`
- for `Winter (custom style)` or `Custom`, qfit reveals the advanced style owner / style ID fields for your own Mapbox Studio style
- click `Load basemap` when you want to add or refresh the basemap explicitly

When qfit loads the background layer, it keeps it below the qfit activity layers in the QGIS layer tree so tracks, starts, and points render on top of the basemap. qfit requests Mapbox's `512px` style tiles (without the `@2x` suffix) and marks the XYZ source with `tilePixelRatio=2`. The `512px` tile size is already Mapbox's high-density format — adding `@2x` on top would request an even larger tile that QGIS cannot compensate for correctly, resulting in blurry resampled rendering. With `512px` + `tilePixelRatio=2`, QGIS treats the tiles as true high-DPI content and adjusts zoom selection accordingly, producing crisp rendering on both standard and high-DPI displays.

The built-in presets intentionally keep the configuration small and predictable. The Winter slot is just a convenience label for a custom winter-themed style if you have one.

## Activity styling and basemap-aware rendering

qfit's `By activity type` preset now follows the semantic palette documented in `docs/map-style-guide.md`.

Highlights:
- `sport_type` is used as the preferred categorization field when it exists, with `activity_type` as a fallback
- common Strava sports map to stable semantic color families (for example runs stay red, rides orange, winter sports blue, water sports blue/cyan, indoor fitness purple, machine/virtual grey)
- the line palette stays semantically consistent while the rendering adapts to the active Mapbox context
- `Outdoor` keeps the base line weights, `Light` adds a dark casing and slightly heavier lines, and `Satellite` adds a stronger white casing plus higher opacity for readability over imagery
- unknown or future activity names fall back to a semantic family heuristic and ultimately to a neutral grey instead of failing silently

The simpler `Simple lines` preset also uses the same basemap-aware width/opacity/outline rules, so switching basemaps does not require manually restyling tracks every time.

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

Build an installable plugin zip locally with:

```bash
python -m pip install pypdf
python3 scripts/package_plugin.py
```

This writes a release-style archive to `dist/`.

The packager vendors the pure-Python `pypdf` runtime into the plugin ZIP so atlas
PDF export stays self-contained on Linux, macOS, and Windows QGIS installs.

### Automated build artifacts

qfit now also packages the plugin automatically on GitHub:

- every push/merge to `main` runs the `build` workflow and uploads the plugin ZIP as a GitHub Actions artifact
- every version tag matching `v*` runs the `release` workflow and publishes the plugin ZIP as a GitHub Release asset

That gives two distribution paths:

1. **Latest CI build**
   - open the latest successful `build` workflow run on GitHub Actions
   - download the `qfit-plugin` artifact

2. **Versioned release build**
   - create and push a tag like `v0.43.0`
   - GitHub will create a release and attach the generated plugin ZIP automatically

## Contributing and architecture

If you are changing internals, read these first:

- `CONTRIBUTING.md` — workflow, tests, SonarCloud, and PR quality gates
- `docs/architecture.md` — intended module boundaries, dependency direction, and placement rules
- `docs/atlas-validation-harness.md` — supported atlas/export validation scenarios and artifact workflow

## Testing

qfit includes a comprehensive test suite for all core, QGIS-independent modules and an optional headless PyQGIS smoke test.

Run everything with pytest:

```bash
python3 -m pytest tests/ -x -q
```

Or with unittest discovery:

```bash
python3 -m unittest discover -s tests -v
```

Run just the PyQGIS smoke test with:

```bash
python3 -m unittest tests.test_qgis_smoke -v
```

On machines without PyQGIS installed, the smoke test is skipped automatically.

The covered areas currently include:
- activity querying, sorting, summary formatting, and layer subset expression helpers
- filter parity between in-memory Python filtering and SQL subset string generation
- atlas-page extent/label planning helpers for publish workflows
- atlas export task orchestration and per-page filtering
- atlas export controller orchestration
- background map controller logic
- headless PyQGIS smoke coverage for GeoPackage writing/loading, EPSG:3857 project wiring, temporal expressions, atlas-layer presence, and basemap ordering
- temporal-playback field selection / expression helpers
- polyline decoding
- ISO time parsing/formatting helpers
- local stream-cache behavior
- Mapbox background preset/config resolution
- Strava normalization and helper logic
- sync repository hashing, upserts, reload behavior, and unchanged-row detection
- fetch task success/error/cancellation handling
- contextual help binding
- settings service credential storage
- narrowed exception handling verification

## Development notes

This project is beyond the original scaffold/MVP stage and uses a controller-based architecture: `SyncController`, `BackgroundMapController`, and `AtlasExportController` encapsulate orchestration logic that was previously embedded in the dock widget, keeping the UI layer thin and testable.

## License

GPL-2.0-or-later — see [LICENSE](LICENSE) for details.
