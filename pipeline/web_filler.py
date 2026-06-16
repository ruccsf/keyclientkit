"""
Web Search 填充器 — 🟡 字段搜索引擎编排
=========================================
在 Claude Code Skill 中运行，利用 Claude 内置的 WebSearch + WebFetch 工具，
按优先级搜索公开数据，填充报告骨架的 🟡 字段，标注来源 URL。

用法（在 Claude Code 中）:
    # 加载数据
    data = load_client_data('中粮集团')

    # 获取搜索计划
    plans = get_search_plans(data)

    # Claude 执行搜索后，调用 fill_field() 写入
    fill_field(data, '重要行业政策', '政策内容...', 'https://gov.cn/...')

    # 保存
    save_client_data('中粮集团', data)

命令行:
    python web_filler.py --client 中粮集团  # 打印搜索计划（供 Claude 执行）
    python web_filler.py --list-plans        # 列出所有搜索计划
"""

import json, os, sys
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field

# ---- Paths ----
SKILL_DIR = Path(__file__).parent.parent
SESSIONS_DIR = SKILL_DIR / 'sessions'

sys.path.insert(0, str(Path(__file__).parent))
from qcc_fetch import _s


# ================================================================
# 搜索计划定义
# ================================================================

@dataclass
class SearchPlan:
    """单个 🟡 字段的搜索策略"""
    field_key: str              # 字段标识（用于匹配）
    description: str            # 人类可读描述
    queries: list[str]          # 搜索查询模板（{公司名} {行业} 会被替换）
    priority_domains: list[str] # 优先域名（空列表 = 不限制）
    extract_hint: str           # 提取提示（告诉 Claude 提取什么）
    target_table: str = ''      # 目标表名（用于定位）
    target_row_key: str = ''    # 目标行关键字（用于定位）


def get_search_plans(data: dict = None) -> list[SearchPlan]:
    """
    返回所有 🟡 字段的搜索计划。
    如果提供 data，会自动从骨架中提取公司名和行业名替换模板变量。
    """
    company = ''
    industry = ''
    if data:
        company = data.get('meta', {}).get('client_name', '')
        industry = data.get('cover', {}).get('所属行业', {}).get('value', '')

    plans = []

    # === Ch1(1) 经营情况表 ===
    plans.extend([
        SearchPlan('主营业务板块营收占比', '主营业务板块营收占比',
            queries=[f'{company} 主营业务 营收构成 业务板块 占比'],
            priority_domains=[], extract_hint='摘取各业务板块名称及营收占比百分比',
            target_table='经营情况《★》', target_row_key='主营业务板块营收占比'),

        SearchPlan('行业排名', '行业排名',
            queries=[f'{industry} 企业排名 TOP10 2025', f'{company} 行业排名 市场份额'],
            priority_domains=[], extract_hint='摘取行业排名、榜单名称、发布机构',
            target_table='经营情况《★》', target_row_key='行业排名'),

        SearchPlan('市场份额', '市场份额(%)及测算依据',
            queries=[f'{company} 市场份额 {industry}', f'{industry} 市场占有率 头部企业'],
            priority_domains=[], extract_hint='摘取市场份额百分比及测算依据',
            target_table='经营情况《★》', target_row_key='市场份额'),
    ])

    # === Ch1(2) 行业分析 ===
    plans.extend([
        SearchPlan('重要行业政策', '重要行业政策',
            queries=[f'{industry} 行业政策 十四五 2025 发改委', f'{industry} 产业政策 最新 2025'],
            priority_domains=['gov.cn', 'ndrc.gov.cn', 'miit.gov.cn'],
            extract_hint='摘取1-3个最重要的行业政策名称、发布机构、核心要点',
            target_table='行业分析', target_row_key='重要行业政策'),

        SearchPlan('行业特征与周期', '行业特征与周期',
            queries=[f'{industry} 发展现状 行业特征 经济周期 2025'],
            priority_domains=['miit.gov.cn'],
            extract_hint='摘取行业关键特征（如集中度、技术门槛、周期性等）',
            target_table='行业分析', target_row_key='行业特征与周期'),

        SearchPlan('主要增长点与转型方向', '主要增长点与转型方向',
            queries=[f'{industry} 发展趋势 增长点 转型升级 2025'],
            priority_domains=[], extract_hint='摘取2-3个主要增长点和转型方向',
            target_table='行业分析', target_row_key='主要增长点与转型方向'),

        SearchPlan('竞争格局', '竞争格局',
            queries=[f'{industry} 竞争格局 市场份额 头部企业 2025', f'{industry} CR5 CR10 市场集中度'],
            priority_domains=[], extract_hint='摘取头部企业名称、市场份额、集中度指标',
            target_table='行业分析', target_row_key='竞争格局'),

        SearchPlan('主要风险点', '主要风险点',
            queries=[f'{industry} 风险分析 挑战 经营风险 2025'],
            priority_domains=[], extract_hint='列举3-5个行业主要风险因素',
            target_table='行业分析', target_row_key='主要风险点'),
    ])

    # === Ch1(2) 企业发展前景 ===
    plans.extend([
        SearchPlan('扩张转型规划', '未来3-5年扩张/转型规划',
            queries=[f'{company} 发展战略 十四五 十五五 规划', f'{company} 战略目标 2025 2026'],
            priority_domains=['sasac.gov.cn'] if data and '国务院' in str(data.get('cover', {}).get('客户名称', {})) else [],
            extract_hint='摘取公司公开的战略规划、发展目标、转型方向',
            target_table='企业发展前景《★》', target_row_key='扩张/转型'),

        SearchPlan('重点投资项目', '重点投资项目(附预算/时间表)',
            queries=[f'{company} 投资项目 建设 2025 2026', f'{company} 重大项目 投资 预算'],
            priority_domains=['ndrc.gov.cn'],
            extract_hint='摘取重点项目名称、投资金额、预计时间表',
            target_table='企业发展前景《★》', target_row_key='重点投资'),

        SearchPlan('新增业务板块', '新增业务板块',
            queries=[f'{company} 新业务 布局 板块 拓展 2025'],
            priority_domains=[], extract_hint='摘取公司新进入或计划进入的业务领域',
            target_table='企业发展前景《★》', target_row_key='新增业务'),

        SearchPlan('预计营收年均增速', '预计营收年均增速(%)+依据',
            queries=[f'{company} 营收 增长 预测 2025 2026', f'{company} 业绩目标 增速'],
            priority_domains=[], extract_hint='摘取营收增速预测数字及依据（年报/研报/公开表态）',
            target_table='企业发展前景《★》', target_row_key='营收增速'),
    ])

    # === Ch1(2) 近期发展动向 ===
    plans.extend([
        SearchPlan('业务经营动向', '业务经营动向',
            queries=[f'{company} 经营 业绩 最新 2025 2026'],
            priority_domains=[], extract_hint='摘取近期重要经营事件、业绩亮点（1-2条）',
            target_table='近期发展动向', target_row_key='业务经营动向'),

        SearchPlan('人事与治理调整', '人事与治理调整',
            queries=[f'{company} 人事变动 高管 任免 2025 2026'],
            priority_domains=[], extract_hint='摘取重要人事变动信息',
            target_table='近期发展动向', target_row_key='人事与治理调整'),

        SearchPlan('国际与地缘', '国际与地缘(如适用)',
            queries=[f'{company} 海外业务 国际 一带一路 布局'],
            priority_domains=['mofcom.gov.cn'],
            extract_hint='摘取海外业务布局、国际合作的地区/国家/规模',
            target_table='近期发展动向', target_row_key='国际与地缘'),
    ])

    # === Ch2(1) 融资管理模式 ===
    plans.append(
        SearchPlan('融资管理模式', '融资管理模式(统贷统还/子公司自主融资)',
            queries=[f'{company} 融资模式 资金集中管理 财务公司'],
            priority_domains=['chinamoney.com.cn'],
            extract_hint='摘取融资管理模式描述，如统贷统还、财务公司集中管理、子公司自主融资等',
            target_table='融资管理模式', target_row_key=''),
    )

    # === Ch2(2) 债券及其他融资情况 ===
    plans.extend([
        SearchPlan('总体融资规模变动', '总体融资规模变动',
            queries=[f'{company} 债券 融资规模 债务 2024 2025'],
            priority_domains=['chinamoney.com.cn', 'shclearing.com.cn'],
            extract_hint='摘取近年付息负债总额、同比变动、主要融资方式',
            target_table='总体融资规模变动《★》', target_row_key='上一年'),

        SearchPlan('近五年发债情况', '近五年发债情况',
            queries=[f'{company} 债券发行 中期票据 公司债 2024 2025'],
            priority_domains=['chinamoney.com.cn'],
            extract_hint='摘取债券类型、发行金额、利率、发行时间',
            target_table='本级近五年发债情况', target_row_key='信用债'),

        SearchPlan('存续期债券明细', '存续期债券明细',
            queries=[f'{company} 存续债券 余额 到期 2025'],
            priority_domains=['chinamoney.com.cn', 'shclearing.com.cn'],
            extract_hint='摘取存续债券名称、余额、利率、到期日、资金用途',
            target_table='存续期债券明细', target_row_key=''),
    ])

    # === Ch3 机会缺口事实 ===
    plans.extend([
        SearchPlan('新增项目融资需求', '新增项目融资需求',
            queries=[f'{company} 新项目 投资计划 融资需求 2025 2026'],
            priority_domains=[], extract_hint='摘取公司公开的新项目信息及融资需求金额',
            target_table='机会缺口事实《★》', target_row_key='新增项目融资'),

        SearchPlan('业务扩张融资', '业务扩张融资',
            queries=[f'{company} 业务扩张 融资 资金需求'],
            priority_domains=[], extract_hint='摘取业务扩张相关的融资需求',
            target_table='机会缺口事实《★》', target_row_key='业务扩张融资'),

        SearchPlan('续贷需求', '续贷需求',
            queries=[f'{company} 到期债务 续贷 再融资 2025 2026'],
            priority_domains=['chinamoney.com.cn'],
            extract_hint='摘取近期到期债务和可能的续贷需求',
            target_table='机会缺口事实《★》', target_row_key='续贷需求'),

        SearchPlan('上下游配套金融', '上下游配套金融',
            queries=[f'{company} 供应链金融 上下游 配套金融 产业链'],
            priority_domains=[], extract_hint='摘取供应链金融需求规模和方向',
            target_table='机会缺口事实《★》', target_row_key='上下游配套金融'),
    ])

    # === Ch4 政策导向 ===
    plans.extend([
        SearchPlan('投融资政策指引', '投融资政策指引',
            queries=[f'{industry} 投融资政策 监管指引 2025', f'{industry} 信贷政策 产业指导'],
            priority_domains=['ndrc.gov.cn', 'gov.cn'],
            extract_hint='摘取与该行业相关的投融资政策、监管指引要点',
            target_table='政策导向与分层定位《★》', target_row_key='投融资政策指引'),

        SearchPlan('适用贴息优惠政策', '适用贴息/优惠政策',
            queries=[f'{industry} 贴息 优惠 扶持政策 财政补贴 2025'],
            priority_domains=['mof.gov.cn', 'gov.cn'],
            extract_hint='摘取行业可享受的贴息、税收优惠、财政补贴等政策',
            target_table='政策导向与分层定位《★》', target_row_key='贴息/优惠'),
    ])

    # === Ch1(1) 上下游生态 ===
    plans.extend([
        SearchPlan('产业链全景图', '集团产业链/供应链全景图',
            queries=[f'{company} 产业链 供应链 上下游 业务布局'],
            priority_domains=[], extract_hint='描述公司产业链布局和供应链特征',
            target_table='上下游生态', target_row_key='产业链'),

        SearchPlan('上游核心供应商', '上游核心供应商',
            queries=[f'{industry} 上游 供应商 原材料 采购'],
            priority_domains=[], extract_hint='描述行业上游供应结构和核心供应商类型',
            target_table='上下游生态', target_row_key='上游核心供应商'),

        SearchPlan('下游核心客户', '下游核心客户',
            queries=[f'{company} 客户 销售渠道 下游 市场'],
            priority_domains=[], extract_hint='描述公司下游客户结构和销售渠道',
            target_table='上下游生态', target_row_key='下游核心客户'),
    ])

    return plans


# ================================================================
# 数据读写工具
# ================================================================

def load_client_data(client_name: str) -> dict:
    """加载客户数据 JSON"""
    session_path = SESSIONS_DIR / client_name / 'data.json'
    if not session_path.exists():
        raise FileNotFoundError(f'未找到客户数据: {session_path}\n请先在 Streamlit 中完成 QCC 采集。')
    with open(session_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_client_data(client_name: str, data: dict):
    """保存客户数据 JSON"""
    session_path = SESSIONS_DIR / client_name
    session_path.mkdir(parents=True, exist_ok=True)
    with open(session_path / 'data.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fill_field(data: dict, field_key: str, content: str, source_url: str = '',
               source_note: str = '', status: str = 'yellow'):
    """
    在骨架中查找并填充指定字段。

    Args:
        data: 报告 JSON 数据
        field_key: 字段关键字（匹配"信息项"/"分析维度"/"指标"等列的值）
        content: 填充内容
        source_url: 数据来源 URL
        source_note: 来源备注（如未提供，从 URL 提取域名）
        status: _status 值，默认 yellow
    """
    found = False
    for ch_key, ch_val in data.get('chapters', {}).items():
        if not isinstance(ch_val, dict):
            continue
        for sec_key, sec_val in ch_val.items():
            if not isinstance(sec_val, dict):
                continue
            for tbl in sec_val.get('tables', []):
                for row in tbl.get('data', []):
                    # 查找匹配的行
                    matched = False
                    for col in ['信息项', '分析维度', '指标', '财务指标', '动向类别',
                                '年份', '债券类型', '需求类型', '维度', '产品类别',
                                '风险类别', '准入方式', '合作维度', '未覆盖业务领域',
                                '短板类别', '目标维度', '拓展方向', '联动/创新类型',
                                '接触类型', '对标维度']:
                        if field_key in str(row.get(col, '')):
                            matched = True
                            break

                    if not matched:
                        continue

                    # 找到目标行 → 填充内容
                    content_col = _find_content_column(row)
                    if content_col:
                        row[content_col] = content

                    # 设置来源
                    if source_url:
                        row['_source_url'] = source_url
                    if source_note:
                        if '备注/来源' in row:
                            row['备注/来源'] = source_note
                        elif '数据来源' in row:
                            row['数据来源'] = source_note

                    row['_status'] = status
                    found = True

    return found


def _find_content_column(row: dict) -> str:
    """找到行的主内容列（非键列、非来源列）"""
    key_cols = {'信息项', '分析维度', '指标', '财务指标', '动向类别', '年份',
                '债券类型', '需求类型', '维度', '产品类别', '风险类别', '准入方式',
                '合作维度', '未覆盖业务领域', '短板类别', '目标维度', '拓展方向',
                '序号', '接触类型', '角色', '银行', '职务', '子公司名称', '产品/服务',
                '联动/创新类型', '对标维度', '年份'}
    source_cols = {'备注/来源', '数据来源', '备注', '来源'}
    candidates = ['内容', '核心内容', '具体内容', '付息负债总额(万元)',
                  '发行金额(万元)', '余额(万元)', '方案描述', '风险点描述',
                  '需求描述', '集团业务规模', '内容(含趋势对比)']
    for c in candidates:
        if c in row:
            return c
    # Fallback: first non-key non-source column
    for c in row:
        if not c.startswith('_') and c not in key_cols and c not in source_cols:
            return c
    return None


def get_yellow_fields(data: dict) -> list[dict]:
    """获取所有 🟡 空字段的列表，供 Claude 批量搜索"""
    fields = []
    for ch_key, ch_val in data.get('chapters', {}).items():
        if not isinstance(ch_val, dict):
            continue
        for sec_key, sec_val in ch_val.items():
            if not isinstance(sec_val, dict):
                continue
            for tbl in sec_val.get('tables', []):
                for row in tbl.get('data', []):
                    if row.get('_status') != 'yellow':
                        continue
                    # 检查是否有实质内容
                    content_col = _find_content_column(row)
                    content = str(row.get(content_col, '')) if content_col else ''
                    if content.strip():
                        continue  # 已填充
                    # 提取行标识
                    key_val = ''
                    for col in ['信息项', '分析维度', '方向类别', '需求类型', '年份',
                               '债券类型', '维度', '产品类别', '风险类别', '未覆盖业务领域']:
                        if row.get(col):
                            key_val = row.get(col)
                            break
                    fields.append({
                        'chapter': ch_key,
                        'section': sec_key,
                        'table': tbl.get('title', ''),
                        'key': key_val,
                        'row': row,
                    })
    return fields


def get_search_tasks(client_name: str) -> dict:
    """
    获取搜索任务清单（JSON 格式）。
    任何 AI 智能体都可以读这个输出，然后用自己的搜索引擎执行。
    """
    try:
        data = load_client_data(client_name)
        company = data.get('meta', {}).get('client_name', client_name)
        industry = data.get('cover', {}).get('所属行业', {}).get('value', '')
    except FileNotFoundError:
        data = None
        company = client_name
        industry = ''

    plans = get_search_plans(data)

    # Count empty yellow fields
    yellow_empty = 0
    yellow_total = 0
    if data:
        for ch_key, ch_val in data.get('chapters', {}).items():
            if not isinstance(ch_val, dict): continue
            for sec_key, sec_val in ch_val.items():
                if not isinstance(sec_val, dict): continue
                for tbl in sec_val.get('tables', []):
                    for row in tbl.get('data', []):
                        if row.get('_status') != 'yellow': continue
                        yellow_total += 1
                        content_col = _find_content_column(row)
                        has_data = bool(row.get(content_col, '').strip()) if content_col else False
                        if not has_data:
                            yellow_empty += 1

    tasks = []
    for i, plan in enumerate(plans):
        tasks.append({
            'id': i + 1,
            'table': plan.target_table,
            'field': plan.field_key,
            'description': plan.description,
            'query': plan.queries[0],
            'backup_queries': plan.queries[1:],
            'priority_domains': plan.priority_domains,
            'extract_hint': plan.extract_hint,
        })

    return {
        'company': company,
        'industry': industry,
        'total_tasks': len(tasks),
        'yellow_fields_total': yellow_total,
        'yellow_fields_empty': yellow_empty,
        'instructions': {
            'how_to_search': '对每条 task，用你的搜索引擎搜索 query。优先打开 priority_domains 中的链接。',
            'how_to_fill': '搜到数据后，调用 Python: web_filler.fill_field(data, field_key="...", content="...", source_url="...")',
            'no_fabrication': '搜不到就跳过，不要编造数据。该字段保持空 🟡。',
            'source_required': '每条数据必须标注 source_url，即你实际打开的页面 URL。',
        },
        'tasks': tasks,
    }


# ================================================================
# CLI
# ================================================================

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法:')
        print('  python web_filler.py --plan <企业名>     输出搜索任务清单（JSON，供 AI 读取）')
        print('  python web_filler.py --list-plans          列出所有搜索计划模板')
        print('  python web_filler.py --stats <企业名>      查看填充统计')
        sys.exit(1)

    if sys.argv[1] == '--list-plans':
        plans = get_search_plans()
        for i, p in enumerate(plans):
            print(f'{i+1}. [{p.target_table}] {p.description}')
            print(f'   查询: {p.queries[0][:100]}')
            print(f'   优先域名: {p.priority_domains or "不限"}')
            print()

    elif sys.argv[1] == '--plan' and len(sys.argv) > 2:
        tasks = get_search_tasks(sys.argv[2])
        print(json.dumps(tasks, ensure_ascii=False, indent=2))

    elif sys.argv[1] == '--stats' and len(sys.argv) > 2:
        try:
            data = load_client_data(sys.argv[2])
            greens = yellows = reds = 0
            for ch_key, ch_val in data['chapters'].items():
                if not isinstance(ch_val, dict): continue
                for sec_key, sec_val in ch_val.items():
                    if not isinstance(sec_val, dict): continue
                    for tbl in sec_val.get('tables', []):
                        for row in tbl.get('data', []):
                            st = row.get('_status', 'red')
                            if st == 'green': greens += 1
                            elif st == 'yellow': yellows += 1
                            else: reds += 1
            total = greens + yellows + reds
            print(f'公司: {data["meta"]["client_name"]}')
            print(f'行业: {data["cover"]["所属行业"]["value"]}')
            print(f'总计: {total} 行')
            print(f'🟢 QCC数据: {greens} ({greens*100//total}%)')
            print(f'🟡 Web搜索: {yellows} ({yellows*100//total}%)')
            print(f'🔴 待人工填: {reds} ({reds*100//total}%)')
            print(f'自动填充率: {(greens+yellows)*100//total}%')
        except FileNotFoundError as e:
            print(f'Error: {e}', file=sys.stderr)
            sys.exit(1)
