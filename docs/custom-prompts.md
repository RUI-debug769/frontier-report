# 自定义 prompt：把「AI 前沿」换成任何你想追踪的主题

frontier-report 的管线（校验 → 渲染 → 投递）与 prompt 完全解耦。
日报/周报各自只是「一段 prompt + 一个 JSON 契约」，换掉 prompt、
（可选）同步调整校验器，就能追踪任何主题。

## 三层 prompt 来源（优先级从高到低）

1. `config.toml` 的 `daily_prompt` / `weekly_prompt` 指向任意路径
2. 数据目录下的 `prompt.md`：`<数据目录>/daily/prompt.md`、`<数据目录>/weekly/prompt.md`
3. 包内置 prompt（`src/frontier_report/prompts/`）

把自定义 prompt 放进数据目录即可生效，无需改代码。

## 周报模板的两个占位符

周报 prompt 是**模板**，运行时会注入聚合的日报素材：

| 占位符 | 注入内容 |
|--------|----------|
| `<<DIGEST_NOTE>>` | 聚合说明（本周聚合到几份日报、日期列表；无日报时的降级说明） |
| `<<DAILY_DIGEST>>` | 各日报全文，按日期倒序，`---` 分隔 |

自定义周报主题时保留这两个占位符，即可继续享受日报聚合。

## JSON 契约必须同步

prompt 里声明的输出结构由校验器强制执行，二者必须一致：

| 报告 | 校验器 | 关键约束 |
|------|--------|----------|
| 日报 | `daily.py:_validate_daily` | `briefs` 3–5 条；`sections` 固定 4 个且 id 顺序锁定（people/companies/events/policy），items 各 0–12 条；`deep_reads` 1–2 条、url 限 http(s) |
| 周报 | `weekly.py:_validate_weekly` | 1 条 `headline` + `items` 5–7 条（concept/idea/path，path ≤2 且与总数联动）+ `signal.points` 3–5 条 |

换主题时有两条路：

- **轻改**：保持 JSON 结构不变，只改 prompt 里的追踪范围与栏目语义
  （渲染层按 id 映射栏目标题，日报栏目标题在 `daily.py:SECTION_TITLES`）。
- **重构**：同步修改校验器、渲染器与对应测试。校验失败的行为契约
  （exit 1、不发信、原始输出存 `failed-output-*.txt`）不依赖具体 schema，
  可放心改。

## 示例

`examples/prompts/employment-weekly.md` 是本项目周报的前身——
「就业与社会范式周报」的完整 prompt，演示了另一种主题下
占位符与 JSON 契约的配合方式，可作为改写起点。

## 调试技巧

- 校验失败时模型原始输出保存在 `<数据目录>/<daily|weekly>/failed-output-<日期>.txt`，
  对照它调整 prompt 的格式约束措辞。
- 模型偶发输出 Python repr（单引号）而非 JSON、或在字符串里用裸双引号——
  提取器（`common.extract_json`）已内建三级回退容忍，通常无需处理；
  若连续失败，在 prompt 里加强「只输出一个 JSON 对象」的措辞比重写代码有效。
- 用 `frontier-report sample` 随时验证渲染改动，不消耗模型调用。
