"""Small argument-safe helpers for PowerShell 5.1; no shell command interpolation."""
import argparse
import json
from pathlib import Path
import platform
from factory_monitor.config import default_config, save_config


def initialize(candidate: Path, bundle: Path) -> Path:
    target=candidate/'runtime/monitor.json'
    if not target.exists():
        config=default_config()
        config['detection'].update(model_path=str(bundle/'resources/models/yolo11n.pt'), device='auto')
        config['review']['endpoint']='http://127.0.0.1:11435'
        target.parent.mkdir(parents=True,exist_ok=True)
        save_config(config,target)
    return target


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['init','gpu'])
    p.add_argument('--candidate',type=Path)
    p.add_argument('--bundle',type=Path)
    p.add_argument('--require-cuda',action='store_true')
    args=p.parse_args()
    if args.mode=='init':
        if args.candidate is None or args.bundle is None:p.error('init needs --candidate and --bundle')
        print(json.dumps({'config':str(initialize(args.candidate,args.bundle)),'field_verified':False}))
        return 0
    import torch
    available=torch.cuda.is_available()
    print(json.dumps({'os':platform.platform(),'torch':torch.__version__,'cuda_version':torch.version.cuda,
                      'cuda_available':available,'gpu':torch.cuda.get_device_name(0) if available else None,
                      'device_memory_bytes':torch.cuda.get_device_properties(0).total_memory if available else None}))
    return 2 if args.require_cuda and not available else 0


if __name__=='__main__':
    raise SystemExit(main())
