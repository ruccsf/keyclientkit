"""
Group Client Strategy Report System - Environment Doctor
Usage: python install.py
"""

import sys, os, subprocess, json
from pathlib import Path

SKILL_DIR = Path(__file__).parent
PIPELINE_DIR = SKILL_DIR / 'pipeline'

ok_list, warn_list, fail_list = [], [], []

def ok(msg):
    ok_list.append(msg); print(f'  [OK] {msg}')
def warn(msg):
    warn_list.append(msg); print(f'  [WARN] {msg}')
def fail(msg):
    fail_list.append(msg); print(f'  [FAIL] {msg}')

def check_module(name):
    try:
        __import__(name); return True
    except ImportError:
        return False

def pip_install(packages):
    cmd = [sys.executable, '-m', 'pip', 'install', '-q', '--only-binary', ':all:'] + packages
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding='utf-8', errors='replace', env=env)
        return r.returncode == 0, (r.stderr or '')[:200]
    except Exception as e:
        return False, str(e)[:200]

print()
print('=' * 50)
print('  Group Client Strategy Report - Environment Doctor')
print('=' * 50)

# 1. Python
print('\n[1/5] Python')
v = sys.version_info
ok(f'Python {v.major}.{v.minor}.{v.micro}') if v >= (3, 10) else fail(f'Need 3.10+')

# 2. Core deps (auto-install)
print('\n[2/5] Dependencies')
for pkg, mod in [('requests', 'requests'), ('openpyxl', 'openpyxl')]:
    if check_module(mod):
        ok(pkg)
    else:
        warn(f'{pkg} missing - installing...')
        success, err = pip_install([pkg])
        if success:
            ok(f'{pkg} installed')
        else:
            fail(f'{pkg} failed: {err}')
            print(f'       Manual: pip install {pkg} --only-binary :all:')

# 3. Files
print('\n[3/5] Files')
for f in ['oauth_qcc.py', 'config.json', 'requirements.txt', 'CLAUDE.md']:
    ok(f) if (SKILL_DIR / f).exists() else fail(f'missing: {f}')
for f in ['qcc_client.py', 'qcc_fetch.py', 'web_filler.py', 'excel_renderer.py', 'html_renderer.py', 'excel_reader.py', 'export.py']:
    ok(f'pipeline/{f}') if (PIPELINE_DIR / f).exists() else fail(f'missing: pipeline/{f}')

# 4. QCC Auth
print('\n[4/5] QCC OAuth')
config_path = SKILL_DIR / 'config.json'
if config_path.exists():
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        if config.get('qcc_oauth', {}).get('access_token'):
            ok('QCC OAuth authorized')
        else:
            warn('QCC not authorized -> python oauth_qcc.py auth')
    except Exception:
        warn('config.json parse error')
else:
    warn('config.json missing -> python oauth_qcc.py auth')

# 5. Pipeline
print('\n[5/5] Pipeline')
sys.path.insert(0, str(PIPELINE_DIR))
try:
    from qcc_fetch import build_skeleton
    tables = sum(len(sec.get('tables',[])) for ch in build_skeleton('test').get('chapters',{}).values() for sec in ch.values())
    ok(f'Pipeline normal ({tables} tables)')
except Exception as e:
    fail(f'Pipeline load failed: {e}')

# Summary
print()
print('=' * 50)
if not fail_list and not warn_list:
    print(f'  All {len(ok_list)} checks passed!')
elif not fail_list:
    print(f'  {len(ok_list)} passed, {len(warn_list)} warnings (non-blocking)')
else:
    print(f'  {len(ok_list)} passed, {len(warn_list)} warnings, {len(fail_list)} FAILED')

print()
print('  Usage:')
print('    python pipeline/export.py --client <name>        Export Excel + HTML')
print('    python pipeline/export.py --client <name> --readback  Sync Excel back')
print('    python oauth_qcc.py auth                         First-time OAuth')
print('=' * 50)
