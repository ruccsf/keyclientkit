"""
报告导出 CLI — 统一入口
========================
用法:
  python pipeline/export.py --client 中粮集团              # 导出 Excel + HTML（需要所有 🟡 字段已搜索）
  python pipeline/export.py --client 中粮集团 --force       # 强制导出（跳过 🟡 字段检查）
  python pipeline/export.py --client 中粮集团 --readback    # 读回 Excel → 合并 JSON → 重生成 HTML
  python pipeline/export.py --client 中粮集团 --stats       # 查看统计
  python pipeline/export.py --client 中粮集团 --excel-only  # 仅 Excel
  python pipeline/export.py --client 中粮集团 --html-only   # 仅 HTML
"""

import sys, os, json
from pathlib import Path
from datetime import datetime


def _pick_output_dir() -> Path:
    """弹出系统对话框让用户选择保存位置。仅在交互式环境弹窗，否则回退默认 output/。"""
    # 非桌面环境（AI agent / 远程终端 / CI）跳过弹窗，直接回退
    _no_gui = os.environ.get('WORKBUDDY_AGENT') or os.environ.get('CI') or os.environ.get('SSH_CLIENT')
    if _no_gui:
        return OUTPUT_DIR

    # 跨进程单次锁：同 session 只弹一次（AI 可能多次调用 export.py）
    if os.environ.get('_KEYCLIENTKIT_DIALOG_SHOWN'):
        return OUTPUT_DIR
    os.environ['_KEYCLIENTKIT_DIALOG_SHOWN'] = '1'

    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        try:
            root.tk.call('tk', 'windowingsystem')  # 探测 GUI 是否可用
        except Exception:
            root.destroy()
            return OUTPUT_DIR
        chosen = filedialog.askdirectory(
            title='选择报告保存位置（Excel + HTML）',
            initialdir=str(OUTPUT_DIR),
        )
        root.destroy()
        _dialog_shown = True
        if chosen:
            return Path(chosen)
    except Exception:
        pass
    return OUTPUT_DIR

SKILL_DIR = Path(__file__).parent.parent
PIPELINE_DIR = Path(__file__).parent
SESSIONS_DIR = SKILL_DIR / 'sessions'
OUTPUT_DIR = SKILL_DIR / 'output'

sys.path.insert(0, str(PIPELINE_DIR))
from web_filler import _find_content_column


def print_stats(data: dict):
    greens = yellows = reds = 0
    for ch_key, ch_val in data.get('chapters', {}).items():
        if not isinstance(ch_val, dict): continue
        for sec_key, sec_val in ch_val.items():
            if not isinstance(sec_val, dict): continue
            for tbl in sec_val.get('tables', []):
                for row in tbl.get('data', []):
                    st = row.get('_status') or 'red'
                    if st == 'green': greens += 1
                    elif st == 'yellow': yellows += 1
                    else: reds += 1
    total = greens + yellows + reds
    print(f'\n📊 数据统计')
    print(f'  公司: {data["meta"]["client_name"]}')
    print(f'  行业: {data["cover"]["所属行业"]["value"]}')
    print(f'  总计: {total} 行')
    print(f'  🟢 公开数据: {greens} ({greens*100//total}%)')
    print(f'  🟡 Web搜索: {yellows} ({yellows*100//total}%)')
    print(f'  🔴 待人工填: {reds} ({reds*100//total}%)')
    print(f'  自动填充率: {(greens+yellows)*100//total}%')


def _check_yellow_fields(data: dict) -> int:
    """
    检查 🟡 字段是否都已处理。
    返回未搜索的空 🟡 行数（已标注"经检索未发现"的不计入）。
    """
    empty_yellows = 0
    total_yellows = 0
    for ch_val in data.get('chapters', {}).values():
        if not isinstance(ch_val, dict):
            continue
        for sec_val in ch_val.values():
            if not isinstance(sec_val, dict):
                continue
            for tbl in sec_val.get('tables', []):
                for row in tbl.get('data', []):
                    if row.get('_status') != 'yellow':
                        continue
                    total_yellows += 1
                    content_col = _find_content_column(row)
                    content = str(row.get(content_col, '')).strip() if content_col else ''
                    if not content:
                        empty_yellows += 1
    return empty_yellows, total_yellows


def _check_subsidiary_pdf_data(data: dict) -> int:
    """检查子公司表中是否有 PDF 数据（注册地、国标行业非空）。返回缺失行数。"""
    empty_count = 0
    for ch_val in data.get('chapters', {}).values():
        if not isinstance(ch_val, dict):
            continue
        for sec_val in ch_val.values():
            if not isinstance(sec_val, dict):
                continue
            for tbl in sec_val.get('tables', []):
                if '子公司列表' not in tbl.get('title', ''):
                    continue
                for row in tbl.get('data', []):
                    注册地 = str(row.get('注册地', '')).strip()
                    国标行业 = str(row.get('国标行业', '')).strip()
                    if not 注册地 and not 国标行业:
                        empty_count += 1
    return empty_count


def _check_executive_resumes(data: dict) -> tuple[int, int]:
    """检查高管表中履历列是否已填充。返回 (为空人数, 总人数)。表未找到返回 (-1, 0)。"""
    for ch_val in data.get('chapters', {}).values():
        if not isinstance(ch_val, dict):
            continue
        for sec_val in ch_val.values():
            if not isinstance(sec_val, dict):
                continue
            for tbl in sec_val.get('tables', []):
                if '高管' in tbl.get('title', ''):
                    total = 0
                    empty = 0
                    for row in tbl.get('data', []):
                        total += 1
                        if not str(row.get('履历', '')).strip():
                            empty += 1
                    return empty, total
    return -1, 0  # 表未找到


def cmd_export(client_name: str, excel_only=False, html_only=False, force=False,
               output_dir: Path = None) -> dict:
    """导出 Excel + HTML。output_dir 必须由调用方在生成前确定（只弹一次对话框）。"""
    session_path = SESSIONS_DIR / client_name / 'data.json'
    if not session_path.exists():
        print(f'❌ 未找到客户数据: {session_path}')
        print(f'   请先运行 QCC 采集')
        sys.exit(1)

    with open(session_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 🟡 字段检查：所有 yellow 字段必须已搜索（内容非空或已标注"经检索未发现"）
    empty_yellows, total_yellows = _check_yellow_fields(data)
    if empty_yellows > 0 and not force:
        print()
        print(f'❌ 还有 {empty_yellows}/{total_yellows} 个 🟡 字段未搜索')
        print(f'   请先完成 Web 搜索填充（搜不到请标注"经检索未发现公开数据"）')
        print(f'   强制导出: python pipeline/export.py --client {client_name} --force')
        sys.exit(1)
    elif empty_yellows > 0 and force:
        print(f'⚠️  --force: 跳过 🟡 字段检查（{empty_yellows}/{total_yellows} 个未搜索）')
    elif total_yellows > 0:
        print(f'✅ 🟡 字段已全部搜索（{total_yellows} 行）')

    # 子公司 PDF 数据检查：注册地/国标行业必须已填充
    empty_subs = _check_subsidiary_pdf_data(data)
    if empty_subs > 0 and not force:
        print()
        print(f'❌ 子公司表缺少 PDF 数据：{empty_subs} 家子公司的注册地/国标行业为空')
        print(f'   请先完成 Step 1.5 PDF 募集书补充（子公司数据唯一来源）')
        print(f'   强制导出: python pipeline/export.py --client {client_name} --force')
        sys.exit(1)
    elif empty_subs > 0 and force:
        print(f'⚠️  --force: 跳过子公司 PDF 数据检查（{empty_subs} 家无注册地/国标行业）')
    else:
        print(f'✅ 子公司 PDF 数据已填充')

    # 高管履历检查：🟡 履历列必须已搜索
    empty_resumes, total_resumes = _check_executive_resumes(data)
    if empty_resumes < 0:
        print('⚠️  未找到高管信息表，跳过履历检查')
    elif empty_resumes > 0 and not force:
        print()
        print(f'❌ 高管表缺少履历：{empty_resumes}/{total_resumes} 位高管的履历列为空')
        print(f'   请先完成 Step 2 高管履历搜索')
        print(f'   强制导出: python pipeline/export.py --client {client_name} --force')
        sys.exit(1)
    elif empty_resumes > 0 and force:
        print(f'⚠️  --force: 跳过履历检查（{empty_resumes}/{total_resumes} 位高管履历为空）')
    else:
        print(f'✅ 高管履历已填充（{total_resumes} 人）')

    if output_dir is None:
        output_dir = OUTPUT_DIR
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    safe_name = data['meta'].get('client_name', client_name).replace('/', '_')[:30]
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')

    print(f'📁 输出目录: {out_dir.absolute()}')
    print(f'⏳ 正在生成报告...')

    if not html_only:
        from excel_renderer import generate_excel
        excel_path = out_dir / f'{safe_name}_核对表_{ts}.xlsx'
        generate_excel(data, str(excel_path))
        size_kb = excel_path.stat().st_size // 1024
        print(f'✅ Excel: {excel_path.name} ({size_kb}KB)')

    if not excel_only:
        from html_renderer import generate_html
        html_path = out_dir / f'{safe_name}合作策略_报告_{ts}.html'
        generate_html(data, str(html_path))
        size_kb = html_path.stat().st_size // 1024
        print(f'✅ HTML:  {html_path.name} ({size_kb}KB)')

    print_stats(data)
    return data


def cmd_readback(client_name: str, output_dir: Path = None):
    """读回 Excel 修改 → 合并 JSON → 重生成 HTML"""
    session_path = SESSIONS_DIR / client_name / 'data.json'
    if not session_path.exists():
        print(f'❌ 未找到客户数据: {session_path}')
        sys.exit(1)

    with open(session_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    out_dir = output_dir or OUTPUT_DIR
    safe_name = data['meta'].get('client_name', client_name).replace('/', '_')[:30]

    # 找最新的核对表（按时间戳排序）
    candidates = sorted(out_dir.glob(f'{safe_name}_核对表_*.xlsx'), reverse=True)
    if not candidates:
        # 兼容旧版无时间戳文件
        legacy = out_dir / f'{safe_name}_核对表.xlsx'
        if legacy.exists():
            candidates = [legacy]
    if not candidates:
        print(f'❌ 未找到 Excel 核对表: {safe_name}_核对表_*.xlsx')
        print(f'   请先运行: python pipeline/export.py --client {client_name}')
        sys.exit(1)

    excel_path = candidates[0]

    from excel_reader import read_excel_changes

    modified = read_excel_changes(str(excel_path), data)
    print(f'\n📝 合并结果: {modified} 处修改')

    # 保存 JSON
    with open(session_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f'💾 JSON 已更新: {session_path}')

    # 重新生成 HTML（带去重时间戳）
    from html_renderer import generate_html
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    html_path = out_dir / f'{safe_name}合作策略_报告_{ts}.html'
    generate_html(data, str(html_path))
    print(f'✅ HTML 已更新: {html_path.absolute()}')

    print_stats(data)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    client = None
    readback = False
    stats_only = False
    excel_only = False
    html_only = False
    force = False
    output_dir = None

    for i, arg in enumerate(sys.argv[1:]):
        if arg == '--client' and i + 1 < len(sys.argv) - 1:
            client = sys.argv[i + 2]
        elif arg == '--output-dir' and i + 1 < len(sys.argv) - 1:
            output_dir = Path(sys.argv[i + 2])
        elif arg == '--readback':
            readback = True
        elif arg == '--stats':
            stats_only = True
        elif arg == '--excel-only':
            excel_only = True
        elif arg == '--html-only':
            html_only = True
        elif arg == '--force':
            force = True

    if not client:
        print('用法: python pipeline/export.py --client <企业名> [--output-dir <目录>] [--readback|--stats|--excel-only|--html-only|--force]')
        sys.exit(1)

    # Determine session folder
    session_folder = SESSIONS_DIR / client
    if not session_folder.exists():
        print(f'❌ 未找到客户数据: {session_folder}')
        print(f'   已采集的客户: {[d.name for d in SESSIONS_DIR.iterdir() if d.is_dir()] if SESSIONS_DIR.exists() else "无"}')
        sys.exit(1)

    if stats_only:
        session_path = session_folder / 'data.json'
        if session_path.exists():
            with open(session_path, 'r', encoding='utf-8') as f:
                print_stats(json.load(f))
        else:
            print(f'❌ 未找到 data.json')
    elif readback:
        if output_dir is None:
            print('📁 正在打开保存位置选择对话框...')
            output_dir = _pick_output_dir()
        if output_dir is not None:
            print(f'📁 已选择: {output_dir.absolute()}')
        cmd_readback(client, output_dir=output_dir)
    else:
        if output_dir is None:
            print('📁 报告即将生成（Excel 核对表 + HTML 报告），请选择保存位置...')
            print('   （直接点确定使用默认 output/ 目录）')
            output_dir = _pick_output_dir()
        if output_dir is not None:
            print(f'📁 已选择: {output_dir.absolute()}')
        cmd_export(client, excel_only=excel_only, html_only=html_only, force=force, output_dir=output_dir)
