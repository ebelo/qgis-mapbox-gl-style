# QGIS Mapbox GL Style

QGIS plugin and validation harness for loading Mapbox vector tiles in QGIS and adapting Mapbox GL JS styles to the QGIS styling engine.

The first target style is Mapbox Outdoors (`mapbox/outdoors-v12`). The project keeps the qfit history that built the current QGIS symbology, then continues that work in a smaller repository focused only on Mapbox vector tiles and QGIS style parity.

## Style Maturity At A Glance

The Chamonix sequence below shows how the QGIS rendering of Mapbox Outdoors evolved from an early converter pass to a mature outdoor style with clearer terrain, hillshade relief, paths, landcover, labels, and contour balance. Each frame is a fresh QGIS render from a historical iteration of the style work.

| Early conversion | Style audit |
| --- | --- |
| ![Initial comparable QGIS render of Mapbox Outdoors around Chamonix](docs/images/style-progression/chamonix/01_initial_llm_guess.png) | ![Style audit iteration of the QGIS Mapbox Outdoors render around Chamonix](docs/images/style-progression/chamonix/02_first_visual_pass.png) |

| Symbols and labels | Opacity simplification |
| --- | --- |
| ![Sprites provided to QGIS for Mapbox Outdoors labels and symbols around Chamonix](docs/images/style-progression/chamonix/03_label_and_terrain_iteration.png) | ![Opacity simplification pass for Mapbox Outdoors in QGIS around Chamonix](docs/images/style-progression/chamonix/04_landcover_rebalance.png) |

| Landcover separation | Hillshade relief |
| --- | --- |
| ![Landcover color separation in the QGIS Mapbox Outdoors render around Chamonix](docs/images/style-progression/chamonix/05_area_fill_and_trails.png) | ![Hillshade relief preserved in the QGIS Mapbox Outdoors render around Chamonix](docs/images/style-progression/chamonix/06_road_label_refinement.png) |

| Trail legibility | Mature result |
| --- | --- |
| ![Trail overlay legibility improved in the QGIS Mapbox Outdoors render around Chamonix](docs/images/style-progression/chamonix/07_high_zoom_outdoor_detail.png) | ![Mature QGIS Mapbox Outdoors render around Chamonix with terrain, paths, labels, and contour balance](docs/images/style-progression/chamonix/08_late_vision_tuned_result.png) |

## What It Does

- adds a QGIS action to load Mapbox Outdoors as a vector tile layer
- fetches the Mapbox style JSON and sprite sheet at runtime
- simplifies Mapbox GL expressions that QGIS cannot convert directly
- applies the converted renderer and labeling with QGIS' `QgsMapBoxGlStyleConverter`
- post-processes labels, line styles, terrain fills, road shields, contour labels, paths, and other Outdoors-specific details from the qfit work
- keeps an iteration manifest so historical and future style snapshots can be rendered and compared

No Mapbox token, tile payload, sprite payload, downloaded style JSON, or render output is committed.

## QGIS Plugin Use

Install or package the plugin as `qgis_mapbox_gl_style`.

The plugin reads the Mapbox token from QGIS settings first, then from environment variables:

```bash
export QGIS_MAPBOX_GL_STYLE_MAPBOX_TOKEN="pk..."
```

The generic `MAPBOX_ACCESS_TOKEN` variable is also supported. `QFIT_MAPBOX_ACCESS_TOKEN` remains supported only for old validation scripts and local migration continuity.

In QGIS:

1. Enable the plugin.
2. Open `QGIS Mapbox GL Style -> Settings`.
3. Enter a Mapbox token, style owner, style id, and tile mode.
4. Run `QGIS Mapbox GL Style -> Load Mapbox Outdoors`.

The default style is `mapbox/outdoors-v12` in vector mode.

## Iterations

Iterations are stored in `iterations/index.json`. Each entry records:

- a stable iteration id
- the original qfit commit
- the rewritten commit in this repository
- source pull requests and issue references
- the style owner/id and camera set
- notes about the visual/style milestone

List iterations:

```bash
python -m qgis_mapbox_gl_style.iterations list
```

Render one historical iteration:

```bash
python -m qgis_mapbox_gl_style.iterations render --iteration 006 --camera-set issue-949
```

The render command creates a detached git worktree at the selected filtered commit and runs the retained comparison harness from that snapshot. Artifacts are written under `debug/iterations/<iteration>/<timestamp>/`, which is ignored by git.

Use `--dry-run` to inspect the exact worktree and render command without executing QGIS/browser rendering.

Accept a future agent run as the next iteration:

```bash
python -m qgis_mapbox_gl_style.iterations accept --from-run debug/iterations/012/20260620T120000Z --label "Improve contour label placement"
```

That appends the next manifest entry against the current repository `HEAD`.

## Validation Harness

The retained validation scripts live under `validation/` and are intentionally close to their qfit originals. Some report fields still say `qfit_*` or "qfit preprocessing" because those names are part of the historical audit output and tests.

The main visual comparison harness is:

```bash
python validation/mapbox_outdoors_comparison.py --all-cameras --output-root debug/mapbox-outdoors-comparison
```

See [docs/mapbox-outdoors-comparison-harness.md](docs/mapbox-outdoors-comparison-harness.md) for the full harness workflow.

## Packaging

Build a QGIS install ZIP:

```bash
python scripts/package_plugin.py
```

The package is written to `dist/qgis_mapbox_gl_style-<version>.zip`.

## Provenance

This repository was seeded from the Mapbox/QGIS parts of `ebelo/qfit` with `git filter-repo`, so the style work remains inspectable as real git history instead of a copied final snapshot.

More detail is in [docs/provenance.md](docs/provenance.md).
