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
    """弹出系统对话框让用户选择保存位置。仅交互式环境弹窗，否则回退默认 output/。"""
    # 非交互式环境（AI agent / 管道）直接回退
    if not sys.stdin.isatty():
        return OUTPUT_DIR

    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        chosen = filedialog.askdirectory(
            title='选择报告保存位置',
            initialdir=str(OUTPUT_DIR),
        )
        root.destroy()
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


def _find_content_column(row: dict) -> str:
    """找到行的主内容列（排除键列、来源列、元数据列）"""
    key_cols = {'信息项', '分析维度', '指标', '财务指标', '动向类别', '年份',
                '债券类型', '需求类型', '维度', '产品类别', '风险类别', '准入方式',
                '合作维度', '未覆盖业务领域', '短板类别', '目标维度', '拓展方向',
                '序号', '接触类型', '角色', '银行', '职务', '子公司名称', '产品/服务',
                '联动/创新类型', '对标维度', '准入方式', '年份', '准入方式'}
    source_cols = {'备注/来源', '数据来源', '备注', '来源'}
    candidates = ['内容', '核心内容', '具体内容', '付息负债总额(万元)',
                  '发行金额(万元)', '余额(万元)', '方案描述', '风险点描述',
                  '需求描述', '集团业务规模', '内容(含趋势对比)',
                  '具体产品', '方案描述', '任务描述', '目标值']
    for c in candidates:
        if c in row:
            return c
    for c in row:
        if not c.startswith('_') and c not in key_cols and c not in source_cols:
            return c
    return None


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
