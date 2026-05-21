# Mapbox Outdoors visual comparison harness

qfit includes a dev-only harness for comparing Mapbox Outdoors rendered by Mapbox GL JS in a browser with qfit's native QGIS vector-tile rendering.

The harness is a **manual visual QA aid**, not a CI gate. Pixel-perfect parity is not expected because Mapbox GL JS and QGIS differ in expression support, label placement, sprites, fonts, antialiasing, and zoom interpolation.

## What it captures

Run outputs are written under the ignored debug tree:

```text
debug/mapbox-outdoors-comparison/<camera>/<UTC timestamp>/
```

A complete run writes:

- `mapbox-gl-reference.png` — browser/Mapbox GL JS reference image
- `qgis-vector-render.png` — qfit/QGIS native vector-tile render for the same camera
- `mapbox-gl-vs-qgis-diff.png` — pixel diff for quick drift inspection
- `metrics.json` — simple image-diff metrics such as changed-pixel ratio when diff generation runs
- `qgis-label-styles.json` — token-free QGIS vector-tile label rule and label-setting snapshot when QGIS capture runs
- `manifest.json` — camera, output paths, capture status, and metrics without any token values
- `contact-sheet.jpg` — all-camera side-by-side thumbnail sheet when running the matrix mode

## Cameras

List supported cameras:

```bash
python3 validation/mapbox_outdoors_comparison.py --list-cameras
```

Recommended z5-z18 inspection matrix:

| Camera | Band | Purpose |
| --- | --- | --- |
| `switzerland-alps-z5-outdoors` | z5 | Country-wide Switzerland/Alps context: landcover, terrain/water balance, major roads, label density. |
| `valais-geneva-outdoors` | z7-z8 | Regional qfit map context: terrain/outdoor features, main road hierarchy, settlement visibility. |
| `lausanne-lavaux-z10-outdoors` | z9-z11 | Primary qfit activity-area target: road/trail hierarchy, labels, feature density, color/width balance. |
| `geneva-airport-motorway-z14-outdoors` | z14 | Motorway/urban detail around Geneva airport: road-exit shields, junction labels, urban POIs, dense road hierarchy. |
| `chamonix-trails-z14-outdoors` | z13-z14 | Local outdoor detail: paths/trails, minor roads, POIs, label emphasis. |
| `zermatt-piste-z17-outdoors` | z17 | Ski-piste stress target: colored path casings, cycleway/piste overlays, contour density, and high-zoom outdoor route legibility. |
| `zermatt-trails-z18-outdoors` | z18 | Street/trail-level stress test: casing, widths, local labels, POIs, high-detail symbols. |

## Required local tools

For a complete browser + QGIS + diff run, install or run from an environment with:

- a Mapbox access token in `MAPBOX_ACCESS_TOKEN` or `QFIT_MAPBOX_ACCESS_TOKEN`
- Node.js, the Playwright npm package, and Chromium for the Mapbox GL JS screenshot
  - from the qfit checkout or its parent dev workspace: `npm install --save-dev playwright`
  - the harness searches `node_modules` in the qfit checkout and its parent workspace
  - the harness uses system Chromium when available (`chromium`, `chromium-browser`, or `QFIT_CHROMIUM_EXECUTABLE`)
- PyQGIS available to the Python runtime for the QGIS vector render
  - for example, run from a QGIS Python shell, OSGeo/QGIS environment, or another shell where `import qgis` works
- Pillow for the diff image
  - already available in the qfit development environment; if missing, install it in a virtual environment rather than using system `sudo pip`
- QGIS/Qt support for the `offscreen` platform when running headlessly; the harness sets `QT_QPA_PLATFORM=offscreen` by default unless you provide a different value
- `xvfb-run` or another virtual display when your local Chromium/QGIS build cannot render headlessly without an X server

Keep tokens out of shell history where practical by exporting an environment variable instead of passing `--mapbox-token` directly.

## Run a complete comparison

```bash
export MAPBOX_ACCESS_TOKEN="***"
python3 validation/mapbox_outdoors_comparison.py valais-geneva-outdoors
```

The command prints only artifact paths. It does not print the token, and `manifest.json` intentionally excludes token values. The QGIS capture builds a temporary vector-tile layer for rendering, defaults Qt to the `offscreen` platform for headless validation unless `QT_QPA_PLATFORM` is already set, and does not clear the active QGIS project.

To compare against a pinned/downloaded style snapshot instead of fetching live style metadata, pass `--style-json`. The browser reference uses the same style object and the QGIS render uses it for qfit's style preprocessing; Mapbox vector tiles, glyphs, and sprites still require a token.

```bash
python3 validation/mapbox_outdoors_comparison.py \
  valais-geneva-outdoors \
  --style-json /tmp/mapbox-outdoors-v12.json
```

To refresh the full inspection matrix manually, run the z5-z18 camera matrix and compare the generated run directories:

```bash
python3 validation/mapbox_outdoors_comparison.py \
  --all-cameras \
  --style-json /tmp/mapbox-outdoors-v12.json
```

`--all-cameras` uses the same capture options as a single-camera run, so you can combine it with setup-isolation flags such as `--skip-qgis` or `--skip-browser`.
Do not pass a positional camera name with `--all-cameras`; use one mode or the other.
Each camera is captured in its own child Python process so a local Chromium/PyQGIS crash in one camera does not kill the whole matrix runner or expose token values on the command line. If any camera fails or times out, the runner continues with the remaining cameras and exits non-zero with the failed camera names.
The parent run also writes token-free aggregate `summary.json` and `summary.md` files under `debug/mapbox-outdoors-comparison/all-cameras/<timestamp>/` with per-camera subprocess status, artifact status, manifest paths, captured image artifact paths, and the main diff metrics. When captured images are available and Pillow can load them, the same directory includes `contact-sheet.jpg` so the Mapbox GL reference, QGIS render, and diff columns can be inspected as one overview image. Treat rows with missing manifests or unavailable metrics as operational smoke-test output, not visual parity evidence.

## Partial captures

When developing on a machine that only has one rendering stack available, run a partial capture:

```bash
# Browser reference only
python3 validation/mapbox_outdoors_comparison.py valais-geneva-outdoors --skip-qgis --skip-diff

# QGIS vector render only
python3 validation/mapbox_outdoors_comparison.py valais-geneva-outdoors --skip-browser --skip-diff
```

Diff generation requires both the browser and QGIS images in the same run.

## Interpreting output

Use the browser image as the Mapbox GL reference. Inspect the QGIS image for high-signal gaps such as:

- missing terrain/outdoor features
- over- or under-emphasized roads/trails
- noisy settlement or POI labels
- label size/priority mismatches
- broad color/width drift caused by expression simplification

Use the brightness-enhanced diff image and metrics as navigation aids, not as pass/fail gates. Label placement and antialiasing differences will create expected diff noise.

## Visual hotspot crops

After a full comparison run, crop the highest-delta windows when the contact sheet is too broad to choose the next tuning slice:

```bash
python3 validation/mapbox_outdoors_visual_crops.py \
  --comparison-summary-json debug/mapbox-outdoors-comparison/all-cameras/<timestamp>/summary.json \
  --crop-size 420x300 \
  --crops-per-camera 2
```

When path/pedestrian styling is under review, pass the matching focus report so each crop row is annotated with the strongest per-camera stroke-width and dash cues:

```bash
python3 validation/mapbox_outdoors_visual_crops.py \
  --comparison-summary-json debug/mapbox-outdoors-comparison/all-cameras/<timestamp>/summary.json \
  --path-pedestrian-focus-json debug/mapbox-outdoors-path-pedestrian-focus/<timestamp>/path-pedestrian-focus.json
```

To inspect only cameras that still have candidate-backed path/pedestrian focus cues, add `--focus-cue-cameras`. This is useful after a global highest-delta crop run is dominated by a different camera and hides lower-scoring stroke-width or dash candidates:

```bash
python3 validation/mapbox_outdoors_visual_crops.py \
  --comparison-summary-json debug/mapbox-outdoors-comparison/all-cameras/<timestamp>/summary.json \
  --path-pedestrian-focus-json debug/mapbox-outdoors-path-pedestrian-focus/<timestamp>/path-pedestrian-focus.json \
  --focus-cue-cameras
```

When reviewing a before/after styling probe, pass the matching comparison-delta report so each crop row includes the per-camera mean/RMS movement from the candidate run:

```bash
python3 validation/mapbox_outdoors_visual_crops.py \
  --comparison-summary-json debug/mapbox-outdoors-comparison/<candidate>/all-cameras/<timestamp>/summary.json \
  --comparison-delta-json debug/mapbox-outdoors-comparison-delta/<timestamp>/comparison-delta.json \
  --path-pedestrian-focus-json debug/mapbox-outdoors-path-pedestrian-focus/<timestamp>/path-pedestrian-focus.json \
  --focus-cue-cameras
```

The delta context is shown only when the comparison-delta candidate summary matches the crop report's comparison summary, which keeps stale probe movement from being mixed into a newer visual crop sheet.

Every crop report also includes a crop color metrics section with crop-local mean RGB values for the Mapbox GL and QGIS crops, plus QGIS-minus-Mapbox RGB/luminance deltas and the dominant color direction. Use these numbers as triage context when broad terrain, landcover, water, or tint differences dominate the hotspot sheet.
The report also ranks the largest crop color deltas first, which keeps the worst tint and terrain outliers and their dominant movement visible before scanning the full per-crop metrics table.

When reviewing broad landcover, terrain, airport, or other area-fill differences, pass the latest style audit.
The crop report includes the global terrain/landcover and airport/special-landuse candidate counts, sample layers, and compact qfit simplification snippets for sampled controls:

```bash
python3 validation/mapbox_outdoors_visual_crops.py \
  --comparison-summary-json debug/mapbox-outdoors-comparison/all-cameras/<timestamp>/summary.json \
  --style-audit-json debug/mapbox-outdoors-style-audit/mapbox-outdoors-v12/<timestamp>/audit.json
```

The focus cues are triage context only. Candidate-backed rows, source-capped rows, and zero-candidate dash rows still need visual inspection before becoming a rendering change.

## Style audit before tuning

Before choosing another rendering-tuning slice, generate a style audit so the work is tied to the actual Mapbox Outdoors layer rules and qfit's current QGIS preprocessing choices.

From the live Mapbox style JSON:

```bash
export MAPBOX_ACCESS_TOKEN="***"
python3 validation/mapbox_outdoors_style_audit.py
```

From an already downloaded style JSON, which is useful for offline review and credential-free test fixtures:

```bash
python3 validation/mapbox_outdoors_style_audit.py \
  --style-json /tmp/mapbox-outdoors-v12.json \
  --format json
```

Default audit outputs are written under:

```text
debug/mapbox-outdoors-style-audit/<style>/<UTC timestamp>/audit.md
```

The audit summarizes each relevant style layer's source layer, filter, zoom band, paint/layout symbology, properties qfit preserves, properties qfit simplifies or substitutes before handing the style to QGIS, and cues that remain QGIS-dependent such as Mapbox filter expressions, sprites, patterns, non-fallback fonts, or still-live paint/layout expressions. It also records simplified and unresolved properties by layer group, expression operators, filter-operator signatures, visible label-density candidate layers by visual layer group, visible road/trail hierarchy candidate layers, visible terrain/landcover palette candidate layers, visible water surface/flow candidate layers, visible sprite/icon candidate layers, and visible route overlay candidate layers with source/type/property summaries. qfit simplifies supported `layout.text-field` expressions, including nested `case`, `match`, and `step` outputs, to the best simple label field reference QGIS can resolve, drops literal empty `layout.icon-image` placeholders that represent no sprite, reduces literal non-empty zoom-step `layout.icon-image` expressions to a representative sprite name, moves exact zero-to-full zoom-step `paint.line-opacity` and `paint.fill-opacity` gates to layer `minzoom`, and scalarizes conservative full-opacity paint expressions, including zoom-step opacity that is full for a layer's visible zoom range, to QGIS' equivalent default opacity while leaving data-driven partial opacity branches unresolved. Use those focused sections so follow-up work can distinguish broad buckets such as filters from concrete Mapbox operators like `match`, `step`, or `interpolate` and see whether repeated filter, label-density, road/path casing, dash, opacity, color, width, landcover palette, contour, hillshade, waterway, depth, sprite, shield, POI icon, ferry, ferry_auto, aerialway, piste, ski, golf, or transit controls concentrate in roads/trails, terrain/landcover, water, labels, or other groups.

When PyQGIS is available, include QGIS' native Mapbox GL converter warnings to compare the raw Mapbox style with qfit's preprocessed style and see which warnings remain after qfit simplification. The optional warning report includes message, layer, visual layer-group, and layer-group-by-message summaries to show which converter messages concentrate in categories such as roads/trails, terrain/landcover, or labels:

```bash
QT_QPA_PLATFORM=offscreen \
python3 validation/mapbox_outdoors_style_audit.py \
  --format json \
  --include-qgis-converter-warnings
```

This optional probe does not render screenshots. It records converter warning counts, remaining warning summaries, warnings reduced by qfit preprocessing, and per-layer qfit-preprocessed warning summaries in the audit artifact so the next #949 slice can target issues QGIS itself reports, not just qfit's static style-expression audit. It also includes diagnostic filter-removal, icon-image-removal, sprite-context, line-opacity, line-dasharray, and symbol-spacing probes that report how many converter warnings would disappear if selected qfit-preprocessed properties were removed, literalized, or scalarized, plus the warnings that would still remain by message, layer group, group/message pair, layer, and non-probed qfit unresolved property where relevant. qfit now literalizes the supported line-dasharray expression shapes that this probe identified as QGIS-safe and scalarizes conservative full-opacity line/fill opacity expressions that match QGIS' default opacity, so the probes are most useful for spotting residual dash or opacity expressions that still need visual validation before any stronger simplification. The line-opacity probe also lists the scalar replacement each candidate layer would receive. These probes are upper-bound signals only, not automatic rendering-change recommendations, because Mapbox filters, sprites/icons, line opacity, unresolved line dash arrays, and symbol spacing all carry feature meaning or zoom/data-driven cartographic emphasis.

For filter-specific converter triage, add `--include-qgis-filter-parse-support`. This implies `--include-qgis-converter-warnings` and isolates each remaining qfit-preprocessed filter expression in a minimal same-type QGIS converter style. The resulting report counts how many filters the QGIS expression parser accepts, groups parser-rejected filters by visual layer group, converter warning message, and operator signature, probes fixed-z12 zoom plus parser-friendly simplifications (including additive-zero identities) for whole rejected filters, re-tests unsupported direct filter parts after fixed-z12 zoom normalization, then probes parser-friendly simplifications for the still-rejected parts. qfit applies the semantics-preserving parser-friendly rewrites that do not require zoom normalization, while the fixed-z12 rows remain diagnostic only. Treat this as parser attribution only: a parser-accepted filter can still need visual validation, while a rejected filter identifies a stronger candidate for deeper simplification research.

For a slower but broader converter-warning triage pass, add `--include-qgis-property-removal-impact`. This implies `--include-qgis-converter-warnings` and removes each remaining expression-bearing property from the qfit-preprocessed style in isolation, then ranks the warning-count delta, top affected layer groups, top affected layers, and compact expression excerpts. The resulting matrix is diagnostic only: it helps identify which residual property families, layer groups, and layer expressions are worth deeper visual investigation before promoting any rendering behavior into qfit preprocessing.

Use the audit together with the screenshot harness: first identify high-signal gaps visually, then check the corresponding style layers to decide the smallest safe qfit preprocessing improvement.

## Contour feature diagnostic

When contour labels are the candidate parity slice, inspect the underlying vector-tile geometry before changing QGIS label behavior:

```bash
export MAPBOX_ACCESS_TOKEN="***"
python3 validation/mapbox_outdoors_contour_features.py --all-cameras
```

The all-camera contour diagnostic writes compact aggregate `summary.json` and `summary.md` files under:

```text
debug/mapbox-outdoors-contour-features/all-cameras/<UTC timestamp>/
```

Use the aggregate table to check whether contour-label candidates are line-compatible, polygon-only, or absent across the z5-z18 comparison matrix. Per-camera status and error columns keep the batch useful when one camera fails before tile-level diagnostics can be collected. A polygon-only result means a Mapbox `symbol-spacing` or QGIS line-repeat tweak is not enough by itself; follow-up work should stay focused on QGIS vector-tile polygon/perimeter-label behavior or a separately validated contour-boundary overlay. The diagnostic also classifies polygon candidates as rectangular or non-rectangular and totals each shape group's boundary segments, including axis-aligned, diagonal, and bounding-box-edge segments, so perimeter-label probes can distinguish likely tile/source-boundary polygons from shapes that might plausibly follow contour boundaries.

When the contour diagnostic reports polygon-only label candidates, the screenshot harness can append a diagnostic-only QGIS polygon/perimeter label style for the `contour` source layer before rendering:

```bash
python3 validation/mapbox_outdoors_comparison.py \
  zermatt-piste-z17-outdoors \
  --qgis-contour-polygon-label-probe
```

This probe renders an extra QGIS vector-tile label rule named `contour-label-polygon-perimeter-probe`, limited to contour label candidate indices 5 and 10, with polygon geometry and curved perimeter placement. Use it to test whether QGIS can place contour labels on the polygon candidate geometry before considering a production rendering change. It is diagnostic output only; compare the browser reference, QGIS render, and diff image before treating the result as evidence for a qfit style change.

To compare QGIS' polygon placement with a line-output geometry-generator approach, use the boundary-generator probe instead:

```bash
python3 validation/mapbox_outdoors_comparison.py \
  zermatt-piste-z17-outdoors \
  --qgis-contour-boundary-generator-label-probe
```

This renders a separate diagnostic rule named `contour-label-boundary-generator-probe` for the same contour label candidates, but labels the generated `boundary($geometry)` line output with curved line placement. Use it to check whether QGIS' line-label engine behaves better on polygon-derived contour boundaries than direct polygon perimeter placement. It is also diagnostic output only.

To test whether removing obvious polygon bounding-box edges improves the contour-label candidate geometry, use the bbox-edge-difference probe:

```bash
python3 validation/mapbox_outdoors_comparison.py \
  zermatt-piste-z17-outdoors \
  --qgis-contour-bbox-edge-difference-label-probe
```

This renders a diagnostic rule named `contour-label-bbox-edge-difference-probe` using `line_merge(difference(boundary($geometry), boundary(bounds($geometry))))` as the label geometry generator. It is intended to compare whether filtering rectangular/tile-edge-like boundary pieces before QGIS line labeling reduces the over-labeling seen in the broader contour probes.

To repeat that geometry test with the converted production contour label text and buffer settings copied onto the probe, use the source-style variant:

```bash
python3 validation/mapbox_outdoors_comparison.py \
  zermatt-piste-z17-outdoors \
  --qgis-contour-bbox-edge-difference-source-style-label-probe
```

This renders a separate diagnostic rule named `contour-label-bbox-edge-difference-source-style-probe`. Use it when the visual question is whether the bbox-edge-difference geometry is still promising after matching the production `contour-label` text styling.

To test the same source-styled probe without enabling it in the z14 cameras where it can over-label slopes, use the high-zoom variant:

```bash
python3 validation/mapbox_outdoors_comparison.py \
  zermatt-piste-z17-outdoors \
  --qgis-contour-bbox-edge-difference-source-style-high-zoom-label-probe
```

This renders `contour-label-bbox-edge-difference-source-style-high-zoom-probe`, with the diagnostic rule's minimum QGIS zoom raised to 17. Use it to compare a tightly gated production candidate against the broad source-style probe; it remains diagnostic output only.

When QGIS capture runs, inspect `qgis-label-styles.json` beside the screenshots to confirm the converted label settings and any probe geometry-generator settings that were active for the render. Use that snapshot with the preprocessed style JSON before deciding whether a diagnostic probe is safe to promote into production styling.

For a table-oriented label-settings report with the same diagnostic rule appended, run:

```bash
python3 validation/mapbox_outdoors_label_settings.py \
  --qgis-contour-bbox-edge-difference-label-probe
```

Add `--qgis-contour-bbox-edge-difference-source-style-label-probe` or `--qgis-contour-bbox-edge-difference-source-style-high-zoom-label-probe` to include source-style variants in the same report.

The report keeps the probe separate from source Mapbox layers, so the summary can distinguish converted production labels from diagnostic-only QGIS rules.

## Road feature diagnostic

When road/path hierarchy, high-detail trail behavior, road shields, or oneway arrows are the candidate parity slice, inspect the underlying road vector-tile features before changing QGIS style preprocessing:

```bash
export MAPBOX_ACCESS_TOKEN="***"
python3 validation/mapbox_outdoors_road_features.py --all-cameras
```

The all-camera road diagnostic writes compact aggregate `road-features.json` and `summary.md` files under:

```text
debug/mapbox-outdoors-road-features/all-cameras/<UTC timestamp>/
```

Use the aggregate table to compare candidate road/path feature counts across the z5-z18 camera matrix before selecting a rendering slice. Per-camera status and error columns keep the batch useful when one camera fails before tile-level diagnostics can be collected.

To connect decoded road/path features with the source Mapbox style, QGIS-preprocessed style, and visual artifacts, build a path/pedestrian focus report from the road diagnostic, the matching comparison summary, and the latest style audit:

```bash
python3 validation/mapbox_outdoors_path_pedestrian_focus.py \
  --road-features-json debug/mapbox-outdoors-road-features/all-cameras/<timestamp>/road-features.json \
  --style-audit-json debug/mapbox-outdoors-style-audit/mapbox-outdoors-v12/<timestamp>/audit.json \
  --comparison-summary-json debug/mapbox-outdoors-comparison/all-cameras/<timestamp>/summary.json
```

Passing `--style-audit-json` uses the audit's source Mapbox layer records, which keeps source-vs-QGIS stroke and dash cues available without maintaining a separate downloaded style snapshot.

## PR notes

For rendering-sensitive Mapbox vector-style changes, include a concise validation note such as:

```markdown
## Visual comparison
- Harness: `python3 validation/mapbox_outdoors_comparison.py valais-geneva-outdoors`
- Artifacts: `debug/mapbox-outdoors-comparison/valais-geneva-outdoors/<timestamp>/`
- Checked: browser reference, QGIS vector render, and diff image
- Result: summarize visible improvements and any accepted QGIS/Mapbox GL limitations
```
