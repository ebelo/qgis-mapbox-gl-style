# Provenance

This repository was created from `ebelo/qfit` with `git filter-repo` so the Mapbox Outdoors and QGIS style adaptation work remains inspectable as git history.

The filtered repository keeps:

- the QGIS plugin entrypoint and Mapbox configuration/style conversion module
- the QGIS background map service that creates raster/vector tile layers
- the Mapbox Outdoors validation and comparison harnesses
- the tests that describe the style simplification and parity behavior
- the issue-949 Mapbox Outdoors comparison harness documentation

The new public project removes qfit's Strava, activity, atlas, and fitness-specific application code from the working tree. Some retained validation fields still use `qfit_*` names because those strings are part of the historical audit output and help compare old artifacts with new ones.

The initial iteration manifest records both original qfit commit ids and their rewritten commit ids in this repository. Future accepted iterations should use `python -m qgis_mapbox_gl_style.iterations accept` so the manifest remains append-only and renderable.
