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
