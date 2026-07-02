"""
企查查数据采集编排器 V2
======================
输入企业名称 → 并行调用企查查 MCP → 按真实 API 格式映射 → 输出标准化 JSON

用法:
  python pipeline_v2/qcc_fetch.py --client 中粮集团
  python pipeline_v2/qcc_fetch.py --client 中粮集团 --tools registration,shareholders,finance

前置条件: mcporter 已配置 qcc-company 并完成授权
"""

import sys, os, json, argparse, re
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qcc_client import QccClient, QccAuthError, QccCallError

WORKSPACE = Path(__file__).parent.parent
OUTPUT_DATA_DIR = WORKSPACE / 'output_v2' / 'data'

# ============================================================
# 字段映射（基于真实 API 响应格式）
# ============================================================

def _s(val, default=''):
    """安全字符串转换"""
    if val is None:
        return default
    return str(val).strip()


def _yuan_to_wan(val):
    """元转万元（非数字返回空字符串，避免文字污染数值列）"""
    try:
        n = float(str(val).replace(',', '').replace('，', ''))
        return f'{n/10000:,.0f}'
    except (ValueError, TypeError):
        return ''


def map_registration_to_basic_info(reg: dict, company_name: str) -> list[dict]:
    """
    工商登记信息 → 基础信息表
    API 返回格式: 扁平 dict, 中文 key
    """
    source = '企查查 API: get_company_registration_info'
    rows = []

    # 股权结构先占位（后面 shareholders 补充）
    rows.append({
        '信息项': '股权结构及历史沿革',
        '内容': '', '备注/来源': '[待 shareholders 补充]', '_status': 'yellow',
    })

    field_map = [
        ('企业全称', _s(reg.get('企业名称', company_name))),
        ('统一社会信用代码', _s(reg.get('统一社会信用代码'))),
        ('法定代表人', _s(reg.get('法定代表人'))),
        ('注册资本', _s(reg.get('注册资本'))),
        ('实缴资本', _s(reg.get('实缴资本'))),
        ('成立日期', _s(reg.get('成立日期'))),
        ('企业类型', _s(reg.get('企业类型'))),
        ('登记状态', _s(reg.get('登记状态'))),
        ('注册地址', _s(reg.get('注册地址'))),
        ('经营范围', _s(reg.get('经营范围', ''))[:200]),
        ('国标行业', _s(reg.get('国标行业'))),
        ('英文名', _s(reg.get('英文名'))),
        ('人员规模', _s(reg.get('人员规模'))),
        ('参保人数', _s(reg.get('参保人数'))),
    ]

    for fname, fval in field_map:
        rows.append({
            '信息项': fname,
            '内容': fval if fval else '',
            '备注/来源': source if fval else '[需 AI web_search 补充]',
            '_status': 'green' if fval else 'yellow',
        })

    # 外部评级（企查查不返回）
    rows.insert(5, {'信息项': '外部评级', '内容': '', '备注/来源': '[需评级报告/企业年报获取]', '_status': 'yellow'})
    # 行内评级
    rows.insert(5, {'信息项': '行内评级', '内容': '', '备注/来源': '[内部系统]', '_status': 'red'})

    return rows


def map_shareholders_to_equity(shareholders: dict) -> tuple:
    """
    股东信息 → (股权结构描述, 是否为央企)
    API 返回格式: {"股东信息": [{股东名称, 持股比例, 认缴出资额, ...}]}
    """
    items = shareholders.get('股东信息', [])
    if not items:
        return '', False

    parts = []
    is_central = False
    for item in items[:5]:
        name = _s(item.get('股东名称', ''))
        ratio = _s(item.get('持股比例', ''))
        amount = _s(item.get('认缴出资额', ''))
        parts.append(f'{name}{ratio}（认缴{amount}万元）' if amount else f'{name}{ratio}')
        if '国务院' in name or '国资委' in name:
            is_central = True

    return '; '.join(parts), is_central


def map_personnel_to_executives(personnel: dict) -> list[dict]:
    """
    高管信息 → 高管信息表
    API 返回格式: {"主要人员信息": [{姓名, 职务, 持股比例}]}
    """
    items = personnel.get('主要人员信息', [])
    rows = []
    for item in items[:8]:
        name = _s(item.get('姓名', ''))
        position = _s(item.get('职务', ''))
        if name:
            rows.append({
                '职务': position,
                '姓名': name,
                '备注': f'企查查 API: get_key_personnel（共{len(items)}人）',
                '_status': 'green',
            })
    return rows


def map_financial_to_table(finance: dict) -> list[dict]:
    """
    财务数据 → 财务情况表（近三年）
    API 返回格式: {"财务数据信息": [{报告期, 指标详情: {主要财务指标, 财务报表: {利润表, 资产负债表, 现金流量表}, 分析数据}}]}
    """
    records = finance.get('财务数据信息', [])
    if not records:
        return []

    source = '企查查 API: get_financial_data'
    # 构建 指标 → {报告期: 值} 的映射
    indicators = {}  # {指标名: {报告期: 值}}

    for rec in records[:3]:  # 最多3年
        period = _s(rec.get('报告期', '')).replace('年报', '')
        detail = rec.get('指标详情', {})

        # 利润表
        pl = detail.get('财务报表', {}).get('利润表', {})
        for k, v in pl.items():
            indicators.setdefault(k, {})[period] = _yuan_to_wan(v)

        # 资产负债表
        bs = detail.get('财务报表', {}).get('资产负债表', {})
        for k in ['资产合计', '负债合计', '所有者权益总计']:
            v = bs.get(k, '')
            indicators.setdefault(k, {})[period] = _yuan_to_wan(v)

        # 现金流量表
        cf = detail.get('财务报表', {}).get('现金流量表', {})
        for k, v in cf.items():
            indicators.setdefault(k, {})[period] = _yuan_to_wan(v)

        # 分析数据
        analysis = detail.get('分析数据', {})
        for cat in ['盈利能力', '偿还能力']:
            for k, v in analysis.get(cat, {}).items():
                indicators.setdefault(k, {})[period] = _s(v) + ('%' if not _s(v).endswith('%') and _s(v).replace('.','').isdigit() else '')

    # 构建表格行
    all_periods = sorted(set(p for v in indicators.values() for p in v.keys()), reverse=True)

    rows = []
    # 优先显示的指标
    priority_keys = ['营业总收入', '利润总额', '净利润', '资产合计', '负债合计', '所有者权益总计',
                     '经营活动产生的现金流', '资产负债率', '净利率', '毛利率', '流动比率', '速动比率']

    for key in priority_keys:
        if key in indicators:
            vals = []
            for p in all_periods[:3]:
                vals.append(f'{p}: {indicators[key].get(p, "—")}')
            rows.append({
                '指标': key,
                '近三年数据': ' | '.join(vals),
                '数据来源': source,
                '_status': 'green',
            })
            del indicators[key]

    # 其余指标
    for key, periods in indicators.items():
        vals = [f'{p}: {periods.get(p, "—")}' for p in all_periods[:3]]
        rows.append({
            '指标': key,
            '近三年数据': ' | '.join(vals),
            '数据来源': source,
            '_status': 'green',
        })

    return rows


def map_investments_to_subsidiaries(investments: dict) -> list[dict]:
    """
    对外投资 → 子公司列表
    API 返回格式: 可能包含 data/list/对外投资信息 等 key
    """
    items = []
    for key in ['对外投资信息', 'data', 'list', 'records', 'items']:
        candidates = investments.get(key, [])
        if isinstance(candidates, list) and candidates:
            items = candidates
            break
    if not items and isinstance(investments, list):
        items = investments

    rows = []
    for item in items[:20]:
        if isinstance(item, dict):
            name = _s(item.get('被投资企业名称', item.get('企业名称', item.get('name', ''))))
            ratio = _s(item.get('持股比例', item.get('ratio', '')))
            status = _s(item.get('状态', item.get('status', '')))
            rows.append({
                '公司名称': name,
                '与集团关系': '子公司' if '100' in ratio else '参股公司',
                '持股比例': ratio,
                '经营状态': status,
                '_status': 'green',
            })

    if not rows:
        rows.append({'公司名称': '(无对外投资记录或数据未返回)', '与集团关系': '', '持股比例': '', '经营状态': '', '_status': 'yellow'})

    return rows


def map_listing_to_desc(listing: dict) -> str:
    """上市信息 → 描述文本"""
    if not listing:
        return ''

    # 检测 QCC "未发现记录" 或无上市数据 → 返回空字符串
    search_result = listing.get('搜索结果', '')
    if '未发现' in str(search_result):
        return ''
    # 也检查其他常见空结果字段
    for nil_key in ['未匹配项', '无匹配项']:
        if nil_key in listing:
            return ''

    # 可能的格式: {"上市信息": [...]} 或直接包含上市字段
    items = listing.get('上市信息', listing.get('data', []))
    if not items and isinstance(listing, dict):
        # 检查是否直接有股票代码等字段
        if listing.get('股票代码') or listing.get('stockCode'):
            items = [listing]

    if not isinstance(items, list):
        return ''

    parts = []
    for item in items[:5]:
        if isinstance(item, dict):
            code = _s(item.get('股票代码', item.get('stockCode', '')))
            name = _s(item.get('股票简称', item.get('stockName', '')))
            exchange = _s(item.get('上市交易所', item.get('exchange', '')))
            if code:
                parts.append(f'{code} {name}（{exchange}）')

    return '; '.join(parts) if parts else ''


# ============================================================
# V2 映射函数（匹配模板 V2.0 列结构）
# ============================================================

def map_registration_to_basic_info_v2(reg: dict, company_name: str) -> tuple:
    """
    工商登记信息 → (关键字段行, 补充信息行)
    关键字段: 对齐模板"基础信息表《★》"的 5 行
    补充信息: 12 个详细字段
    """
    source = '企查查 API: get_company_registration_info'

    # 关键字段（5行）
    key_rows = [
        {'信息项': '股权结构及历史沿革', '内容': '',   '备注/来源': '[QCC: shareholders]', '_status': 'green'},
        {'信息项': '注册地/注册资本',    '内容': f"{_s(reg.get('注册地址', ''))} / {_s(reg.get('注册资本', ''))}",
                                          '备注/来源': source, '_status': 'green'},
        {'信息项': '成立年限',          '内容': '',   '备注/来源': source, '_status': 'green'},
        {'信息项': '行内评级',          '内容': '',   '备注/来源': '[内部系统]', '_status': 'red'},
        {'信息项': '外部评级',          '内容': '',   '备注/来源': '[Web Search / 评级报告] → 中国货币网-债券发行公告-债项/主体评级', '_status': 'yellow'},
    ]

    # 计算成立年限
    reg_date = _s(reg.get('成立日期', ''))
    if reg_date:
        try:
            from datetime import datetime
            year = int(reg_date[:4])
            this_year = datetime.now().year
            key_rows[2]['内容'] = f'{this_year - year}年 (自{year})'
        except Exception:
            key_rows[2]['内容'] = reg_date

    # 补充信息（12行）
    detail_fields = [
        ('企业全称',         _s(reg.get('企业名称', company_name))),
        ('统一社会信用代码', _s(reg.get('统一社会信用代码'))),
        ('法定代表人',       _s(reg.get('法定代表人'))),
        ('实缴资本',         _s(reg.get('实缴资本'))),
        ('企业类型',         _s(reg.get('企业类型'))),
        ('登记状态',         _s(reg.get('登记状态'))),
        ('注册地址',         _s(reg.get('注册地址'))),
        ('经营范围',         _s(reg.get('经营范围', ''))[:200]),
        ('国标行业',         _s(reg.get('国标行业'))),
        ('英文名',           _s(reg.get('英文名'))),
        ('人员规模',         _s(reg.get('人员规模'))),
        ('参保人数',         _s(reg.get('参保人数'))),
    ]
    detail_rows = []
    for fname, fval in detail_fields:
        detail_rows.append({
            '信息项': fname,
            '内容': fval if fval else '',
            '备注/来源': source if fval else '[需 QCC / Web Search 补充]',
            '_status': 'green' if fval else 'yellow',
        })

    return key_rows, detail_rows


def map_personnel_to_executives_v2(personnel: dict) -> list[dict]:
    """
    高管信息 V2 → 增加"联系方式"列（🔴行内数据）
    """
    items = personnel.get('主要人员信息', [])
    rows = []
    for item in items[:8]:
        name = _s(item.get('姓名', ''))
        position = _s(item.get('职务', ''))
        if name:
            rows.append({
                '职务': position,
                '姓名': name,
                '履历': '',
                '联系方式': '',
                '备注': f'企查查 API: get_key_personnel（共{len(items)}人）',
                '_status': 'green',
                '_column_status': {'职务': 'green', '姓名': 'green', '履历': 'yellow', '联系方式': 'red', '备注': 'green'},
            })
    return rows


def _extract_annual_report_bs(annual_reports: dict) -> dict:
    """
    从工商年报中提取资产负债表详细科目。
    返回 {指标名: {报告期: 值}} 格式，可直接 merge 到 indicators。

    QCC 实际响应结构:
      {'企业年报信息': [{'年报年度': '2025年度报告',
                      '企业资产状况信息': {'资产总额': ..., '负债总额': ..., ...}}]}
    注意：很多企业选择"不公示"财务数据，此时这些字段的值是"企业选择不公示"。
    """
    indicators = {}
    if not annual_reports or not isinstance(annual_reports, dict):
        return indicators

    # QCC 实际 key: 企业年报信息
    report_list = (
        annual_reports.get('企业年报信息', []) or
        annual_reports.get('年报列表', []) or
        annual_reports.get('annualReports', []) or
        []
    )
    if not isinstance(report_list, list) or not report_list:
        return indicators

    # 不公示标记
    HIDDEN_MARKERS = ('企业选择不公示', '不公示', '—', '-', '')

    for report in report_list:
        if not isinstance(report, dict):
            continue
        # 报告期: 年报年度 → 提取年份数字
        period = _s(
            report.get('年报年度', '') or
            report.get('报告年份', '') or
            report.get('year', '')
        )
        period = period.replace('年度报告', '').replace('年报', '').replace('年', '').strip()
        if not period:
            continue

        # 路径 1: 企业资产状况信息（QCC 实际结构）
        asset_info = report.get('企业资产状况信息', {})
        if isinstance(asset_info, dict):
            for k, v in asset_info.items():
                val = _s(v)
                if val and val not in HIDDEN_MARKERS:
                    indicators.setdefault(k, {})[period] = val

        # 路径 2: 资产负债表（备选）
        bs = report.get('资产负债表', {}) or report.get('balanceSheet', {})
        if isinstance(bs, dict):
            for k, v in bs.items():
                val = _s(v)
                if val and val not in HIDDEN_MARKERS:
                    indicators.setdefault(k, {})[period] = val

    return indicators


def map_financial_to_table_v2(finance: dict, annual_reports: dict = None) -> list[dict]:
    """
    财务数据 V2 → 模板列结构: 财务指标 | 前三年 | 近两年 | 上一年
    当前 API 数据最多覆盖 3 年，映射为逐年列。
    支持可选的工商年报数据补充资产负债表科目。
    """
    records = finance.get('财务数据信息', [])
    if not records:
        return []

    # 收集指标数据
    indicators = {}  # {指标名: {报告期: 值}}
    for rec in records[:3]:
        period = _s(rec.get('报告期', '')).replace('年报', '').rstrip('年')
        detail = rec.get('指标详情', {})

        pl = detail.get('财务报表', {}).get('利润表', {})
        for k, v in pl.items():
            indicators.setdefault(k, {})[period] = _yuan_to_wan(v)

        bs = detail.get('财务报表', {}).get('资产负债表', {})
        for k, v in bs.items():
            indicators.setdefault(k, {})[period] = _yuan_to_wan(v)

        cf = detail.get('财务报表', {}).get('现金流量表', {})
        for k, v in cf.items():
            indicators.setdefault(k, {})[period] = _yuan_to_wan(v)

        analysis = detail.get('分析数据', {})
        for cat in ['盈利能力', '偿还能力']:
            for k, v in analysis.get(cat, {}).items():
                val = _s(v)
                if val.replace('.', '').replace('-', '').isdigit() and not val.endswith('%'):
                    val += '%'
                indicators.setdefault(k, {})[period] = val

    # ---- 工商年报补充资产负债表科目 ----
    if annual_reports:
        ar_indicators = _extract_annual_report_bs(annual_reports)
        for k, periods in ar_indicators.items():
            if k not in indicators:  # 只补充 finance 中没有的字段
                indicators[k] = periods
            else:
                # 合并 period 数据（年报可能有更早年份）
                for p, v in periods.items():
                    if p not in indicators[k]:
                        indicators[k][p] = v

    all_periods = sorted(set(p for v in indicators.values() for p in v.keys()))  # 升序
    global_yrs = all_periods[-3:]  # 最近三年，从左到右为旧→新（2023年 / 2024年 / 2025年）

    # 列头标签：纯年份，从左到右升序
    col_labels = [f'{y}年' for y in global_yrs]
    col_1 = col_labels[0] if len(col_labels) > 0 else '早年'
    col_2 = col_labels[1] if len(col_labels) > 1 else '近年'
    col_3 = col_labels[2] if len(col_labels) > 2 else '今年'

    # 计算付息负债 = 短期借款 + 长期借款 + 应付债券 + 一年内到期非流动负债
    debt_keys = ['短期借款', '长期借款', '应付债券', '一年内到期非流动负债']
    for period in global_yrs:
        total = 0.0
        has_any = False
        for dk in debt_keys:
            val_str = indicators.get(dk, {}).get(period, '')
            if val_str:
                try:
                    total += float(str(val_str).replace(',', ''))
                    has_any = True
                except (ValueError, AttributeError):
                    pass
        if has_any:
            indicators.setdefault('付息负债', {})[period] = f'{total:,.2f}'

    priority_keys = [
        # 计息负债相关（最前面）
        '短期借款', '长期借款', '应付债券', '一年内到期非流动负债',
        '一年内到期的应付债券', '一年内到期的长期借款',  # 🔴 需行内拆分
        '付息负债',
        '应付票据', '应收票据',
        # 原有核心财务指标
        '营业总收入', '利润总额', '净利润', '资产合计', '负债合计',
        '所有者权益总计', '经营活动产生的现金流', '资产负债率',
        '净利率', '毛利率', '流动比率', '速动比率',
    ]

    # 需行内拆分的明细科目 → 标记为 🔴
    red_keys = {'一年内到期的应付债券', '一年内到期的长期借款'}

    rows = []
    for key in priority_keys:
        if key in indicators:
            periods = indicators[key]
            row = {
                '财务指标': key,
                col_1: periods.get(global_yrs[0], '') if len(global_yrs) > 0 else '',
                col_2: periods.get(global_yrs[1], '') if len(global_yrs) > 1 else '',
                col_3: periods.get(global_yrs[2], '') if len(global_yrs) > 2 else '',
                '_status': 'green',
            }
            rows.append(row)
            del indicators[key]
        else:
            # QCC 未返回 → 🟡 占位行（或 🔴 需行内填写）
            status = 'red' if key in red_keys else 'yellow'
            row = {
                '财务指标': key,
                col_1: '',
                col_2: '',
                col_3: '',
                '_status': status,
            }
            rows.append(row)

    for key, periods in indicators.items():
        row = {
            '财务指标': key,
            col_1: periods.get(global_yrs[0], '') if len(global_yrs) > 0 else '',
            col_2: periods.get(global_yrs[1], '') if len(global_yrs) > 1 else '',
            col_3: periods.get(global_yrs[2], '') if len(global_yrs) > 2 else '',
            '_status': 'green',
        }
        rows.append(row)

    return rows


def map_investments_to_subsidiaries_v2(investments: dict) -> list[dict]:
    """
    对外投资 V2 → 对齐模板列: 子公司名称🟢 | 层级🟢 | 业务板块🟡 | 持股比例🟢 | 备注🟡
    """
    items = []
    for key in ['对外投资信息', 'data', 'list', 'records', 'items']:
        candidates = investments.get(key, [])
        if isinstance(candidates, list) and candidates:
            items = candidates
            break
    if not items and isinstance(investments, list):
        items = investments

    rows = []
    for item in items:
        if isinstance(item, dict):
            name = _s(item.get('被投资企业名称', item.get('企业名称', item.get('name', ''))))
            ratio = _s(item.get('持股比例', item.get('ratio', '')))
            status = _s(item.get('状态', item.get('status', '')))

            if not name:
                continue

            # 推断层级
            level = '子公司' if ('100' in ratio or '全资' in _s(item.get('与集团关系', ''))) else '参股公司'

            rows.append({
                '子公司名称': name,
                '层级': level,
                '注册地': '',
                '国标行业': '',
                '业务板块': '',
                '持股比例': ratio,
                '实收资本(万元)': '',
                '备注': f'经营状态: {status}' if status else '',
                '_status': 'green',
                '_column_status': {'子公司名称': 'green', '层级': 'green', '注册地': 'yellow', '国标行业': 'yellow', '业务板块': 'yellow', '持股比例': 'green', '实收资本(万元)': 'yellow', '备注': 'yellow'},
            })

    if not rows:
        rows.append({
            '子公司名称': '(无对外投资记录或数据未返回)',
            '层级': '', '业务板块': '', '持股比例': '', '备注': '',
            '_status': 'yellow',
        })

    # 排序：子公司优先，同层级按持股比例降序
    def _sort_key(r):
        is_subsidiary = 0 if r.get('层级') == '子公司' else 1
        ratio_str = r.get('持股比例', '0').replace('%', '').strip()
        try:
            ratio_val = -float(ratio_str)
        except ValueError:
            ratio_val = 0
        return (is_subsidiary, ratio_val)
    rows.sort(key=_sort_key)

    return rows


# ============================================================
# 骨架 + 编排
# ============================================================

def build_skeleton(client_name: str) -> dict:
    """
    构建模板 V2.0 标准化 JSON 骨架
    ================================
    严格对齐《集团客户合作策略_模板.md》的章节结构和表格。

    标注规则:
      🟢 = QCC API 数据（自动填充）
      🟡 = Web Search 数据（公开但需交叉验证）
      🔴 = 行内系统数据（客户经理手工填）
    """
    now = datetime.now().strftime('%Y-%m-%d')
    return {
        'meta': {
            'client_name': client_name,
            'report_version': 'V2.0',
            'template_version': 'V2.0',
            'generated_at': now,
            'source': 'qcc+ai',
        },

        # ================================================================
        # 封面
        # ================================================================
        'cover': {
            '客户名称':    {'value': client_name, 'status': 'green'},
            '客户等级':    {'value': '', 'status': 'red'},
            '所属行业':    {'value': '', 'status': 'green'},  # QCC 注册信息中有国标行业
            '主责客户经理': {'value': '', 'status': 'red'},
            '编制日期':    {'value': now, 'status': 'green'},
            '版本号':      {'value': 'V2.0', 'status': 'green'},
        },

        # ================================================================
        # 6 章报告正文
        # ================================================================
        'chapters': {

            # ===== 第 1 章：客户核心画像 =====
            'chapter1': {

                '（1）集团主体资质与核心优势': {'tables': [
                    {
                        'title': '基础信息表《★》',
                        'type': 'kv',
                        'data': [
                            {'信息项': '股权结构及历史沿革', '内容': '', '备注/来源': '[QCC: shareholders]', '_status': 'green'},
                            {'信息项': '注册地/注册资本',    '内容': '', '备注/来源': '[QCC: registration]', '_status': 'green'},
                            {'信息项': '成立年限',          '内容': '', '备注/来源': '[QCC: registration]', '_status': 'green'},
                            {'信息项': '行内评级',          '内容': '', '备注/来源': '[内部系统]',          '_status': 'red'},
                            {'信息项': '外部评级',          '内容': '', '备注/来源': '[Web Search / 评级报告] → 中国货币网-债券发行公告-债项/主体评级', '_status': 'yellow'},
                        ],
                    },
                    {
                        'title': '补充信息',
                        'type': 'kv',
                        'data': [
                            {'信息项': '企业全称',          '内容': '', '备注/来源': '[QCC: registration]', '_status': 'green'},
                            {'信息项': '统一社会信用代码',  '内容': '', '备注/来源': '[QCC: registration]', '_status': 'green'},
                            {'信息项': '法定代表人',        '内容': '', '备注/来源': '[QCC: registration]', '_status': 'green'},
                            {'信息项': '实缴资本',          '内容': '', '备注/来源': '[QCC: registration]', '_status': 'green'},
                            {'信息项': '企业类型',          '内容': '', '备注/来源': '[QCC: registration]', '_status': 'green'},
                            {'信息项': '登记状态',          '内容': '', '备注/来源': '[QCC: registration]', '_status': 'green'},
                            {'信息项': '注册地址',          '内容': '', '备注/来源': '[QCC: registration]', '_status': 'green'},
                            {'信息项': '经营范围',          '内容': '', '备注/来源': '[QCC: registration]', '_status': 'green'},
                            {'信息项': '国标行业',          '内容': '', '备注/来源': '[QCC: registration]', '_status': 'green'},
                            {'信息项': '英文名',            '内容': '', '备注/来源': '[QCC: registration]', '_status': 'green'},
                            {'信息项': '人员规模',          '内容': '', '备注/来源': '[QCC: registration]', '_status': 'green'},
                            {'信息项': '参保人数',          '内容': '', '备注/来源': '[QCC: registration]', '_status': 'green'},
                        ],
                    },
                    {
                        'title': '高管信息',
                        'type': 'list',
                        'data': [
                            {'职务': '(待QCC数据)', '姓名': '', '履历': '', '联系方式': '', '备注': '数据采集后自动填充', '_status': 'yellow'},
                        ],
                        '_columns_brief': '职务🟢 | 姓名🟢 | 履历🟡 | 联系方式🔴 | 备注🟢',
                    },
                    {
                        'title': '经营情况《★》',
                        'type': 'kv',
                        'data': [
                            {'信息项': '营业收入(近三年)',         '内容': '', '备注/来源': '[QCC: finance]', '_status': 'green'},
                            {'信息项': '净利润(近三年)',           '内容': '', '备注/来源': '[QCC: finance]', '_status': 'green'},
                            {'信息项': '主营业务板块营收占比',     '内容': '', '备注/来源': '[Web Search / 年报]', '_status': 'yellow'},
                            {'信息项': '行业排名',                 '内容': '', '备注/来源': '[Web Search]', '_status': 'yellow'},
                            {'信息项': '市场份额(%)及测算依据',    '内容': '', '备注/来源': '[Web Search]', '_status': 'yellow'},
                            {'信息项': '专利/核心技术/知名品牌',   '内容': '', '备注/来源': '[QCC 知识产权 / Web Search]', '_status': 'yellow'},
                            {'信息项': '上市信息',                 '内容': '', '备注/来源': '[QCC: listing]', '_status': 'green'},
                            {'信息项': '实际控制人',               '内容': '', '备注/来源': '[QCC: controller]', '_status': 'green'},
                        ],
                    },
                    {
                        'title': '财务情况《★》',
                        'type': 'list',
                        'data': [
                            {'财务指标': '(待QCC财务数据)', '早年': '', '近年': '', '今年': '', '_status': 'yellow'},
                        ],
                        '_columns_brief': '财务指标🟢 (万元) | 早年 | 近年 | 今年',
                        '_unit': '万元',
                    },
                    {
                        'title': '上下游生态',
                        'type': 'kv',
                        'data': [
                            {'信息项': '集团产业链/供应链全景图', '内容': '', '备注/来源': '[Web Search]', '_status': 'yellow'},
                            {'信息项': '上游核心供应商',          '内容': '', '备注/来源': '[Web Search]', '_status': 'yellow'},
                            {'信息项': '下游核心客户',            '内容': '', '备注/来源': '[Web Search]', '_status': 'yellow'},
                        ],
                    },
                ]},

                '（2）发展前景': {'tables': [
                    {
                        'title': '行业分析',
                        'type': 'kv',
                        'data': [
                            {'分析维度': '重要行业政策',        '核心内容': '', '_status': 'yellow'},
                            {'分析维度': '行业特征与周期',      '核心内容': '', '_status': 'yellow'},
                            {'分析维度': '主要增长点与转型方向', '核心内容': '', '_status': 'yellow'},
                            {'分析维度': '竞争格局',            '核心内容': '', '_status': 'yellow'},
                            {'分析维度': '主要风险点',          '核心内容': '', '_status': 'yellow'},
                        ],
                    },
                    {
                        'title': '企业发展前景《★》',
                        'type': 'kv',
                        'data': [
                            {'信息项': '未来3-5年扩张/转型规划',               '内容': '', '备注/来源': '[Web Search / 年报]', '_status': 'yellow'},
                            {'信息项': '重点投资项目(附预算/时间表)',            '内容': '', '备注/来源': '[Web Search / QCC: investments]', '_status': 'yellow'},
                            {'信息项': '新增业务板块',                          '内容': '', '备注/来源': '[Web Search]', '_status': 'yellow'},
                            {'信息项': '预计营收年均增速(%)+依据',              '内容': '', '备注/来源': '[Web Search / 分析]', '_status': 'yellow'},
                        ],
                    },
                    {
                        'title': '近期发展动向',
                        'type': 'list',
                        'data': [
                            {'动向类别': '政策类',            '具体内容': '', '时间': '', '影响评估': '', '_status': 'yellow'},
                            {'动向类别': '业务经营动向',      '具体内容': '', '时间': '', '影响评估': '', '_status': 'yellow'},
                            {'动向类别': '资本运作与股权变动', '具体内容': '', '时间': '', '影响评估': '', '_status': 'yellow'},
                            {'动向类别': '风险/负面新闻',      '具体内容': '', '时间': '', '影响评估': '', '_status': 'yellow'},
                            {'动向类别': '人事与治理调整',     '具体内容': '', '时间': '', '影响评估': '', '_status': 'yellow'},
                            {'动向类别': '国际与地缘(如适用)', '具体内容': '', '时间': '', '影响评估': '', '_status': 'yellow'},
                        ],
                    },
                ]},
            },

            # ===== 第 2 章：银企合作情况 =====
            'chapter2': {

                '（1）总体资金管理模式': {'tables': [
                    {
                        'title': '在京企业架构 / 子公司列表',
                        'type': 'list',
                        'data': [
                            {'子公司名称': '(待QCC投资数据)', '层级': '', '注册地': '', '国标行业': '', '业务板块': '', '持股比例': '', '实收资本(万元)': '', '备注': '数据采集后自动填充', '_status': 'yellow'},
                        ],
                        '_columns_brief': '子公司名称🟢 | 层级🟢 | 注册地🟡 | 国标行业🟡 | 业务板块🟡 | 持股比例🟢 | 实收资本(万元)🟡 | 备注🟡',
                    },
                    {
                        'title': '客户合作准入方式',
                        'type': 'kv',
                        'data': [
                            {'准入方式': '司库系统接入',  '是否已开通': '', '覆盖成员单位': '', '备注': '', '_status': 'red'},
                            {'准入方式': '银企直联',      '是否已开通': '', '覆盖成员单位': '', '备注': '', '_status': 'red'},
                            {'准入方式': '白名单准入',    '是否已开通': '', '覆盖成员单位': '', '备注': '', '_status': 'red'},
                        ],
                    },
                    {
                        'title': '存款结算管理模式',
                        'type': 'kv',
                        'data': [
                            {'信息项': '存款结算管理模式', '内容': '', '备注/来源': '[内部系统]', '_status': 'red'},
                        ],
                    },
                    {
                        'title': '融资管理模式',
                        'type': 'kv',
                        'data': [
                            {'信息项': '融资管理模式(统贷统还/子公司自主融资)', '内容': '', '备注/来源': '[Web Search / 年报]', '_status': 'yellow'},
                        ],
                    },
                ]},

                '（2）债券及其他融资情况': {'tables': [
                    {
                        'title': '总体融资规模变动《★》',
                        'type': 'list',
                        'data': [
                            {'年份': '五年前', '付息负债总额(万元)': '', '同比变动(%)': '', '主要融资方式': '', '_status': 'yellow'},
                            {'年份': '四年前', '付息负债总额(万元)': '', '同比变动(%)': '', '主要融资方式': '', '_status': 'yellow'},
                            {'年份': '三年前', '付息负债总额(万元)': '', '同比变动(%)': '', '主要融资方式': '', '_status': 'yellow'},
                            {'年份': '两年前', '付息负债总额(万元)': '', '同比变动(%)': '', '主要融资方式': '', '_status': 'yellow'},
                            {'年份': '上一年', '付息负债总额(万元)': '', '同比变动(%)': '', '主要融资方式': '', '_status': 'yellow'},
                        ],
                    },
                    {
                        'title': '公司发行的债券、其他债务融资工具以及偿还情况',
                        'type': 'list',
                        'data': [
                            {'债券简称': '(待PDF或WebSearch)', '发行主体': '', '发行日期': '', '到期日期': '', '债券期限': '', '发行规模(亿元)': '', '票面利率(%)': '', '余额(亿元)': '', '_status': 'yellow'},
                        ],
                        '_columns_brief': '债券简称🟡 | 发行主体🟡 | 发行日期🟡 | 到期日期🟡 | 债券期限🟡 | 发行规模(亿元)🟡 | 票面利率(%)🟡 | 余额(亿元)🟡',
                    },
                    {
                        'title': '存续期债券明细',
                        'type': 'list',
                        'data': [
                            {'债券名称': '(待检索存续债券)', '余额(万元)': '', '利率(%)': '', '到期日': '', '资金用途': '', '_status': 'yellow'},
                        ],
                    },
                ]},

                '（3）与我行合作情况': {'tables': [
                    {
                        'title': '集团整体情况',
                        'type': 'list',
                        'data': [
                            {'银行': '我行',   '对公结算账户数': '', '专户类型及数量': '', '我行排名': '', '_status': 'red'},
                            {'银行': '竞争行1', '对公结算账户数': '', '专户类型及数量': '', '我行排名': '', '_status': 'red'},
                            {'银行': '竞争行2', '对公结算账户数': '', '专户类型及数量': '', '我行排名': '', '_status': 'red'},
                        ],
                    },
                    {
                        'title': '集团整体授信与业务分布',
                        'type': 'list',
                        'data': [
                            {'序号': '1', '分支行': '', '客户名称': '', '客户层级': '', '授信额度': '', '流贷': '', '项目融资': '', '债券': '', '贸融-国内证等': '', '贸融-供应链': '', '保函': '', '银承': '', '贷款余额': '', '存款余额': '', '集中度': '', '_status': 'red'},
                            {'序号': '',  '分支行': '', '客户名称': '', '客户层级': '', '授信额度': '', '流贷': '', '项目融资': '', '债券': '', '贸融-国内证等': '', '贸融-供应链': '', '保函': '', '银承': '', '贷款余额': '', '存款余额': '', '集中度': '', '_status': 'red'},
                            {'序号': '',  '分支行': '', '客户名称': '', '客户层级': '', '授信额度': '', '流贷': '', '项目融资': '', '债券': '', '贸融-国内证等': '', '贸融-供应链': '', '保函': '', '银承': '', '贷款余额': '', '存款余额': '', '集中度': '', '_status': 'red'},
                            {'序号': '合计', '分支行': '', '客户名称': '', '客户层级': '', '授信额度': '', '流贷': '', '项目融资': '', '债券': '', '贸融-国内证等': '', '贸融-供应链': '', '保函': '', '银承': '', '贷款余额': '', '存款余额': '', '集中度': '', '_status': 'red'},
                        ],
                    },
                    {
                        'title': '信贷业务',
                        'type': 'list',
                        'data': [
                            {'序号': '1', '品种': '', '贷款余额': '', '利率水平(%)': '', '期限': '', '到期日': '', '担保方式': '', '_status': 'red'},
                            {'序号': '2', '品种': '', '贷款余额': '', '利率水平(%)': '', '期限': '', '到期日': '', '担保方式': '', '_status': 'red'},
                        ],
                    },
                    {
                        'title': '存款业务',
                        'type': 'list',
                        'data': [
                            {'序号': '1', '品种': '', '存款金额': '', '利率水平(%)': '', '期限': '', '到期日': '', '_status': 'red'},
                            {'序号': '2', '品种': '', '存款金额': '', '利率水平(%)': '', '期限': '', '到期日': '', '_status': 'red'},
                        ],
                    },
                    {
                        'title': '其他业务',
                        'type': 'list',
                        'data': [
                            {'产品/服务': '银企直连',     '合作状态': '', '规模': '', '_status': 'red'},
                            {'产品/服务': '结算',         '合作状态': '', '规模': '', '_status': 'red'},
                            {'产品/服务': '对公理财',     '合作状态': '', '规模': '', '_status': 'red'},
                            {'产品/服务': '债券主承销',   '合作状态': '', '规模': '', '_status': 'red'},
                            {'产品/服务': '系统建设',     '合作状态': '', '规模': '', '_status': 'red'},
                            {'产品/服务': '第三代社保卡', '合作状态': '', '规模': '', '_status': 'red'},
                            {'产品/服务': '其他',         '合作状态': '', '规模': '', '_status': 'red'},
                        ],
                    },
                    {
                        'title': '标杆合作案例',
                        'type': 'kv',
                        'data': [
                            {'信息项': '标杆合作案例', '内容': '', '备注/来源': '[内部系统]', '_status': 'red'},
                        ],
                    },
                ]},
                '（3）同业对比': {'tables': [
                    {
                        'title': '存款及结算',
                        'type': 'list',
                        'data': [
                            {'银行': '我行',     '账户类型': '', '数量': '', '存款': '', '结算量': '', '_status': 'red'},
                            {'银行': '竞争行1',  '账户类型': '', '数量': '', '存款': '', '结算量': '', '_status': 'red'},
                            {'银行': '竞争行2',  '账户类型': '', '数量': '', '存款': '', '结算量': '', '_status': 'red'},
                        ],
                    },
                    {
                        'title': '贷款',
                        'type': 'list',
                        'data': [
                            {'银行': '我行',       '品种': '', '金额': '', '期限': '', '利率水平(%)': '', '起始日期': '', '到期日': '', '_status': 'red'},
                            {'银行': '同业1',      '品种': '', '金额': '', '期限': '', '利率水平(%)': '', '起始日期': '', '到期日': '', '_status': 'red'},
                            {'银行': '同业2',      '品种': '', '金额': '', '期限': '', '利率水平(%)': '', '起始日期': '', '到期日': '', '_status': 'red'},
                            {'银行': '同业3',      '品种': '', '金额': '', '期限': '', '利率水平(%)': '', '起始日期': '', '到期日': '', '_status': 'red'},
                            {'银行': '全行业平均',  '品种': '', '金额': '', '期限': '', '利率水平(%)': '', '起始日期': '', '到期日': '', '_status': 'red'},
                        ],
                    },
                    {
                        'title': '其他业务',
                        'type': 'list',
                        'data': [
                            {'产品/服务': '银企直连',     '我行': '', '同业1': '', '同业2': '', '同业3': '', '_status': 'red'},
                            {'产品/服务': '对公理财',     '我行': '', '同业1': '', '同业2': '', '同业3': '', '_status': 'red'},
                            {'产品/服务': '债券主承销',   '我行': '', '同业1': '', '同业2': '', '同业3': '', '_status': 'red'},
                            {'产品/服务': '系统建设',     '我行': '', '同业1': '', '同业2': '', '同业3': '', '_status': 'red'},
                            {'产品/服务': '第三代社保卡', '我行': '', '同业1': '', '同业2': '', '同业3': '', '_status': 'red'},
                            {'产品/服务': '其他',         '我行': '', '同业1': '', '同业2': '', '同业3': '', '_status': 'red'},
                        ],
                    },
                    {
                        'title': '特色产品合作',
                        'type': 'kv',
                        'data': [
                            {'信息项': '标杆合作案例', '内容': '', '备注/来源': '[内部系统]', '_status': 'red'},
                        ],
                    },
                ]},
            },

            # ===== 第 3 章：金融需求挖掘 =====
            'chapter3': {
                '金融需求挖掘': {'tables': [
                    {
                        'title': '业务覆盖缺口《★》',
                        'type': 'list',
                        'data': [
                            {'未覆盖业务领域': '跨境金融',       '集团业务规模': '', '他行覆盖情况': '', '我行切入可行性': '', '_status': 'yellow'},
                            {'未覆盖业务领域': '财富管理',       '集团业务规模': '', '他行覆盖情况': '', '我行切入可行性': '', '_status': 'yellow'},
                            {'未覆盖业务领域': '投行/债券承销',  '集团业务规模': '', '他行覆盖情况': '', '我行切入可行性': '', '_status': 'yellow'},
                            {'未覆盖业务领域': '其他',           '集团业务规模': '', '他行覆盖情况': '', '我行切入可行性': '', '_status': 'yellow'},
                        ],
                    },
                    {
                        'title': '服务适配短板',
                        'type': 'list',
                        'data': [
                            {'短板类别': '融资利率',           '具体表现': '', '与需求差距': '', '改进建议': '', '_status': 'red'},
                            {'短板类别': '服务响应效率',       '具体表现': '', '与需求差距': '', '改进建议': '', '_status': 'red'},
                            {'短板类别': '数字化接入覆盖率',   '具体表现': '', '与需求差距': '', '改进建议': '', '_status': 'red'},
                            {'短板类别': '产品丰富度',         '具体表现': '', '与需求差距': '', '改进建议': '', '_status': 'red'},
                        ],
                    },
                    {
                        'title': '机会缺口事实《★》',
                        'type': 'list',
                        'data': [
                            {'需求类型': '新增项目融资',    '需求描述': '', '金额(万元)': '', '时间节点': '', '优先级': '', '_status': 'yellow'},
                            {'需求类型': '业务扩张融资',    '需求描述': '', '金额(万元)': '', '时间节点': '', '优先级': '', '_status': 'yellow'},
                            {'需求类型': '续贷需求',        '需求描述': '', '金额(万元)': '', '时间节点': '', '优先级': '', '_status': 'yellow'},
                            {'需求类型': '上下游配套金融',  '需求描述': '', '金额(万元)': '', '时间节点': '', '优先级': '', '_status': 'yellow'},
                        ],
                    },
                    {
                        'title': '补充信息',
                        'type': 'kv',
                        'data': [
                            {'信息项': '未与我行合作的在京子公司数量及规模', '内容': '', '备注/来源': '[QCC: investments + 内部]', '_status': 'yellow'},
                            {'信息项': '上下游未合作客户数量及规模',          '内容': '', '备注/来源': '[Web Search]', '_status': 'yellow'},
                        ],
                    },
                ]},
            },

            # ===== 第 4 章：服务方案设计 =====
            'chapter4': {
                '服务方案设计': {'tables': [
                    {
                        'title': '政策导向与分层定位《★》',
                        'type': 'kv',
                        'data': [
                            {'维度': '投融资政策指引',      '内容': '', '_status': 'yellow'},
                            {'维度': '分层分类定位',        '内容': '', '_status': 'yellow'},
                            {'维度': '适用贴息/优惠政策',   '内容': '', '_status': 'yellow'},
                            {'维度': '战略客户专属权益',    '内容': '', '_status': 'red'},
                        ],
                    },
                    {
                        'title': '合作份额目标《★》',
                        'type': 'list',
                        'data': [
                            {'目标维度': '贷款份额占比(%)',      '当前值': '', '目标值': '', '时间节点': '', '提升路径': '', '_status': 'red'},
                            {'目标维度': '整体授信额度(万元)',   '当前值': '', '目标值': '', '时间节点': '', '提升路径': '', '_status': 'red'},
                            {'目标维度': '日均存款目标(万元)',   '当前值': '', '目标值': '', '时间节点': '', '提升路径': '', '_status': 'red'},
                            {'目标维度': '中收目标(万元)',       '当前值': '', '目标值': '', '时间节点': '', '提升路径': '', '_status': 'red'},
                        ],
                    },
                    {
                        'title': '拓展计划',
                        'type': 'list',
                        'data': [
                            {'拓展方向': '未覆盖业务板块',            '具体内容': '', '责任人': '', '计划时间': '', '_status': 'red'},
                            {'拓展方向': '下属未合作在京子公司',      '具体内容': '', '责任人': '', '计划时间': '', '_status': 'red'},
                            {'拓展方向': '产业链上下游客户',          '具体内容': '', '责任人': '', '计划时间': '', '_status': 'red'},
                        ],
                    },
                    {
                        'title': '适用产品矩阵《★》',
                        'type': 'list',
                        'data': [
                            {'产品类别': '存款',        '具体产品': '', '适用主体': '', '规模(万元)': '', '优先级': '', '_status': 'yellow'},
                            {'产品类别': '贷款',        '具体产品': '', '适用主体': '', '规模(万元)': '', '优先级': '', '_status': 'yellow'},
                            {'产品类别': '债券',        '具体产品': '', '适用主体': '', '规模(万元)': '', '优先级': '', '_status': 'yellow'},
                            {'产品类别': '结算',        '具体产品': '', '适用主体': '', '规模(万元)': '', '优先级': '', '_status': 'yellow'},
                            {'产品类别': '票据',        '具体产品': '', '适用主体': '', '规模(万元)': '', '优先级': '', '_status': 'yellow'},
                            {'产品类别': '汇兑/跨境',   '具体产品': '', '适用主体': '', '规模(万元)': '', '优先级': '', '_status': 'yellow'},
                        ],
                    },
                    {
                        'title': '公私联动与创新服务',
                        'type': 'kv',
                        'data': [
                            {'联动/创新类型': '公私联动(员工金融)',   '方案描述': '', '预期效益': '', '_status': 'red'},
                            {'联动/创新类型': '供应链金融',          '方案描述': '', '预期效益': '', '_status': 'red'},
                            {'联动/创新类型': '综合财富管理',        '方案描述': '', '预期效益': '', '_status': 'red'},
                            {'联动/创新类型': '数字化/科技合作',     '方案描述': '', '预期效益': '', '_status': 'red'},
                            {'联动/创新类型': 'ESG/绿色金融',        '方案描述': '', '预期效益': '', '_status': 'red'},
                        ],
                    },
                    {
                        'title': '风险防控',
                        'type': 'list',
                        'data': [
                            {'风险类别': '信用风险',      '风险点描述': '', '等级': '', '应对措施': '', '_status': 'yellow'},
                            {'风险类别': '流动性风险',    '风险点描述': '', '等级': '', '应对措施': '', '_status': 'yellow'},
                            {'风险类别': '集中度风险',    '风险点描述': '', '等级': '', '应对措施': '', '_status': 'red'},
                            {'风险类别': '合规/法律风险',  '风险点描述': '', '等级': '', '应对措施': '', '_status': 'yellow'},
                            {'风险类别': '声誉/舆情风险', '风险点描述': '', '等级': '', '应对措施': '', '_status': 'yellow'},
                        ],
                    },
                ]},
            },

            # ===== 第 5 章：行动计划与跟踪 =====
            'chapter5': {
                '行动计划与跟踪': {'tables': [
                    {
                        'title': '年度重点任务清单',
                        'type': 'list',
                        'data': [
                            {'序号': '1', '任务描述': '', '责任部门/人': '', '完成时限': '', '进展状态': '', '_status': 'red'},
                            {'序号': '2', '任务描述': '', '责任部门/人': '', '完成时限': '', '进展状态': '', '_status': 'red'},
                            {'序号': '3', '任务描述': '', '责任部门/人': '', '完成时限': '', '进展状态': '', '_status': 'red'},
                            {'序号': '4', '任务描述': '', '责任部门/人': '', '完成时限': '', '进展状态': '', '_status': 'red'},
                            {'序号': '5', '任务描述': '', '责任部门/人': '', '完成时限': '', '进展状态': '', '_status': 'red'},
                        ],
                    },
                    {
                        'title': '关键接触计划',
                        'type': 'list',
                        'data': [
                            {'接触类型': '高层拜访',      '目标对象': '', '计划时间': '', '预期目标': '', '跟进结果': '', '_status': 'red'},
                            {'接触类型': '业务推介会',    '目标对象': '', '计划时间': '', '预期目标': '', '跟进结果': '', '_status': 'red'},
                            {'接触类型': '合同/协议续签', '目标对象': '', '计划时间': '', '预期目标': '', '跟进结果': '', '_status': 'red'},
                            {'接触类型': '联席工作会',    '目标对象': '', '计划时间': '', '预期目标': '', '跟进结果': '', '_status': 'red'},
                        ],
                    },
                    {
                        'title': '绩效目标季度追踪',
                        'type': 'list',
                        'data': [
                            {'指标': '贷款余额(万元)',        '目标值': '', 'Q1': '', 'Q2': '', 'Q3': '', 'Q4': '', '_status': 'red'},
                            {'指标': '日均存款(万元)',        '目标值': '', 'Q1': '', 'Q2': '', 'Q3': '', 'Q4': '', '_status': 'red'},
                            {'指标': '中收(万元)',            '目标值': '', 'Q1': '', 'Q2': '', 'Q3': '', 'Q4': '', '_status': 'red'},
                            {'指标': '新增合作子公司(家)',    '目标值': '', 'Q1': '', 'Q2': '', 'Q3': '', 'Q4': '', '_status': 'red'},
                        ],
                    },
                ]},
            },

            # ===== 第 6 章：审核与签发 =====
            'chapter6': {
                '审核与签发': {'tables': [
                    {
                        'title': '审核签发表',
                        'type': 'list',
                        'data': [
                            {'角色': '编制人(客户经理)',   '姓名': '', '签字': '', '日期': '', '_status': 'red'},
                            {'角色': '复核人(部门主管)',   '姓名': '', '签字': '', '日期': '', '_status': 'red'},
                            {'角色': '审批人(部门负责人)', '姓名': '', '签字': '', '日期': '', '_status': 'red'},
                        ],
                    },
                    {
                        'title': '归档信息',
                        'type': 'kv',
                        'data': [
                            {'信息项': '归档编号', '内容': '', '备注/来源': '[内部系统]', '_status': 'red'},
                        ],
                    },
                ]},
            },
        },
    }


def resolve_company_name(keyword: str, qcc: QccClient) -> str:
    """
    模糊搜索 → 精确全称。
    如果用户给的是简称（如"中粮集团"），先用 get_company_by_query 找到全称。
    """
    try:
        result = qcc.get_company_by_query(keyword)
        match_type = result.get('匹配结果', '')

        # 精确匹配：直接返回
        if match_type == '精确匹配' or match_type == '唯一匹配':
            items = result.get('企业信息', result.get('data', []))
            if items and isinstance(items, list) and len(items) > 0:
                name = items[0].get('企业名称', keyword)
                print(f'  🔍 名称解析: "{keyword}" → "{name}"（精确匹配）')
                return name

        # 多候选：取第一个（得分最高的）
        if match_type == '多候选':
            items = result.get('企业信息', result.get('data', []))
            if items and isinstance(items, list) and len(items) > 0:
                name = items[0].get('企业名称', keyword)
                print(f'  🔍 名称解析: "{keyword}" → "{name}"（从{len(items)}个候选中选第一个）')
                return name

        # 自动锁定（没有匹配结果字段）
        name = result.get('企业名称', result.get('锁定名称', ''))
        if name and name != keyword:
            print(f'  🔍 名称解析: "{keyword}" → "{name}"')
            return name
    except Exception as e:
        print(f'  ⚠️  名称解析异常: {e}')

    print(f'  ⚠️  无法解析名称，直接用: "{keyword}"')
    return keyword


# ============================================================
# 扩展采集：知识产权/风险/经营/高管
# ============================================================

def collect_extended_qcc_data(company_name: str, personnel: list = None) -> dict:
    """
    调用企查查扩展 MCP 服务器（ipr/risk/operation/executive），
    获取知识产权、风险、经营动态、高管处罚等补充数据。
    """
    from qcc_client import QccIprClient, QccRiskClient, QccOperationClient, QccExecutiveClient

    results = {}
    errors = []

    # IPR：知识产权
    try:
        ipr = QccIprClient()
        results['ipr'] = ipr.get_all_ipr(company_name)
    except Exception as e:
        results['ipr'] = {'error': str(e)}
        errors.append(('ipr', str(e)))

    # Risk：风险信息
    try:
        risk = QccRiskClient()
        results['risk'] = risk.get_all_risks(company_name)
    except Exception as e:
        results['risk'] = {'error': str(e)}
        errors.append(('risk', str(e)))

    # Operation：经营动态
    try:
        op = QccOperationClient()
        results['operation'] = op.get_all_operations(company_name)
    except Exception as e:
        results['operation'] = {'error': str(e)}
        errors.append(('operation', str(e)))

    # Executive：高管信息
    if personnel:
        try:
            exec_client = QccExecutiveClient()
            results['executive'] = exec_client.get_all_executive_info(company_name, personnel)
        except Exception as e:
            results['executive'] = {'error': str(e)}
            errors.append(('executive', str(e)))

    return results


def _extract_qcc_list(result: dict, possible_keys: list) -> list:
    """通用：从 QCC API 响应中提取列表数据"""
    if not isinstance(result, dict) or result.get('error'):
        return []
    # 精确匹配
    for key in possible_keys:
        val = result.get(key)
        if isinstance(val, list) and val:
            return val
    # 模糊匹配：找第一个列表类型的值
    for key, val in result.items():
        if isinstance(val, list) and val and key not in ('errors', 'warnings'):
            return val
    return []


def _qcc_has_data(result: dict) -> bool:
    """判断 QCC API 是否返回了有效数据（而非'未发现记录'）"""
    if not isinstance(result, dict):
        return False
    search_result = result.get('搜索结果', '')
    if isinstance(search_result, str) and ('未发现' in search_result or '未发现任何记录' in search_result):
        return False
    # 有非搜索结果的 key 说明有数据
    for key in result:
        if key not in ('企业名称', '搜索结果', 'error', '_raw'):
            return True
    return False


def fill_extended_qcc_data(data: dict, extended: dict, personnel: list = None):
    """
    将扩展 QCC 数据填入骨架，把 🟡 字段升级为 🟢。
    API 响应格式适配 QCC MCP 实际返回结构。
    """

    ch1_sec1 = data['chapters']['chapter1']['（1）集团主体资质与核心优势']
    ch1_sec2 = data['chapters']['chapter1']['（2）发展前景']
    ch4 = data['chapters']['chapter4']['服务方案设计']

    # --- Ch1(1) 经营情况：专利/核心技术 (qcc-ipr) ---
    ipr = extended.get('ipr', {})
    if isinstance(ipr, dict):
        ipr_parts = []

        # APP 信息
        app_info = ipr.get('app_info', {})
        if isinstance(app_info, dict) and _qcc_has_data(app_info):
            apps = _extract_qcc_list(app_info, ['APP信息', 'data', 'list', 'records'])
            if apps:
                names = [_s(a.get('APP名称', a.get('name', ''))) for a in apps[:5] if isinstance(a, dict)]
                if names:
                    ipr_parts.append(f'APP: {"; ".join([n for n in names if n])}')

        # 特许经营
        franchise = ipr.get('franchise', {})
        if isinstance(franchise, dict) and _qcc_has_data(franchise):
            fran = _extract_qcc_list(franchise, ['特许经营', '特许经营信息', 'data', 'list'])
            if fran:
                fran_names = [_s(f.get('特许人名称', f.get('name', ''))) for f in fran[:3] if isinstance(f, dict)]
                if fran_names:
                    ipr_parts.append(f'特许经营: {"; ".join([n for n in fran_names if n])}')

        if ipr_parts:
            desc = ' | '.join(ipr_parts)
            for r in ch1_sec1['tables'][3]['data']:
                if '专利' in r.get('信息项', ''):
                    r['内容'] = desc
                    r['备注/来源'] = '企查查 API: qcc-ipr'
                    r['_status'] = 'green'
        else:
            # QCC 检索了但未发现记录 — 留 🟡 让 WebSearch 补搜专利/商标/品牌
            for r in ch1_sec1['tables'][3]['data']:
                if '专利' in r.get('信息项', ''):
                    r['内容'] = '经企查查知识产权数据库检索，该主体暂无APP/特许经营备案记录（还需搜索专利、商标、知名品牌等公开信息）'
                    r['备注/来源'] = '企查查 API: qcc-ipr — 检索无记录'
                    r['_status'] = 'yellow'

    # --- Ch1(2) 近期发展动向：风险数据 (qcc-risk) ---
    risk = extended.get('risk', {})
    penalty_desc = ''
    bankruptcy_desc = ''

    if isinstance(risk, dict):
        # 行政处罚
        admin_penalty = risk.get('administrative_penalty', {})
        if isinstance(admin_penalty, dict) and _qcc_has_data(admin_penalty):
            items = _extract_qcc_list(admin_penalty,
                ['行政处罚', '行政处罚信息', 'data', 'list', 'records'])
            if items:
                parts = []
                for item in items[:3]:
                    if isinstance(item, dict):
                        reason = _s(item.get('处罚结果',
                            item.get('行政处罚决定书文号',
                            item.get('决定文书/许可证名称', ''))))
                        date = _s(item.get('处罚日期', item.get('有效期自', '')))
                        agency = _s(item.get('处罚机关', item.get('许可机关', '')))
                        parts.append(f'{reason} ({date}, {agency})')
                penalty_desc = '; '.join(parts)

        # 破产重整
        bankruptcy = risk.get('bankruptcy', {})
        if isinstance(bankruptcy, dict) and _qcc_has_data(bankruptcy):
            items = _extract_qcc_list(bankruptcy,
                ['破产重整', '破产重整信息', 'data', 'list'])
            if items:
                b_parts = []
                for item in items[:3]:
                    if isinstance(item, dict):
                        case = _s(item.get('案号', ''))
                        date = _s(item.get('公开日期', ''))
                        b_parts.append(f'{case} ({date})')
                bankruptcy_desc = '; '.join([p for p in b_parts if p])

    # 填入近期动向
    for r in ch1_sec2['tables'][2]['data']:
        cat = r.get('动向类别', '')
        if '风险' in cat:
            if penalty_desc:
                r['具体内容'] = f'[行政处罚] {penalty_desc}'[:500]
                r['备注/来源'] = '企查查 API: qcc-risk'
                r['_status'] = 'green'
            else:
                r['具体内容'] = '经企查查风险数据库检索，近三年无行政处罚记录（还需搜索公开负面新闻）'
                r['备注/来源'] = '企查查 API: qcc-risk — 检索无记录'
                r['_status'] = 'yellow'  # QCC 只查行政处罚，负面新闻需 WebSearch
        elif '资本' in cat:
            if bankruptcy_desc:
                r['具体内容'] = f'[破产重整] {bankruptcy_desc}'[:500]
                r['备注/来源'] = '企查查 API: qcc-risk'
                r['_status'] = 'green'
            else:
                r['具体内容'] = '经企查查破产重整数据库检索，无破产重整记录（还需搜索资本运作、股权变动等公开信息）'
                r['备注/来源'] = '企查查 API: qcc-risk — 检索无记录'
                r['_status'] = 'yellow'  # QCC 只查破产重整，资本运作/股权变动需 WebSearch

    # --- Ch1(2) 近期发展动向：行政许可 (qcc-operation) ---
    operation = extended.get('operation', {})
    if isinstance(operation, dict):
        admin_license = operation.get('administrative_license', {})
        if isinstance(admin_license, dict) and _qcc_has_data(admin_license):
            items = _extract_qcc_list(admin_license,
                ['行政许可信息', '行政许可', 'data', 'list', 'records'])
            if items:
                lic_parts = []
                for item in items[:5]:
                    if isinstance(item, dict):
                        lic_name = _s(item.get('决定文书/许可证名称',
                            item.get('许可内容', item.get('行政许可决定文书号', ''))))
                        lic_date = _s(item.get('有效期自',
                            item.get('许可决定日期', '')))
                        lic_parts.append(f'{lic_name} ({lic_date})')
                lic_desc = '; '.join([p for p in lic_parts if p])
                for r in ch1_sec2['tables'][2]['data']:
                    if '政策' in r.get('动向类别', '') and lic_desc:
                        r['具体内容'] = f'[行政许可] {lic_desc}'[:500]
                        r['备注/来源'] = '企查查 API: qcc-operation (get_administrative_license)'
                        r['_status'] = 'green'

    # --- Ch4 风险防控：合规/法律风险 ---
    for r in ch4['tables'][5]['data']:
        cat = r.get('风险类别', '')
        if '合规' in cat and penalty_desc:
            r['风险点描述'] = f'[行政处罚记录] {penalty_desc}'[:500]
            r['_status'] = 'green'
        elif '信用' in cat and bankruptcy_desc:
            r['风险点描述'] = f'[破产重整记录] {bankruptcy_desc}'[:500]
            r['_status'] = 'green'

    # --- Ch1(2) 人事与治理：高管处罚 (qcc-executive) ---
    exec_data = extended.get('executive', {})
    if isinstance(exec_data, dict):
        penalty_records = []
        for key, val in exec_data.items():
            if 'admin_penalty' in key and isinstance(val, dict) and _qcc_has_data(val):
                items = _extract_qcc_list(val,
                    ['行政处罚', '行政处罚信息', 'data', 'list'])
                if items:
                    person_name = key.replace('_admin_penalty', '')
                    for item in items[:2]:
                        if isinstance(item, dict):
                            detail = _s(item.get('处罚结果',
                                item.get('行政处罚决定书文号', '')))
                            penalty_records.append(f'{person_name}: {detail}')

        if penalty_records:
            for r in ch1_sec2['tables'][2]['data']:
                if '人事' in r.get('动向类别', ''):
                    r['具体内容'] = '[高管处罚] ' + '; '.join(penalty_records)[:500]
                    r['备注/来源'] = '企查查 API: qcc-executive'
                    r['_status'] = 'green'


def fetch_qcc_data(client_name: str, tools_filter: list = None) -> dict:
    """主入口：企查查采集 → 标准化 JSON"""

    data = build_skeleton(client_name)
    qcc = QccClient()

    # Step 0: 解析企业全称
    full_name = resolve_company_name(client_name, qcc)
    if full_name != client_name:
        data['meta']['client_name'] = full_name
        data['cover']['客户名称']['value'] = full_name

    all_tools = {
        'registration': ('工商信息', qcc.get_company_registration_info),
        'shareholders': ('股东信息', qcc.get_shareholder_info),
        'personnel': ('高管信息', qcc.get_key_personnel),
        'finance': ('财务数据', qcc.get_financial_data),
        'annual_reports': ('工商年报', qcc.get_annual_reports),
        'investments': ('对外投资', qcc.get_external_investments),
        'listing': ('上市信息', qcc.get_listing_info),
        'branches': ('分支机构', qcc.get_branches),
        'controller': ('实际控制人', qcc.get_actual_controller),
    }

    if tools_filter:
        all_tools = {k: v for k, v in all_tools.items() if k in tools_filter}

    print(f'企查查采集: {full_name}')
    print(f'工具数: {len(all_tools)}（{", ".join(all_tools.keys())}）\n')

    # ---- 并行调用（用全称）----
    results = {}
    errors = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(method, full_name): (key, label) for key, (label, method) in all_tools.items()}
        for future in as_completed(futures):
            key, label = futures[future]
            try:
                result = future.result(timeout=30)
                results[key] = result
                size = len(json.dumps(result, ensure_ascii=False)) if result else 0
                print(f'  ✅ {label} ({key}): {size} 字符')
            except Exception as e:
                errors.append((key, str(e)))
                results[key] = None
                print(f'  ❌ {label} ({key}): {e}')

    print()

    # ---- 企查查积分检查 ----
    total_tools = len(all_tools)
    credit_exhausted = []  # 积分不足的工具名

    # 检查: JSON-RPC 错误 code=-32000, message="当前积分余额不足"
    for key, result in results.items():
        if isinstance(result, dict) and result.get('_qcc_error') == 'points_insufficient':
            credit_exhausted.append(key)

    if credit_exhausted:
        print()
        print('=' * 50)
        print('❌ 企查查积分余额不足，数据采集无法继续。')
        print(f'   积分不足: {", ".join(credit_exhausted)}（{len(credit_exhausted)}/{total_tools} 个工具）')
        print(f'   建议: 充值企查查积分或使用会员账号后重新运行')
        print('=' * 50)
        sys.exit(4)

    # ---- Chapter 1: 客户核心画像 ----
    ch1 = data['chapters']['chapter1']
    ch1_sec1 = ch1['（1）集团主体资质与核心优势']
    ch1_sec2 = ch1['（2）发展前景']

    reg = results.get('registration', {})
    shareholders = results.get('shareholders', {})
    personnel = results.get('personnel', {})
    finance = results.get('finance', {})
    annual_reports = results.get('annual_reports', {})
    listing = results.get('listing', {})
    investments = results.get('investments', {})
    controller = results.get('controller', {})
    branches = results.get('branches', {})

    # --- 1-1 基础信息表《★》 + 补充信息 ---
    if reg:
        key_rows, detail_rows = map_registration_to_basic_info_v2(reg, full_name)

        # 股权结构
        if shareholders:
            equity_desc, is_central = map_shareholders_to_equity(shareholders)
            if equity_desc:
                for r in key_rows:
                    if '股权结构' in r.get('信息项', ''):
                        r['内容'] = equity_desc
                        r['备注/来源'] = '企查查 API: get_shareholder_info'
                        r['_status'] = 'green'

        # 写入骨架中的表 0 和表 1
        ch1_sec1['tables'][0]['data'] = key_rows    # 基础信息表《★》
        ch1_sec1['tables'][1]['data'] = detail_rows # 补充信息

        # Cover: 所属行业
        industry = _s(reg.get('国标行业', ''))
        data['cover']['所属行业']['value'] = industry
        data['cover']['所属行业']['status'] = 'green' if industry else 'yellow'

    # --- 1-2 高管信息 ---
    if personnel:
        exec_rows = map_personnel_to_executives_v2(personnel)
        ch1_sec1['tables'][2]['data'] = exec_rows  # 表 2: 高管信息

    # --- 1-3 经营情况《★》 ---
    listing_desc = map_listing_to_desc(listing) if listing else ''
    controller_desc = ''
    if controller:
        raw_ctrl = controller.get('摘要', controller.get('实际控制人', ''))
        if isinstance(raw_ctrl, dict):
            controller_desc = _s(raw_ctrl.get('name', raw_ctrl.get('姓名', '')))
        elif isinstance(raw_ctrl, (list, tuple)):
            controller_desc = '; '.join([_s(x) for x in raw_ctrl[:3] if x])
        else:
            controller_desc = _s(raw_ctrl)[:200]

    # 预先从财务原始数据提取营收/净利润（供经营情况表使用）
    # 提取最近三年数据，而非仅最近一年
    revenue_parts = []
    profit_parts = []
    if finance:
        records = finance.get('财务数据信息', [])
        for rec in records[:3]:
            period = _s(rec.get('报告期', '')).replace('年报', '').rstrip('年')
            pl = rec.get('指标详情', {}).get('财务报表', {}).get('利润表', {})
            rev = _yuan_to_wan(pl.get('营业总收入', ''))
            prf = _yuan_to_wan(pl.get('净利润', ''))
            if rev:
                revenue_parts.append(f'{period}年 {rev}万元')
            if prf:
                profit_parts.append(f'{period}年 {prf}万元')
    revenue_val = '  |  '.join(revenue_parts) if revenue_parts else ''
    profit_val = '  |  '.join(profit_parts) if profit_parts else ''

    ops_rows = ch1_sec1['tables'][3]['data']  # 经营情况表 skeleton
    for r in ops_rows:
        key = r['信息项']
        if '营业收入' in key:
            if revenue_val:
                r['内容'] = revenue_val
                r['备注/来源'] = '企查查 API: get_financial_data'
                r['_status'] = 'green'
            else:
                r['_status'] = 'yellow'
        elif '净利润' in key:
            if profit_val:
                r['内容'] = profit_val
                r['备注/来源'] = '企查查 API: get_financial_data'
                r['_status'] = 'green'
            else:
                r['_status'] = 'yellow'
        elif '上市信息' in key:
            r['内容'] = listing_desc
            r['备注/来源'] = '企查查 API: get_listing_info' if listing_desc else '[非上市企业或无记录]'
            r['_status'] = 'green' if listing_desc else 'yellow'
        elif '实际控制人' in key:
            r['内容'] = controller_desc
            r['备注/来源'] = '企查查 API: get_actual_controller' if controller_desc else '[API 未返回]'
            r['_status'] = 'green' if controller_desc else 'yellow'

    # --- 1-4 财务情况《★》 ---
    if finance:
        fin_rows = map_financial_to_table_v2(finance, annual_reports)
        ch1_sec1['tables'][4]['data'] = fin_rows  # 表 4: 财务情况

    # --- 1-5 上下游生态（保持骨架，全🟡） ---
    # 表 5 已在 skeleton 中定义

    # --- 1-6 发展前景：行业分析 ---
    industry_name = _s(reg.get('国标行业', '')) if reg else ''
    ch1_sec2['tables'][0]['data'][0]['核心内容'] = f'[QCC 行业分类: {industry_name}]' if industry_name else ''
    # 其余行保持空🟡

    # ---- Chapter 2: 银企合作情况 ----
    ch2 = data['chapters']['chapter2']

    # --- 2-1 子公司列表 ---
    if investments:
        sub_rows = map_investments_to_subsidiaries_v2(investments)
        ch2['（1）总体资金管理模式']['tables'][0]['data'] = sub_rows

    # --- 2-2 分支机构（附加到子公司列表后或单独） ---
    if branches:
        branch_items = branches.get('分支机构信息', branches.get('data', []))
        if isinstance(branch_items, list) and branch_items:
            branch_rows = []
            for item in branch_items[:10]:
                if isinstance(item, dict):
                    branch_rows.append({
                        '子公司名称': _s(item.get('分支机构名称', item.get('name', ''))),
                        '层级': '分支机构',
                        '业务板块': '',
                        '持股比例': '—',
                        '备注': _s(item.get('登记状态', '')),
                        '_status': 'green',
                    })
            # 追加到子公司表
            existing = ch2['（1）总体资金管理模式']['tables'][0]['data']
            if existing and existing[0].get('公司名称', existing[0].get('子公司名称')):
                ch2['（1）总体资金管理模式']['tables'][0]['data'] = existing + branch_rows

    # Ch2(2) 债券融资、Ch2(3) 银行合作 — 骨架已含全部表结构，全🟡/🔴

    # ---- Chapter 3-6: 骨架已含完整表结构，无需额外处理 ----

    # ---- 扩展采集：知识产权/风险/经营/高管 ----
    executives = ch1_sec1['tables'][2]['data']  # 高管数据（如果已填充）
    personnel_list = [{'姓名': r.get('姓名', '')} for r in executives if r.get('姓名') and '待QCC' not in r.get('姓名', '')]
    extended = collect_extended_qcc_data(full_name, personnel_list)
    fill_extended_qcc_data(data, extended, personnel_list)

    # ---- 元信息 ----
    data['meta']['qcc_fetched_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    data['meta']['qcc_tools'] = list(results.keys())
    if errors:
        data['meta']['qcc_errors'] = [{'tool': t, 'error': e} for t, e in errors]
        data['meta']['qcc_errors_note'] = '这些工具调用失败，对应字段已标记为 yellow，需 AI web_search 补充'

    # ---- 原始 QCC 接口数据（供 Excel 核对） ----
    tool_labels = {
        'registration': '工商信息', 'shareholders': '股东信息', 'personnel': '高管信息',
        'finance': '财务数据', 'annual_reports': '工商年报', 'investments': '对外投资',
        'listing': '上市信息', 'branches': '分支机构', 'controller': '实际控制人',
    }
    ext_labels = {
        'ipr': '知识产权', 'risk': '风险信息', 'operation': '经营动态', 'executive': '高管处罚',
    }
    raw_data = []
    for key, label in tool_labels.items():
        if key in results:
            raw_data.append({'tool': key, 'label': label, 'response': results[key]})
    for key, label in ext_labels.items():
        if key in extended:
            raw_data.append({'tool': key, 'label': label, 'response': extended[key]})
    data['_qcc_raw'] = raw_data

    # 采集日志
    try:
        qcc_stats = {}
        for entry in raw_data:
            resp = entry.get('response', {})
            if isinstance(resp, dict) and '_qcc_error' in resp:
                qcc_stats[entry['tool']] = f'error: {resp["_qcc_error"]}'
            elif resp is None:
                qcc_stats[entry['tool']] = 'null'
            else:
                qcc_stats[entry['tool']] = 'ok'
        greens = sum(1 for ch in data['chapters'].values() if isinstance(ch, dict)
                     for sec in ch.values() if isinstance(sec, dict)
                     for tbl in sec.get('tables', [])
                     for r in tbl.get('data', []) if r.get('_status') == 'green')
        yellows = sum(1 for ch in data['chapters'].values() if isinstance(ch, dict)
                      for sec in ch.values() if isinstance(sec, dict)
                      for tbl in sec.get('tables', [])
                      for r in tbl.get('data', []) if r.get('_status') == 'yellow')
        try:
            from workflow_log import log_event as _qcc_log
            _qcc_log(client_name, 'qcc_fetch', 'done', greens=greens, yellows=yellows, apis=qcc_stats)
        except Exception as _e:
            pass  # 日志不可用不阻塞
    except Exception:
        pass

    return data


# ============================================================
# CLI
# ============================================================
if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')

    parser = argparse.ArgumentParser(description='企查查数据采集编排器 V2')
    parser.add_argument('--client', '-c', required=True, help='企业全称')
    parser.add_argument('--tools', help='限定工具（逗号分隔），默认全部')
    parser.add_argument('--output', '-o', help='输出 JSON 路径')

    args = parser.parse_args()

    tools_filter = None
    if args.tools:
        tools_filter = [t.strip() for t in args.tools.split(',')]

    try:
        data = fetch_qcc_data(args.client, tools_filter)
    except QccAuthError as e:
        print(f'\n❌ 企查查未授权:\n{e}')
        sys.exit(2)
    except QccCallError as e:
        print(f'\n❌ 调用失败:\n{e}')
        sys.exit(3)

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
                    if st == 'green': green += 1
                    elif st == 'yellow': yellow += 1
                    else: red += 1

    output_path = args.output or str(OUTPUT_DATA_DIR / f'{args.client}_data.json')
    OUTPUT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    sz_kb = Path(output_path).stat().st_size // 1024
    total = green + yellow + red
    pct = (green + yellow) / total * 100 if total > 0 else 0
    print(f'\n{"="*50}')
    print(f'✅ 企查查采集完成: {args.client}')
    print(f'{"="*50}')
    print(f'  输出: {output_path} ({sz_kb}KB)')
    print(f'  数据: 总 {total} 行 | 🟢{green} 🟡{yellow} 🔴{red}')
    print(f'  企查查覆盖率: {pct:.0f}%')
    print(f'  使用工具: {", ".join(data["meta"]["qcc_tools"])}')
    print(f'\n  下一步: python pipeline_v2/run_v2.py --client {args.client}')
