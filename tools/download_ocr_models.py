from __future__ import annotations

import argparse
import json
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "ocr_models.json"


def _default_targets() -> list[Path]:
    targets = [ROOT / "cache" / "paddlex" / "official_models"]
    packaged_cache = ROOT / "dist" / "Konggu" / "cache" / "paddlex" / "official_models"
    if packaged_cache.parent.parent.parent.exists():
        targets.append(packaged_cache)
    return targets


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as response:
        with dest.open("wb") as file:
            shutil.copyfileobj(response, file)


def _extract_model(archive_path: Path, model_name: str, target_root: Path) -> Path:
    model_dir = target_root / model_name
    if model_dir.exists():
        shutil.rmtree(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)

    with tarfile.open(archive_path) as tar:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tar.extractall(tmp_path)
            children = [child for child in tmp_path.iterdir()]
            source = children[0] if len(children) == 1 and children[0].is_dir() else tmp_path
            for item in source.iterdir():
                destination = model_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, destination)
                else:
                    shutil.copy2(item, destination)
    return model_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="下载空谷扫描件 OCR 离线模型。")
    parser.add_argument(
        "--target",
        action="append",
        type=Path,
        help="模型保存目录。可重复传入；默认写入项目 cache，若 dist/Konggu 存在也同步写入 exe 旁边的 cache。",
    )
    args = parser.parse_args()

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    targets = args.target or _default_targets()
    for target in targets:
        target.mkdir(parents=True, exist_ok=True)

    for model in config["models"]:
        name = model["name"]
        url = model["url"]
        print(f"下载 {name} ...")
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / f"{name}.tar"
            _download(url, archive)
            for target in targets:
                model_dir = _extract_model(archive, name, target)
                print(f"已安装：{model_dir}")

    print("OCR 模型准备完成。重新打开 Konggu.exe 后即可识别扫描件 PDF。")


if __name__ == "__main__":
    main()
