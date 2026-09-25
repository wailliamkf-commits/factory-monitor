#!/usr/bin/env python3
"""Build a small source-only lab with explicit allowlist and read-back hashes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--evidence-dir', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.output.exists():
        parser.error('output exists; choose a new package path')
    files = [
        'docs/ACTIVE_OBSERVATION_LAB_zh.md',
        'docs/ACTIVE_OBSERVATION_ARCHITECTURE_20260925_zh.md',
        'docs/ACTIVE_OBSERVATION_VERIFICATION_20260925_zh.md',
        'docs/ACTIVE_VIEW_RESEARCH_20260925_zh.md', 'docs/HETEROGENEOUS_STABILITY_20260925_zh.md',
        'docs/superpowers/specs/2026-09-25-active-observation-design.md',
        'docs/superpowers/plans/2026-09-25-active-observation.md',
        'docs/superpowers/specs/2026-09-25-mainland-ten-camera-design.md',
        'scripts/experiment_active_observation.py', 'scripts/run-active-observation-lab.ps1',
        'scripts/run-active-observation-lab.cmd', 'src/factory_monitor/__init__.py',
    ]
    for name in ('active_inspection', 'inspection_profile', 'scene_watch', 'view_feasibility'):
        files += [f'src/factory_monitor/{name}.py', f'tests/test_{name}.py']
    files += ['tests/test_active_experiment_report.py']
    payload = {name: (root / name).read_bytes() for name in files}
    payload['README_zh.md'] = (
        '# 主动观察合成实验包\n\n请先打开 docs/ACTIVE_OBSERVATION_LAB_zh.md。\n'
        '双击 scripts/run-active-observation-lab.cmd，选择此前离线项目的 Python 环境。\n'
        '本包不采集屏幕、不点击客户端、不替换原软件。\n').encode('utf-8')
    if args.evidence_dir:
        for name in ('experiment.json', '结果说明.md', 'full-pytest.log'):
            payload[f'evidence/{name}'] = (args.evidence_dir / name).read_bytes()
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=root, text=True).strip())
    manifest = {'schema_version': 1, 'source_commit': commit, 'source_worktree_dirty': dirty,
                'scope': 'synthetic isolated lab; no native control; field NOT_TESTED',
                'files': {name: {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                          for name, data in payload.items()}}
    payload['MANIFEST.json'] = (json.dumps(manifest, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
    with zipfile.ZipFile(args.output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(payload.items()):
            archive.writestr(name, data)
    with zipfile.ZipFile(args.output) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(payload)
        for name, data in payload.items():
            assert archive.read(name) == data, name
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    summary = {'zip_sha256': digest, 'bytes': args.output.stat().st_size,
               'archive_files': len(payload), 'all_payloads_read_back': True,
               'source_commit': commit, 'source_worktree_dirty': dirty}
    args.output.with_suffix(args.output.suffix + '.sha256').write_text(
        f'{digest}  {args.output.name}\n', encoding='utf-8')
    args.output.with_suffix(args.output.suffix + '.verification.json').write_text(
        json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
