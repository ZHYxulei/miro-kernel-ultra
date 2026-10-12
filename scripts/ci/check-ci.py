#!/usr/bin/env python3
import ast
from pathlib import Path
import subprocess
import sys

import yaml

root = Path(__file__).resolve().parents[2]
for path in [root / 'build.sh', *sorted((root / 'scripts/ci').glob('*.sh'))]:
    subprocess.run(['bash', '-n', str(path)], check=True)
for path in sorted((root / 'scripts/ci').glob('*.py')):
    ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
for path in sorted((root / '.github/workflows').glob('*.yml')):
    document = yaml.safe_load(path.read_text(encoding='utf-8'))
    if not isinstance(document, dict) or not document.get('jobs'):
        raise ValueError(f'Invalid workflow: {path.name}')
    for job in document['jobs'].values():
        for step in job.get('steps', []):
            if 'run' in step and step.get('shell', 'bash').startswith('bash'):
                subprocess.run(['bash', '-n'], input=step['run'], text=True, check=True)
for script in ('telegram-notify.py', 'check-upstreams.py'):
    subprocess.run([sys.executable, '-I', str(root / 'scripts/ci' / script), '--self-test'], check=True)
print('Shell/Python syntax, workflow YAML and notification/upstream offline tests passed')
