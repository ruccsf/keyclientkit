"""
PDF 募集说明书数据提取器
========================
从债券募集说明书/年度报告 PDF 中提取合并资产负债表详细科目，
补充 QCC API 对 6 个资产负债表科目（短期借款/长期借款/应付债券/
一年内到期非流动负债/应付票据/应收票据）返回不稳定的缺口。

依赖：pypdfium2（已安装 5.7.1）

用法：
    from pdf_extractor import download_pdf, extract_balance_sheet, extract_financial_data

    # 下载 PDF
    pdf_path = download_pdf('https://static.sse.com.cn/.../xxx.pdf', 'sessions/首农/pdf/')

    # 提取资产负债表
    bs = extract_balance_sheet(pdf_path)
    # → {'短期借款': {'2022年末': '2,943,294.21', '2021年末': '2,747,111.47', ...}, ...}

    # 或提取完整财务数据（BS + 利润表关键项）
    fin = extract_financial_data(pdf_path)
    # → {'balance_sheet': {...}, 'income_statement': {...}}

数据优先级：PDF（完整审计） > QCC（不稳定） > Web Search（兜底）
"""

import re
import os
import sys
import json
import urllib.request
from pathlib import Path
from collections import OrderedDict
from typing import Optional


# ================================================================
# PDF 下载
# ================================================================

def download_pdf(url: str, output_dir: str = 'sessions/_pdf_cache') -> Optional[Path]:
    """
    下载 PDF 文件到本地缓存目录。

    Args:
        url: PDF 下载链接
        output_dir: 保存目录

    Returns:
        下载后的文件路径，失败返回 None
    """
    try:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        # 从 URL 提取文件名
        fname = url.rsplit('/', 1)[-1].split('?')[0]
        if not fname.endswith('.pdf'):
            fname = fname + '.pdf'
        dest = out / fname

        if dest.exists():
            return dest  # 已缓存

        urllib.request.urlretrieve(url, str(dest))
        size_kb = dest.stat().st_size // 1024
        print(f'📥 PDF 已下载: {dest.name} ({size_kb} KB)')
        return dest

    except Exception as e:
        print(f'❌ PDF 下载失败: {e}')
        return None


# ================================================================
# PDF 缓存管理
# ================================================================

# 缓存目录：智能体工作空间下（Path.cwd()），而非 skill 目录内
def _get_workspace() -> Path:
    """智能体工作空间 = 当前工作目录"""
    return Path.cwd()

_CACHE_DIR = _get_workspace() / 'sessions'

# 共享缓存（工作空间根目录下）
_WORKSPACE_CACHE = _get_workspace() / '_pdf_cache'


def find_cached_pdf(client_name: str) -> Optional[Path]:
    """
    检查是否有该企业的募集书 PDF 缓存。

    优先级：
    1. sessions/{client_name}/pdf/ 下的 PDF
    2. _pdf_cache/ 下的共享缓存
    """
    # 每个企业的独立缓存
    client_dir = _CACHE_DIR / client_name / 'pdf'
    if client_dir.exists():
        pdfs = sorted(client_dir.glob('*.pdf'))
        if pdfs:
            return pdfs[0]  # 返回最新的（按文件名排序）

    # 工作空间共享缓存
    if _WORKSPACE_CACHE.exists():
        # 搜索文件名含企业名的 PDF
        for pdf in sorted(_WORKSPACE_CACHE.glob('*.pdf'), reverse=True):
            if client_name[:4] in pdf.stem or any(c in pdf.stem for c in client_name[:4]):
                return pdf

    return None


def cache_pdf(src_path: str, client_name: str) -> Path:
    """
    将 PDF 复制到企业缓存目录，返回缓存路径。
    如果源文件已在缓存目录中，直接返回。
    """
    src = Path(src_path)
    if not src.exists():
        raise FileNotFoundError(f'PDF 文件不存在: {src_path}')

    dest_dir = _CACHE_DIR / client_name / 'pdf'
    dest_dir.mkdir(parents=True, exist_ok=True)

    # 如果源文件已在目标目录，直接返回
    if dest_dir in src.parents or str(src.parent) == str(dest_dir):
        return src

    dest = dest_dir / src.name
    if dest.exists():
        # 如果已存在同名文件，追加时间戳
        import time
        ts = time.strftime('%Y%m%d_%H%M%S')
        dest = dest_dir / f'{src.stem}_{ts}{src.suffix}'

    import shutil
    shutil.copy2(src, dest)
    print(f'📋 PDF 已缓存: {dest.relative_to(_CACHE_DIR.parent)}')
    return dest


def ask_pdf_path() -> Optional[str]:
    """
    弹出系统文件对话框，让用户选择 PDF 文件。
    非桌面环境（AI agent / 远程终端 / CI）跳过弹窗。
    """
    _no_gui = os.environ.get('WORKBUDDY_AGENT') or os.environ.get('CI') or os.environ.get('SSH_CLIENT')
    if _no_gui:
        return None

    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        chosen = filedialog.askopenfilename(
            title='选择募集说明书 PDF',
            filetypes=[('PDF 文件', '*.pdf'), ('所有文件', '*.*')],
        )
        root.destroy()
        if chosen:
            print(f'📁 已选择: {chosen}')
            return chosen
    except Exception:
        pass
    return None


# ================================================================
# PDF 文本提取
# ================================================================

def _extract_text_lines(pdf_path) -> list[str]:
    """用 pypdfium2 提取 PDF 全部文本，返回行列表。"""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_path))
    all_lines = []

    for page_num in range(len(pdf)):
        page = pdf[page_num]
        textpage = page.get_textpage()
        text = textpage.get_text_range()
        # PDF 换行可能是 \n 或 \r，统一处理
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        for line in text.split('\n'):
            line = line.strip()
            if line:
                all_lines.append(line)

    return all_lines


# ================================================================
# AI 阅读辅助：定位章节 + 提取页面文本
# ================================================================

# 各数据类型的章节关键词
SECTION_KEYWORDS = {
    'balance_sheet': ['合并资产负债表', '资产负债表'],
    'income_statement': ['合并利润表', '利润表'],
    'subsidiaries': ['主要子公司', '纳入合并报表范围', '二级子公司'],
    'bonds': ['存续债券', '已发行尚未兑付', '债务融资工具', '发行人本部存续'],
}


def find_section_pages(pdf_path) -> dict[str, list[int]]:
    """
    扫描 PDF，定位各类数据所在的页码范围。

    返回: {'balance_sheet': [14,15,16], 'subsidiaries': [286,287], 'bonds': [109,110], ...}
    找不到的类型不会出现在返回 dict 中。
    """
    lines = _extract_text_lines(pdf_path)
    result = {}

    for section_name, keywords in SECTION_KEYWORDS.items():
        pages = set()
        for kw in keywords:
            for i, line in enumerate(lines):
                if kw in line:
                    # 估算行号→页码（按每页 45 行估算）
                    page = i // 45 + 1
                    pages.add(page)
        if pages:
            # 聚类：将连续页码合并为范围
            sorted_pages = sorted(pages)
            clusters = []
            cluster_start = sorted_pages[0]
            cluster_end = sorted_pages[0]
            for p in sorted_pages[1:]:
                if p <= cluster_end + 2:  # 间隔不超过2页视为连续
                    cluster_end = p
                else:
                    clusters.append((cluster_start, cluster_end))
                    cluster_start = p
                    cluster_end = p
            clusters.append((cluster_start, cluster_end))
            # 取最大的那个 cluster（通常就是正文中的章节）
            best = max(clusters, key=lambda c: c[1] - c[0])
            result[section_name] = list(range(best[0], best[1] + 1))

    return result


def extract_pages_text(pdf_path, page_range: list[int]) -> str:
    """
    提取指定页面的纯文本，供 AI 阅读和解析。

    Args:
        pdf_path: PDF 文件路径
        page_range: 页码列表（1-based），如 [14, 15, 16]

    Returns:
        合并后的纯文本（页面间用分隔线隔开）
    """
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_path))
    total = len(pdf)
    chunks = []

    for pg in page_range:
        if pg < 1 or pg > total:
            continue
        page = pdf[pg - 1]
        textpage = page.get_textpage()
        text = textpage.get_text_range()
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        if text.strip():
            chunks.append(f'--- 第 {pg} 页 ---\n{text.strip()}')

    return '\n\n'.join(chunks)


# ================================================================
# 资产负债表提取（保留旧函数，标记为 deprecated）
# ================================================================

# 资产负债表中需要提取的关键科目
BS_TARGET_ITEMS = [
    # 资产类
    '货币资金', '应收票据', '应收账款', '预付款项', '其他应收款',
    '存货', '流动资产合计',
    '长期股权投资', '固定资产', '在建工程', '无形资产',
    '非流动资产合计', '资产总计',
    # 负债类
    '短期借款', '应付票据', '应付账款', '预收款项',
    '合同负债', '其他应付款',
    '一年内到期的非流动负债', '其他流动负债',
    '流动负债合计',
    '长期借款', '应付债券', '租赁负债', '长期应付款',
    '非流动负债合计', '负债合计',
    # 权益类
    '实收资本', '资本公积', '盈余公积', '未分配利润',
    '少数股东权益', '所有者权益合计',
    '负债及所有者权益总计', '负债和所有者权益总计',
]

# 跳过非科目行（section 标签等）
BS_SKIP_PATTERNS = [
    '募集说明书', '年度报告', '审计报告',
    '项目', '单位：', '流动资产：', '非流动资产：',
    '流动负债：', '非流动负债：', '所有者权益', '归属于母公司',
    '北京首农', '发行人', '财务报表',
    '金额 占比', '金额  占比',  # 双列表格子头
]


def _is_numeric_cell(val: str) -> bool:
    """判断字符串是否为数值（含千分位逗号和可选负号）。"""
    cleaned = val.replace(',', '').replace('-', '0').strip()
    return bool(re.match(r'^\d+(?:\.\d+)?$', cleaned))


def _is_table_header_line(line: str) -> bool:
    """判断是否为表格列头行（含多个年份列）。"""
    # 匹配 "2023年9月末" "2023 年 9 月末" "2022年末" 等格式
    years = re.findall(r'\d{4}\s*年(?:\s*\d{1,2}\s*[月未])?\s*[末]?', line)
    return len(years) >= 3


def _detect_year_columns(lines: list[str]) -> list[str]:
    """在 PDF 文本行中检测年份列头。"""
    for line in lines:
        if _is_table_header_line(line):
            parts = re.findall(r'\d{4}\s*年(?:\s*\d{1,2}\s*[月未])?\s*[末]?', line)
            if len(parts) >= 3:
                # 规范化：去掉空格
                return [p.replace(' ', '') for p in parts]
    return []


def _identify_full_year_columns(col_names: list[str]) -> list[int]:
    """
    识别哪些列是完整年度数据（年末），哪些是中期（月末/季末）。
    返回完整年度列的索引列表（按原始顺序）。

    规则：
    - "年末" 或以 "12月31日" 结尾 → 完整年度
    - "月末" 或其他月份 → 中期数据
    - 仅年份如 "2023年" → 视为年末
    """
    full_year_idx = []
    for i, col in enumerate(col_names):
        # "2022年末" or "2022年" → full year
        if '末' not in col or '年末' in col:
            full_year_idx.append(i)
        elif re.search(r'12\s*月\s*31', col):
            full_year_idx.append(i)
        # Skip interim: '月末', '6月末', '9月末', etc.
    return full_year_idx


def extract_balance_sheet(pdf_path) -> dict[str, dict[str, str]]:
    """
    从 PDF 中提取合并资产负债表。

    返回格式: {科目名: {年份列: 数值}}
    例如: {'短期借款': {'2022年末': '2,943,294.21', '2021年末': '2,747,111.47'}}

    提取策略：
    1. 扫描全部文本行，找到资产负债表区域（含"资产负债表"关键词）
    2. 检测年份列头
    3. 对每行解析：如果匹配"科目名 + N个数值"模式，则提取
    """
    lines = _extract_text_lines(pdf_path)

    # 步骤 1: 定位资产负债表区域
    bs_start = None
    bs_end = None

    # 策略 1: 搜索"合并资产负债表"章节标题（跳过会计政策注释和表头）
    for i, line in enumerate(lines):
        if bs_start is None and ('资产负债表' in line and '合并' in line):
            # 跳过会计政策注释/表头: "影响", "项目", "报表数" 等
            skip_words = ['影响', '执行', '会计政策', '报表数', '项目']
            if any(w in line for w in skip_words) or len(line) < 10:
                continue
            bs_start = i
        if bs_start is not None and bs_end is None:
            if '利润表' in line and '合并' in line and i > bs_start + 50:
                bs_end = i
                break

    # 策略 2: fallback — 搜索含年份列头+BS特征科目的表格区域
    # 优先级: 合并资产负债表 > 母公司资产负债表
    if bs_start is None:
        candidates = []
        for i, line in enumerate(lines):
            if _is_table_header_line(line) and i + 30 < len(lines):
                ahead = '\n'.join(lines[i:i+50])
                if '货币资金' in ahead and '资产总计' in ahead and '短期借款' in ahead:
                    # 确认是合并表（包含更多科目）还是母公司表
                    is_consolidated = ('应收票据' in ahead or '存货' in ahead or '无形资产' in ahead)
                    candidates.append((i, 0 if is_consolidated else 1))
        if candidates:
            candidates.sort(key=lambda x: x[1])  # 合并表优先
            bs_start = candidates[0][0] - 5

    if bs_start is None:
        print('⚠️  PDF 中未找到合并资产负债表')
        return {}

    # 确定搜索范围
    search_start = bs_start
    search_end = bs_end or min(bs_start + 3000, len(lines))

    # 步骤 2: 检测年份列头（只搜 bs_start 之后 30 行——紧接标题后的表格）
    col_names = _detect_year_columns(lines[bs_start:bs_start + 30])
    if not col_names:
        print('⚠️  未能识别年份列头')
        return {}

    num_cols = len(col_names)
    # 只取完整年度列
    full_year_idx = _identify_full_year_columns(col_names)
    if not full_year_idx:
        full_year_idx = list(range(num_cols))  # fallback: 全部视为年末

    target_cols = [col_names[i] for i in full_year_idx]

    # 检测是否双列模式（金额+占比），如：金额 占比 金额 占比 金额 占比
    dual_col = False
    for i in range(search_start + 1, min(search_start + 10, search_end)):
        sub_parts = lines[i].split()
        if len(sub_parts) >= 6 and all(p in ('金额', '占比') for p in sub_parts[:6]):
            dual_col = True
            break
    effective_cols = num_cols * 2 if dual_col else num_cols

    # 步骤 3: 逐行解析
    result = OrderedDict()
    for i in range(search_start, search_end):
        line = lines[i]

        # 跳过非数据行
        if any(pat in line for pat in BS_SKIP_PATTERNS):
            continue
        if _is_table_header_line(line):
            continue

        parts = line.split()
        if len(parts) < 3:
            continue

        # 最后 effective_cols 个部分应该是数值
        if len(parts) < effective_cols + 1:
            continue

        vals_candidate = parts[-effective_cols:]
        name_candidate = ' '.join(parts[:-effective_cols])

        # 验证：最后 effective_cols 个是否都是数值
        if not all(_is_numeric_cell(v) for v in vals_candidate):
            continue

        # 双列模式：只取 金额 列（每对第一个值），跳过 占比 列
        if dual_col:
            vals_candidate = vals_candidate[::2]  # 每两步取一步

        # 验证：科目名是否在目标列表中（或看起来像科目名）
        if not any(t in name_candidate for t in BS_TARGET_ITEMS):
            # 也允许其他看起来像会计科目的中文名
            if not re.match(r'^[一-鿿]+[一-鿿\s/（）\(\)]*$', name_candidate):
                continue
            if len(name_candidate) < 2:
                continue

        # 提取目标列的值
        row_data = {}
        for target_i, col_name in zip(full_year_idx, target_cols):
            if target_i < len(vals_candidate):
                row_data[col_name] = vals_candidate[target_i]

        if row_data:
            result[name_candidate] = row_data

    return result


# ================================================================
# 完整财务数据提取（BS + 关键 P&L 项）
# ================================================================

# 利润表关键科目
PL_TARGET_ITEMS = [
    '营业总收入', '营业收入', '营业成本', '税金及附加',
    '销售费用', '管理费用', '研发费用', '财务费用',
    '投资收益', '其他收益', '营业利润', '营业外收入', '营业外支出',
    '利润总额', '所得税费用', '净利润',
    '经营活动产生的现金流量净额',
    '投资活动产生的现金流量净额',
    '筹资活动产生的现金流量净额',
]


def extract_financial_data(pdf_path) -> dict:
    """
    从 PDF 中提取完整财务数据（BS + 利润表关键项）。

    返回: {'balance_sheet': {...}, 'income_statement': {...}, 'columns': [...]}
    """
    bs = extract_balance_sheet(pdf_path)

    # P&L 提取（简化版：搜索利润表关键词区域）
    pl = {}
    lines = _extract_text_lines(pdf_path)

    pl_start = None
    for i, line in enumerate(lines):
        if ('利润表' in line and '合并' in line) or ('利润表' in line and '公司' in line):
            pl_start = i
            break

    if pl_start:
        col_names = _detect_year_columns(lines[pl_start:pl_start + 500])
        if not col_names:
            col_names = _detect_year_columns(lines)  # fallback: global search

        num_cols = len(col_names) if col_names else 0
        if num_cols >= 3:
            for i in range(pl_start, min(pl_start + 500, len(lines))):
                line = lines[i]
                if any(pat in line for pat in ['募集说明书', '项目', '单位：', '利润表', '北京首农']):
                    continue
                parts = line.split()
                if len(parts) < num_cols + 1:
                    continue
                vals = parts[-num_cols:]
                name = ' '.join(parts[:-num_cols])
                if all(_is_numeric_cell(v) for v in vals):
                    if any(t in name for t in PL_TARGET_ITEMS):
                        pl[name] = dict(zip(col_names, vals))

    return {
        'balance_sheet': bs,
        'income_statement': pl,
        'bs_columns': _detect_year_columns(lines) if bs else [],
    }


# ================================================================
# 子公司提取
# ================================================================

def extract_subsidiaries(pdf_path) -> list[dict]:
    """
    从债券募集说明书 PDF 中提取二级子公司列表。

    募集书通常有"发行人权益投资情况 → 主要子公司情况"章节。

    返回: [{'子公司名称': '...', '层级': '二级子公司',
            '业务板块': '...', '持股比例': '...', '实收资本(万元)': '...',
            '备注': '...', '_status': 'green'}, ...]
    """
    lines = _extract_text_lines(pdf_path)

    # 定位"主要子公司情况"
    sec_start = None
    for i, line in enumerate(lines):
        if ('主要子公司情况' in line or ('纳入合并报表范围' in line and '二级子公司' in line)
                or ('发行人' in line and '子公司情况' in line)
                or ('主要子公司' in line and '情况' in line)):
            sec_start = i
            break
    if sec_start is None:
        return []

    search_end = min(sec_start + 300, len(lines))
    subsidiaries = []
    pending = None  # 跨行缓存: (name, location, biz, capital, ratio)

    for i in range(sec_start, search_end):
        line = lines[i].strip()
        if not line:
            continue

        # 序号行: "1 企业名 ..."
        m = re.match(r'^(\d{1,2})\s+(.+)', line)
        if m:
            # 先保存上一条 pending（如有）
            if pending:
                subsidiaries.append(pending)

            rest = m.group(2)
            parts = rest.split()

            # 从后往前解析数值列
            # 持股比例：≤100 无逗号；实收资本：>100 或带逗号
            # 部分表有"享有的表决权比例"作为第三列，跳过
            ratio = ''
            capital = ''
            for _ in range(3):  # 最多 3 个尾部数值（比例 + 资本 + 表决权比例）
                if not parts:
                    break
                val = parts[-1]
                val_clean = val.replace(',', '').replace('%', '')
                if not re.match(r'^[\d.]+$', val_clean):
                    break
                num = float(val_clean)
                if num <= 100 and ',' not in val and not ratio:
                    ratio = parts.pop()  # 第一个 ≤100 = 持股比例
                elif (',' in val or num > 100) and not capital:
                    capital = parts.pop()  # >100 或带逗号 = 实收资本
                elif num <= 100 and not ratio:
                    ratio = parts.pop()
                else:
                    parts.pop()  # 额外的 ≤100 列（表决权比例），跳过

            # 剩余: 企业名 + 经营地 + 业务性质
            locations = {'北京','上海','天津','香港','深圳','广州','河北','浙江','江苏','西藏'}
            name_parts = []
            location = ''
            biz_type = ''
            found_loc = False
            for p in parts:
                if not found_loc and p in locations:
                    location = p
                    found_loc = True
                elif not found_loc:
                    name_parts.append(p)
                else:
                    biz_type = (biz_type + p) if biz_type else p
            name = ' '.join(name_parts)

            pending = {
                '子公司名称': name,
                '层级': '二级子公司',
                '注册地': location,
                '国标行业': biz_type,
                '业务板块': biz_type,
                '持股比例': ratio,
                '实收资本(万元)': capital,
                '备注': '',
                '_status': 'green',
            }
            continue

        # 续行（无序号开头）
        if pending:
            clean = line.replace('：', ':')
            # 持股比例续行: "直接:35.34" / "间接:18.67"
            if clean.startswith('直接') or clean.startswith('间接'):
                pending['持股比例'] = (pending['持股比例'] + ' / ' + clean) if pending['持股比例'] else clean
            # 混合续行: "服务等 200,000.00 100.00" → 业务 + 资本 + 比例
            elif re.search(r'\d', clean):
                parts = clean.split()
                extracted_ratio = ''
                extracted_capital = ''
                # 从后往前，用数值特征区分：≤100 无逗号=比例，大额/带逗号=资本
                for _ in range(len(parts)):
                    if not parts:
                        break
                    val = parts[-1]
                    val_clean = val.replace(',', '').replace('%', '')
                    if not re.match(r'^[\d.]+$', val_clean):
                        break
                    num = float(val_clean)
                    if num <= 100 and ',' not in val and not extracted_capital and not extracted_ratio:
                        extracted_ratio = parts.pop()
                    elif (',' in val or num > 100) and not extracted_capital:
                        extracted_capital = parts.pop()
                    else:
                        break
                biz_rest = ' '.join(parts)
                if extracted_ratio and not pending['持股比例']:
                    pending['持股比例'] = extracted_ratio
                if extracted_capital and not pending['实收资本(万元)']:
                    pending['实收资本(万元)'] = extracted_capital
                if biz_rest:
                    pending['业务板块'] = pending['业务板块'] + biz_rest if pending['业务板块'] else biz_rest
            # 纯中文续行：业务性质
            elif re.match(r'^[一-鿿]+', clean):
                pending['业务板块'] = pending['业务板块'] + clean if pending['业务板块'] else clean
            continue

        # 结束信号
        if '发行人拥有被投资单位' in line:
            break

    # 保存最后一条
    if pending:
        subsidiaries.append(pending)

    # 质量过滤：移除无效行
    subsidiaries = [
        s for s in subsidiaries
        if not re.search(r'\d+\.?\d*%', s['子公司名称'])  # 不含百分比
        and len(s['子公司名称']) >= 4  # 名称至少4个字
        and '募集说明书' not in s.get('业务板块', '')  # 排除页眉混入
        and '序号' not in s.get('业务板块', '')  # 排除表头混入
        and '联营企业' not in s.get('业务板块', '')  # 排除章节标题混入
    ]

    # 表格式失败（<3条 OR 实收资本全空）→ 尝试段落式
    has_capital = any(s.get('实收资本(万元)', '').strip() for s in subsidiaries)
    if len(subsidiaries) < 3 or not has_capital:
        para_subs = _extract_subs_paragraph_format(pdf_path)
        if para_subs:
            return para_subs

    return subsidiaries


# ================================================================
# 债券明细提取
# ================================================================

def extract_bonds(pdf_path) -> Optional[list[dict]]:
    """
    从募集说明书 PDF 中提取发行人存续债券明细。

    搜索章节: "发行人本部存续债券" / "已发行尚未兑付" / "存续期债券"

    返回: [{'债券简称': '...', '发行主体': '...', '发行日期': '...',
            '到期日期': '...', '债券期限': '...', '发行规模(亿元)': '...',
            '票面利率(%)': '...', '余额(亿元)': '...', '_status': 'green'}, ...]
    找不到则返回 None（保留原骨架）。
    """
    lines = _extract_text_lines(pdf_path)

    # 定位债券章节
    sec_start = None
    for i, line in enumerate(lines):
        if any(kw in line for kw in ['发行人本部存续债券', '发行人存续债券',
                                       '已发行尚未兑付的债券', '已发行尚未到期的债券']):
            sec_start = i
            break
    if sec_start is None:
        return None

    search_end = min(sec_start + 150, len(lines))
    bonds = []
    pending_bond = None

    for i in range(sec_start, search_end):
        line = lines[i].strip()
        if not line:
            continue

        # 序号行
        m = re.match(r'^(\d{1,2})(?:\s+(.+))?$', line)
        if m:
            # 先保存上一条
            if pending_bond and pending_bond.get('债券简称'):
                bonds.append(pending_bond)

            rest = (m.group(2) or '').strip()
            if '小计' in rest or '合计' in rest:
                pending_bond = None
                continue

            # 债券简称：拼接序号后的非纯中文、非日期、非纯数字部分
            if rest:
                parts = rest.split()
                name_parts = []
                for p in parts:
                    if re.match(r'^\d{4}/\d{2}/\d{2}', p):
                        break
                    if re.match(r'^[\d.]+$', p) and len(p) > 3:  # 大数字=规模
                        break
                    name_parts.append(p)
                name = ''.join(name_parts)
            else:
                name = ''  # 序号独占一行，名称在续行

            pending_bond = {
                '债券简称': name,
                '发行主体': '',
                '发行日期': '',
                '到期日期': '',
                '债券期限': '',
                '发行规模(亿元)': '',
                '票面利率(%)': '',
                '余额(亿元)': '',
                '_status': 'green',
            }
            continue

        # 续行
        if pending_bond:
            clean = line.replace('：', ':')
            if '小计' in clean or '合计' in clean:
                continue

            # 累加债券简称（跨行拼接: "22 首农食品" + "MTN001" → "22首农食品MTN001"）
            parts = clean.split()
            name_parts = []
            for p in parts:
                if re.match(r'^\d{4}/\d{2}/\d{2}', p):  # 碰到日期 → 停止
                    break
                if re.match(r'^[\d.]+$', p) and len(p) > 3 and '.' in p:  # 发行规模等
                    break
                name_parts.append(p)
            if name_parts:
                new_name = ''.join(name_parts)
                if not pending_bond['债券简称']:
                    pending_bond['债券简称'] = new_name
                elif not re.search(r'[一-鿿]', new_name):  # 续行是字母数字
                    pending_bond['债券简称'] = pending_bond['债券简称'] + new_name
                continue

            parts = clean.split()
            # 发行主体续行（纯中文）
            if parts and re.match(r'^[一-鿿]+$', parts[0]) and not pending_bond['发行主体']:
                pending_bond['发行主体'] = clean
                continue

            # 数据行: 包含日期格式 YYYY/MM/DD 和数字
            has_date = bool(re.search(r'\d{4}/\d{2}/\d{2}', clean))
            nums = [p for p in parts if re.match(r'^[\d.]+$', p.replace(',', ''))]

            if has_date and len(nums) >= 3:
                # 提取日期
                dates = re.findall(r'\d{4}/\d{2}/\d{2}', clean)
                if len(dates) >= 2:
                    pending_bond['发行日期'] = dates[0]
                    pending_bond['到期日期'] = dates[-1] if len(dates) > 1 else ''
                elif dates:
                    pending_bond['发行日期'] = dates[0]

                # 剩下的数字: 发行规模, 票面利率, 余额（也可能是债券期限）
                # 格式: 日期1 日期2 期限 规模 利率 余额
                # 例如: 2022/11/02 - 2025/11/04 3+N 20.00 2.87 20.00
                non_date_parts = [p for p in parts if not re.match(r'\d{4}/\d{2}/\d{2}', p) and p != '-']
                # 债券期限: 如 "3+N", "3", "10"
                term_idx = 0
                if non_date_parts and re.match(r'^[\d]+[+]?[Nn]?$', non_date_parts[0]):
                    pending_bond['债券期限'] = non_date_parts[term_idx]
                    term_idx = 1
                # 剩下的数值: 规模, 利率, 余额（共3个）
                remaining_nums = [p for p in non_date_parts[term_idx:] if re.match(r'^[\d.]+$', p)]
                if len(remaining_nums) >= 3:
                    pending_bond['发行规模(亿元)'] = remaining_nums[0]
                    pending_bond['票面利率(%)'] = remaining_nums[1]
                    pending_bond['余额(亿元)'] = remaining_nums[2]
                elif len(remaining_nums) >= 2:
                    pending_bond['发行规模(亿元)'] = remaining_nums[0]
                    pending_bond['票面利率(%)'] = remaining_nums[1]
                continue

        # 结束信号
        if pending_bond and ('偿还' in line or '募集资金' in line):
            break

    if pending_bond:
        bonds.append(pending_bond)

    # 过滤：只保留有完整数据的行（至少要有发行日期和规模）
    bonds = [b for b in bonds if b['发行日期'] and b['发行规模(亿元)'] and re.search(r'[A-Za-z0-9]', b['债券简称'])]

    return bonds if bonds else None


# ================================================================
# 段落式子公司提取（fallback）
# ================================================================

def _extract_subs_paragraph_format(pdf_path) -> list[dict]:
    """段落式：'N、企业名' + 段落中含'持股比例为X%''注册资本为Y万元'"""
    lines = _extract_text_lines(pdf_path)

    # 定位段落式子公司章节（要求包含"子公司"+"情况"或"如下"）
    sec_start = None
    for i, line in enumerate(lines):
        if ('子公司' in line and ('基本情况' in line or '情况如下' in line or '明细如下' in line)):
            sec_start = i
            break
    # fallback: 任何包含"主要子公司"的行
    if sec_start is None:
        for i, line in enumerate(lines):
            if '主要子公司' in line:
                sec_start = i
                break
    if sec_start is None:
        return []

    # 找编号条目: "N、企业名（可选备注）"
    subsidiaries = []
    for i in range(sec_start, min(sec_start + 500, len(lines))):
        line = lines[i].strip()
        # 匹配: 数字 + 、/.) + 公司名
        m = re.match(r'^(\d{1,2})\s*[、．.)）]\s*(.+)$', line)
        if not m:
            continue

        seq = int(m.group(1))
        if seq > 80:  # 序号太大可能是其他内容
            continue

        name = m.group(2).strip()
        # 必须是法人实体，排除部门/个人
        if not any(kw in name for kw in ['有限公司', '股份公司', '有限责任公司',
                                           '集团', '工厂', '学校', '医院']):
            continue
        # 清理括号内的股票代码等
        name = re.sub(r'[（(][^）)]*股票代码[：:\s]*\d+[^）)]*[）)]', '', name)
        name = re.sub(r'[（(]\s*\d{6}\s*[）)]', '', name)
        name = name.strip().rstrip('，。,；;')
        if len(name) < 6:
            continue

        # 取后续文本直到下一条目或 section 边界
        tail_lines = []
        for j in range(i + 1, min(len(lines), i + 25)):
            l = lines[j].strip()
            # 下一条目（任何编号的公司条目）
            if re.match(r'^\d{1,2}\s*[、．.)）]\s*.{2,}', l) and '公司' in l:
                break
            # 章节边界
            if any(kw in l for kw in ['（四）', '（五）', '（六）', '四、', '五、', '六、']):
                break
            # 跳过页眉/页码
            if re.match(r'^\d{1,3}$', l) or '募集说明书' in l:
                continue
            tail_lines.append(l)
        tail = '\n'.join(tail_lines)

        # 提取持股比例
        ratio = ''
        ratio_m = re.search(r'持股比例[为约]?\s*(\d+\.?\d*)\s*%', tail)
        if ratio_m:
            ratio = ratio_m.group(1)
        else:
            ratio_m2 = re.search(r'[占持]股[份比].*?(\d+\.?\d*)\s*%', tail)
            if ratio_m2:
                ratio = ratio_m2.group(1)

        # 提取注册资本/实收资本
        capital = ''
        cap_m = re.search(r'(?:注册资本|实收资本)[为约]?\s*([\d,]+\.?\d*)\s*万元', tail)
        if cap_m:
            capital = cap_m.group(1)

        # 提取注册地
        location = ''
        loc_m = re.search(r'注册[地址地][：:]?\s*([^\s，。,\.；;]{2,10})', tail)
        if loc_m:
            location = loc_m.group(1).strip()

        # 业务描述：取第一个"经营范围/主要从事/主要业务为"后的内容
        biz = ''
        biz_m = re.search(r'(?:主要从事|主要业务[为是]|经营范围\s*[为包括：:]?)(.{8,60})', tail)
        if biz_m:
            biz = biz_m.group(1).strip()[:50]

        subsidiaries.append({
            '子公司名称': name,
            '层级': '二级子公司',
            '业务板块': biz,
            '持股比例': ratio,
            '实收资本(万元)': capital,
            '备注': f'注册地: {location}' if location else '',
            '_status': 'green',
        })

    return subsidiaries


# ================================================================
# 骨架映射
# ================================================================

def map_to_skeleton_columns(
    pdf_data: dict[str, dict[str, str]],
    skeleton_year_cols: list[str],
) -> dict[str, dict[str, str]]:
    """
    将 PDF 提取的财务数据映射到骨架的年份列。

    PDF 列名示例: '2022年末', '2021年末', '2020年末'
    骨架列名示例: '2023年', '2024年', '2025年'

    策略：取 PDF 中最新的 N 个完整年度列，按从旧到新顺序与骨架列一一对应。
    如果 PDF 年份少于骨架列数，用最接近的年份填充。
    """
    if not pdf_data or not skeleton_year_cols:
        return {}

    # 获取 PDF 列名
    first_item_vals = list(pdf_data.values())[0] if pdf_data else {}
    pdf_cols = list(first_item_vals.keys())

    # 提取 PDF 列中的年份数字，按年份排序
    pdf_col_years = []
    for col in pdf_cols:
        m = re.search(r'(\d{4})', col)
        if m:
            pdf_col_years.append((int(m.group(1)), col))
    pdf_col_years.sort(key=lambda x: x[0])  # 升序

    # 提取骨架列中的年份数字
    sk_years = []
    for col in skeleton_year_cols:
        m = re.search(r'(\d{4})', col)
        if m:
            sk_years.append(int(m.group(1)))

    # 1:1 映射：PDF 最新 N 个年份 → 骨架 N 个年份
    n_map = min(len(pdf_col_years), len(sk_years))
    pdf_subset = pdf_col_years[-n_map:]  # 最新的 N 个 PDF 年份
    sk_subset = sk_years[-n_map:]        # 骨架中对应的 N 个年份

    year_map = {}
    for (pdf_year, pdf_col), sk_year in zip(pdf_subset, sk_subset):
        year_map[sk_year] = pdf_col

    # 对于骨架中较早的年份（PDF 覆盖不到的），用 PDF 最旧的年份填充
    uncovered = sk_years[:-n_map] if n_map > 0 else sk_years
    oldest_pdf_col = pdf_col_years[0][1] if pdf_col_years else None
    for sk_year in uncovered:
        if oldest_pdf_col:
            year_map[sk_year] = oldest_pdf_col

    # 构建结果
    result = {}
    for item_name, values in pdf_data.items():
        row = {}
        for sk_col in skeleton_year_cols:
            m = re.search(r'(\d{4})', sk_col)
            if not m:
                continue
            sk_year = int(m.group(1))
            pdf_col = year_map.get(sk_year)
            if pdf_col and pdf_col in values:
                val = values[pdf_col]
                if val and val != '-':
                    row[sk_col] = val
        if any(v for v in row.values()):
            result[item_name] = row

    return result


# ================================================================
# 科目名标准化
# ================================================================

# PDF 科目名 → 骨架中的标准名
ITEM_NAME_ALIASES = {
    '一年内到期的非流动负债': '一年内到期非流动负债',
    '一年内到期非流动负债': '一年内到期非流动负债',
    '营业收入': '营业总收入',
    '一、营业总收入': '营业总收入',
    '其中：营业收入': '营业总收入',
    '其中：营业成本': '营业成本',
    '企业的投资收益': '投资收益',
    '加：其他收益': '其他收益',
    '加：营业外收入': '营业外收入',
    '减：营业外支出': '营业外支出',
    '减：所得税费用': '所得税费用',
    '取得投资收益收到的现金': '投资收益',
}


def normalize_item_name(pdf_name: str) -> str:
    """将 PDF 中的科目名标准化为骨架中的标准名。"""
    # 先去前缀（中文序号后必须跟标点）
    clean = re.sub(r'^[一二三四五六七八九十]+[、．.]\s*', '', pdf_name)
    clean = re.sub(r'^(其中|加|减)[：:]\s*', '', clean)
    clean = clean.strip()
    return ITEM_NAME_ALIASES.get(clean, clean)
