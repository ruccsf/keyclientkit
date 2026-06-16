# 集团客户合作策略报告生成系统

为银行对公客户经理打造的**企业合作策略报告自动生成工具**。输入企业名称，自动采集企查查数据 + 公开信息搜索，生成标准化 Excel 核对表和 HTML 正式报告。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 环境检查
python install.py

# 3. 首次使用需 OAuth 授权（一键命令，自动打开浏览器）
python oauth_qcc.py auth

# 4. 生成报告
python pipeline/export.py --client <企业名称>
```

仅需 Python 3.10+，无需 Node.js 或任何外部 CLI 工具。

## 功能概览

| 功能 | 说明 |
|------|------|
| 🔍 数据采集 | QCC 8 个核心工具自动采集（工商/股东/高管/财务/投资/上市/分支/控制人），生成 39 张表骨架 |
| 📝 Web 填充 | AI 智能体搜索 🟡 字段（行业政策/债券/评级/新闻），标注来源 URL |
| 📦 报告导出 | 一键生成 Excel 核对表（9 sheets）+ HTML 正式报告（公文格式） |
| 🔄 Excel 回读 | 用户编辑 🔴 字段后，回读合并 → 重生成 HTML |

## 三色数据体系

| 颜色 | 含义 | 数据来源 | 自动化程度 |
|:---:|------|---------|:---:|
| 🟢 | 公开可获取 | 企查查 API（工商/财务/高管/投资/风险/知识产权） | ✅ 全自动 |
| 🟡 | 半公开需分析 | Web Search（行业分析/政策/债券/新闻动态） | ✅ AI 全自动 |
| 🔴 | 银行内部数据 | 行内系统（银企合作/份额目标/行动计划/审核） | 📝 客户经理填写 |

## 目录结构

```
group-client-strategy/
├── README.md              ← 本文件
├── CLAUDE.md              ← AI 智能体执行指引
├── requirements.txt       ← Python 依赖（仅 requests + openpyxl）
├── install.py             ← 一键安装检查
├── oauth_qcc.py           ← OAuth 2.0 + PKCE 授权（python oauth_qcc.py auth）
├── config.json            ← OAuth 配置（含 token，.gitignore 已排除）
├── pipeline/              ← 数据管线
│   ├── qcc_client.py      ← 企查查 MCP 客户端（HTTP 直调，纯 Python）
│   ├── qcc_fetch.py       ← 数据采集编排 + 39 表骨架
│   ├── web_filler.py      ← Web Search 搜索编排器（28 个搜索计划）
│   ├── excel_renderer.py  ← Excel 导出（多 Sheet，三色底色）
│   ├── excel_reader.py    ← Excel 回读合并
│   ├── html_renderer.py   ← HTML 报告渲染（公文格式）
│   └── export.py          ← 统一 CLI 入口
├── sessions/              ← 客户数据（JSON，按企业分目录）
└── output/                ← 生成的报告（Excel + HTML）
```

## AI 智能体模式

本项目封装为 **AI 智能体 Skill**，可被任何 AI 智能体（Claude Code、WorkBuddy 等）引用。Skill 读取 `CLAUDE.md` 中的执行指引，自动完成：

1. 调用企查查 API 采集 🟢 数据
2. 使用内置搜索引擎填充 🟡 字段（政府网站优先，标注来源 URL）
3. 生成 Excel + HTML 报告

使用时：将整个 `group-client-strategy/` 文件夹复制到你的智能体项目中，AI 会自动读取 `CLAUDE.md` 并按指引执行。

## OAuth 授权

本项目通过 OAuth 2.0 + PKCE 调用企查查 MCP API。

```bash
# 一键授权（自动打开浏览器 + 本地回调服务器）
python oauth_qcc.py auth

# 如浏览器无法自动打开，用手动模式
python oauth_qcc.py auth --manual

# 查看 token 状态
python oauth_qcc.py status

# 测试 API 连通性
python oauth_qcc.py test
```

每个 QCC MCP 服务器（company/ipr/risk/operation/executive）需要独立授权。company 服务器覆盖 80% 数据，扩展服务器按需授权：

```bash
python oauth_qcc.py auth --resource ipr
python oauth_qcc.py auth --resource risk
```

Token 过期时系统自动刷新，无需手动操作。

## 数据来源说明

| 类别 | 来源 | 示例 |
|------|------|------|
| 企查查 API | agent.qcc.com | 工商登记、财务数据、股东高管、投资信息 |
| 政府网站 | gov.cn, ndrc.gov.cn, sasac.gov.cn | 行业政策、央企信息、重大项目 |
| 金融数据 | chinamoney.com.cn, shclearing.com.cn, lhratings.com | 债券发行、融资规模、信用评级 |
| 企业官网 | 各集团官网 | 发展战略、经营动态、新闻公告 |
| 行内系统 | 银行内部 | 授信额度、存款规模、合作详情 |

🟡 字段每条数据均标注来源 URL，可点击验证。

## 许可

内部使用。
