"""Run a copied worker with no project, Python environment, or previous OCR cache.

The host Python only creates synthetic PDFs and drives the executable protocol.
The worker is launched outside the checkout with an allowlisted environment.
This is a local isolation check, not a replacement for a clean Windows account.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def isolated_environment(stage: Path, inherited: dict[str, str]) -> dict[str, str]:
    system = inherited.get('SystemRoot') or inherited.get('SYSTEMROOT') or r'C:\Windows'
    return {
        'SystemRoot': system, 'WINDIR': system,
        'COMSPEC': str(Path(system)/'System32/cmd.exe'),
        'PATH': str(Path(system)/'System32'),
        'TEMP': str(stage/'temp'), 'TMP': str(stage/'temp'),
        'USERPROFILE': str(stage/'profile'),
        'APPDATA': str(stage/'profile/roaming'), 'LOCALAPPDATA': str(stage/'profile/local'),
        'PROGRAMDATA': str(stage/'programdata'), 'PUBLIC': str(stage/'public'),
        'KONGGU_APP_DATA_ROOT': str(stage/'appdata'),
        'KONGGU_OCR_MODEL_DIR': str(stage/'models'),
        'PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK': 'True',
        'PADDLE_PDX_MODEL_SOURCE': 'bos',
        'HF_HUB_OFFLINE': '1', 'PADDLE_PDX_DISABLE_MODEL_DOWNLOAD': '1',
    }


def synthetic_sources(directory: Path) -> dict[str, Path]:
    import fitz

    directory.mkdir(parents=True, exist_ok=True)
    chinese = ('空谷打包隔离验收人工合成材料不含真实个人信息用于本地离线测试\n'*3
               + '星期一\n1周\n2026-03-02\nPACKCN101\n--\n--\n--\n--\n--\n')
    english = ('Konggu synthetic offline packaging verification, no personal data.\n'
               'Generated only for the executable integration check.\n'
               'Monday\n2026-03-02\n3\nPACKEN202\n')
    paths = {}
    for kind, text, font in [('中方', chinese, 'china-s'), ('英方', english, 'helv')]:
        path = directory/f'办公室-合成甲-干事-{kind}课表.pdf'
        with fitz.open() as document:
            page = document.new_page(width=720, height=520)
            page.insert_text((35, 45), text, fontname=font, fontsize=15)
            document.save(path)
        paths[kind] = path
    image_path = directory/'image/办公室-合成甲-干事-英方课表.pdf'
    image_path.parent.mkdir()
    with fitz.open() as source:
        page = source.new_page(width=720, height=520)
        page.insert_text((35, 50), 'PACKAGING OCR CHECK 2468\nMonday\n2026-03-02\n3\nPACKEN202', fontsize=23)
        pixels = page.get_pixmap(dpi=144)
        with fitz.open() as raster:
            raster.new_page(width=720, height=520).insert_image(page.rect, pixmap=pixels)
            raster.save(image_path)
    paths['image'] = image_path
    return paths


class WorkerSession:
    def __init__(self, worker: Path, stage: Path, environment: dict[str, str], timeout: float):
        self.timeout = timeout
        self.messages = queue.Queue()
        self.stderr = (stage/'worker.stderr.log').open('w', encoding='utf-8')
        self.stdout_log = (stage/'worker.stdout.jsonl').open('w', encoding='utf-8')
        self.process = subprocess.Popen([str(worker), '--serve'], cwd=stage, env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
            text=True, encoding='utf-8', errors='replace',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.counter = 0

    def _read(self):
        try:
            for line in self.process.stdout:
                self.stdout_log.write(line)
                self.stdout_log.flush()
                try:
                    self.messages.put(json.loads(line))
                except ValueError:
                    self.messages.put(dict(protocol_error=True))
        finally:
            self.messages.put(dict(process_ended=True))

    def request(self, command: str, **payload):
        self.counter += 1
        request_id = str(self.counter)
        self.process.stdin.write(json.dumps(dict(id=request_id, command=command, **payload), ensure_ascii=False)+'\n')
        self.process.stdin.flush()
        deadline = time.monotonic()+self.timeout
        while True:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f'worker request timed out: {command}')
            try:
                message = self.messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError(f'worker request timed out: {command}') from exc
            if message.get('protocol_error') or message.get('process_ended'):
                raise RuntimeError('Worker exited or emitted non-JSON protocol output.')
            if message.get('event') == 'sidecar.fatal':
                raise RuntimeError(str(message.get('message') or 'Worker startup failed.'))
            if str(message.get('id')) == request_id and message.get('type') == 'response':
                return message.get('data', message) if message.get('ok') else message

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'], capture_output=True)
            else:
                self.process.kill()
            self.process.wait(timeout=10)
        self.reader.join(timeout=2)
        self.stderr.close()
        self.stdout_log.close()


def verify(worker: Path, *, work_dir: Path | None = None, timeout: float = 180) -> dict:
    worker = worker.resolve()
    stage = work_dir.resolve() if work_dir else Path(tempfile.mkdtemp(prefix='konggu-isolated-'))
    if stage.is_relative_to(ROOT) or ROOT.is_relative_to(stage):
        raise ValueError('Isolation work directory must be outside the checkout.')
    if work_dir and stage.exists():
        raise ValueError('Choose a new isolation directory; existing data is never overwritten.')
    stage.mkdir(parents=True, exist_ok=True)
    for name in ['temp', 'profile', 'appdata', 'models', 'programdata', 'public']:
        (stage/name).mkdir()
    copied = stage/'konggu-worker.exe'
    shutil.copy2(worker, copied)
    sources = synthetic_sources(stage/'synthetic')
    environment = isolated_environment(stage, dict(os.environ))
    report = dict(format_version=1, ok=False, isolation_directory_name=stage.name,
                  worker_sha256=hashlib.sha256(copied.read_bytes()).hexdigest(),
                  environment=dict(fresh_profile=True, fresh_models=True, system_only_path=True,
                                   project_outside_working_directory=True, inherited_python=False),
                  steps=[])
    session = None
    started = time.monotonic()
    try:
        session = WorkerSession(copied, stage, environment, timeout)
        repaired = session.request('resources.repair')
        if not repaired.get('ok') or not repaired.get('ready'):
            raise RuntimeError('Bundled model restoration or runtime probe failed.')
        report['steps'].append(dict(stage='resource_restore_and_inference_probe', ok=True,
                                   copied_count=len(repaired.get('copied', []))))
        saved = session.request('settings.calendar.save', settings=dict(semester_start_date='2026-03-02', teaching_weeks=18))
        if not saved.get('ok'):
            raise RuntimeError('Isolated calendar save failed.')
        parsed = session.request('schedules.parse', paths=[str(sources['中方']), str(sources['英方'])])
        if (not parsed.get('ok') or parsed.get('quality_state') != 'accepted'
                or parsed.get('can_export') is not False or not parsed.get('verification', {}).get('pending')):
            raise RuntimeError('Synthetic text pair did not enter course verification.')
        report['steps'].append(dict(stage='text_pair_preview', ok=True))
        job_id = parsed['result_ref']
        premature = stage/'unverified-text.xlsx'
        denied = session.request('exports.excel', result_ref=job_id, target_path=str(premature))
        if denied.get('ok') or premature.exists() or '待人工核验' not in str(denied.get('error', '')):
            raise RuntimeError('Unverified text pair did not fail at the course verification gate.')
        review = session.request('review.get', job_id=job_id)
        pending_items = review['verification']['items']
        if len(pending_items) != parsed['verification']['pending'] or any(i['confirmed'] for i in pending_items):
            raise RuntimeError('Unexpected initial verification queue for the synthetic text pair.')
        for index, item in enumerate(pending_items, 1):
            review = session.request('review.verify', job_id=job_id, item_id=item['item_id'], revision=review['revision'])
            if not review.get('ok') or review['verification']['confirmed'] != index:
                raise RuntimeError('Synthetic course verification was not recorded.')
        if review['verification']['pending']:
            raise RuntimeError('Synthetic text pair has verification items left.')
        report['steps'].append(dict(stage='text_pair_course_verification', ok=True, verified=len(pending_items)))
        exported = session.request('exports.excel', result_ref=parsed['result_ref'], target_path=str(stage/'synthetic.xlsx'))
        target = stage/'synthetic.xlsx'
        if not exported.get('ok') or not target.is_file():
            raise RuntimeError('Excel export failed.')
        with zipfile.ZipFile(target) as workbook:
            if 'xl/workbook.xml' not in workbook.namelist() or workbook.testzip():
                raise RuntimeError('Excel export is not a valid workbook.')
        report['steps'].append(dict(stage='excel_export', ok=True, bytes=target.stat().st_size))
        image = session.request('schedules.parse', paths=[str(sources['中方']), str(sources['image'])])
        texts = []
        for cache in (stage/'appdata/cache').rglob('*.json'):
            try:
                payload = json.loads(cache.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            texts.extend(str(item.get('text') or '') for item in payload.get('items', []) if isinstance(item, dict))
        joined = ''.join(texts).replace(' ', '').upper()
        if '2468' not in joined or 'PACK' not in joined:
            raise RuntimeError('Actual image OCR did not recognize the synthetic sentinel.')
        report['steps'].append(dict(stage='actual_image_ocr', ok=True, recognized_sentinel=True))
        if (not image.get('ok') or not image.get('result_ref') or image.get('can_export') is not False
                or image.get('quality_state') not in {'blocked', 'needs_review'}):
            raise RuntimeError('Image batch did not produce a real blocked or unreviewed result for the export check.')
        report['steps'].append(dict(stage='image_preview_state', ok=True,
                                   produced_preview=True, valid_result_ref=True,
                                   quality_state=image['quality_state']))
        denied = session.request('exports.excel', result_ref=image['result_ref'], target_path=str(stage/'must-not-exist.xlsx'))
        quality_message = '存在阻断错误' if image['quality_state'] == 'blocked' else '仍有未确认问题'
        if (denied.get('ok') or (stage/'must-not-exist.xlsx').exists()
                or quality_message not in str(denied.get('error') or '')):
            raise RuntimeError('Export did not fail specifically at the real quality gate.')
        report['steps'].append(dict(stage='unreviewed_export_denied', ok=True, denial_reason='quality_gate'))
        report['ok'] = True
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {str(exc).replace(str(stage), "<isolated>")}'
    finally:
        if session is not None:
            session.close()
            report['exit_code'] = session.process.returncode
            report['ok'] = report['ok'] and session.process.returncode == 0
        report['elapsed_seconds'] = round(time.monotonic()-started, 3)
        (stage/'isolation_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--timeout', type=float, default=180)
    args = parser.parse_args()
    report = verify(args.worker, work_dir=args.work_dir, timeout=args.timeout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
