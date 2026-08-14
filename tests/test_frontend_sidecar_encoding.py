from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_sidecar_spawn_declares_utf8_output_encoding() -> None:
    source = (ROOT / "src" / "sidecarClient.ts").read_text(encoding="utf-8")

    assert 'Command.sidecar("binaries/konggu-worker", ["--serve"], { encoding: "utf-8" })' in source
