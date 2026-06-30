# 阶段总结：keyclientkit v2.5

**日期：** 2026-06-29  
**基线：** `21da812` → **当前：** `9936786`  
**改动：** 9 文件，+1,437 / -74 行，8 个 commits

---

## 一、已完成的改进

### 1. 财务表重构（`2eeb66d`）
- 列头从 `上一年-2025年` → 纯年份 `2023年 / 2024年 / 2025年`，从左到右升序
- 表格上方显示"单位：万元"
- 数字/百分比单元格自动右对齐（HTML + Excel）
- 导出对话框前置到文件生成前，只弹一次

### 2. 多列填充机制（`2eeb66d`）
- `fill_field()` 新增 `column_values` 参数，支持财务表多列同时写入
- `batch_fill()` 同步适配
- `recompute_derived_fields()`：付息负债自动重算（save 时自动调用）

### 3. PDF 募集说明书提取（`b90da84`）
- 新增 `pipeline/pdf_extractor.py`（~565 行）
- `download_pdf()`：下载 SSE/chinamoney PDF
- `extract_balance_sheet()`：pypdfium2 提取合并资产负债表（61 科目验证通过）
- `extract_financial_data()`：BS + 利润表联合提取
- `map_to_skeleton_columns()`：年份列智能映射
- `extract_subsidiaries()`：提取二级子公司列表（28/30 验证通过）
- 数据优先级：**PDF（完整审计） > QCC（不稳定） > Web Search（兜底）**

### 4. 搜索策略完善（`a7b50b5` / `c64d5b7`）
- 四数据源并行搜索：SSE + 新浪财经 + 东方财富 + chinamoney
- 逐年级联策略：当年 → 去年 → 前年，每轮四源全搜再退一年

### 5. QCC 数据修复（`306d817` / `4dd86c2` / `9936786`）
- 经营情况表营收/净利润显示三年数据（不再只取最近一年）
- 子公司列表不限条数，子公司优先排列

### 6. 搜索计划强化（web_filler.py）
- 6 个财务字段 extract_hint 重写：强制 WebFetch + 精确数值 + 禁止估算
- `get_search_tasks` 内置指引更新

---

## 二、当前数据流

```
Step 1:   QCC MCP 采集（8核+4扩展） → 🟢 工商/股东/高管/投资...
Step 1.5: WebSearch 搜索 PDF → download_pdf()
         → extract_balance_sheet() [pypdfium2]
         → extract_subsidiaries()
         → batch_fill(column_values=...) → 🟢 财务科目 + 子公司
Step 2:   WebSearch → 🟡 行业分析/供应链/债券/政策...
Step 3:   export.py → HTML + Excel
```

---

## 三、关键文件

| 文件 | 行数 | 职责 |
|------|------|------|
| `pipeline/pdf_extractor.py` | 565 | PDF 下载/提取/映射/标准化 |
| `pipeline/web_filler.py` | ~500 | 搜索计划 + fill_field/batch_fill/recompute |
| `pipeline/qcc_fetch.py` | ~1600 | QCC 采集编排 + 骨架 + 映射 |
| `pipeline/export.py` | ~300 | 导出 CLI + 黄底守卫 + 对话框 |
| `CLAUDE.md` | ~280 | AI 代理执行指引 |

---

## 四、已知局限

| 问题 | 状态 |
|------|:--:|
| 募集书需完整版（摘要版无子公司章节） | 需用户注意 |
| chinamoney.com.cn 被 WebFetch 阻止 | 改用 SSE/新浪/东方财富 |
| 不同 PDF 排版差异，子公司提取偶有漏网 | 28/30 命中率可接受 |
| PDF 需手动搜索，无法全自动 | **下阶段解决：支持上传** |
| 年份映射：PDF 数据可能比 QCC 旧 1-2 年 | 需多份 PDF 拼接 |

---

## 五、版本记录

| Tag | Commit | 说明 |
|-----|--------|------|
| v2.3 | `2eeb66d` | 财务表优化 |
| v2.4 | `b90da84` | PDF 提取模块 |
| v2.5 | `9936786` | 子公司提取 + 搜索策略完善 |
