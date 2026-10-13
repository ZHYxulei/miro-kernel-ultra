#!/usr/bin/env python3
import ast
from pathlib import Path
import re
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

# 锁定两个独立构建的依赖关系、输出引用和产物隔离，防止退回串行构建。
jobs = yaml.safe_load((root / '.github/workflows/kernel-ci.yml').read_text(encoding='utf-8'))['jobs']
uploads = []
for job_id, job in jobs.items():
    needs = job.get('needs', [])
    needs = [needs] if isinstance(needs, str) else needs
    for dependency, output in re.findall(r'needs\.([\w-]+)\.outputs\.(\w+)', str(job)):
        assert dependency in needs, (job_id, dependency)
        assert output in jobs[dependency].get('outputs', {}), (dependency, output)
    for step in job.get('steps', []):
        if step.get('uses', '').partition('@')[0] == 'actions/upload-artifact':
            name = step['with']['name']
            for key, value in job.get('env', {}).items():
                name = name.replace('${{ env.' + key + ' }}', value)
            uploads.append(name)
assert len(uploads) == len(set(uploads)), 'Artifact names must be unique'

build_id_step = next(step for step in jobs['preflight']['steps'] if step.get('id') == 'build-id')
for output, expression in jobs['preflight']['outputs'].items():
    assert expression == '${{ steps.build-id.outputs.' + output + ' }}'
    assert f'echo "{output}=' in build_id_step['run'], output

for variant, out_dir, suffix, modules, archive in (
        ('release', 'out', '', 'modules_install_release', 'symbols_archive'),
        ('debug', 'out-debug', '-debug', 'modules_install', 'symbols_archive_debug')):
    build = jobs[f'build-{variant}']
    assert build['needs'] == 'preflight'
    assert build['runs-on'] == 'ubuntu-24.04' and 'strategy' not in build
    assert build['timeout-minutes'] == 240
    assert build['env']['BUILD_VARIANT'] == variant
    assert build['env']['OUT_DIR'] == out_dir
    assert build['env']['LOCALVERSION'] == suffix
    assert build['env']['MODULES_TREE'] == modules
    assert build['env']['KPATCH_TOOLCHAIN_DIR'] == 'out/kpatch-toolchain'
    assert build['env']['DEBUG_SYMBOLS_ARCHIVE'] == '${{ needs.preflight.outputs.' + archive + ' }}'
    steps = {step.get('name'): step for step in build['steps']}
    assert steps['Build kernel']['timeout-minutes'] == 110
    assert sum('bash build.sh all' in step.get('run', '') for step in build['steps']) == 1
    cache = next(step['with'] for step in build['steps'] if step.get('with', {}).get('path') == '.ccache')
    assert '${{ env.BUILD_VARIANT }}' in cache['key']
    assert all('${{ env.BUILD_VARIANT }}' in key for key in cache['restore-keys'].splitlines())
    assert 'cp -r "${OUT_DIR}/${MODULES_TREE}"' in steps['Stage kernel outputs for packaging']['run']
    assert 'KPatch-Next/tools/kptools' in steps['Stage kernel outputs for packaging']['run']
    for name in ('Stage build logs', 'Upload build logs'):
        assert steps[name]['if'] == 'always()'
    package = jobs[f'package-{variant}']
    assert package['needs'] == f'build-{variant}'
    downloads = [step['with']['name'] for step in package['steps'] if step.get('uses', '').partition('@')[0] == 'actions/download-artifact']
    assert downloads == [f'miro-kernel-ultra-kernel-{variant}-${{{{ github.run_id }}}}']

symbols = next(step['run'] for step in jobs['build-release']['steps'] if step.get('name') == 'Stage debug symbols archives')
assert 'if [ "${BUILD_VARIANT}" = release ]; then' in symbols
assert symbols.index('if [ "${BUILD_VARIANT}" = release ]; then') < symbols.index('for file in Image System.map vmlinux.symvers .config; do') < symbols.index('else')
assert 'SHA256SUMS-debug-symbols' in symbols and 'SHA256SUMS-debug-kernel-symbols' in symbols
symbol_downloads = [step['with'] for step in jobs['release']['steps'] if step.get('uses', '').partition('@')[0] == 'actions/download-artifact' and step['with']['path'] == 'release-assets/symbols']
assert {step['name'] for step in symbol_downloads} == {
    f'miro-kernel-ultra-symbols-{variant}-${{{{ github.run_id }}}}' for variant in ('release', 'debug')}
assert len(symbol_downloads) == 2
assert jobs['smoke-test']['needs'] == ['package-release', 'package-debug']
assert any('for zip in smoke-release/*.zip smoke-debug/*.zip' in step.get('run', '') for step in jobs['smoke-test']['steps'])
assert jobs['release']['needs'] == ['package-release', 'package-debug', 'smoke-test']
assert jobs['release']['if'] == "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')"
print('Parallel build dependencies, outputs, cache/artifact isolation and release gating passed')
for script in ('telegram-notify.py', 'check-upstreams.py'):
    subprocess.run([sys.executable, '-I', str(root / 'scripts/ci' / script), '--self-test'], check=True)
print('Shell/Python syntax, workflow YAML and notification/upstream offline tests passed')
