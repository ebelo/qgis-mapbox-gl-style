from __future__ import annotations

import argparse
import base64
import dataclasses
import datetime as dt
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, TypeAlias

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PARENT = REPO_ROOT.parent
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "debug" / "mapbox-outdoors-comparison"
DEFAULT_MAPBOX_STYLE_OWNER = "mapbox"
DEFAULT_MAPBOX_STYLE_ID = "outdoors-v12"
WEB_MERCATOR_HALF_WORLD = 20037508.342789244
WEB_MERCATOR_TILE_SIZE = 512
ImageMetrics: TypeAlias = dict[str, object]


class ComparisonCaptureError(RuntimeError):
    """Non-sensitive comparison harness failure safe to show on stderr."""


@dataclass(frozen=True)
class MapboxComparisonCamera:
    """Camera used by the manual Mapbox Outdoors comparison harness."""

    name: str
    description: str
    longitude: float
    latitude: float
    zoom: float
    width: int = 1280
    height: int = 900
    bearing: float = 0.0
    pitch: float = 0.0
    style_owner: str = DEFAULT_MAPBOX_STYLE_OWNER
    style_id: str = DEFAULT_MAPBOX_STYLE_ID

    @property
    def style_url(self) -> str:
        return f"mapbox://styles/{self.style_owner}/{self.style_id}"


CAMERAS: dict[str, MapboxComparisonCamera] = {
    "switzerland-alps-z5-outdoors": MapboxComparisonCamera(
        name="switzerland-alps-z5-outdoors",
        description=(
            "z5 broad Switzerland-Alps context for landcover, terrain/water balance, "
            "major roads, and country-scale label density."
        ),
        longitude=8.20,
        latitude=46.80,
        zoom=5.35,
        width=1280,
        height=900,
    ),
    "valais-geneva-outdoors": MapboxComparisonCamera(
        name="valais-geneva-outdoors",
        description=(
            "z7-z8 regional qfit map context spanning the Geneva/Valais corridor "
            "for terrain/outdoor features, main roads, and settlement visibility."
        ),
        longitude=7.14,
        latitude=46.20,
        zoom=8.15,
        width=1280,
        height=900,
    ),
    "lausanne-lavaux-z10-outdoors": MapboxComparisonCamera(
        name="lausanne-lavaux-z10-outdoors",
        description=(
            "z9-z11 primary qfit activity-area target around Lausanne/Lavaux for "
            "road/trail hierarchy, labels, feature density, and color/width balance."
        ),
        longitude=6.72,
        latitude=46.49,
        zoom=10.25,
        width=1280,
        height=900,
    ),
    "chamonix-trails-z14-outdoors": MapboxComparisonCamera(
        name="chamonix-trails-z14-outdoors",
        description=(
            "z13-z14 local outdoor detail around Chamonix for paths/trails, minor "
            "roads, POIs, and label emphasis."
        ),
        longitude=6.868,
        latitude=45.923,
        zoom=13.75,
        width=1280,
        height=900,
    ),
    "zermatt-trails-z18-outdoors": MapboxComparisonCamera(
        name="zermatt-trails-z18-outdoors",
        description=(
            "z18 street/trail-level stress test around Zermatt for casing, widths, "
            "local labels, POIs, and high-detail symbol behavior."
        ),
        longitude=7.748,
        latitude=46.020,
        zoom=18.0,
        width=1280,
        height=900,
    ),
}


@dataclass(frozen=True)
class ComparisonPaths:
    run_dir: Path
    browser_png: Path
    qgis_png: Path
    diff_png: Path
    metrics_json: Path
    manifest_json: Path


@dataclass(frozen=True)
class ComparisonConfig:
    camera: MapboxComparisonCamera
    token: str
    output_root: Path
    style_json_path: Path | None = None
    browser: bool = True
    qgis: bool = True
    diff: bool = True
    browser_timeout_ms: int = 120_000
    now: dt.datetime | None = None


@dataclass(frozen=True)
class ComparisonResult:
    paths: ComparisonPaths
    browser_captured: bool
    qgis_captured: bool
    diff_captured: bool
    image_metrics: dict[str, object] = dataclasses.field(default_factory=dict)
    style_json_path: str | None = None


def _utc_timestamp(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def build_run_directory(
    *,
    output_root: Path,
    camera_name: str,
    now: dt.datetime | None = None,
) -> Path:
    return output_root / camera_name / _utc_timestamp(now)


def build_comparison_paths(*, run_dir: Path) -> ComparisonPaths:
    return ComparisonPaths(
        run_dir=run_dir,
        browser_png=run_dir / "mapbox-gl-reference.png",
        qgis_png=run_dir / "qgis-vector-render.png",
        diff_png=run_dir / "mapbox-gl-vs-qgis-diff.png",
        metrics_json=run_dir / "metrics.json",
        manifest_json=run_dir / "manifest.json",
    )


def camera_center_web_mercator(camera: MapboxComparisonCamera) -> tuple[float, float]:
    clamped_lat = min(85.05112878, max(-85.05112878, camera.latitude))
    x = camera.longitude * WEB_MERCATOR_HALF_WORLD / 180.0
    y = math.log(math.tan((90.0 + clamped_lat) * math.pi / 360.0)) / (math.pi / 180.0)
    y = y * WEB_MERCATOR_HALF_WORLD / 180.0
    return x, y


def camera_extent_web_mercator(camera: MapboxComparisonCamera) -> tuple[float, float, float, float]:
    """Return an EPSG:3857 extent matching Mapbox GL's zoom/viewport approximation."""

    center_x, center_y = camera_center_web_mercator(camera)
    world_pixels = WEB_MERCATOR_TILE_SIZE * (2 ** camera.zoom)
    meters_per_pixel = (WEB_MERCATOR_HALF_WORLD * 2.0) / world_pixels
    half_width = camera.width * meters_per_pixel / 2.0
    half_height = camera.height * meters_per_pixel / 2.0
    return (
        center_x - half_width,
        center_y - half_height,
        center_x + half_width,
        center_y + half_height,
    )


def list_cameras() -> str:
    lines: list[str] = []
    for camera in CAMERAS.values():
        lines.append(
            f"- {camera.name}: {camera.description} "
            f"({camera.longitude:.4f}, {camera.latitude:.4f}, z{camera.zoom:g}, "
            f"{camera.width}x{camera.height}, {camera.style_owner}/{camera.style_id})"
        )
    return "\n".join(lines)


def resolve_mapbox_token(*, provided_token: str | None, environ: dict[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    token = provided_token or env.get("MAPBOX_ACCESS_TOKEN") or env.get("QFIT_MAPBOX_ACCESS_TOKEN")
    if not token:
        raise ValueError(
            "Mapbox token required via --mapbox-token, MAPBOX_ACCESS_TOKEN, or QFIT_MAPBOX_ACCESS_TOKEN."
        )
    return token


def load_style_definition(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        raise ValueError(f"Mapbox style JSON must be an object: {path}")
    return loaded


def _ensure_output_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _redacted_manifest(
    *,
    camera: MapboxComparisonCamera,
    result: ComparisonResult,
) -> dict[str, object]:
    return {
        "camera": dataclasses.asdict(camera),
        "style_url": None if result.style_json_path is not None else camera.style_url,
        "outputs": {
            "browser_reference": str(result.paths.browser_png),
            "qgis_vector_render": str(result.paths.qgis_png),
            "diff": str(result.paths.diff_png),
            "metrics": str(result.paths.metrics_json),
        },
        "style_json_path": result.style_json_path,
        "captured": {
            "browser_reference": result.browser_captured,
            "qgis_vector_render": result.qgis_captured,
            "diff": result.diff_captured,
        },
        "metrics": result.image_metrics,
        "notes": [
            "Mapbox tokens are intentionally excluded from this manifest.",
            "This is a manual visual QA aid, not a CI gate.",
        ],
    }


def write_manifest(*, camera: MapboxComparisonCamera, result: ComparisonResult) -> None:
    manifest = _redacted_manifest(camera=camera, result=result)
    result.paths.manifest_json.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def build_mapbox_gl_html(
    *,
    camera: MapboxComparisonCamera,
    style_definition: dict[str, object] | None = None,
) -> str:
    """Return token-free temporary HTML for the browser reference capture."""

    style_json = json.dumps(style_definition if style_definition is not None else camera.style_url)
    center_json = json.dumps([camera.longitude, camera.latitude])
    return f"""<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>qfit Mapbox Outdoors comparison reference</title>
  <link href=\"https://api.mapbox.com/mapbox-gl-js/v3.10.0/mapbox-gl.css\" rel=\"stylesheet\">
  <script src=\"https://api.mapbox.com/mapbox-gl-js/v3.10.0/mapbox-gl.js\"></script>
  <style>
    html, body, #map {{ margin: 0; width: 100%; height: 100%; overflow: hidden; }}
    .mapboxgl-ctrl-logo, .mapboxgl-ctrl-attrib {{ display: none !important; }}
  </style>
</head>
<body>
  <div id=\"map\"></div>
  <script>
    window.startQfitMapboxComparison = (credential) => {{
      mapboxgl['access' + 'Token'] = credential;
      const map = new mapboxgl.Map({{
        container: 'map',
        style: {style_json},
        center: {center_json},
        zoom: {camera.zoom},
        bearing: {camera.bearing},
        pitch: {camera.pitch},
        interactive: false,
        preserveDrawingBuffer: true,
        fadeDuration: 0,
      }});
      map.once('idle', () => {{ window.qfitMapboxReady = true; }});
    }};
  </script>
</body>
</html>
"""


def build_node_playwright_capture_script() -> str:
    return r"""
const fs = require('fs');
const { chromium } = require('playwright');

const [outputPath, widthText, heightText, timeoutText, executablePath] = process.argv.slice(2);
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const html = payload.html;
const width = Number.parseInt(widthText, 10);
const height = Number.parseInt(heightText, 10);
const timeout = Number.parseInt(timeoutText, 10);

(async () => {
  const credential = String(payload.credential || '').trim();
  if (!credential) {
    throw new Error('Mapbox token was not provided on stdin.');
  }
  const launchOptions = {
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  };
  if (executablePath) {
    launchOptions.executablePath = executablePath;
  }
  const browser = await chromium.launch(launchOptions);
  try {
    const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
    await page.setContent(html, { waitUntil: 'domcontentloaded', timeout });
    await page.evaluate((value) => window.startQfitMapboxComparison(value), credential);
    await page.waitForFunction('window.qfitMapboxReady === true', { timeout });
    await page.screenshot({ path: outputPath, fullPage: false });
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error && error.message ? error.message : String(error));
  process.exit(1);
});
""".strip()


def _node_modules_paths() -> list[Path]:
    return [REPO_ROOT / "node_modules", PACKAGE_PARENT / "node_modules"]


def _node_capture_environment() -> dict[str, str]:
    env = os.environ.copy()
    node_paths = [str(path) for path in _node_modules_paths()]
    if env.get("NODE_PATH"):
        node_paths.append(env["NODE_PATH"])
    env["NODE_PATH"] = os.pathsep.join(node_paths)
    return env


def _chromium_executable() -> str:
    configured = os.environ.get("QFIT_CHROMIUM_EXECUTABLE")
    if configured:
        return configured
    for binary_name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        resolved = shutil.which(binary_name)
        if resolved:
            return resolved
    return ""


def redact_sensitive_text(text: str, secret: str) -> str:
    if not secret:
        return text
    return text.replace(secret, "<redacted>")


def encode_browser_capture_html(
    *,
    camera: MapboxComparisonCamera,
    style_definition: dict[str, object] | None = None,
) -> str:
    html = build_mapbox_gl_html(camera=camera, style_definition=style_definition).encode("utf-8")
    return base64.b64encode(html).decode("ascii")


def render_browser_reference(  # pragma: no cover - depends on optional Node/Chromium toolchain
    *,
    camera: MapboxComparisonCamera,
    token: str,
    output_path: Path,
    timeout_ms: int,
    style_definition: dict[str, object] | None = None,
) -> None:
    node_binary = shutil.which("node")
    if not node_binary:
        raise RuntimeError(
            "Browser reference capture requires Node.js plus the Playwright npm package, "
            "or run with --skip-browser."
        )

    with tempfile.TemporaryDirectory(prefix="qfit-mapbox-reference-") as tmpdir:
        tmp_path = Path(tmpdir)
        script_path = tmp_path / "capture-reference.js"
        script_path.write_text(build_node_playwright_capture_script(), encoding="utf-8")
        command = [
            node_binary,
            str(script_path),
            str(output_path),
            str(camera.width),
            str(camera.height),
            str(timeout_ms),
            _chromium_executable(),
        ]
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=_node_capture_environment(),
            input=json.dumps({
                "credential": token,
                "html": build_mapbox_gl_html(camera=camera, style_definition=style_definition),
            }),
            capture_output=True,
            text=True,
            timeout=max(5.0, timeout_ms / 1000.0 + 10.0),
            check=False,
        )
    if completed.returncode != 0:
        detail = redact_sensitive_text((completed.stderr or completed.stdout).strip(), token)
        raise RuntimeError(
            "Browser reference capture failed. Install dev dependencies with "
            "`npm install --save-dev playwright`, run under xvfb-run if needed, "
            f"or use --skip-browser. Details: {detail}"
        )


def _ensure_package_parent_on_path() -> None:  # pragma: no cover - exercised only in PyQGIS capture
    package_parent_text = str(PACKAGE_PARENT)
    if package_parent_text not in sys.path:
        sys.path.insert(0, package_parent_text)


def is_valid_qgis_vector_tile_layer(*, layer: object, vector_tile_layer_type: type) -> bool:
    return isinstance(layer, vector_tile_layer_type) and bool(layer.isValid())


def render_qgis_vector(  # pragma: no cover - depends on optional PyQGIS runtime
    *,
    camera: MapboxComparisonCamera,
    token: str,
    output_path: Path,
    style_definition: dict[str, object] | None = None,
) -> None:
    _ensure_package_parent_on_path()
    try:
        from qgis.PyQt.QtCore import QSize  # type: ignore[import-not-found]
        from qgis.PyQt.QtGui import QColor  # type: ignore[import-not-found]
        from qgis.core import (  # type: ignore[import-not-found]
            QgsApplication,
            QgsCoordinateReferenceSystem,
            QgsMapRendererParallelJob,
            QgsMapSettings,
            QgsRectangle,
            QgsVectorTileLayer,
        )
    except ImportError as exc:  # pragma: no cover - depends on optional PyQGIS runtime
        raise RuntimeError(
            "QGIS vector capture requires a Python runtime with PyQGIS available. "
            "Run this command from a QGIS Python shell or configured qgis_process environment, "
            "or use --skip-qgis to capture only the browser reference."
        ) from exc

    from qfit.mapbox_config import (
        build_vector_tile_layer_uri,
        extract_mapbox_vector_source_ids,
        fetch_mapbox_style_definition,
        fetch_mapbox_sprite_resources,
        simplify_mapbox_style_expressions,
    )
    from qfit.visualization.infrastructure.background_map_service import BackgroundMapService

    app = QgsApplication.instance()
    created_app = app is None
    if created_app:
        app = QgsApplication([], False)
        app.initQgis()

    try:
        resolved_style_definition = (
            style_definition
            if style_definition is not None
            else fetch_mapbox_style_definition(token, camera.style_owner, camera.style_id)
        )
        simplified_style = simplify_mapbox_style_expressions(resolved_style_definition)
        sprite_resources = None
        try:
            sprite_resources = fetch_mapbox_sprite_resources(
                token,
                camera.style_owner,
                camera.style_id,
                sprite_url=resolved_style_definition.get("sprite"),
            )
        except (RuntimeError, KeyError, ValueError, OSError):
            pass
        tileset_ids = extract_mapbox_vector_source_ids(resolved_style_definition)
        layer_uri = build_vector_tile_layer_uri(
            token,
            camera.style_owner,
            camera.style_id,
            tileset_ids=tileset_ids,
            include_style_url=False,
        )
        layer = QgsVectorTileLayer(layer_uri, f"qfit comparison {camera.style_owner}/{camera.style_id}")
        if not is_valid_qgis_vector_tile_layer(layer=layer, vector_tile_layer_type=QgsVectorTileLayer):
            raise RuntimeError("QGIS did not create a valid Mapbox vector tile layer.")
        BackgroundMapService()._apply_mapbox_gl_style(layer, simplified_style, sprite_resources=sprite_resources)

        destination_crs = QgsCoordinateReferenceSystem("EPSG:3857")
        settings = QgsMapSettings()
        settings.setLayers([layer])
        settings.setDestinationCrs(destination_crs)
        settings.setExtent(QgsRectangle(*camera_extent_web_mercator(camera)))
        settings.setOutputSize(QSize(camera.width, camera.height))
        settings.setBackgroundColor(QColor(255, 255, 255, 255))

        job = QgsMapRendererParallelJob(settings)
        job.start()
        job.waitForFinished()
        image = job.renderedImage()
        if image.isNull():
            raise RuntimeError("QGIS returned an empty image for the vector tile render.")
        if not image.save(str(output_path), "PNG"):
            raise RuntimeError(f"QGIS failed to write render output: {output_path}")
    finally:
        if created_app:
            app.exitQgis()


def build_image_diff(*, reference_path: Path, candidate_path: Path, output_path: Path) -> ImageMetrics:  # pragma: no cover
    try:
        from PIL import Image, ImageChops, ImageEnhance, ImageStat  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on optional local toolchain
        raise RuntimeError(
            "Diff image generation requires Pillow. Install it locally with "
            "`python3 -m pip install pillow`, or run with --skip-diff."
        ) from exc

    with Image.open(reference_path).convert("RGBA") as reference:
        with Image.open(candidate_path).convert("RGBA") as candidate:
            if reference.size != candidate.size:
                raise RuntimeError(
                    f"Cannot diff images with different sizes: {reference.size} vs {candidate.size}."
                )
            diff = ImageChops.difference(reference, candidate)
            ImageEnhance.Brightness(diff).enhance(8.0).save(output_path)
            diff_rgb = diff.convert("RGB")
            stats = ImageStat.Stat(diff_rgb)
            channel_count = max(1, len(stats.mean))
            mean_absolute_delta = sum(stats.mean) / channel_count
            rms_delta = sum(stats.rms) / channel_count
            changed_pixel_count = sum(1 for pixel in diff_rgb.getdata() if any(channel != 0 for channel in pixel))
            pixel_count = reference.width * reference.height
            metrics: ImageMetrics = {
                "pixel_count": pixel_count,
                "changed_pixel_count": changed_pixel_count,
                "changed_pixel_ratio": changed_pixel_count / pixel_count if pixel_count else 0.0,
                "mean_absolute_channel_delta": mean_absolute_delta,
                "normalized_mean_absolute_channel_delta": mean_absolute_delta / 255.0,
                "rms_channel_delta": rms_delta,
                "normalized_rms_channel_delta": rms_delta / 255.0,
            }
            metrics.update(_optional_ssim_metric(reference=reference, candidate=candidate))
            return metrics


def _optional_ssim_metric(*, reference: object, candidate: object) -> ImageMetrics:  # pragma: no cover
    try:
        import numpy as np  # type: ignore[import-not-found]
        from skimage.metrics import structural_similarity  # type: ignore[import-not-found]
    except ImportError:
        return {"ssim_status": "unavailable"}

    reference_array = np.asarray(reference.convert("RGB"))
    candidate_array = np.asarray(candidate.convert("RGB"))
    return {
        "ssim_status": "available",
        "structural_similarity": float(
            structural_similarity(reference_array, candidate_array, channel_axis=2, data_range=255)
        ),
    }


def run_comparison(
    config: ComparisonConfig,
    *,
    browser_renderer: Callable[..., None] = render_browser_reference,
    qgis_renderer: Callable[..., None] = render_qgis_vector,
    diff_builder: Callable[..., ImageMetrics | None] = build_image_diff,
) -> ComparisonResult:
    run_dir = build_run_directory(
        output_root=config.output_root,
        camera_name=config.camera.name,
        now=config.now,
    )
    _ensure_output_directory(run_dir)
    paths = build_comparison_paths(run_dir=run_dir)

    browser_captured = False
    qgis_captured = False
    diff_captured = False
    image_metrics: ImageMetrics = {}
    style_definition = (
        load_style_definition(config.style_json_path)
        if config.style_json_path is not None
        else None
    )

    if config.browser:
        browser_renderer(
            camera=config.camera,
            token=config.token,
            output_path=paths.browser_png,
            timeout_ms=config.browser_timeout_ms,
            style_definition=style_definition,
        )
        browser_captured = True

    if config.qgis:
        qgis_renderer(
            camera=config.camera,
            token=config.token,
            output_path=paths.qgis_png,
            style_definition=style_definition,
        )
        qgis_captured = True

    if config.diff and browser_captured and qgis_captured:
        image_metrics = diff_builder(
            reference_path=paths.browser_png,
            candidate_path=paths.qgis_png,
            output_path=paths.diff_png,
        ) or {}
        paths.metrics_json.write_text(json.dumps(image_metrics, indent=2) + "\n", encoding="utf-8")
        diff_captured = True

    result = ComparisonResult(
        paths=paths,
        browser_captured=browser_captured,
        qgis_captured=qgis_captured,
        diff_captured=diff_captured,
        image_metrics=image_metrics,
        style_json_path=str(config.style_json_path) if config.style_json_path is not None else None,
    )
    write_manifest(camera=config.camera, result=result)
    return result


def _parse_camera(value: str) -> MapboxComparisonCamera:
    try:
        return CAMERAS[value]
    except KeyError as exc:
        known = ", ".join(sorted(CAMERAS))
        raise argparse.ArgumentTypeError(f"Unknown camera '{value}'. Known cameras: {known}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture manual Mapbox Outdoors browser-vs-QGIS visual comparison artifacts.",
    )
    parser.add_argument(
        "camera",
        nargs="?",
        type=_parse_camera,
        help="Comparison camera to capture. Defaults to valais-geneva-outdoors.",
    )
    parser.add_argument(
        "--list-cameras",
        action="store_true",
        help="List supported comparison cameras and exit.",
    )
    parser.add_argument(
        "--all-cameras",
        action="store_true",
        help="Capture the full recommended z5-z18 inspection camera matrix.",
    )
    parser.add_argument(
        "--mapbox-token",
        help="Mapbox access token. Prefer MAPBOX_ACCESS_TOKEN to avoid shell history exposure.",
    )
    parser.add_argument(
        "--style-json",
        type=Path,
        help=(
            "Use an already downloaded Mapbox style JSON snapshot for the browser reference "
            "and QGIS style preprocessing instead of fetching live style metadata."
        ),
    )
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Ignored/debug root where comparison artifacts are written.",
    )
    parser.add_argument(
        "--skip-browser",
        action="store_true",
        help="Skip the browser/Mapbox GL reference screenshot.",
    )
    parser.add_argument(
        "--skip-qgis",
        action="store_true",
        help="Skip the native QGIS vector-tile screenshot.",
    )
    parser.add_argument(
        "--skip-diff",
        action="store_true",
        help="Skip diff image generation.",
    )
    parser.add_argument(
        "--browser-timeout-ms",
        type=int,
        default=120_000,
        help="Timeout for browser map loading and screenshot capture.",
    )
    return parser


def _print_result(result: ComparisonResult, *, camera_name: str | None = None) -> None:
    if camera_name is not None:
        print(f"Camera: {camera_name}")
    print(f"Run directory: {result.paths.run_dir}")
    if result.browser_captured:
        print(f"Mapbox GL reference: {result.paths.browser_png}")
    if result.qgis_captured:
        print(f"QGIS vector render: {result.paths.qgis_png}")
    if result.diff_captured:
        print(f"Diff image: {result.paths.diff_png}")
    print(f"Manifest: {result.paths.manifest_json}")


def _selected_cameras(args: argparse.Namespace) -> list[MapboxComparisonCamera]:
    if args.all_cameras:
        return list(CAMERAS.values())
    return [args.camera or CAMERAS["valais-geneva-outdoors"]]


def _comparison_config(
    *,
    args: argparse.Namespace,
    camera: MapboxComparisonCamera,
    token: str,
    output_root: Path,
) -> ComparisonConfig:
    return ComparisonConfig(
        camera=camera,
        token=token,
        output_root=output_root,
        style_json_path=args.style_json,
        browser=not args.skip_browser,
        qgis=not args.skip_qgis,
        diff=not args.skip_diff,
        browser_timeout_ms=args.browser_timeout_ms,
    )


def _run_and_print_configured_comparisons(args: argparse.Namespace) -> None:
    token = resolve_mapbox_token(provided_token=args.mapbox_token)
    output_root = Path(args.output_root).expanduser().resolve()
    cameras = _selected_cameras(args)
    if args.all_cameras:
        _run_all_cameras_in_subprocesses(args=args, cameras=cameras, token=token, output_root=output_root)
        return
    multiple_results = len(cameras) > 1
    for camera in cameras:
        result = run_comparison(
            _comparison_config(
                args=args,
                camera=camera,
                token=token,
                output_root=output_root,
            )
        )
        _print_result(result, camera_name=camera.name if multiple_results else None)


def _single_camera_subprocess_command(
    *,
    args: argparse.Namespace,
    camera: MapboxComparisonCamera,
    output_root: Path,
) -> list[str]:
    command = [sys.executable, str(Path(__file__).resolve()), camera.name]
    if args.style_json is not None:
        command.extend(["--style-json", str(args.style_json.expanduser().resolve())])
    command.extend(["--output-root", str(output_root)])
    if args.skip_browser:
        command.append("--skip-browser")
    if args.skip_qgis:
        command.append("--skip-qgis")
    if args.skip_diff:
        command.append("--skip-diff")
    command.extend(["--browser-timeout-ms", str(args.browser_timeout_ms)])
    return command


def _camera_subprocess_timeout_seconds(args: argparse.Namespace) -> float:
    return max(args.browser_timeout_ms / 1000 + 60, 1.0)


def _single_camera_subprocess_environment(*, token: str) -> dict[str, str]:
    env = os.environ.copy()
    env["MAPBOX_ACCESS_TOKEN"] = token
    return env


def _write_child_output(*, stdout: str, stderr: str, token: str) -> None:
    safe_stdout = redact_sensitive_text(stdout, token)
    safe_stderr = redact_sensitive_text(stderr, token)
    if safe_stdout:
        print(safe_stdout, end="")
    if safe_stderr:
        print(safe_stderr, end="", file=sys.stderr)


def _timeout_output_text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _run_all_cameras_in_subprocesses(
    *,
    args: argparse.Namespace,
    cameras: list[MapboxComparisonCamera],
    token: str,
    output_root: Path,
) -> None:
    failures: list[str] = []
    for camera in cameras:
        timeout_seconds = _camera_subprocess_timeout_seconds(args)
        try:
            completed = subprocess.run(  # noqa: S603 - command is built from this script and static camera names.
                _single_camera_subprocess_command(args=args, camera=camera, output_root=output_root),
                cwd=REPO_ROOT,
                env=_single_camera_subprocess_environment(token=token),
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            _write_child_output(
                stdout=_timeout_output_text(exc.output),
                stderr=_timeout_output_text(exc.stderr),
                token=token,
            )
            failures.append(f"{camera.name} (timeout after {timeout_seconds:g}s)")
            print(
                f"error: camera {camera.name} timed out after {timeout_seconds:g}s; continuing.",
                file=sys.stderr,
            )
            continue
        _write_child_output(stdout=completed.stdout, stderr=completed.stderr, token=token)
        if completed.returncode != 0:
            failures.append(f"{camera.name} (exit {completed.returncode})")
            print(
                f"error: camera {camera.name} failed with exit code {completed.returncode}; continuing.",
                file=sys.stderr,
            )
    if failures:
        raise ComparisonCaptureError(f"comparison capture failed for: {', '.join(failures)}")


def _write_stdout_line(text: str) -> None:
    os.write(1, f"{text}\n".encode("utf-8"))


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.list_cameras:
        _write_stdout_line(list_cameras())
        return 0
    if args.all_cameras and args.camera is not None:
        print("error: pass either a single camera or --all-cameras, not both.", file=sys.stderr)
        return 2

    try:
        _run_and_print_configured_comparisons(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"error: style JSON not found: {exc.filename}", file=sys.stderr)
        return 2
    except ComparisonCaptureError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (RuntimeError, OSError):
        print("error: comparison capture failed; use --skip-browser or --skip-qgis to isolate setup issues.", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
