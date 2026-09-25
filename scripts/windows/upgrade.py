#!/usr/bin/env python3
"""Apply the feature patch to an isolated copy of the latest onsite source."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

DIRECTORIES = {'src', 'tests', 'scripts'}
ROOT_FILES = {'pyproject.toml', 'README.md', 'AGENTS.md', 'LICENSE', 'LICENSE.md', 'LICENSE.txt'}


def prepare(source: Path, destination: Path, patch: Path, git: str = 'git') -> dict:
    source, destination, patch = source.resolve(), destination.resolve(), patch.resolve()
    if not source.is_dir() or not (source / 'src').is_dir():
        raise ValueError('Source must be the latest onsite project with a src directory')
    if destination.exists() or destination == source or source in destination.parents:
        raise ValueError('Destination must be a new directory outside the onsite source')
    text = patch.read_text(encoding='utf-8')
    for target in re.findall(r'^(?:---|\+\+\+) (.+)$', text, re.MULTILINE):
        if target == '/dev/null':
            continue
        if not target.startswith(('a/', 'b/')):
            raise ValueError('Unexpected patch path')
        parts = Path(target[2:]).parts
        if not parts or '..' in parts or ':' in target or '\\' in target:
            raise ValueError('Unsafe patch path')
        if parts[0] not in DIRECTORIES and target[2:] not in ROOT_FILES:
            raise ValueError(f'Patch touches an unsupported path: {target}')
    contents = []
    for name in sorted(DIRECTORIES | ROOT_FILES):
        root = source / name
        if root.is_symlink():
            raise ValueError(f'Symlink is not copied: {name}')
        if not root.exists():
            continue
        paths = sorted(root.rglob('*')) if root.is_dir() else [root]
        for path in paths:
            if path.is_symlink():
                raise ValueError(f'Symlink is not copied: {path.relative_to(source)}')
            relative = path.relative_to(source)
            if '__pycache__' in relative.parts or path.suffix in {'.pyc', '.pyo'}:
                continue
            if path.is_file():
                # Read once so the source hash describes the exact bytes copied.
                data = path.read_bytes()
                contents.append((relative, data))
    destination.mkdir(parents=True)
    files = []
    for relative, data in contents:
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files.append({'path': relative.as_posix(), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    record = {'source_was_modified': False, 'copied': files,
              'patch_sha256': hashlib.sha256(patch.read_bytes()).hexdigest(), 'patch_applied': False,
              'excluded': ['camera configuration', 'evidence', 'models', 'credentials', 'old virtual environment']}
    receipt = destination / 'source-fingerprints.json'
    receipt.write_text(json.dumps(record, indent=2), encoding='utf-8')
    subprocess.run([git, '-C', str(destination), 'init', '-q'], check=True)
    base = [git, '-C', str(destination), 'apply', '--ignore-space-change']
    subprocess.run([*base, '--check', str(patch)], check=True)
    subprocess.run([*base, str(patch)], check=True)
    record['patch_applied'] = True
    receipt.write_text(json.dumps(record, indent=2), encoding='utf-8')
    return record


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--destination', type=Path, required=True)
    p.add_argument('--patch', type=Path, required=True)
    p.add_argument('--git', default='git')
    args=p.parse_args()
    try:
        result=prepare(args.source,args.destination,args.patch,args.git)
    except (OSError, ValueError, subprocess.CalledProcessError) as e:
        print(json.dumps({'ok':False,'error':str(e),'action':'Stop. Preserve onsite source; inspect the isolated candidate.'}))
        return 1
    print(json.dumps({'ok':True,'copied_files':len(result['copied']), 'patch_applied':True,
                      'acceptance':'not tested; onsite repaired code still requires regression'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
