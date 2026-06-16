"""
报告导出 CLI — 统一入口
========================
用法:
  python pipeline/export.py --client 中粮集团              # 导出 Excel + HTML
  python pipeline/export.py --client 中粮集团 --readback    # 读回 Excel → 合并 JSON → 重生成 HTML
  python pipeline/export.py --client 中粮集团 --stats       # 查看统计
  python pipeline/export.py --client 中粮集团 --excel-only  # 仅 Excel
  python pipeline/export.py --client 中粮集团 --html-only   # 仅 HTML
"""

import sys, os, json
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent
PIPELINE_DIR = Path(__file__).parent
SESSIONS_DIR = SKILL_DIR / 'sessions'
OUTPUT_DIR = SKILL_DIR / 'output'

sys.path.insert(0, str(PIPELINE_DIR))


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


def cmd_export(client_name: str, excel_only=False, html_only=False) -> dict:
    """导出 Excel + HTML"""
    session_path = SESSIONS_DIR / client_name / 'data.json'
    if not session_path.exists():
        print(f'❌ 未找到客户数据: {session_path}')
        print(f'   请先运行 QCC 采集')
        sys.exit(1)

    with open(session_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    safe_name = data['meta'].get('client_name', client_name).replace('/', '_')[:30]

    if not html_only:
        from excel_renderer import generate_excel
        excel_path = OUTPUT_DIR / f'{safe_name}_核对表.xlsx'
        generate_excel(data, str(excel_path))
        size_kb = excel_path.stat().st_size // 1024
        print(f'✅ Excel: output/{excel_path.name} ({size_kb}KB)')

    if not excel_only:
        from html_renderer import generate_html
        html_path = OUTPUT_DIR / f'{safe_name}合作策略_报告.html'
        generate_html(data, str(html_path))
        size_kb = html_path.stat().st_size // 1024
        print(f'✅ HTML:  output/{html_path.name} ({size_kb}KB)')

    print_stats(data)
    return data


def cmd_readback(client_name: str):
    """读回 Excel 修改 → 合并 JSON → 重生成 HTML"""
    session_path = SESSIONS_DIR / client_name / 'data.json'
    if not session_path.exists():
        print(f'❌ 未找到客户数据: {session_path}')
        sys.exit(1)

    with open(session_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    safe_name = data['meta'].get('client_name', client_name).replace('/', '_')[:30]
    excel_path = OUTPUT_DIR / f'{safe_name}_核对表.xlsx'

    if not excel_path.exists():
        print(f'❌ 未找到 Excel: {excel_path}')
        print(f'   请先运行: python pipeline/export.py --client {client_name}')
        sys.exit(1)

    from excel_reader import read_excel_changes

    modified = read_excel_changes(str(excel_path), data)
    print(f'\n📝 合并结果: {modified} 处修改')

    # 保存 JSON
    with open(session_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f'💾 JSON 已更新: {session_path}')

    # 重新生成 HTML
    from html_renderer import generate_html
    html_path = OUTPUT_DIR / f'{safe_name}合作策略_报告.html'
    generate_html(data, str(html_path))
    print(f'✅ HTML 已更新: output/{html_path.name}')

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

    for i, arg in enumerate(sys.argv[1:]):
        if arg == '--client' and i + 1 < len(sys.argv) - 1:
            client = sys.argv[i + 2]
        elif arg == '--readback':
            readback = True
        elif arg == '--stats':
            stats_only = True
        elif arg == '--excel-only':
            excel_only = True
        elif arg == '--html-only':
            html_only = True

    if not client:
        print('用法: python pipeline/export.py --client <企业名> [--readback|--stats|--excel-only|--html-only]')
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
        cmd_readback(client)
    else:
        cmd_export(client, excel_only=excel_only, html_only=html_only)
