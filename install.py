"""
Group Client Strategy Report System - Environment Doctor
========================================================
一键安装检查 + 引导 OAuth 授权。

用法: python install.py
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
print('\n[1/4] Python')
v = sys.version_info
ok(f'Python {v.major}.{v.minor}.{v.micro}') if v >= (3, 10) else fail(f'Need 3.10+')

# 2. Core deps (auto-install)
print('\n[2/4] Dependencies')
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
print('\n[3/4] Files')
for f in ['oauth_qcc.py', 'config.json', 'requirements.txt', 'CLAUDE.md']:
    ok(f) if (SKILL_DIR / f).exists() else fail(f'missing: {f}')
for f in ['qcc_client.py', 'qcc_fetch.py', 'web_filler.py', 'excel_renderer.py', 'html_renderer.py', 'excel_reader.py', 'export.py']:
    ok(f'pipeline/{f}') if (PIPELINE_DIR / f).exists() else fail(f'missing: pipeline/{f}')

# 4. QCC OAuth
print('\n[4/4] QCC OAuth')
config_path = SKILL_DIR / 'config.json'
authorized = False
if config_path.exists():
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        oauth = config.get('qcc_oauth', {})

        # 新格式: qcc_oauth.company / 旧格式: qcc_oauth 本身是 token
        if 'company' in oauth and 'access_token' in oauth['company']:
            ok('QCC OAuth (company) — 已授权')
            authorized = True
        elif 'access_token' in oauth:
            ok('QCC OAuth (company) — 已授权 [旧格式，将在首次使用时自动迁移]')
            authorized = True
        else:
            warn('QCC OAuth 未授权')
    except Exception:
        warn('config.json 解析异常')
else:
    warn('config.json 缺失')

if not authorized:
    print()
    print('  ╔══════════════════════════════════════════════════╗')
    print('  ║  企查查数据采集需要 OAuth 授权。              ║')
    print('  ║  请运行以下命令完成一键授权:                ║')
    print('  ║                                              ║')
    print('  ║    python oauth_qcc.py auth                 ║')
    print('  ║                                              ║')
    print('  ║  授权后即可开始采集企业数据。            ║')
    print('  ╚══════════════════════════════════════════════════╝')

# 5. Pipeline validation
print()
print('[验证] 数据管线')
sys.path.insert(0, str(PIPELINE_DIR))
try:
    from qcc_fetch import build_skeleton
    tables = sum(len(sec.get('tables',[])) for ch in build_skeleton('test').get('chapters',{}).values() for sec in ch.values())
    ok(f'管线正常 ({tables} 张表)')
except Exception as e:
    fail(f'管线加载失败: {e}')

# Summary
print()
print('=' * 50)
if not fail_list and not warn_list:
    print(f'  ✅ 全部 {len(ok_list)} 项检查通过！')
elif not fail_list:
    print(f'  ✅ {len(ok_list)} 通过, {len(warn_list)} 提醒')
else:
    print(f'  {len(ok_list)} 通过, {len(warn_list)} 提醒, {len(fail_list)} 失败')

print()
print('  快速开始:')
print('    python oauth_qcc.py auth                  ← 首次使用先授权')
print('    python pipeline/export.py --client <企业名>  ← 导出报告')
print('=' * 50)
