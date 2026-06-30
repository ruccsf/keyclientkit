"""
统一 HTML 报告渲染器
====================
- 从标准化 JSON 生成 HTML 报告
- 所有客户走同一渲染路径，输出结构完全一致
- 修复了旧 json_to_html.py 的 4 个已知 bug:
  1. _status 为 None/空时不回退默认值
  2. 封面字段显示 raw emoji + 占位符
  3. 首列 emoji 双重显示
  4. 版本日志硬编码
"""

import json, os, re
from datetime import datetime
from pathlib import Path

# ============================================================
# CSS 样式（复用旧版验证过的视觉风格）
# ============================================================
CSS = """
<style>
  body { font-family: "Microsoft YaHei","PingFang SC","Helvetica Neue",Arial,sans-serif; max-width:900px; margin:0 auto; padding:30px 20px; background:#fff; color:#222; line-height:1.7; font-size:14px; }
  h1 { text-align:center; font-size:26px; margin-bottom:5px; color:#1a1a2e; border-bottom:3px solid #c0392b; padding-bottom:12px; }
  h2 { font-size:20px; color:#c0392b; border-left:5px solid #c0392b; padding:8px 15px; margin:35px 0 18px; background:linear-gradient(to right,#fef0f0,#fff); }
  h3 { font-size:16px; color:#2c3e50; margin:22px 0 12px; padding-left:10px; border-left:3px solid #3498db; }
  h4 { font-size:15px; color:#34495e; margin:18px 0 10px; }
  p { margin:8px 0; }
  table { width:100%; border-collapse:collapse; margin:14px 0; font-size:13px; box-shadow:0 1px 4px rgba(0,0,0,0.08); }
  thead th { background:#c0392b; color:#fff; padding:10px 12px; text-align:left; font-weight:600; white-space:nowrap; }
  tbody td { padding:9px 12px; border-bottom:1px solid #eee; vertical-align:top; }
  tbody td:first-child { white-space:nowrap; font-weight:500; min-width:120px; }
  tbody tr:hover { background:#fff8f0; }
  blockquote { border-left:4px solid #e67e22; margin:12px 0; padding:10px 18px; background:#fdf6ec; color:#555; }
  a { color:#2980b9; text-decoration:none; }
  a:hover { text-decoration:underline; }
  .cover-box { border:2px solid #c0392b; border-radius:10px; padding:35px; margin:20px auto; max-width:600px; text-align:center; background:linear-gradient(135deg,#fff5f5,#fff); }
  .cover-title { font-size:28px; font-weight:bold; color:#c0392b; margin-bottom:20px; }
  .cover-field { display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px dashed #ddd; font-size:14px; }
  .cover-label { color:#666; }
  .cover-value { font-weight:bold; color:#222; }
  .legend { display:flex; gap:20px; justify-content:center; margin:15px 0 25px; flex-wrap:wrap; }
  .legend-item { display:flex; align-items:center; gap:5px; font-size:13px; }
  .summary-bar { display:flex; gap:15px; justify-content:center; margin:20px 0; flex-wrap:wrap; }
  .summary-card { border-radius:8px; padding:15px 25px; text-align:center; min-width:100px; }
  .summary-card.green { background:#e8f8f5; border:2px solid #27ae60; }
  .summary-card.yellow { background:#fef9e7; border:2px solid #f39c12; }
  .summary-card.red { background:#fdedec; border:2px solid #e74c3c; }
  .summary-card .num { font-size:28px; font-weight:bold; }
  .summary-card .lbl { font-size:12px; color:#666; margin-top:4px; }
  .footer { text-align:center; color:#aaa; font-size:11px; margin-top:30px; padding-top:15px; border-top:1px solid #eee; }
</style>
"""

# ============================================================
# 工具函数
# ============================================================

STATUS_ICON = {'green': '🟢', 'yellow': '🟡', 'red': '🔴'}
STATUS_BG = {'green': '#e8f8f5', 'yellow': '#fef9e7', 'red': '#fdedec'}


def esc(s):
    """HTML 转义"""
    if s is None:
        return ''
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def format_cell(val: str) -> str:
    """格式化单元格内容：URL 变可点击链接"""
    s = esc(val)
    # 将 https?://... 转为可点击链接
    s = re.sub(
        r'(https?://[^\s<>\[\]]+)',
        r'<a href="\1" target="_blank" style="font-size:11px">[↗]</a>',
        s
    )
    return s


def _celan_cover_value(val):
    """清理封面字段值：去掉 emoji 和占位符 [bug fix #2]"""
    if not val:
        return ''
    s = str(val)
    for emoji in ['🟢', '🟡', '🔴']:
        s = s.replace(emoji, '')
    s = re.sub(r'[［\[].*?[］\]]', '', s)
    s = s.replace('待银行内部填写', '')
    return s.strip()


def _strip_emoji(text):
    """从文本中剥离 emoji [bug fix #3]"""
    if not text:
        return text
    s = str(text)
    for emoji in ['🟢', '🟡', '🔴']:
        s = s.replace(emoji, '')
    return s.strip()


def _is_numeric(val: str) -> bool:
    """判断单元格值是否为金额/数字格式（用于右对齐）"""
    if not val or not val.strip():
        return False
    return bool(re.match(r'^-?[\d,]+(?:\.\d+)?%?$', val.strip()))


def _get_cover_val(cover, *keys):
    """尝试多个 key 获取封面值"""
    for k in keys:
        v = cover.get(k)
        if v:
            return v.get('value', '') if isinstance(v, dict) else str(v)
    return ''


# ============================================================
# 渲染模块
# ============================================================

def render_cover(data: dict) -> str:
    """渲染封面卡片"""
    meta = data.get('meta', {})
    cover = data.get('cover', {})

    client_name = meta.get('client_name', '') or _get_cover_val(cover, '集团名称', '客户名称', '企业全称')

    # bug fix #2: 清理封面值中的 emoji 和占位符
    level = _celan_cover_value(_get_cover_val(cover, '客户等级'))
    manager = _celan_cover_value(_get_cover_val(cover, '主责客户经理', '客户经理'))
    date_raw = _celan_cover_value(meta.get('generated_at', '') or _get_cover_val(cover, '编制日期', '报告日期'))
    report_date = date_raw[:10] if date_raw else datetime.now().strftime('%Y-%m-%d')

    # bug fix #4: 从 meta 动态读取版本号
    version_raw = meta.get('report_version', '') or _get_cover_val(cover, '版本号')
    ver_match = re.match(r'(V?\d+\.\d+)', _celan_cover_value(version_raw))
    version = ver_match.group(1) if ver_match else (_celan_cover_value(version_raw) or 'V2.0')

    return f'''<div class="cover-box">
  <div class="cover-title">{esc(client_name)} — 集团客户合作策略报告</div>
  <div class="cover-field"><span class="cover-label">客户等级：</span><span class="cover-value">{esc(level) or '—'}</span></div>
  <div class="cover-field"><span class="cover-label">客户经理：</span><span class="cover-value">{esc(manager) or '—'}</span></div>
  <div class="cover-field"><span class="cover-label">报告日期：</span><span class="cover-value">{esc(report_date)}</span></div>
  <div class="cover-field"><span class="cover-label">版本号：</span><span class="cover-value">{esc(version)}</span></div>
</div>'''


def render_legend() -> str:
    """渲染红绿灯图例"""
    return '''<div class="legend">
  <div class="legend-item"><span>🟢</span> 数据已确认（公开数据/官方披露）</div>
  <div class="legend-item"><span>🟡</span> 推断/部分数据（基于已知信息推断）</div>
  <div class="legend-item"><span>🔴</span> 待人工填写（内部数据/无法获取）</div>
</div>'''


def render_summary(data: dict) -> str:
    """渲染数据概览统计卡片"""
    green = yellow = red = 0
    for ch_val in data.get('chapters', {}).values():
        if not isinstance(ch_val, dict):
            continue
        for sec_val in ch_val.values():
            if not isinstance(sec_val, dict):
                continue
            for tbl in sec_val.get('tables', []):
                for row in tbl.get('data', []):
                    st = row.get('_status') or 'red'  # bug fix #1
                    if st == 'green':
                        green += 1
                    elif st == 'yellow':
                        yellow += 1
                    else:
                        red += 1
    total = green + yellow + red
    if total == 0:
        return ''
    gp = round(green / total * 100)
    yp = round(yellow / total * 100)
    rp = round(red / total * 100)
    return f'''<div class="summary-bar">
  <div class="summary-card green"><div class="num">{green}<span style="font-size:14px">（{gp}%）</span></div><div class="lbl">🟢 公开数据</div></div>
  <div class="summary-card yellow"><div class="num">{yellow}<span style="font-size:14px">（{yp}%）</span></div><div class="lbl">🟡 AI 推断</div></div>
  <div class="summary-card red"><div class="num">{red}<span style="font-size:14px">（{rp}%）</span></div><div class="lbl">🔴 待人工填写</div></div>
</div>'''


def render_table(tbl: dict, table_index: int) -> str:
    """渲染单张表格"""
    rows = tbl.get('data', [])
    if not rows:
        return ''

    # 表头：排除内部字段，但 _source_url 作为"数据来源"列显示
    all_keys = list(rows[0].keys())
    display_headers = [k for k in all_keys if not k.startswith('_') or k == '_source_url']

    # 检查是否有任何行含 _source_url
    has_source = any(row.get('_source_url') for row in rows)

    html = '\n<table>\n<thead><tr>'
    for h in display_headers:
        if h == '_source_url' and has_source:
            html += '<th>数据来源</th>'
        elif h != '_source_url':
            html += f'<th>{esc(h)}</th>'
    html += '</tr></thead>\n<tbody>'

    for row in rows:
        status = row.get('_status') or 'red'
        if status not in STATUS_ICON:
            status = 'red'
        icon = STATUS_ICON[status]
        bg = STATUS_BG.get(status, '#fdedec')

        html += f'<tr style="background:{bg}">'
        first = True
        for h in display_headers:
            if h == '_source_url':
                if has_source:
                    url = row.get('_source_url', '')
                    if url:
                        # 数据来源列：显示简写+可点击链接
                        if url.startswith('http'):
                            domain = re.sub(r'^https?://(?:www\.)?([^/]+).*', r'\1', url)
                            href = url
                        else:
                            # 本地文件路径 → 只显示文件名，加 file:/// 前缀让浏览器可打开
                            domain = url.replace('\\', '/').rsplit('/', 1)[-1]
                            # 转为绝对路径 + file:// 协议
                            from pathlib import Path
                            abs_path = str(Path(url).resolve())
                            href = 'file:///' + abs_path.replace('\\', '/')
                        html += f'<td style="font-size:11px"><a href="{esc(href)}" target="_blank" title="{esc(url)}">{esc(domain)}</a></td>'
                    else:
                        html += '<td></td>'
                continue
            val = str(row.get(h, '')) if row.get(h) is not None else ''
            if first:
                clean_val = _strip_emoji(val)
                html += f'<td><strong>{icon} {format_cell(clean_val)}</strong></td>'
                first = False
            else:
                align = 'text-align:right;' if _is_numeric(val) else ''
                html += f'<td style="{align}">{format_cell(val)}</td>'
        html += '</tr>\n'

    html += '</tbody></table>\n'
    return html


def render_section(chapter_data: dict, chapter_num: int, chapter_name: str = '') -> str:
    """渲染一个章节的所有子节"""
    html = ''
    CN_NUMS_SEC = ['（一）', '（二）', '（三）', '（四）', '（五）', '（六）']

    for sec_idx, (sec_title, sec_val) in enumerate(chapter_data.items()):
        if not isinstance(sec_val, dict):
            continue

        # 子节标题（跳过 _default，且当 section 名与 chapter 名重复时跳过避免双重标题）
        if sec_title != '_default' and sec_title != chapter_name:
            # 清理 section key 中自带的旧编号，如 "（1）集团主体资质" -> "集团主体资质"
            clean_sec = re.sub(r'^[（\(]?\d+[）\)]?[\.\s、]*', '', sec_title).strip()
            # 使用中文公文编号：（一）（二）（三）
            sec_label = CN_NUMS_SEC[sec_idx] if sec_idx < len(CN_NUMS_SEC) else f'（{sec_idx + 1}）'
            html += f'\n<h3>{sec_label} {esc(clean_sec)}</h3>\n'

        # 表格 — 每节独立编号，从 1 开始
        table_counter = 0
        for tbl in sec_val.get('tables', []):
            tbl_title = tbl.get('title', '')
            if tbl_title:
                table_counter += 1
                # 清理旧编号和 emoji
                clean_title = re.sub(r'^[（\(]?\d+[）\)\.]?\s*', '', tbl_title)
                clean_title = re.sub(r'^[一二三四五六七八九十]+[、，]\s*', '', clean_title)
                clean_title = _strip_emoji(clean_title)
                html += f'\n<h4>{table_counter}. {esc(clean_title)}</h4>\n'
            # 单位标注（财务表等）
            unit = tbl.get('_unit', '')
            if unit:
                html += f'<div style="font-size:12px;color:#888;margin:2px 0 6px 4px;">单位：{esc(unit)}</div>\n'
            html += render_table(tbl, table_counter)

        # 文本块
        for tb in sec_val.get('text_blocks', []):
            txt = tb.get('text', '')
            st = tb.get('status') or 'red'
            icon = STATUS_ICON.get(st, '🔴')
            html += f'\n<blockquote>{icon} {esc(txt)}</blockquote>\n'

    return html


def render_body(data: dict) -> str:
    """渲染报告主体（6 章）"""
    chapters = data.get('chapters', {})

    CN_NUMS = ['一', '二', '三', '四', '五', '六']
    CHAPTER_NAMES = {
        'chapter1': '客户核心画像',
        'chapter2': '银企合作情况',
        'chapter3': '金融需求挖掘',
        'chapter4': '服务方案设计',
        'chapter5': '行动计划与跟踪',
        'chapter6': '审核与签发',
    }

    html = ''
    for i, (ch_key, ch_name) in enumerate(CHAPTER_NAMES.items()):
        ch_data = chapters.get(ch_key, {})
        if not ch_data:
            continue
        cn_num = CN_NUMS[i] if i < len(CN_NUMS) else str(i + 1)
        html += f'\n<h2>{cn_num}、{ch_name}</h2>\n'
        html += render_section(ch_data, i + 1, chapter_name=ch_name)
        html += '\n<hr />\n'

    return html


def render_footer(data: dict) -> str:
    """渲染版本日志 + 数据说明 [bug fix #4: 从 meta 动态读取]"""
    meta = data.get('meta', {})
    gen_date = (meta.get('generated_at', '') or '')[:10] or datetime.now().strftime('%Y-%m-%d')
    version = meta.get('report_version', '') or 'V2.0'

    # 统计各状态数量
    green = yellow = red = 0
    for ch_val in data.get('chapters', {}).values():
        if not isinstance(ch_val, dict):
            continue
        for sec_val in ch_val.values():
            if not isinstance(sec_val, dict):
                continue
            for tbl in sec_val.get('tables', []):
                for row in tbl.get('data', []):
                    st = row.get('_status') or 'red'
                    if st == 'green':
                        green += 1
                    elif st == 'yellow':
                        yellow += 1
                    else:
                        red += 1

    return f'''<hr />
<blockquote><strong>数据统计</strong>：本版报告共 {green + yellow + red} 个数据行，其中 🟢 公开数据 {green} 行，🟡 AI 推断 {yellow} 行，🔴 待人工填写 {red} 行。</blockquote>
<div class="footer">由 AI 智能体自动生成 · 数据来源：企查查 API + 公开信息 · 生成时间：{esc(gen_date)}</div>'''


# ============================================================
# 主入口
# ============================================================

def generate_html(data: dict, output_path: str) -> str:
    """从标准化 JSON 生成完整 HTML 报告"""
    meta = data.get('meta', {})
    client_name = meta.get('client_name', '集团客户')

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(client_name)} — 集团客户合作策略报告</title>
{CSS}
</head>
<body>
{render_cover(data)}
{render_legend()}
{render_summary(data)}
{render_body(data)}
{render_footer(data)}
</body>
</html>'''

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)

    size_kb = os.path.getsize(output_path) // 1024
    return output_path
