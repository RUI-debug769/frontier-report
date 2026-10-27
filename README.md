# frontier-report

**AI 前沿日报 / 周报全自动生成与投递**：headless Claude 深研当日（当周）AI 前沿 →
严格 JSON 契约校验 → 暖陶暗色 HTML（+ 纯文本 alternative）→ SMTP 邮件直投。

纯 Python 标准库，**零第三方依赖**。Windows / Linux / macOS 均可运行。

[English](README.en.md)

![tests](https://github.com/RUI-debug769/frontier-report/actions/workflows/test.yml/badge.svg)
![python](https://img.shields.io/badge/python-%3E%3D3.11-blue)
![license](https://img.shields.io/badge/license-MIT-green)

---

## 效果

- **日报**：今日速览（3–5 条）+ 四栏目（领军人物 / 公司与产业链 / 重大事件 /
  政策与监管）+ **热门 Paper 技术**（2–4 篇，标注热门应用/理论创新）+
  **每日一概念**（AI/数学/CS 概念精讲，按半衰期模型间隔复习）+ 值得深读，
  约 5 分钟扫读
- **周报**：聚合最近 7 份日报做跨日综合，1 条头条 + 5–7 张概念/想法/路径卡片
  + 3–5 条信号，周末 5–15 分钟深读

### 间隔重复学习机（每日一概念）

思想提炼自墨墨背单词团队的 KDD 2022 间隔重复调度论文：每个概念记录一个
**记忆半衰期** h，间隔 Δ 后回忆概率约 2^(-Δ/h)；当 Δ ≥ h（即将遗忘）时
该概念自动排入当日报纸，以「回忆提示 + 精要重述」复推，每次复推后半衰期
×2.3——间隔自然拉宽（1 → 2.3 → 5.3 → 12 → 28 → 64 天）。调度由
`concepts.py` 确定性地完成（状态存 `<数据目录>/daily/concepts.json`），
模型只负责生成文本；投递失败不消耗复习、不登记新概念。

渲染样例见 [`examples/sample.html`](examples/sample.html)（暖陶暗色、640px、
table 布局、全内联 CSS，主流邮件客户端兼容）与
[`examples/sample.txt`](examples/sample.txt)。本地随时可生成最新样例：

```bash
frontier-report sample --out ./my-samples   # 不发信、不调模型
```

## 架构

```
cron / systemd timer / Windows 计划任务
                ↓
     frontier-report <daily|weekly>
                ↓
  构造 prompt（注入日期 / 聚合最近 7 份日报）
                ↓
   headless `claude -p`（WebSearch / WebFetch 深研）
                ↓
        模型输出 JSON（容忍 repr / 围栏 / 遥测噪声的三级提取）
                ↓
        严格 schema 校验（失败 exit 1，不发信）
                ↓
  渲染纯文本 + 暖陶暗色 HTML（table 布局 / 全内联 CSS / 640px）
                ↓
   存档 .md → SMTP STARTTLS 投递（重试 3 次，退避 15s/45s）
```

## 快速开始

前置条件：

- Python ≥ 3.11
- 本机可用的 [Claude Code CLI](https://claude.com/claude-code)（`claude`，已完成登录）
- 一个 SMTP 账号（QQ 邮箱需用「授权码」而非登录密码）

```bash
# 安装（二选一）
pip install git+https://github.com/RUI-debug769/frontier-report.git
# 或
git clone https://github.com/RUI-debug769/frontier-report.git
cd frontier-report && pip install -e .

# 体检：claude CLI / SMTP 配置 / 数据目录一次看清
frontier-report doctor

# 手动跑一次（约 4-6 分钟，会真实发信）
frontier-report daily
frontier-report weekly
```

## 配置

优先级：**CLI 参数 > 环境变量 > config.toml > 默认值**。

| 配置项 | 环境变量 | config.toml | 默认 |
|--------|----------|-------------|------|
| 数据目录 | `FRONTIER_REPORT_DATA_DIR` | `data_dir` | `~/.frontier-report` |
| claude CLI 路径 | `FRONTIER_CLAUDE_EXE` | `claude_exe` | PATH 自动发现 |
| 配置文件路径 | `FRONTIER_REPORT_CONFIG` | — | 见下 |
| SMTP 主机 | `SMTP_HOST` | `[smtp] host` | `smtp.qq.com` |
| SMTP 端口 | `SMTP_PORT` | `[smtp] port` | `587` |
| 发信账号 | `SMTP_USER` | `[smtp] user` | **必填** |
| SMTP 密码/授权码 | `SMTP_PASS` | `[smtp] pass` | **必填** |
| 收件地址 | `REPORT_TO_EMAIL` | `[smtp] to_email` | **必填** |
| 日报/周报超时（秒） | — | `daily_timeout` / `weekly_timeout` | 1800 / 1500 |
| prompt 覆盖 | — | `daily_prompt` / `weekly_prompt` | 包内置 |

config.toml 搜索顺序：`--config` > `$FRONTIER_REPORT_CONFIG` > `./config.toml` >
`<数据目录>/config.toml`。完整注释示例：[`config.example.toml`](config.example.toml)。

Windows 下可用 `setx SMTP_USER ...` 持久化环境变量（程序会回退读取
`HKCU\Environment`）；cron 等极简环境建议直接写 config.toml 的 `[smtp]` 段。

数据目录布局：

```
~/.frontier-report/
├── daily/    # ai-daily-report-YYYY-MM-DD.md、concepts.json（概念库）、
              # run.log、failed-output-*.txt、prompt.md（可选覆盖）
└── weekly/   # ai-weekly-ideas-YYYY-MM-DD.md、run.log、failed-output-*.txt、prompt.md（可选覆盖）
```

## 定时调度（三选一）

| 平台 | 方式 | 示例文件 |
|------|------|----------|
| Linux | cron | [`examples/crontab.example`](examples/crontab.example) |
| Linux | systemd timer | [`examples/systemd/`](examples/systemd)（`Persistent=true` 错过补跑） |
| Windows | 计划任务 | [`examples/Register-FrontierReportTasks.ps1`](examples/Register-FrontierReportTasks.ps1)（管理员运行，电池可启动/错过补跑） |

建议节奏：日报每天 08:00；周报每周六 08:30（聚合前 7 天日报）。

## Claude Code skill（可选）

[`skill/frontier-report/SKILL.md`](skill/frontier-report/SKILL.md) 是本项目的
Claude Code 薄封装：在 Claude 会话里说「跑一下日报」「今天没收到日报，排查一下」
即可触发——它会选择正确的调用形态、按决策树查计划任务与日志、并在真实发信前
先向你确认。安装：

```bash
# Windows
xcopy /E /I skill\frontier-report %USERPROFILE%\.claude\skills\frontier-report
# Linux / macOS
cp -r skill/frontier-report ~/.claude/skills/frontier-report
```

## 退出码

| 码 | 含义 |
|----|------|
| 0 | 成功（已存档并发信） |
| 1 | 生成或 JSON 校验失败（**不发信**，原始输出存 `failed-output-*.txt`） |
| 2 | 发信失败或环境配置错误（存档保留） |

## JSON 契约

- **日报**：`briefs` 3–5 条；`sections` 固定 4 个且 id 顺序锁定
  （people / companies / events / policy），每栏目 items 0–12 条；
  companies 必标分层（基础层 / 技术层 / 应用层）；
  `papers` 2–4 条（kind 限 hot/theory，url 限 http(s)）；
  `concept_new` 必填（name / one_liner / explanation，news_hook 可空）；
  `concept_reviews` 0–3 条（name 必须与当日注入的复习清单匹配）；
  `deep_reads` 1–2 条，url 限 http(s)。
- **周报**：1 条头条 + 5–7 张卡片（concept / idea / path，概念与想法 ≥70%），
  附 3–5 条信号。

想追踪别的主题？管线与 prompt 完全解耦，见
[docs/custom-prompts.md](docs/custom-prompts.md)。

## 排障备忘

- **计划任务 `LastTaskResult = 0x800710E0`**：电源条件不满足（非网络错误），
  笔记本需放开「电池可启动 / 切换不中断 / 错过补跑」——附带的注册脚本已默认开启。
- claude CLI 的模型目录警告与遥测 JSON 走 **stderr**，本工具 stdout/stderr 分流处理，
  不会污染 JSON 提取。
- 模型偶发输出 **Python repr**（单引号）而非 JSON、漏写闭合括号、或字符串内裸双引号：
  提取器内建三级回退（JSON 游标扫描 → 逐行字面量 → 整段字面量），均能容忍。
- 发信重试以 `sendmail` 是否成功完成为界：成功后的断连/quit 异常**不重试**
  （防重复发信），日志记 warning。

## 开发

```bash
pip install -e .
python -m unittest discover -s tests -v   # 100+ 用例，纯标准库 unittest
```

测试覆盖：JSON 三级提取与 schema 校验、XSS 转义、发信重试边界、
日报聚合、退出码契约（失败绝不发信）、prompt 三级来源。

## License

[MIT](LICENSE)
