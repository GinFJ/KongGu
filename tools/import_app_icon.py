from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ICON_DIR = ROOT / "src-tauri" / "icons"
PNG_OUT = ICON_DIR / "icon.png"
ICO_OUT = ICON_DIR / "icon.ico"


def center_crop_square(image: Image.Image) -> Image.Image:
    width, height = image.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    return image.crop((left, top, left + side, top + side))


def import_icon(source: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(source)

    ICON_DIR.mkdir(parents=True, exist_ok=True)
    image = Image.open(source).convert("RGBA")
    image = center_crop_square(image)
    icon = image.resize((1024, 1024), Image.Resampling.LANCZOS)

    icon.save(PNG_OUT)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    icon.save(ICO_OUT, sizes=[(size, size) for size in sizes])
    print(PNG_OUT)
    print(ICO_OUT)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a square source image as Konggu app icons.")
    parser.add_argument("source", type=Path, help="Source PNG/JPEG image.")
    args = parser.parse_args()
    import_icon(args.source)


if __name__ == "__main__":
    main()
