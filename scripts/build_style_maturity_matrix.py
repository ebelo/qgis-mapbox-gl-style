#!/usr/bin/env python3
"""Build the curated Mapbox Outdoors style-maturity comparison image.

The committed progression frames include a small provenance header and footer.
This script crops their common 1280 x 900 map viewport, lays out the six
meaningful visual milestones, and can wrap a newly rendered QGIS viewport as
the latest committed progression frame.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
PROGRESSION_DIR = ROOT / "docs" / "images" / "style-progression" / "chamonix"
LATEST_FRAME = PROGRESSION_DIR / "09_accepted_qfit_baseline.png"
MATRIX_PATH = PROGRESSION_DIR / "style-maturity-matrix.png"
PPTX_ASSET_DIR = PROGRESSION_DIR / "pptx-assets"

CANVAS_SIZE = (1920, 1080)
FRAME_SIZE = (1280, 1022)
MAP_SIZE = (1280, 900)
MAP_TOP = 86
BACKGROUND = "#F4F6F5"
CARD_BACKGROUND = "#FFFFFF"
INK = "#172126"
MUTED = "#617078"
ACCENT = "#2A7166"
ACCENT_LIGHT = "#DDEDE8"
LINE = "#D9E1DF"


@dataclass(frozen=True)
class Milestone:
    image: str
    title: str
    takeaway: str


MILESTONES = (
    Milestone("01_initial_llm_guess.png", "Basic conversion", "Geometry rendered; hierarchy still flat"),
    Milestone("03_label_and_terrain_iteration.png", "Symbols & labels", "Sprites and settlement labels restored"),
    Milestone("05_area_fill_and_trails.png", "Landcover & trails", "Outdoor classes become distinct"),
    Milestone("06_road_label_refinement.png", "Hillshade & relief", "Terrain structure becomes legible"),
    Milestone("09_accepted_qfit_baseline.png", "Accepted qFit baseline", "Balanced detail, roads, shields and open fonts"),
)

PPTX_FILENAMES = (
    "01-basic-conversion.png",
    "02-symbols-and-labels.png",
    "03-landcover-and-trails.png",
    "04-hillshade-and-relief.png",
    "05-accepted-qfit-baseline.png",
)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    filename = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(filename, size)


def _fit_cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    target_w, target_h = size
    scale = max(target_w / image.width, target_h / image.height)
    resized = image.resize(
        (round(image.width * scale), round(image.height * scale)),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - target_w) // 2
    top = (resized.height - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def wrap_latest_render(source: Path) -> None:
    with Image.open(source) as raw:
        raw = raw.convert("RGB")
        if raw.size != MAP_SIZE:
            raise ValueError(f"latest render must be {MAP_SIZE[0]} x {MAP_SIZE[1]}, got {raw.size}")
        frame = Image.new("RGB", FRAME_SIZE, "#F8F8F4")
        frame.paste(raw, (0, MAP_TOP))

    draw = ImageDraw.Draw(frame)
    draw.text((24, 15), "09 · Accepted qFit baseline", font=_font(25, True), fill=INK)
    draw.text(
        (24, 52),
        "Chamonix trails z13.75 · qFit 0.53.4 baseline · fresh render 2026-09-27",
        font=_font(12),
        fill=MUTED,
    )
    right = "roads, shields, collisions and portable typography"
    right_box = draw.textbbox((0, 0), right, font=_font(12))
    draw.text((1256 - (right_box[2] - right_box[0]), 52), right, font=_font(12), fill=MUTED)
    draw.text(
        (24, 997),
        "QFit native QGIS vector-tile render of Mapbox Outdoors style progression",
        font=_font(11),
        fill=MUTED,
    )
    frame.save(LATEST_FRAME, optimize=True)


def _map_view(path: Path) -> Image.Image:
    with Image.open(path) as image:
        image = image.convert("RGB")
        if image.size != FRAME_SIZE:
            raise ValueError(f"progression frame must be {FRAME_SIZE}, got {image.size}: {path}")
        return image.crop((0, MAP_TOP, MAP_SIZE[0], MAP_TOP + MAP_SIZE[1]))


def build_matrix() -> None:
    canvas = Image.new("RGB", CANVAS_SIZE, BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    draw.text((48, 32), "Mapbox Outdoors in QGIS", font=_font(46, True), fill=INK)
    draw.text(
        (48, 91),
        "Five evidence-backed milestones · from first conversion to the accepted qFit baseline",
        font=_font(23),
        fill=MUTED,
    )

    card_w, card_h = 592, 420
    image_w, image_h = 560, 315
    top_xs = (48, 664, 1280)
    bottom_xs = (356, 972)
    ys = (145, 610)

    for index, milestone in enumerate(MILESTONES, start=1):
        row = (index - 1) // 3
        col = (index - 1) % 3
        x = top_xs[col] if row == 0 else bottom_xs[col]
        y = ys[row]
        is_latest = index == len(MILESTONES)
        outline = ACCENT if is_latest else LINE
        draw.rounded_rectangle(
            (x, y, x + card_w, y + card_h),
            radius=14,
            fill=CARD_BACKGROUND,
            outline=outline,
            width=4 if is_latest else 2,
        )
        badge_fill = ACCENT if is_latest else ACCENT_LIGHT
        badge_ink = "#FFFFFF" if is_latest else ACCENT
        draw.ellipse((x + 16, y + 15, x + 52, y + 51), fill=badge_fill)
        number = str(index)
        number_box = draw.textbbox((0, 0), number, font=_font(19, True))
        draw.text(
            (
                x + 34 - (number_box[2] - number_box[0]) / 2,
                y + 33 - (number_box[3] - number_box[1]) / 2 - 2,
            ),
            number,
            font=_font(19, True),
            fill=badge_ink,
        )
        draw.text((x + 66, y + 14), milestone.title, font=_font(24, True), fill=INK)
        draw.text((x + 66, y + 45), milestone.takeaway, font=_font(15), fill=MUTED)

        map_image = _fit_cover(_map_view(PROGRESSION_DIR / milestone.image), (image_w, image_h))
        canvas.paste(map_image, (x + 16, y + 88))

    draw.text(
        (48, 1044),
        "Matched Chamonix camera · native QGIS vector-tile renders · source history and evidence retained in the repository",
        font=_font(16),
        fill=MUTED,
    )
    canvas.save(MATRIX_PATH, optimize=True)


def build_pptx_assets() -> None:
    """Export one high-resolution, transparent-backed card per milestone."""
    PPTX_ASSET_DIR.mkdir(parents=True, exist_ok=True)
    scale = 2
    card_w, card_h = 592 * scale, 420 * scale
    image_w, image_h = 560 * scale, 315 * scale

    for index, (milestone, filename) in enumerate(
        zip(MILESTONES, PPTX_FILENAMES, strict=True), start=1
    ):
        is_latest = index == len(MILESTONES)
        card = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(card)
        outline = ACCENT if is_latest else LINE
        draw.rounded_rectangle(
            (2, 2, card_w - 3, card_h - 3),
            radius=28,
            fill=CARD_BACKGROUND,
            outline=outline,
            width=8 if is_latest else 4,
        )
        badge_fill = ACCENT if is_latest else ACCENT_LIGHT
        badge_ink = "#FFFFFF" if is_latest else ACCENT
        draw.ellipse((32, 30, 104, 102), fill=badge_fill)
        number = str(index)
        number_font = _font(38, True)
        number_box = draw.textbbox((0, 0), number, font=number_font)
        draw.text(
            (
                68 - (number_box[2] - number_box[0]) / 2,
                66 - (number_box[3] - number_box[1]) / 2 - 4,
            ),
            number,
            font=number_font,
            fill=badge_ink,
        )
        draw.text((132, 28), milestone.title, font=_font(48, True), fill=INK)
        draw.text((132, 90), milestone.takeaway, font=_font(30), fill=MUTED)
        map_image = _fit_cover(
            _map_view(PROGRESSION_DIR / milestone.image),
            (image_w, image_h),
        )
        card.paste(map_image, (32, 176))
        card.save(PPTX_ASSET_DIR / filename, optimize=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--latest-render",
        type=Path,
        help="wrap a 1280 x 900 QGIS viewport as progression frame 09 before rebuilding",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.latest_render:
        wrap_latest_render(args.latest_render)
    build_matrix()
    build_pptx_assets()
    print(MATRIX_PATH.relative_to(ROOT))
    print(PPTX_ASSET_DIR.relative_to(ROOT))


if __name__ == "__main__":
    main()
