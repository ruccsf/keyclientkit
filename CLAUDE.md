# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Quick Reference

```bash
# Environment check + dependency install
python install.py

# OAuth 2.0 PKCE authorization (one-time per server, opens browser)
python oauth_qcc.py auth                    # company server (core, covers 80% of data)
python oauth_qcc.py auth --resource ipr     # intellectual property
python oauth_qcc.py auth --resource risk    # penalties / bankruptcy
python oauth_qcc.py auth --resource operation  # licenses / permits
python oauth_qcc.py auth --resource executive  # executive penalties
python oauth_qcc.py status                  # check all token expiries
python oauth_qcc.py test                    # API connectivity test

# Generate reports from session JSON
python pipeline/export.py --client <企业名称>
python pipeline/export.py --client <企业名称> --force       # skip 🟡-emptiness guard
python pipeline/export.py --client <企业名称> --readback    # merge Excel edits → JSON → HTML
python pipeline/export.py --client <企业名称> --stats       # print stats only, no file generation
python pipeline/export.py --client <企业名称> --excel-only  # Excel only
python pipeline/export.py --client <企业名称> --html-only   # HTML only

# Inspect web-fill state
python pipeline/web_filler.py --plan <企业名称>    # print search plans
python pipeline/web_filler.py --stats <企业名称>   # yellow-field counts
```

Dependencies: `requests>=2.31.0`, `openpyxl>=3.1.0`. Python 3.10+. No Node.js or external CLI tools.

## Architecture

### Data Pipeline

```
QCC MCP APIs (8 core + 4 extension servers, called via pure-Python HTTP/SSE)
        │
        ▼
  qcc_fetch.py ─── builds 39-table JSON skeleton, calls all QCC tools in parallel,
        │            maps raw API responses → standardized rows, saves sessions/{name}/data.json
        ▼
  sessions/{name}/data.json   ← single source of truth (hierarchical JSON, all state lives here)
        │
        ▼
  web_filler.py ─── generates 28 search plans for 🟡 fields, exposes fill_field()/batch_fill()
        │            for the AI agent to write web-search results into the JSON
        ▼
  export.py ─── validates no 🟡 fields are empty (guarded, override with --force),
        │        then delegates to renderers
        ├──► excel_renderer.py  → 9-sheet .xlsx, rows color-coded 🟢/🟡/🔴, frozen headers
        └──► html_renderer.py   → formal document .html, cover card + stat bar + 6 chapters
                                     │
                                     ▼
                              excel_reader.py ← reads user edits from Excel back into JSON
                                     │          (positional key matching — do not reorder rows)
                                     ▼
                              sessions/{name}/data.json (updated) → re-export HTML
```

### Key Design Decisions

**Skeleton-first** (`qcc_fetch.py:build_skeleton()`): All 39 tables are defined upfront as a hierarchical JSON template with `_status` markers on every cell. QCC collection and web search **fill** the skeleton — they never create new tables or change structure. This makes the data model explicit, versionable (v2.0), and independent of which QCC calls succeed.

**Parallel QCC execution** (`qcc_fetch.py:fetch_qcc_data()`): 8 core tools run via `ThreadPoolExecutor(max_workers=6)`. Each call is independent — the skeleton is populated after all return. Extension servers (ipr/risk/operation/executive) run in a second phase via `collect_extended_qcc_data()`.

**Token-per-server OAuth** (`oauth_qcc.py` + `qcc_client.py`): Each of the 5 QCC MCP servers requires its own OAuth 2.0 PKCE token scoped to a `resource` parameter. Tokens are cached in-memory (60s TTL), auto-refreshed 5 min before expiry, stored in `config.json` (gitignored). `QccClient._call_mcp_server()` handles JSON-RPC 2.0 POST + SSE response parsing.

**Yellow-field export guard** (`export.py:_check_yellow_fields()`): Export refuses to run if any 🟡 (web-searchable) field is still empty. This enforces that the AI agent completes Step 2 before Step 3. Override with `--force`.

**Positional key matching** (`excel_reader.py`): Excel → JSON merge matches rows by key value + occurrence order (the Nth "营业收入" in Excel → the Nth "营业收入" in JSON). Users must not insert, delete, or reorder rows in the Excel checklist.

### Three-Color Data System

| Color | Source | Filled By |
|:---:|------|------|
| 🟢 | QCC API (registration, shareholders, financials, executives, investments, branches, controller, IP, risk, licenses) | `qcc_fetch.py` auto-populates |
| 🟡 | Public web (industry policies, bond data, credit ratings, market share, news, supply chain) | AI agent web search → `web_filler.fill_field()` |
| 🔴 | Bank internal (loan balances, deposit levels, account details, cooperation targets, sign-offs) | Customer manager edits Excel → `excel_reader.py` reads back |

### JSON Data Model

```python
{
  'meta': { client_name, report_version, template_version, generated_at, source },
  'cover': { 客户名称, 客户等级, 所属行业, 主责客户经理, 编制日期, 版本号 },
  'chapters': {
    'chapter1': '客户核心画像',     # 2 sections, 11 tables
    'chapter2': '银企合作情况',     # 3 sections, 12 tables
    'chapter3': '金融需求挖掘',     # 1 section,  4 tables
    'chapter4': '服务方案设计',     # 1 section,  7 tables
    'chapter5': '行动计划与跟踪',   # 1 section,  3 tables
    'chapter6': '审核与签发',       # 1 section,  2 tables
  }
}
# Chapter shape: { section_key: { title, tables: [{ title, type: "kv"|"list", data: [{_status, ...}] }] } }
# Chapters are dicts keyed by section, NOT lists — access: data['chapters']['chapter1']['sec1_1']
```

### Module Map

| Module | Role | Key Exports |
|------|------|------|
| `pipeline/qcc_client.py` | QCC MCP HTTP client (JSON-RPC + SSE) | `QccClient`, `QccIprClient`, `QccRiskClient`, `QccOperationClient`, `QccExecutiveClient` |
| `pipeline/qcc_fetch.py` | Orchestrator: skeleton + parallel collection + V2 mapping (~1500 lines) | `fetch_qcc_data(client_name)`, `build_skeleton(client_name)` |
| `pipeline/web_filler.py` | Search planner (28 plans) + JSON field writer | `load_client_data()`, `save_client_data()`, `fill_field()`, `batch_fill()`, `get_search_plans()`, `get_yellow_fields()` |
| `pipeline/pdf_extractor.py` | PDF 募集书提取（pypdfium2）→ 资产负债表补充 QCC 缺口 | `download_pdf()`, `extract_balance_sheet()`, `extract_financial_data()`, `map_to_skeleton_columns()` |
| `pipeline/export.py` | Unified CLI with yellow-field guard | `argparse` CLI: `--client`, `--readback`, `--force`, `--stats`, `--excel-only`, `--html-only` |
| `pipeline/excel_renderer.py` | JSON → 9-sheet color-coded Excel | `generate_excel(data, output_path)` |
| `pipeline/excel_reader.py` | Excel → JSON merge (positional match) | `read_excel_changes(excel_path, data)` |
| `pipeline/html_renderer.py` | JSON → formal document HTML | `generate_html(data, output_path)` |
| `oauth_qcc.py` | OAuth 2.0 PKCE full flow | `get_valid_token(resource)`, `run_auto_auth()`, `refresh_access_token()` |
| `install.py` | Environment doctor (4 checks) | Python ver, deps, file integrity, OAuth status |

## Report Generation Workflow

You are a bank corporate client manager's AI assistant. When asked to generate a cooperation strategy report for a company, follow these steps **in order, without stopping until HTML + Excel files exist**.

**Trigger phrases:** "帮我生成 XX集团 的合作策略报告", "给 XX公司 做一份报告", "采集 XX企业 的数据", "分析 XX集团 的合作策略", "生成 XX 的报告".

**Completion checklist (all must be ✓ before replying to user):**
- [ ] Step 1: QCC data collected → 🟢 count > 0
- [ ] Step 1.5: PDF supplement attempted → balance sheet debt items filled (non-blocking if PDF unavailable)
- [ ] Step 2: Every 🟡 field searched → 🟡 empty count = 0 (export.py will block otherwise)
- [ ] Step 3: HTML + Excel files generated → both confirmed on disk
- [ ] Step 4: Stats and file paths reported to user

### Step 1: QCC Data Collection (🟢)

```bash
cd keyclientkit
PYTHONIOENCODING=utf-8 python -c "
import sys; sys.path.insert(0, 'pipeline')
from qcc_fetch import fetch_qcc_data
import json; from pathlib import Path
data = fetch_qcc_data('{企业名称}')
Path('sessions/{企业名称}').mkdir(parents=True, exist_ok=True)
with open('sessions/{企业名称}/data.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
greens = sum(1 for ch in data['chapters'].values() for sec in ch.values() for t in sec.get('tables',[]) for r in t.get('data',[]) if r.get('_status')=='green')
yellows = sum(1 for ch in data['chapters'].values() for sec in ch.values() for t in sec.get('tables',[]) for r in t.get('data',[]) if r.get('_status')=='yellow')
reds = sum(1 for ch in data['chapters'].values() for sec in ch.values() for t in sec.get('tables',[]) for r in t.get('data',[]) if r.get('_status')=='red')
print(f'\n采集完成: 🟢{greens} 🟡{yellows} 🔴{reds}')
"
```

Calls 8 core QCC tools in parallel (registration, shareholders, personnel, finance, investments, listing, branches, controller), then 4 extension servers (ipr/risk/operation/executive) for IP, penalties, licenses, and executive sanctions.

**If exit code 4 with "积分余额不足":** Hard stop. Tell user to recharge QCC credits. Do NOT substitute with web search — the 🟢 foundational data is missing.

**If extension servers report auth errors:** Tell user to run `python oauth_qcc.py auth --resource <server>`. Core data collection continues without them.

### Step 1.5: PDF 募集说明书补充（资产负债表详细科目）

⚠️ **本步骤强制必须执行！** 即使 QCC 返回了全部财务数据，PDF 仍是**子公司注册地/国标行业/实收资本**的唯一来源。

QCC 的 `get_financial_data` 对以下 6 个资产负债表科目返回不稳定：短期借款、长期借款、应付债券、一年内到期非流动负债、应付票据、应收票据。**用债券募集说明书 PDF 补充**——PDF 包含完整审计过的资产负债表，一次性覆盖全部科目。

**数据优先级：PDF（完整审计） > QCC（不稳定） > Web Search（兜底）**

#### 1.5.1 获取 PDF（三情况逐级 fallback）

按以下优先级获取募集说明书 PDF：

**情况 1：用户拖入了 PDF 文件**

如果用户在聊天框中直接拖入了 PDF 文件，使用该文件并建立缓存：

```python
from pdf_extractor import cache_pdf

# 用户拖入的文件路径（从对话上下文中获取）
pdf_path = cache_pdf('{用户提供的PDF路径}', '{企业名称}')
print(f'📋 使用用户上传的 PDF 并已缓存')
```

→ 然后跳到 1.5.2 提取数据。

**情况 2：检查本地缓存**

检查 `sessions/{企业名称}/pdf/` 下是否有缓存的 PDF：

```python
from pdf_extractor import find_cached_pdf

cached = find_cached_pdf('{企业名称}')
```

- **有缓存 → 询问用户**："发现本地有 {企业名称} 的募集书缓存（{文件名}），是否使用？"
  - 用户选"是" → `pdf_path = cached` → 跳到 1.5.2
  - 用户选"否" → 继续情况 3
- **无缓存 →** 继续情况 3

**情况 3：让用户选择上传或搜索**

无缓存或用户拒绝缓存时，**询问用户**：

> "请选择募集书来源："
> 选项 A: "**我来上传** — 从本地选择一个 PDF 文件"
> 选项 B: "**在线搜索** — AI 自动搜索并下载最新募集书"

- **用户选 A（上传）** → 弹出系统文件对话框：
  ```python
  from pdf_extractor import ask_pdf_path, cache_pdf
  
  pdf_path = ask_pdf_path()
  if pdf_path:
      pdf_path = cache_pdf(pdf_path, '{企业名称}')
  ```
  → 跳到 1.5.2（如果用户取消对话框，回到情况 3 重新选择）

- **用户选 B（搜索）** → 执行 WebSearch（保留原有四源逐年级联逻辑）：

  **从当年起逐年级联搜索**——先在全部四个数据源搜本年度，搜不到再退一年：

  ```
  第1轮 — 搜 YYYY 年（本年度）：
    "{企业名称} YYYY年 募集说明书 site:sse.com.cn"
    "{企业名称} YYYY年 募集说明书 site:money.finance.sina.com.cn"
    "{企业名称} YYYY年 募集说明书 site:data.eastmoney.com"
    "{企业名称} YYYY年 债券 募集说明书 chinamoney"
  第2轮 — 搜 YYYY-1 年（上一年）：（同上，替换年份）
  第3轮 — 搜 YYYY-2 年：（同上）
  第4轮 — 兜底（不限年份）
  ```

  **关键规则：** 每轮四个数据源都搜完，确认都没有才进入下一轮；找到后立即停止。

  | 数据源 | 优点 | 缺点 |
  |--------|------|------|
  | SSE 上交所 | 官方文件，PDF 直链可下载 | 搜索引擎收录慢 |
  | 新浪财经 | 债券公告聚合页，按日期排序 | 部分页面需二次跳转 |
  | 东方财富 | 公告列表完整，含 PDF 附件下载 | WebFetch 偶有反爬 |
  | chinamoney.com.cn | 银行间债券官方平台 | WebFetch 被安全策略阻止 |

  下载后缓存：
  ```python
  from pdf_extractor import download_pdf, cache_pdf
  pdf_path = download_pdf('{搜索到的PDF URL}', 'sessions/{企业名称}/pdf/')
  if pdf_path:
      cache_pdf(str(pdf_path), '{企业名称}')
  ```

#### 1.5.2 提取数据

以下 4 个子任务必须**全部完成**（每个 ✅ 确认后再做下一个）。无论 PDF 来自哪种情况，提取流程一致。

---

**[ ] 任务 A：缓存 PDF + 定位章节**

```python
from pdf_extractor import cache_pdf, find_section_pages

if not pdf_path:
    print('⚠️ 未获取到 PDF，跳过 PDF 补充')

pdf_path = cache_pdf(str(pdf_path), '{企业名称}')  # ⚠️ 必须缓存！
sections = find_section_pages(pdf_path)
print(f'📄 找到章节: {list(sections.keys())}')
```

---

**[ ] 任务 B：提取资产负债表**

```python
from pdf_extractor import extract_pages_text, map_to_skeleton_columns

if 'balance_sheet' in sections:
    bs_text = extract_pages_text(pdf_path, sections['balance_sheet'])
    # → 你用阅读能力解析 bs_text
    # → 输出 JSON: {"科目名": {"年份列": "数值"}, ...}
    #   如: {"短期借款": {"2023年末": "2,943,294.21", ...}, "长期借款": {...}, ...}
    # → 然后必须年份映射:
    #   skeleton_cols = 从骨架财务表中读取的年份列名列表
    #   bs_data = map_to_skeleton_columns(bs_output, skeleton_cols)
```

**规则：**
- ✅ 只提取骨架中已存在的科目名
- ✅ 跳过页眉/表头/小计/合计行
- ❌ 绝对不要填 `_status=='red'` 的行（一年内到期的应付债券/长期借款）

---

**[ ] 任务 C：提取子公司列表（⚠️ 必须从 PDF，不能用 QCC！）**

```python
if 'subsidiaries' in sections:
    sub_text = extract_pages_text(pdf_path, sections['subsidiaries'])
    # → 你用阅读能力解析 sub_text
    # → 输出 JSON 数组:
    #   [{"子公司名称": "北京首农股份有限公司",
    #     "层级": "二级子公司",
    #     "注册地": "北京",
    #     "国标行业": "现代农牧业",
    #     "业务板块": "现代农牧业",
    #     "持股比例": "45.32",
    #     "实收资本(万元)": "84,000.00",
    #     "备注": "", "_status": "green"}, ...]
```

**规则：**
- ❌ 禁止使用 QCC 的对外投资数据——必须从 PDF 子公司的章节文本解析
- ✅ 每行必须包含：子公司名称、注册地、国标行业、业务板块、持股比例、实收资本
- ✅ 注册地 = PDF 中"主要经营地"列，国标行业 = "业务性质"列
- ✅ 找不到的字段留空 ""

---

**[ ] 任务 D：提取债券明细（可选）**

```python
if 'bonds' in sections:
    bond_text = extract_pages_text(pdf_path, sections['bonds'])
    # → 你用阅读能力解析 bond_text
    # → 输出 JSON 数组:
    #   [{"债券简称": "22首农Y1", "发行主体": "", "发行日期": "2022/11/02",
    #     "到期日期": "2025/11/04", "债券期限": "3+N",
    #     "发行规模(亿元)": "20.00", "票面利率(%)": "2.87",
    #     "余额(亿元)": "20.00", "_status": "green"}, ...]
```

**规则：**
- ✅ 跳过小计/合计行
- ✅ 找不到 → bonds = None（保留骨架占位）

#### 1.5.3 写入骨架

无论数据来自 Python regex 还是 AI 阅读，写入逻辑一致：

提取到的数据自动匹配骨架的年份列，然后用 `column_values` 写入：

```python
from web_filler import batch_fill

if bs_data:
    # 将 PDF 年份列映射到骨架年份列
    results = []
    for item_name, year_values in bs_data.items():
        results.append({
            "field": item_name,
            "column_values": year_values,
            "source_url": str(pdf_path.resolve()),  # PDF 绝对路径（浏览器可打开）
            "source_note": "募集说明书PDF提取"
        })
    filled = batch_fill('{企业名称}', results)
    print(f'✅ PDF 补充 {filled} 个财务科目')

# ⚠️ 子公司必须从 PDF 提取，不能用 QCC 数据！QCC 只有持股比例，缺少注册地/国标行业/业务板块
# 步骤: AI 从 PDF 子公司章节文本解析出 subs 列表 → 直接替换骨架整张表
if subs:
    for ch_val in data['chapters'].get('chapter2', {}).values():
        for tbl in ch_val.get('tables', []):
            if '子公司列表' in tbl.get('title', ''):
                clean_subs = [s for s in subs if '公司' in s.get('子公司名称', '')]
                if clean_subs:
                    tbl['data'] = clean_subs
                    print(f'✅ PDF 子公司: {len(clean_subs)} 家（已替换QCC数据）')
                break
```

```python
# 债券明细写入（替换"公司发行的债券、其他债务融资工具以及偿还情况"表）
from pdf_extractor import extract_bonds

bonds = extract_bonds(pdf_path)
if bonds:
    for ch_val in data['chapters'].get('chapter2', {}).values():
        for tbl in ch_val.get('tables', []):
            if '债券' in tbl.get('title', '') and '融资工具' in tbl.get('title', ''):
                tbl['data'] = bonds
                print(f'✅ PDF 债券: {len(bonds)} 笔')
                break
else:
    print('⚠️ PDF 未找到债券明细章节，保留骨架占位')
```

**注意：**
- `extract_balance_sheet()` 返回的 dict key 是骨架中的 `财务指标` 值，value 是 `{年份列: 数值}` dict
- `extract_subsidiaries()` 返回的 list 中每项含 `子公司名称`、`层级`、`注册地`、`国标行业`、`业务板块`、`持股比例`、`实收资本(万元)`、`备注`，直接对齐骨架表格列名
- `extract_bonds()` 返回的 list 中每项含 `债券简称`、`发行主体`、`发行日期`、`到期日期`、`债券期限`、`发行规模(亿元)`、`票面利率(%)`、`余额(亿元)`
- **必须下载完整版募集书**（50MB+），摘要版不含子公司章节和债券明细

**如果 PDF 搜索失败或提取失败：** 继续执行 Step 2 Web Search——那 6 个科目会保持 🟡 状态，由 Web Search 兜底。

### Step 2: Web Search for 🟡 Fields

Get context and search plans:

```bash
PYTHONIOENCODING=utf-8 python -c "
import sys, json; sys.path.insert(0, 'pipeline')
with open('sessions/{企业名称}/data.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
print(f'公司全称: {data[\"meta\"][\"client_name\"]}')
print(f'所属行业: {data[\"cover\"][\"所属行业\"][\"value\"]}')
"
```

```bash
PYTHONIOENCODING=utf-8 python -c "
import sys; sys.path.insert(0, 'pipeline')
from web_filler import get_search_plans, load_client_data
import json
data = load_client_data('{企业名称}')
plans = get_search_plans(data)
print(json.dumps([{
    'field': p.field_key,
    'table': p.target_table,
    'query': p.queries[0],
    'priority': p.priority_domains,
    'extract': p.extract_hint
} for p in plans], ensure_ascii=False, indent=2))
"
```

For each plan: search using your built-in web search, prioritize `priority` domain links, extract per `extract` hint. Batch-write every 5-10 fields:

```python
from web_filler import batch_fill

# 单列填充（大多数字段）
filled = batch_fill('{企业名称}', [
    {"field": "field_key", "content": "extracted data", "source_url": "https://...", "source_note": "gov.cn"},
    {"field": "field_key2", "content": "经检索未发现公开数据", "source_url": "", "source_note": ""},
])
print(f'✅ 已填充 {filled} 个字段')
```

**⚠️ 财务情况《★》表（多列填充）：** 短期借款/长期借款/应付债券/一年内到期非流动负债/应付票据/应收票据 这 6 个字段需要分别填入三个年份列（上一年-YYYY年 / 近两年-YYYY年 / 前三年-YYYY年），必须使用 `column_values`：

```python
from web_filler import batch_fill

# 财务字段多列填充（从 chinamoney.com.cn 资产负债表 WebFetch 提取精确数值后）
# ⚠️ 列名从左到右升序：最旧年份 → 最新年份
filled = batch_fill('{企业名称}', [
    {"field": "短期借款",
     "column_values": {"2023年": "1,600,000", "2024年": "1,750,000", "2025年": "1,852,345"},
     "source_url": "https://www.chinamoney.com.cn/chinese/cwbg/...", "source_note": "chinamoney.com.cn"},
    {"field": "长期借款",
     "column_values": {"2023年": "2,200,000", "2024年": "2,350,000", "2025年": "2,500,000"},
     "source_url": "https://www.chinamoney.com.cn/chinese/cwbg/...", "source_note": "chinamoney.com.cn"},
    # ... 其余 4 个字段同理
])
print(f'✅ 已填充 {filled} 个财务字段')
```

**财务字段搜索特殊要求：**
1. **必须用 WebFetch** 直接打开 `chinamoney.com.cn` 的年报/审计报告页面（不要只依赖 WebSearch 摘要）
2. **必须提取精确数值**（万元），如 `"1,852,345"`——禁止写文字描述（"约180-200亿"）、禁止估算范围
3. **必须分别提取近三年数据**，每个年份列填入对应值
4. 所有 6 个字段通常在同一份审计报告资产负债表页面中，一个 WebFetch 即可全部获取
5. 填充完成后，`save_client_data()` 会自动重算 `付息负债`

**[ ] 强制任务：高管履历搜索**

对高管信息表中的**每一位**高管，逐一搜索并填入履历：

```python
# 对每位高管执行:
履历 = WebSearch(f"{高管姓名} {企业名称} {职务} 履历")
fill_field(data, 高管姓名, content=履历摘要, source_url=...)
```

**Search rules:**
- Government/official sources first: gov.cn > ndrc.gov.cn > sasac.gov.cn > industry associations > company website > news
- Every filled field MUST have a `source_url` (the actual page you opened)
- **Never fabricate.** If nothing is found, write "经检索未发现公开数据" and leave empty source_url
- Cross-verify critical fields with 2-3 different search queries

### Step 3: Export Reports (must execute, do not skip)

```bash
cd keyclientkit
PYTHONIOENCODING=utf-8 python pipeline/export.py --client {企业名称}
```

If blocked by empty 🟡 fields, use `--force` or return to Step 2.

Generates: `output/{企业名称}_核对表.xlsx` and `output/{企业名称}合作策略_报告.html`.

### Step 4: Excel Readback (after user edits)

```bash
cd keyclientkit
PYTHONIOENCODING=utf-8 python pipeline/export.py --client {企业名称} --readback
```

Merges user's 🔴-field edits from Excel back into JSON via positional key matching, regenerates HTML.

### Step 5: Report to User

```
采集完成：🟢X 🟡X 🔴X（共X行，自动填充率X%）

报告已生成：
  Excel → output/{企业名称}_核对表.xlsx
  HTML  → output/{企业名称}合作策略_报告.html

🟢 数据来自企查查 API
🟡 数据来自 Web 搜索（每条标注了来源 URL）
🔴 需行内填写（银企合作/份额目标/行动计划等）
```

## Troubleshooting

- **OAuth expired**: Run `python oauth_qcc.py status`. Tokens auto-refresh, but if all are dead, re-auth: `python oauth_qcc.py auth`.
- **Module not found (requests/openpyxl)**: `pip install requests openpyxl --only-binary :all:` (avoids Windows ARM64 compilation issues).
- **Excel readback mismatches**: Positional matching breaks if rows were inserted/deleted/reordered. Tell user not to modify column A or add/remove rows.
- **Points exhaustion (exit code 4)**: Hard stop — QCC credits depleted. Do not attempt web search fallback.
- **Extension server auth errors**: Non-fatal. Core report still generates. User can authorize later with `--resource` flag.

<!-- Superpowers skills are installed globally at ~/.claude/skills/ -->
