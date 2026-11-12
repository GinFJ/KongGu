from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_sidecar_spawn_declares_utf8_output_encoding() -> None:
    source = (ROOT / "src" / "sidecarClient.ts").read_text(encoding="utf-8")

    spawn_options = source.split('Command.sidecar("binaries/konggu-worker", ["--serve"], {', 1)[1].split('});', 1)[0]
    assert 'encoding: "utf-8"' in spawn_options
