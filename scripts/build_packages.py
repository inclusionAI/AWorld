#!/usr/bin/env python3
"""Build aworld and aworld-cli together with their declared PEP 517 backend."""
from __future__ import annotations

import argparse
from email.parser import Parser
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--outdir', type=Path, default=ROOT / 'dist')
    parser.add_argument('--no-isolation', action='store_true', help='Use an environment with declared build dependencies already installed')
    args = parser.parse_args()
    destination = args.outdir.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, '-m', 'build', '--outdir', str(destination)]
    if args.no_isolation:
        command.append('--no-isolation')
    for source in (ROOT, ROOT / 'aworld-cli'):
        subprocess.run([*command, str(source)], check=True)
    metadata = {}
    for name in ('aworld', 'aworld_cli'):
        # Read the just-built wheel's project metadata rather than importing either package.
        wheel = max(destination.glob(name + '-*-py3-none-any.whl'), key=lambda p: p.stat().st_mtime_ns)
        with zipfile.ZipFile(wheel) as archive:
            record = next(p for p in archive.namelist() if p.endswith('.dist-info/METADATA'))
            data = Parser().parsestr(archive.read(record).decode())
        metadata[name] = data
    version = metadata['aworld']['Version']
    if metadata['aworld_cli']['Version'] != version or f'aworld=={version}' not in metadata['aworld_cli'].get_all('Requires-Dist', []):
        raise RuntimeError('aworld-cli must declare the same version and exact aworld dependency')
    filenames = [f'{name}-{version}{suffix}' for name in ('aworld', 'aworld_cli') for suffix in ('-py3-none-any.whl', '.tar.gz')]
    manifest = {'version': version, 'artifacts': [{'filename': name, 'sha256': hashlib.sha256((destination / name).read_bytes()).hexdigest()} for name in filenames]}
    (destination / 'packages.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
