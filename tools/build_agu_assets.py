from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


BRAND_VERSION = "1.0"
BUILD_DATE = "2026-08-11"
PAPER = (247, 245, 236, 255)
DEEP_GREEN = (17, 66, 48, 255)
QINGHE_GREEN = (47, 133, 89, 255)
SPROUT_GREEN = (143, 196, 73, 255)
GRAIN_GOLD = (236, 188, 58, 255)
LAKE_BLUE = (88, 170, 184, 255)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the official A-Gu brand asset set.")
    parser.add_argument("--standard", required=True, type=Path)
    parser.add_argument("--dynamic", required=True, type=Path)
    parser.add_argument("--complete", required=True, type=Path)
    parser.add_argument("--theme", required=True, type=Path)
    parser.add_argument("--wordmark", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--tauri-icons-dir", required=True, type=Path)
    return parser.parse_args()


def open_rgba(path: Path) -> Image.Image:
    image = Image.open(path).convert("RGBA")
    if image.getchannel("A").getextrema()[0] == 255:
        raise ValueError(f"Expected transparent source image: {path}")
    # Image generators may leave near-invisible pixels across the nominally
    # transparent canvas. They become rectangular haze after shadow filters.
    alpha = image.getchannel("A").point(lambda value: 0 if value < 18 else value)
    image.putalpha(alpha)
    return image


def alpha_bbox(image: Image.Image) -> tuple[int, int, int, int]:
    box = image.getchannel("A").getbbox()
    if box is None:
        raise ValueError("Source image contains no visible pixels.")
    return box


def fit_subject(image: Image.Image, max_size: tuple[int, int]) -> Image.Image:
    cropped = image.crop(alpha_bbox(image))
    scale = min(max_size[0] / cropped.width, max_size[1] / cropped.height)
    size = (max(1, round(cropped.width * scale)), max(1, round(cropped.height * scale)))
    return cropped.resize(size, Image.Resampling.LANCZOS)


def paste_center(canvas: Image.Image, subject: Image.Image, center: tuple[int, int]) -> None:
    x = round(center[0] - subject.width / 2)
    y = round(center[1] - subject.height / 2)
    canvas.alpha_composite(subject, (x, y))


def add_shadow(canvas: Image.Image, subject: Image.Image, position: tuple[int, int], blur: int = 28) -> None:
    alpha = subject.getchannel("A")
    shadow = Image.new("RGBA", subject.size, (16, 51, 37, 0))
    shadow.putalpha(alpha.filter(ImageFilter.GaussianBlur(blur)).point(lambda p: p * 72 // 255))
    canvas.alpha_composite(shadow, (position[0] + 8, position[1] + 18))


def paper_gradient(size: tuple[int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGBA", size, PAPER)
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            sun = max(0.0, 1.0 - (((x - width * 0.82) / (width * 0.68)) ** 2 + ((y - height * 0.08) / (height * 0.9)) ** 2))
            green = max(0.0, 1.0 - (((x - width * 0.08) / (width * 0.92)) ** 2 + ((y - height * 0.88) / (height * 0.82)) ** 2))
            base = PAPER[:3]
            r = int(base[0] * (1 - 0.06 * green) + 218 * 0.06 * green + 255 * 0.06 * sun)
            g = int(base[1] * (1 - 0.10 * green) + 239 * 0.10 * green + 245 * 0.05 * sun)
            b = int(base[2] * (1 - 0.12 * green) + 220 * 0.12 * green + 165 * 0.05 * sun)
            pixels[x, y] = (r, g, b, 255)
    return image


def add_paper_grain(image: Image.Image, opacity: int = 12) -> None:
    rng = random.Random(260811)
    grain = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(grain)
    for _ in range(max(1200, image.width * image.height // 1500)):
        x = rng.randrange(image.width)
        y = rng.randrange(image.height)
        tone = rng.choice([(31, 86, 60, opacity), (193, 144, 39, opacity), (255, 255, 255, opacity)])
        draw.ellipse((x, y, x + rng.choice((1, 2, 3)), y + rng.choice((1, 2, 3))), fill=tone)
    image.alpha_composite(grain.filter(ImageFilter.GaussianBlur(0.35)))


def rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
    return mask


def create_avatar(standard: Image.Image, output: Path) -> None:
    canvas = paper_gradient((1024, 1024))
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.ellipse((76, 76, 948, 948), fill=(255, 253, 243, 232), outline=(48, 126, 86, 220), width=18)
    grid = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    grid_draw = ImageDraw.Draw(grid, "RGBA")
    for index in range(5):
        offset = 160 + index * 132
        grid_draw.line((130, offset, 894, offset), fill=(48, 126, 86, 18), width=3)
        grid_draw.line((offset, 130, offset, 894), fill=(48, 126, 86, 18), width=3)
    circle_clip = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(circle_clip).ellipse((76, 76, 948, 948), fill=255)
    grid.putalpha(Image.composite(grid.getchannel("A"), Image.new("L", canvas.size, 0), circle_clip))
    canvas.alpha_composite(grid)
    subject = fit_subject(standard, (790, 790))
    position = ((1024 - subject.width) // 2, (1024 - subject.height) // 2 + 20)
    add_shadow(canvas, subject, position, 24)
    canvas.alpha_composite(subject, position)
    canvas.putalpha(rounded_mask(canvas.size, 512))
    canvas.save(output, optimize=True)


def create_app_icon(standard: Image.Image, output: Path) -> None:
    canvas = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    background = Image.new("RGBA", canvas.size, DEEP_GREEN)
    bg_pixels = background.load()
    for y in range(1024):
        for x in range(1024):
            light = max(0.0, 1.0 - (((x - 760) / 720) ** 2 + ((y - 190) / 850) ** 2))
            bg_pixels[x, y] = (
                int(17 + light * 41),
                int(66 + light * 73),
                int(48 + light * 40),
                255,
            )
    draw = ImageDraw.Draw(background, "RGBA")
    draw.rounded_rectangle((92, 600, 932, 892), 64, fill=(255, 253, 241, 42), outline=(255, 255, 255, 46), width=4)
    for x in range(232, 900, 140):
        draw.line((x, 600, x, 892), fill=(255, 255, 255, 25), width=3)
    for y in range(673, 860, 73):
        draw.line((92, y, 932, y), fill=(255, 255, 255, 25), width=3)
    draw.rounded_rectangle((372, 673, 652, 819), 28, fill=(239, 194, 56, 92), outline=(255, 226, 112, 165), width=5)
    add_paper_grain(background, 8)
    background.putalpha(rounded_mask(background.size, 220))
    canvas.alpha_composite(background)
    subject = fit_subject(standard, (820, 820))
    position = ((1024 - subject.width) // 2, 94)
    add_shadow(canvas, subject, position, 34)
    canvas.alpha_composite(subject, position)
    canvas.save(output, optimize=True)


def draw_schedule_card(canvas: Image.Image) -> None:
    card = Image.new("RGBA", (820, 370), (255, 253, 245, 226))
    draw = ImageDraw.Draw(card, "RGBA")
    draw.rounded_rectangle((2, 2, 817, 367), 42, fill=(255, 253, 245, 226), outline=(59, 122, 87, 72), width=4)
    cols, rows = 6, 5
    left, top, right, bottom = 44, 48, 776, 324
    for col in range(cols + 1):
        x = left + round((right - left) * col / cols)
        draw.line((x, top, x, bottom), fill=(37, 100, 72, 38), width=3)
    for row in range(rows + 1):
        y = top + round((bottom - top) * row / rows)
        draw.line((left, y, right, y), fill=(37, 100, 72, 38), width=3)
    cells = [(1, 1, LAKE_BLUE), (3, 1, QINGHE_GREEN), (4, 2, LAKE_BLUE), (0, 3, SPROUT_GREEN), (5, 3, QINGHE_GREEN)]
    cell_w = (right - left) / cols
    cell_h = (bottom - top) / rows
    for col, row, color in cells:
        x0 = round(left + col * cell_w + 8)
        y0 = round(top + row * cell_h + 8)
        x1 = round(left + (col + 1) * cell_w - 8)
        y1 = round(top + (row + 1) * cell_h - 8)
        draw.rounded_rectangle((x0, y0, x1, y1), 12, fill=color[:3] + (56,))
    x0 = round(left + 2 * cell_w + 7)
    y0 = round(top + 3 * cell_h + 7)
    x1 = round(left + 3 * cell_w - 7)
    y1 = round(top + 4 * cell_h - 7)
    draw.rounded_rectangle((x0, y0, x1, y1), 12, fill=GRAIN_GOLD[:3] + (100,), outline=GRAIN_GOLD[:3] + (180,), width=4)
    card = card.filter(ImageFilter.GaussianBlur(0.25))
    card = card.rotate(-4, resample=Image.Resampling.BICUBIC, expand=True)
    shadow = Image.new("RGBA", card.size, (20, 60, 43, 0))
    shadow.putalpha(card.getchannel("A").filter(ImageFilter.GaussianBlur(28)).point(lambda p: p * 46 // 255))
    canvas.alpha_composite(shadow, (98, 476))
    canvas.alpha_composite(card, (80, 452))


def create_splash(dynamic: Image.Image, output: Path) -> None:
    canvas = paper_gradient((1600, 900))
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.ellipse((1110, -310, 1810, 390), fill=(255, 220, 83, 55))
    draw.ellipse((-350, 545, 520, 1350), fill=(55, 137, 93, 28))
    draw.arc((-180, 420, 940, 1110), 195, 335, fill=(55, 137, 93, 48), width=7)
    draw.arc((-120, 470, 1080, 1160), 195, 340, fill=(94, 174, 188, 42), width=4)
    draw_schedule_card(canvas)
    subject = fit_subject(dynamic, (700, 800))
    position = (1600 - subject.width - 86, 55)
    canvas.alpha_composite(subject, position)
    add_paper_grain(canvas, 8)
    canvas.save(output, optimize=True)


def load_font(size: int, display: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/STKAITI.TTF") if display else Path("C:/Windows/Fonts/msyhbd.ttc"),
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simsun.ttc"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def create_promo(splash: Image.Image, wordmark: Image.Image, output: Path) -> None:
    canvas = splash.copy()
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    draw.rounded_rectangle((88, 92, 790, 454), 32, fill=(250, 248, 238, 224), outline=(42, 106, 75, 42), width=3)
    wm = fit_subject(wordmark, (330, 150))
    overlay.alpha_composite(wm, (130, 126))
    title_font = load_font(52, display=True)
    body_font = load_font(24)
    small_font = load_font(18)
    draw.text((132, 260), "青心如禾，向阳而生", font=title_font, fill=DEEP_GREEN)
    draw.text((134, 340), "空谷虚拟形象 · 阿谷", font=body_font, fill=QINGHE_GREEN)
    draw.text((134, 389), "从共同空课时间出发，让协作与成长发生。", font=small_font, fill=(86, 105, 94, 255))
    canvas.alpha_composite(overlay)
    canvas.save(output, optimize=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def describe_image(path: Path) -> dict[str, object]:
    with Image.open(path) as image:
        return {
            "file": path.name,
            "width": image.width,
            "height": image.height,
            "mode": image.mode,
            "sha256": sha256(path),
        }


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.tauri_icons_dir.mkdir(parents=True, exist_ok=True)

    sources = {
        "agu-standard-v1.png": args.standard,
        "agu-dynamic-v1.png": args.dynamic,
        "agu-complete-v1.png": args.complete,
        "agu-theme-from-schedule-v1.png": args.theme,
    }
    for name, source in sources.items():
        destination = args.output_dir / name
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)

    standard = open_rgba(args.standard)
    dynamic = open_rgba(args.dynamic)
    complete = open_rgba(args.complete)
    wordmark = Image.open(args.wordmark).convert("RGBA")

    create_avatar(standard, args.output_dir / "agu-avatar-1024.png")
    create_app_icon(standard, args.output_dir / "agu-app-icon-1024.png")
    create_splash(dynamic, args.output_dir / "agu-splash-scene-1600x900.png")
    splash = Image.open(args.output_dir / "agu-splash-scene-1600x900.png").convert("RGBA")
    create_promo(splash, wordmark, args.output_dir / "agu-promo-card-1600x900.png")

    standard.resize((720, 720), Image.Resampling.LANCZOS).save(
        args.output_dir / "agu-standard-v1.webp", "WEBP", quality=92, method=6
    )
    dynamic.resize((720, 720), Image.Resampling.LANCZOS).save(
        args.output_dir / "agu-dynamic-v1.webp", "WEBP", quality=92, method=6
    )
    complete.resize((720, 720), Image.Resampling.LANCZOS).save(
        args.output_dir / "agu-complete-v1.webp", "WEBP", quality=92, method=6
    )
    splash.resize((1280, 720), Image.Resampling.LANCZOS).save(
        args.output_dir / "agu-splash-scene-1280x720.webp", "WEBP", quality=90, method=6
    )

    app_icon = Image.open(args.output_dir / "agu-app-icon-1024.png").convert("RGBA")
    app_icon.save(args.tauri_icons_dir / "icon.png", optimize=True)
    app_icon.save(
        args.tauri_icons_dir / "icon.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )

    outputs = sorted(args.output_dir.glob("agu-*"))
    manifest = {
        "character": "阿谷",
        "project": "空谷 Konggu",
        "brand_version": BRAND_VERSION,
        "build_date": BUILD_DATE,
        "core_message": "青心如禾，向阳而生",
        "sources": [describe_image(args.output_dir / name) for name in sources],
        "outputs": [describe_image(path) for path in outputs if path.suffix.lower() in {".png", ".webp"}],
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
