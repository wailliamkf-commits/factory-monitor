#!/usr/bin/env python3
"""Actual local VLM burst over a public still; capacity only, never live acceptance."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import platform
import threading
import time

import cv2
import httpx
from factory_monitor.inference import OllamaReviewer


def negative_fixture_gate(rows: list[dict], expected_count: int) -> str:
    if any(row.get('result', {}).get('decision') == 'supported' for row in rows):
        return 'FAIL'
    if len(rows) != expected_count or any(row.get('status') != 'schema_valid' for row in rows):
        return 'INCOMPLETE'
    if any(row['result']['decision'] != 'dismissed' for row in rows):
        return 'INCOMPLETE'
    return 'PASS_PUBLIC_NEGATIVE_ONLY'


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image', type=Path, required=True)
    p.add_argument('--endpoint', default='http://127.0.0.1:11435')
    p.add_argument('--model', default='qwen3-vl:2b-instruct')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--requests', type=int, choices=range(1, 11), default=10)
    p.add_argument('--frames', type=int, choices=range(1, 7), default=2)
    args = p.parse_args()
    reviewer = OllamaReviewer(args.endpoint, args.model, timeout_seconds=15)
    image = cv2.imread(str(args.image))
    if image is None:
        p.error('input image is unreadable')
    image = cv2.resize(image, (240, 320))
    frames = []
    for i in range(args.requests):
        # Different image bytes prevent the identical-input cache shortcut of the older probe.
        matrix = __import__('numpy').float32([[1, 0, i * 2], [0, 1, 0]])
        shifted = cv2.warpAffine(image, matrix, (240, 320))
        frames.append([{'timestamp': 1700000000.0 + j * .5, 'image': shifted}
                       for j in range(args.frames)])
    release = threading.Event()
    deadline = [0.0]
    def run(i):
        release.wait()
        start = time.monotonic()
        row = {'request_id': i, 'expected_decision': 'not_supported_for_public_bus_negative'}
        try:
            row['result'] = reviewer.review(frames[i], deadline=deadline[0], context={
                'kind': 'material_candidate', 'camera_id': f'PUBLIC-{i:02}',
                'source': 'public still fixture; no real event'})
            row['status'] = 'schema_valid'
        except TimeoutError as e:
            row.update(status='timeout', error=str(e))
        except Exception as e:
            row.update(status='error', error=f'{type(e).__name__}: {e}')
        row['queue_inclusive_ms'] = (time.monotonic() - (deadline[0] - 15)) * 1000
        row['request_ms'] = (time.monotonic() - start) * 1000
        return row
    with ThreadPoolExecutor(max_workers=args.requests) as pool:
        futures = [pool.submit(run, i) for i in range(args.requests)]
        deadline[0] = time.monotonic() + 15
        release.set()
        rows = [future.result() for future in futures]
    try:
        with httpx.Client(trust_env=False, timeout=2) as client:
            loaded = client.get(args.endpoint.rstrip('/') + '/api/ps').json()
    except Exception as e:
        loaded = {'error': str(e)}
    valid = [r for r in rows if r['status'] == 'schema_valid']
    report = {
        'scope': 'actual VLM capacity on varied public still crops; no live cameras or action-accuracy proof',
        'host': platform.platform(), 'generated_at': time.time(),
        'model': args.model, 'image_sha256': hashlib.sha256(args.image.read_bytes()).hexdigest(),
        'ordered_frames_per_request': args.frames, 'request_count': args.requests,
        'deadline_ms': 15000, 'rows': rows,
        'schema_valid': len(valid), 'timeouts': sum(r['status'] == 'timeout' for r in rows),
        'errors': sum(r['status'] == 'error' for r in rows),
        'supported_on_negative_fixture': sum(r['result']['decision'] == 'supported' for r in valid),
        'negative_fixture_gate': negative_fixture_gate(rows, args.requests),
        'all_within_deadline': len(valid) == args.requests and all(r['queue_inclusive_ms'] <= 15000 for r in valid),
        'field_gate': 'NOT_TESTED', 'loaded_model': loaded,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('schema_valid','timeouts','errors','supported_on_negative_fixture','negative_fixture_gate','all_within_deadline')}))
    return 0 if report['all_within_deadline'] and report['negative_fixture_gate'] == 'PASS_PUBLIC_NEGATIVE_ONLY' else 1


if __name__ == '__main__':
    raise SystemExit(main())
