#!/usr/bin/env python3
"""Build a reviewable source archive without local secrets or workspace records."""
import hashlib
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'output/submission/ledgerflow-ps1-source.zip'
ROOT_FILES = ['README.md', 'SUBMISSION.md', 'pyproject.toml', 'uv.lock', 'Dockerfile', 'compose.yaml', 'render.yaml', '.dockerignore', '.gitignore', '.env.example']
EXCLUDED_PARTS = {'node_modules', '__pycache__', '.pytest_cache', '.git', '.venv', 'manual-samples', 'results'}
EXCLUDED_NAMES = {'verification.md', 'generate_manual_test_pack.py', 'live-check.json', '.DS_Store'}


def included(path):
    relative = path.relative_to(ROOT)
    if path.is_symlink() or not path.is_file():
        return False
    if set(relative.parts) & EXCLUDED_PARTS or path.name in EXCLUDED_NAMES:
        return False
    if path.name.startswith('.env') or path.suffix in {'.pyc', '.log', '.zip', '.tsbuildinfo', '.sqlite3', '.db'}:
        return False
    return True


def main():
    files = {ROOT / name for name in ROOT_FILES}
    for folder in ('backend', 'tests', 'scripts', 'docs', 'samples', '.github'):
        files.update(path for path in (ROOT / folder).rglob('*') if included(path))
    for folder in ('src', 'public', 'tests', 'dist'):
        files.update(path for path in (ROOT / 'frontend' / folder).rglob('*') if included(path))
    files.update(path for path in (ROOT / 'frontend').iterdir() if included(path))
    assert all(p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(ROOT) for p in files)
    manifest = {'name': 'LedgerFlow PS-1 source', 'excluded': ['API keys and .env', 'workspace databases and uploads', 'private manual invoices and PO pack', 'historical runtime results', 'dependency caches'], 'files': {}}
    contents = {}
    for path in sorted(files):
        data = path.read_bytes()
        if re.search(rb'sk-or-v1-[A-Za-z0-9]{20,}', data):
            raise RuntimeError(f'Credential-like value found in {path.relative_to(ROOT)}; archive not written.')
        relative = path.relative_to(ROOT).as_posix()
        contents[relative] = data
        manifest['files'][relative] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    assert 'frontend/dist/index.html' in contents, 'Build the frontend before packaging.'
    DEST.parent.mkdir(parents=True, exist_ok=True)
    temporary = DEST.with_suffix('.zip.tmp')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
        for relative, data in contents.items():
            info = zipfile.ZipInfo('ledgerflow/' + relative)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (0o100755 if relative.endswith('.sh') else 0o100644) << 16
            archive.writestr(info, data)
        archive.writestr('ledgerflow/MANIFEST.json', json.dumps(manifest, indent=2) + '\n')
    with zipfile.ZipFile(temporary) as archive:
        assert archive.testzip() is None
        for relative, data in contents.items():
            assert archive.read('ledgerflow/' + relative) == data
    temporary.replace(DEST)
    digest = hashlib.sha256(DEST.read_bytes()).hexdigest()
    DEST.with_suffix('.zip.sha256').write_text(f'{digest}  {DEST.name}\n')
    print(json.dumps({'archive': str(DEST), 'files': len(contents), 'bytes': DEST.stat().st_size, 'sha256': digest}, indent=2))


if __name__ == '__main__':
    main()
