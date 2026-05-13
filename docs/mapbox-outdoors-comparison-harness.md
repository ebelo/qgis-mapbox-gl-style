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
- `manifest.json` — camera, output paths, capture status, and metrics without any token values

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
| `chamonix-trails-z14-outdoors` | z13-z14 | Local outdoor detail: paths/trails, minor roads, POIs, label emphasis. |
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
The parent run also writes token-free aggregate `summary.json` and `summary.md` files under `debug/mapbox-outdoors-comparison/all-cameras/<timestamp>/` with per-camera subprocess status, artifact status, manifest paths, captured image artifact paths, and the main diff metrics. Treat rows with missing manifests or unavailable metrics as operational smoke-test output, not visual parity evidence.

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

The audit summarizes each relevant style layer's source layer, filter, zoom band, paint/layout symbology, properties qfit preserves, properties qfit simplifies or substitutes before handing the style to QGIS, and cues that remain QGIS-dependent such as Mapbox filter expressions, sprites, patterns, non-fallback fonts, or still-live paint/layout expressions. It also records simplified and unresolved properties by layer group, expression operators, filter-operator signatures, visible label-density candidate layers by visual layer group, visible road/trail hierarchy candidate layers, visible terrain/landcover palette candidate layers, visible water surface/flow candidate layers, visible sprite/icon candidate layers, and visible route overlay candidate layers with source/type/property summaries. qfit simplifies supported `layout.text-field` expressions, including nested `case`, `match`, and `step` outputs, to the best simple label field reference QGIS can resolve, drops literal empty `layout.icon-image` placeholders that represent no sprite, reduces literal non-empty zoom-step `layout.icon-image` expressions to a representative sprite name, and scalarizes conservative full-opacity `paint.line-opacity` and `paint.fill-opacity` expressions, including zoom-step opacity that is full for a layer's visible zoom range, to QGIS' equivalent default opacity while leaving data-driven partial opacity branches unresolved. Use those focused sections so follow-up work can distinguish broad buckets such as filters from concrete Mapbox operators like `match`, `step`, or `interpolate` and see whether repeated filter, label-density, road/path casing, dash, opacity, color, width, landcover palette, contour, hillshade, waterway, depth, sprite, shield, POI icon, ferry, ferry_auto, aerialway, piste, ski, golf, or transit controls concentrate in roads/trails, terrain/landcover, water, labels, or other groups.

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

## PR notes

For rendering-sensitive Mapbox vector-style changes, include a concise validation note such as:

```markdown
## Visual comparison
- Harness: `python3 validation/mapbox_outdoors_comparison.py valais-geneva-outdoors`
- Artifacts: `debug/mapbox-outdoors-comparison/valais-geneva-outdoors/<timestamp>/`
- Checked: browser reference, QGIS vector render, and diff image
- Result: summarize visible improvements and any accepted QGIS/Mapbox GL limitations
```
