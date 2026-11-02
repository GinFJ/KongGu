import hashlib
import json
from pathlib import Path

import pytest

from tools.stage_worker_candidate import stage, NAME
from tools.verify_release_bundle import _registration_checks


def test_stage_retains_previous_generated_bytes_and_selects_exact_candidate(tmp_path):
    target=tmp_path/'src-tauri/binaries'/NAME
    target.parent.mkdir(parents=True)
    target.write_bytes(b'previous-generated-worker')
    candidate=tmp_path/'candidate'/NAME
    candidate.parent.mkdir()
    candidate.write_bytes(b'new-audited-worker')
    assert stage(candidate,tmp_path)==target
    backup=tmp_path/'build/sidecar-history'/hashlib.sha256(b'previous-generated-worker').hexdigest()/NAME
    assert backup.read_bytes()==b'previous-generated-worker'
    assert target.read_bytes()==candidate.read_bytes()
    assert stage(candidate,tmp_path)==target
    assert len(list((tmp_path/'build/sidecar-history').glob('*/'+NAME)))==1


def test_corrupt_backup_blocks_replacement(tmp_path):
    target=tmp_path/'src-tauri/binaries'/NAME
    target.parent.mkdir(parents=True)
    target.write_bytes(b'old')
    backup=tmp_path/'build/sidecar-history'/hashlib.sha256(b'old').hexdigest()/NAME
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b'not-old')
    candidate=tmp_path/NAME
    candidate.write_bytes(b'new')
    with pytest.raises(ValueError,match='backup'):
        stage(candidate,tmp_path)
    assert target.read_bytes()==b'old'


def test_effective_overlay_cannot_break_desktop_registration(tmp_path):
    (tmp_path/'src-tauri/capabilities').mkdir(parents=True)
    (tmp_path/'src').mkdir()
    name='binaries/konggu-worker'
    (tmp_path/'src/sidecarClient.ts').write_text('Command.sidecar("'+name+'", ["--serve"])')
    (tmp_path/'src-tauri/tauri.conf.json').write_text(json.dumps({'bundle':{'externalBin':[name]}}))
    (tmp_path/'src-tauri/capabilities/default.json').write_text(json.dumps({'permissions':[
        {'identifier':'shell:allow-spawn','allow':[{'name':name,'sidecar':True,'args':['--serve']}]}]}))
    overlay=tmp_path/'override.json'
    overlay.write_text(json.dumps({'bundle':{'externalBin':['some-candidate/binaries/konggu-worker']}}))
    assert _registration_checks(tmp_path)[0][1]
    assert not _registration_checks(tmp_path,overlay)[0][1]
    overlay.write_text(json.dumps({'bundle':{'externalBin':[name]}}))
    assert _registration_checks(tmp_path,overlay)[0][1]
