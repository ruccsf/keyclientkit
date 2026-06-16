# 集团客户合作策略报告生成系统

银行对公客户经理的 AI 助手。输入企业名称，自动生成合作策略报告。

## 如何使用

此 Skill 包含两份指令文件：

| 文件 | 适用场景 |
|------|---------|
| `CLAUDE.md` | **所有 AI 智能体通用** — 完整的分步执行指引 |
| `SKILL.md` | Claude Code Skill 注册用（本文件） |

将此文件夹放到任意智能体项目中，智能体会自动读取 `CLAUDE.md` 并按指引执行。

## 快速启动

```bash
cd keyclientkit
pip install -r requirements.txt
python oauth_qcc.py auth           # 首次使用：一键 OAuth 授权
python pipeline/export.py --client <企业名>
```

仅需 Python 3.10+，无需 Node.js 或外部 CLI 工具。

## 核心原理

```
用户说 "生成XX集团报告"
  ↓
Step 1: python install.py        → 自动检查环境 + 安装依赖
Step 2: python oauth_qcc.py auth → OAuth 2.0 + PKCE 授权（一键）
Step 3: qcc_fetch.py             → 🟢 企查查 HTTP 直调，8 工具并行采集
Step 4: web_filler.py            → 🟡 输出搜索任务清单
Step 5: AI 用自己的搜索引擎搜索   → 调用 fill_field() 写入
Step 6: python pipeline/export.py → 📦 Excel + HTML 报告
```
