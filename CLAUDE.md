# 集团客户合作策略报告生成系统

你是一个银行对公客户经理的 AI 助手。你的任务是根据用户要求，为指定企业集团生成合作策略报告。

## 首次使用：自主安装

当你（AI 智能体）第一次在这个项目中遇到本 Skill 时，运行环境医生：

```bash
cd group-client-strategy
python install.py
```

`install.py` 会**自动完成**：
1. 安装缺失的核心依赖（requests, openpyxl）— 使用 `--only-binary` 避免编译失败
2. 安装核心依赖 (requests + openpyxl)
3. 验证文件完整性
4. 检查 QCC OAuth 授权状态
5. 验证数据管线能正常加载

如果 `install.py` 提示 QCC OAuth 未授权，告诉用户运行 `python oauth_qcc.py auth` 完成授权。

如果 pip 安装报编译错误（常见于 Windows ARM64），install.py 已内置 `--only-binary :all:` 策略，会自动跳过源码编译。如果依然失败，告诉用户手动运行：
```
pip install requests openpyxl --only-binary :all:
```

环境就绪后，进入下面的执行流程。

## 触发条件

当用户说出以下任一类话时，启动本流程：

- "帮我生成 **XX集团** 的合作策略报告"
- "给 **XX公司** 做一份报告"
- "采集 **XX企业** 的数据"
- "分析 **XX集团** 的合作策略"
- "生成 **XX** 的报告"

提取企业名称（如"中粮集团""北京汽车""三元控股"），然后按下方流程执行。

## 执行流程

### 第一步：QCC 数据采集（🟢 公开数据）

```bash
cd group-client-strategy
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

这会自动调用企查查的 12 个 API（工商/股东/高管/财务/投资/上市/分支/控制人/知识产权/风险/经营/高管处罚），生成 39 张表的完整报告骨架。

### 第二步：Web Search 填充（🟡 公开可检索数据）

**重要：用你自己的搜索引擎完成这一步。** 你是哪个智能体，就用哪个智能体的搜索工具（Claude 用 WebSearch，ChatGPT 用 Bing 搜索，Gemini 用 Google 搜索，等等）。

#### 2.1 先了解上下文

从采集结果中获取关键信息：

```bash
PYTHONIOENCODING=utf-8 python -c "
import sys, json; sys.path.insert(0, 'pipeline')
with open('sessions/{企业名称}/data.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
print(f'公司全称: {data[\"meta\"][\"client_name\"]}')
print(f'所属行业: {data[\"cover\"][\"所属行业\"][\"value\"]}')
"
```

#### 2.2 获取搜索计划

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

#### 2.3 逐条搜索并填充

对搜索计划中的每一条：

1. **执行搜索**：用你的搜索引擎搜索 `query` 字段
2. **优先点击** `priority` 中的域名链接
3. **提取数据**：按 `extract` 中的提示，从搜索结果中提取关键信息
4. **写入骨架**：

```python
from web_filler import load_client_data, fill_field, save_client_data
data = load_client_data('{企业名称}')
fill_field(
    data,
    field_key='{field}',         # 搜索计划中的 field
    content='{提取的数据内容}',    # 从搜索结果中提取
    source_url='{实际来源URL}',    # 必须！你打开的页面的真实URL
    source_note='{来源简述}'      # 如："国务院 (gov.cn)"
)
save_client_data('{企业名称}', data)
```

**搜索原则：**
- **政府/官方优先**：gov.cn > ndrc.gov.cn > sasac.gov.cn > 行业协会 > 企业官网 > 新闻
- **必须标注来源**：每条数据填 `source_url`（你实际打开的页面 URL）
- **绝不编造**：如果搜不到有效信息，那条就保持空 🟡，标注"经检索未发现公开数据"
- **多搜几条**：关键信息建议搜索 2-3 次，用不同关键词交叉验证

### 第三步：导出报告

```bash
cd group-client-strategy
PYTHONIOENCODING=utf-8 python pipeline/export.py --client {企业名称}
```

生成两个文件：
- `output/{企业名称}_核对表.xlsx` — 用户打开编辑 🔴 字段
- `output/{企业名称}合作策略_报告.html` — 正式报告

### 第四步：用户编辑后读回 Excel

用户打开 Excel 核对表，填写 🔴 字段，保存。然后运行：

```bash
cd group-client-strategy
PYTHONIOENCODING=utf-8 python pipeline/export.py --client {企业名称} --readback
```

这会：
1. 读取 Excel 中用户修改的内容
2. 合并回 JSON
3. 重新生成 HTML 报告（反映用户修改）

### 第五步：汇报结果

向用户报告数据统计：

```
采集完成：🟢X行 🟡X行 🔴X行（共X行，自动填充率X%）

报告已生成：
  Excel → output/{企业名称}_核对表.xlsx
  HTML  → output/{企业名称}合作策略_报告.html

🟢 数据来自企查查 API
🟡 数据来自 Web 搜索（每条标注了来源 URL）
🔴 需行内填写（银企合作/份额目标/行动计划等）
```

## 三色数据说明

| 颜色 | 含义 | 谁来填 | 怎么填 |
|:---:|------|------|------|
| 🟢 | 公开可获取 | Python 脚本自动 | 企查查 API（工商/财务/高管/投资/风险/知识产权） |
| 🟡 | 半公开需检索 | **你（AI 智能体）** | 用你自己的搜索引擎，政府网站优先 |
| 🔴 | 行内数据 | 客户经理在 Excel 中填写 | 打开 Excel 核对表，编辑 🔴 单元格后保存 |

## 目录结构

```
group-client-strategy/
├── CLAUDE.md              ← 本文件
├── pipeline/export.py       ← 统一导出入口
├── pipeline/
│   ├── qcc_client.py       ← 企查查 MCP 客户端
│   ├── qcc_fetch.py        ← 🟢 数据采集 + 骨架
│   ├── web_filler.py       ← 🟡 搜索计划 + fill_field()
│   ├── excel_renderer.py   ← Excel 导出
│   └── html_renderer.py    ← HTML 导出
├── sessions/               ← 客户数据 JSON
└── output/                 ← 生成的报告
```

## 注意事项

- **首次使用前**需要完成企查查 OAuth 授权：`python oauth_qcc.py auth`
- **QCC 采集失败**时，检查 OAuth token 是否过期：`python oauth_qcc.py test`
- **搜索不到数据**时，保持 🟡 状态并标注"经检索未发现公开数据"，不要编造
- **每家企业生成独立目录**：`sessions/{企业名称}/data.json`
- **Excel 审核**：导出 Excel 后，用户在 Excel 中编辑 🔴 字段，保存后运行 `python pipeline/export.py --client {企业名称} --readback` 合并回 JSON 并重新生成 HTML

## 示例对话

```
用户：帮我生成中粮集团的合作策略报告

AI：
  好的，开始为中粮集团生成报告。
  
  [第一步] QCC 数据采集...
  采集完成：🟢62 🟡53 🔴69（中粮集团，农副食品加工业，资产7216亿元）
  
  [第二步] 搜索 🟡 字段...
  搜索了 28 个字段，填充了 22 个（其余保留空🟡待后续补充）
  - 行业政策 → gov.cn（十部门《促进农产品消费实施方案》）
  - 信用评级 → lhratings.com（联合资信 AAA）  
  - 债券融资 → chinamoney.com.cn
  - ...
  
  [第三步] 导出报告...
  Excel → output/中粮集团_核对表.xlsx
  HTML  → output/中粮集团合作策略_报告.html
  
  📊 数据统计：🟢62 🟡53 🔴69（共184行，自动填充率62%）
  📁 打开文件查看报告
```
