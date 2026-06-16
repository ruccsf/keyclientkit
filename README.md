# 集团客户合作策略报告生成系统

为银行对公客户经理打造的**企业合作策略报告自动生成工具**。输入企业名称，自动采集企查查数据 + 公开信息搜索，生成标准化 Excel 核对表和 HTML 正式报告。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 检查环境
python install.py

# 3. 启动应用
streamlit run app.py --server.port 8501
```

首次使用需完成**企查查 OAuth 授权**：点击页面上的"连接企查查"按钮，或运行 `python oauth_qcc.py auth`。

## 功能概览

| 页面 | 功能 |
|------|------|
| 🔍 采集数据 | QCC 自动采集 8 项核心数据 + 4 项扩展数据，生成完整报告骨架 |
| 📝 审核数据 | 逐表复核修改，🟢🟡🔴 三色标注，支持手工填写 🔴 行内字段 |
| 📦 导出报告 | 一键生成 Excel 核对表（9 sheets）+ HTML 正式报告 |

## 三色数据体系

| 颜色 | 含义 | 数据来源 | 自动化程度 |
|:---:|------|---------|:---:|
| 🟢 | 公开可获取 | 企查查 API（工商/财务/高管/投资/风险/知识产权） | ✅ 全自动 |
| 🟡 | 半公开需分析 | Web Search（行业分析/政策/债券/新闻动态） | ✅ Skill 模式下全自动 |
| 🔴 | 银行内部数据 | 行内系统（银企合作/份额目标/行动计划/审核） | 📝 客户经理填写 |

## 目录结构

```
group-client-strategy/
├── README.md              ← 本文件
├── SKILL.md               ← Claude Code Skill 指令
├── requirements.txt        ← Python 依赖
├── install.py              ← 一键安装检查
├── app.py                  ← Streamlit 主应用
├── oauth_qcc.py            ← 企查查 OAuth 2.0 + PKCE 授权
├── config.json             ← OAuth 配置（含 token，勿提交 git）
├── pipeline/               ← 数据管线
│   ├── qcc_client.py       ← 企查查 MCP 客户端（6 个服务器）
│   ├── qcc_fetch.py        ← 数据采集编排 + 39 表骨架
│   ├── web_filler.py       ← Web Search 搜索编排器（28 个搜索计划）
│   ├── excel_renderer.py   ← Excel 导出
│   └── html_renderer.py    ← HTML 报告渲染（公文格式）
├── sessions/               ← 客户数据（JSON，按企业分目录）
└── output/                 ← 生成的报告（Excel + HTML）
```

## Claude Code Skill 模式

本项目也封装为 Claude Code Skill。安装后可在 Claude Code 中一键生成报告：

```
/group-client-strategy 中粮集团
```

Skill 模式下，Claude 会自动：
1. 调用企查查 MCP 采集 🟢 数据
2. 使用内置 WebSearch + WebFetch 填充 🟡 字段（政府网站优先，标注来源 URL）
3. 生成完整报告骨架

### 安装 Skill

将 `SKILL.md` 复制到项目的 `.claude/skills/group-client-strategy/` 目录（或 Claude Code 全局 skills 目录）。

## 企查查配置

本项目依赖以下企查查 MCP 服务器（通过 mcporter）：

| 服务器 | 用途 |
|--------|------|
| qcc-company | 工商/股东/高管/财务/投资/上市/分支/控制人 |
| qcc-ipr | 知识产权（APP/特许经营/著作权） |
| qcc-risk | 风险信息（行政处罚/破产重整） |
| qcc-operation | 经营动态（行政许可/广告审查） |
| qcc-executive | 高管信息（个人处罚/受益所有人） |

注册方式：
```bash
mcporter config add qcc-company https://agent.qcc.com/mcp/company/stream
mcporter config add qcc-ipr https://agent.qcc.com/mcp/ipr/stream
mcporter config add qcc-risk https://agent.qcc.com/mcp/risk/stream
mcporter config add qcc-operation https://agent.qcc.com/mcp/operation/stream
mcporter config add qcc-executive https://agent.qcc.com/mcp/executive/stream

mcporter auth qcc-company  # 按提示完成各服务器授权
```

## 数据来源说明

| 类别 | 来源 | 示例 |
|------|------|------|
| 企查查 API | agent.qcc.com | 工商登记、财务数据、股东高管、投资信息 |
| 政府网站 | gov.cn, ndrc.gov.cn, sasac.gov.cn | 行业政策、央企信息、重大项目 |
| 金融数据 | chinamoney.com.cn, shclearing.com.cn, lhratings.com | 债券发行、融资规模、信用评级 |
| 企业官网 | 各集团官网 | 发展战略、经营动态、新闻公告 |
| 行内系统 | 银行内部 | 授信额度、存款规模、合作详情 |

🟡 字段每条数据均标注来源 URL，可点击验证。

## 示例报告

`sessions/中粮集团/` 包含一份完整的示例报告数据。

## 许可

内部使用。
