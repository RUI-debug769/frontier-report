---
name: frontier-report
description: 手动触发与排查「AI 前沿日报/周报」邮件系统（frontier-report CLI）。当用户说"跑一下日报/周报"、"手动发一次日报"、"今天没收到日报/周报"、"日报任务挂了/失败了"、"排查 frontier-report"、"升级 frontier-report"时使用；即使用户没提 frontier-report 这个名字，只要语境是 AI 日报/周报邮件的手动触发、排障或计划任务问题，都应该用本 skill。覆盖：doctor 体检、手动 daily/weekly（真实发信，需先确认）、run.log 与 failed-output 排障、计划任务状态检查、样例渲染、生产版升级。
---

# frontier-report 运维薄封装

frontier-report 是一个已安装的 CLI：headless Claude 深研 → JSON 校验 →
暖陶暗色 HTML → SMTP 发信。本 skill 不重新实现任何链路，只负责
**用对命令、看对日志、说清结论**。所有逻辑细节以仓库 README 为准。

## 调用形态（按此顺序选择）

1. `python -m frontier_report <args>` —— **首选**。任何 pip 安装环境都可用，
   不依赖 exe 在 PATH 中（本机系统 Python 的 Scripts 目录就不在 PATH）。
2. `frontier-report <args>` —— 仅当 `where.exe frontier-report`（Windows）或
   `which frontier-report`（Linux/macOS）能找到时。
3. 上述都失败说明未安装，指引用户 `pip install git+https://github.com/RUI-debug769/frontier-report.git`。

下文统一写 `frontier-report`，实际执行时替换为选定形态。

## 子命令速查

| 命令 | 作用 | 副作用 |
|------|------|--------|
| `frontier-report doctor` | 体检：claude CLI / SMTP / 数据目录 | 无（只读 + 建目录） |
| `frontier-report sample --out <目录>` | 渲染样例 html/txt | 无（不发信、不调模型） |
| `frontier-report daily` | 生成并**真实投递**当日日报 | 调模型 4–6 分钟 + 发邮件 |
| `frontier-report weekly` | 聚合最近 7 份日报，生成并**真实投递**周报 | 调模型 5–8 分钟 + 发邮件 |

退出码：`0` 成功（已存档并发信）；`1` 生成或 JSON 校验失败（**不发信**，
原始输出存 `failed-output-*.txt`）；`2` 发信或环境配置错误（存档保留）。

## 手动触发日报/周报（先确认，再执行）

`daily` / `weekly` 会真实调模型并真实发邮件，执行前必须向用户确认两点：

1. **确认要真发**——收件人会收到邮件；试跑建议加
   `--data-dir <临时目录>`，邮件照发但不污染正式存档
   （周报期号 = 存档文件数 + 1，污染存档会导致期号虚增）。
2. **避开计划任务时间窗**——日报计划任务 08:00、周报周六 08:30，
   前后 15 分钟内不要手动跑，否则同日重复发信；另外同一天重跑前，
   正式数据目录里当日的旧存档要先移走（详见 README「重跑」说明）。

执行时用较长的超时（日报 10 分钟、周报 15 分钟）或后台运行，
结束后核对退出码并查看 `<数据目录>/<daily|weekly>/run.log` 尾部确认。

## 排障决策树（"没收到邮件 / 任务失败"）

按顺序排查，每步都有明确判据：

1. **计划任务是否运行过**（Windows）：
   `powershell -NoProfile -Command "Get-ScheduledTaskInfo -TaskName Frontier-Report-Daily,Frontier-Report-Weekly | Select TaskName,LastRunTime,LastTaskResult"`
   - 任务名：`Frontier-Report-Daily` / `Frontier-Report-Weekly`。
     旧任务 `AI-Daily-Report` / `AI-Weekly-Report` 已停用（Disabled 属正常，
     不要重新启用——那是旧系统，启用会双重发信）。
   - `LastTaskResult = 0` → 任务跑过且成功，问题在收件端（垃圾邮件箱？）。
   - `0x800710E0` → 电源条件不满足（笔记本合盖/电池），错过会补跑，非故障。
   - 其他非 0 → 进入第 2 步。
2. **看日志**：`<数据目录>/<daily|weekly>/run.log` 最后 20 行
   （默认数据目录 `~/.frontier-report`）。日志含每步状态与退出码语义。
3. **退出码 1（生成失败）**：看同目录 `failed-output-<日期>.txt` 里
   模型的原始输出，对照日志中「最后一次校验」的原因——通常是模型没按
   JSON 契约输出，属偶发，重跑一次或次日自愈；连续失败才需要调 prompt。
4. **退出码 2（发信/环境）**：跑 `frontier-report doctor`，
   按输出提示修复（SMTP 凭据缺失/授权码过期/claude CLI 未登录）。
5. **claude CLI 本身**：doctor 会显示版本；若调用异常，
   先确认 `claude` 已登录且额度未耗尽。

排查结论用「退出码 + 日志关键行 + 建议动作」三段式汇报，不要只说"失败了"。

## 生产版升级（改了仓库代码之后）

生产运行的是**系统 Python 的非 editable 安装**，改完仓库代码必须重装
才生效（开发迭代用仓库内 `.venv`，editable 改即生效）：

```bash
python -m pip install F:\project\frontier-report
python -m unittest discover -s F:\project\frontier-report\tests
```

仓库位置：`F:\project\frontier-report`（GitHub: RUI-debug769/frontier-report）。
升级前跑测试（116 用例），全绿再重装。

## 边界（不要做的事）

- 不要修改 `C:\Users\26404\.claude\` 下的旧脚本与 `F:\project\Frontier Daily Weekly Report\`
  只读副本——旧系统已停用但保留作回滚。
- 不要启用 `AI-Daily-Report` / `AI-Weekly-Report` 旧任务（回滚保险，启用=双发）。
- 不要在未确认的情况下执行 `daily` / `weekly`（真实发信，不可撤回）。
- 排障只读日志与状态，不要直接编辑 `run.log` 或存档文件。
