from pathlib import Path

from tools.verify_release_bundle import _pdfjs_asset_checks


def test_release_rejects_missing_or_corrupt_offline_pdf_resources(tmp_path: Path):
    for directory, filename in [('cmaps', 'UniGB-UCS2-H.bcmap'), ('standard_fonts', 'FoxitSerif.pfb')]:
        source = tmp_path/'node_modules/pdfjs-dist'/directory/filename
        target = tmp_path/'dist/pdfjs'/directory/filename
        source.parent.mkdir(parents=True)
        source.write_bytes(b'known resource')
        target.parent.mkdir(parents=True)
        target.write_bytes(source.read_bytes())
    assert all(ok for _, ok, _ in _pdfjs_asset_checks(tmp_path))
    (tmp_path/'dist/pdfjs/cmaps/UniGB-UCS2-H.bcmap').unlink()
    assert not _pdfjs_asset_checks(tmp_path)[0][1]
    (tmp_path/'dist/pdfjs/standard_fonts/FoxitSerif.pfb').write_bytes(b'corrupt')
    assert not any(ok for _, ok, _ in _pdfjs_asset_checks(tmp_path))
