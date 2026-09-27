# Chamonix style-progression images

These frames trace the Mapbox Outdoors style as rendered by QGIS' native vector-tile engine. They use the same `chamonix-trails-z14-outdoors` camera and 1280 × 900 map viewport; the surrounding header records each source revision.

`style-maturity-matrix.png` intentionally selects five milestones with a visible step between them. Frames 02, 04, 07, and 08 remain in this directory for audit history, but are omitted from the matrix because their visual change is too subtle at slide scale or substantially overlaps the following stage.

The latest frame, `09_accepted_qfit_baseline.png`, was rendered on 2026-09-27 from the integrated qFit Outdoors state (`0e79b0e`, subsequently accepted at qFit baseline `63784d0`) with Barlow and Noto registered in QGIS before rendering.

Rebuild the matrix from the committed frames:

```bash
python scripts/build_style_maturity_matrix.py
```

The same command also exports five independent, high-resolution cards under
[`pptx-assets`](pptx-assets). Each PNG is 1184 × 840 with a transparent outer
background and identical geometry, so the stages can be positioned, revealed,
or animated independently in PowerPoint without cropping the full matrix.

To wrap a new 1280 × 900 QGIS render as frame 09 and rebuild the matrix:

```bash
python scripts/build_style_maturity_matrix.py --latest-render /path/to/qgis-vector-render.png
```

The historical iteration metadata is retained in [`iterations/index.json`](../../../../iterations/index.json). Focused September evidence for shield collision behaviour and portable typography is in [`docs/visual-evidence/issue-1453`](../../../visual-evidence/issue-1453).
