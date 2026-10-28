#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 技术与思路周报（每周）链路。

流程：聚合最近 7 份日报 -> 拼入 prompt -> claude 增量深研 -> 严格 JSON ->
渲染纯文本 + 暖陶暗色 HTML -> SMTP（重试 3 次）。运行时与主题来自
common / theme。纯 Python 标准库。

路径解析同 daily：模块级 DAILY_DIR / WEEKLY_DIR 默认为 None，运行时经
_daily_dir() / _weekly_dir() 解析（模块全局优先，其次 config）。
"""
import glob
import os
import time
from datetime import date, timedelta
from importlib import resources

from . import common as rc
from . import config as _config
from . import degrade
from . import fetch_sources
from . import theme as wt

DAILY_DIR = None   # None -> config 的 <data_dir>/daily；测试可 patch
WEEKLY_DIR = None  # None -> config 的 <data_dir>/weekly；测试可 patch

# kind -> （中文名, 标签底色, 标签字色）——周报专有分色
KIND_STYLE = {
    "concept": ("概念", "#3D2C23", "#E09A82"),
    "idea": ("想法", "#3A3226", "#D9B98A"),
    "path": ("实现路径", "#3D3934", "#B9B2A6"),
}


class WeeklyReportError(Exception):
    """周报链路可预期错误。"""


def _noop_log(msg):
    pass


log = _noop_log  # cli 启动时绑定 make_logger；测试可重赋

# 共享运行时 re-export（test_render / 外部按 weekly.get_env 引用）
get_env = rc.get_env


def _daily_dir():
    if DAILY_DIR is not None:
        return DAILY_DIR
    return _config.get_config().daily_dir


def _weekly_dir():
    if WEEKLY_DIR is not None:
        return WEEKLY_DIR
    return _config.get_config().weekly_dir


def _load_weekly_prompt_template():
    """周报 prompt 模板三级来源：config.weekly_prompt 覆盖 > 数据目录
    prompt.md > 包内置 prompts/weekly.md。全部不可用返回 None。"""
    cfg = _config.get_config()
    if cfg.weekly_prompt:
        try:
            with open(cfg.weekly_prompt, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None
    user_prompt = os.path.join(_weekly_dir(), "prompt.md")
    if os.path.exists(user_prompt):
        with open(user_prompt, encoding="utf-8") as f:
            return f.read()
    try:
        return (resources.files("frontier_report.prompts")
                .joinpath("weekly.md").read_text(encoding="utf-8"))
    except (OSError, FileNotFoundError, ModuleNotFoundError):
        return None


def collect_daily_reports(today=None, daily_dir=None, count=7, min_size=200):
    """聚合最近 count 天日报，返回 (digests, missing)。
    digests: [(date_str, content)] 倒序，仅含 >=min_size 且 UTF-8 可读文件；
    missing: 缺失日期，损坏标 (损坏)。daily_dir 为 None 时取 config 的日报目录。"""
    daily_dir = daily_dir if daily_dir is not None else _daily_dir()
    d = today or date.today()
    digests, missing = [], []
    for i in range(count):
        day = d - timedelta(days=i)
        ds = day.strftime("%Y-%m-%d")
        path = os.path.join(daily_dir, "ai-daily-report-%s.md" % ds)
        try:
            if os.path.getsize(path) < min_size:
                missing.append(ds + "(损坏)")
                continue
            with open(path, encoding="utf-8") as f:
                digests.append((ds, f.read()))
        except UnicodeDecodeError:
            missing.append(ds + "(损坏)")
        except OSError:
            missing.append(ds)
    return digests, missing


def _req_str(obj, key, label, allow_empty=False):
    """取字符串字段：必须是 str，strip 后默认非空。"""
    v = obj.get(key)
    if not isinstance(v, str):
        raise WeeklyReportError("%s 必须是字符串" % label)
    v = v.strip()
    if not allow_empty and not v:
        raise WeeklyReportError("%s 不可为空" % label)
    return v


def _validate_weekly(data):
    """周报严格 schema，返回规范化 dict。"""
    if not isinstance(data, dict):
        raise WeeklyReportError("顶层不是 JSON 对象")

    hl = data.get("headline")
    if not isinstance(hl, dict):
        raise WeeklyReportError("headline 缺失或不是对象")
    hl_title = _req_str(hl, "title", "headline.title")
    hl_summary = _req_str(hl, "summary", "headline.summary")

    items = data.get("items")
    if not isinstance(items, list) or not (5 <= len(items) <= 7):
        raise WeeklyReportError("items 必须为 5-7 条")

    norm_items = []
    n_path = 0
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise WeeklyReportError("items[%d] 不是对象" % i)
        kind = it.get("kind")
        if kind not in KIND_STYLE:
            raise WeeklyReportError("items[%d].kind 非法: %r" % (i, kind))
        title = _req_str(it, "title", "items[%d].title" % i)
        what = _req_str(it, "what", "items[%d].what" % i)
        why = _req_str(it, "why", "items[%d].why" % i, allow_empty=True)
        if kind == "path":
            n_path += 1
        elif not why:
            raise WeeklyReportError("items[%d] concept/idea 的 why 不可为空" % i)
        norm_items.append({"kind": kind, "title": title, "what": what, "why": why})
    if n_path > 2:
        raise WeeklyReportError("path 至多 2 条")
    if len(items) < 7 and n_path > 1:
        raise WeeklyReportError(
            "path 至多 2 条；items 为 5-6 条时 path 至多 1 条（概念/想法须占多数）")

    sig = data.get("signal")
    points = sig.get("points") if isinstance(sig, dict) else None
    if not isinstance(points, list) or not (3 <= len(points) <= 5):
        raise WeeklyReportError("signal.points 必须为 3-5 条非空字符串")
    norm_points = []
    for i, p in enumerate(points):
        label = "signal.points[%d]" % i
        if not isinstance(p, str):
            raise WeeklyReportError("%s 必须是字符串" % label)
        p = p.strip()
        if not p:
            raise WeeklyReportError("%s 不可为空" % label)
        norm_points.append(p)

    return {
        "headline": {"title": hl_title, "summary": hl_summary},
        "items": norm_items,
        "signal": {"points": norm_points},
    }


def parse_weekly_json(raw):
    """提取并校验周报。"""
    try:
        return rc.extract_json(raw, validator=_validate_weekly)
    except rc.JsonExtractError as e:
        if e.reason is not None:
            raise WeeklyReportError(
                "输出中找不到合法的周报 JSON 对象；最后一次校验：%s" % e.reason
            ) from e
        raise WeeklyReportError("输出中找不到合法的周报 JSON 对象") from e


def issue_number(weekly_dir=None):
    """下一期 = 已存档 ai-weekly-ideas-*.md 数 + 1。"""
    weekly_dir = weekly_dir if weekly_dir is not None else _weekly_dir()
    return len(glob.glob(os.path.join(weekly_dir, "ai-weekly-ideas-*.md"))) + 1


def _kind_tag_text(kind, seq):
    name = KIND_STYLE[kind][0]
    return name if kind == "path" else "%s %02d" % (name, seq)


def render_weekly_text(data, issue, date_str):
    lines = []
    lines.append("AI 技术与思路周报 · No.%02d" % issue)
    lines.append("WEEKLY · %s" % date_str.replace("-", "."))
    lines.append("=" * 34)
    lines.append("")
    lines.append("【本周头条】%s" % data["headline"]["title"])
    lines.append(data["headline"]["summary"])
    lines.append("")
    lines.append("-" * 34)

    seq = 0
    for it in data["items"]:
        if it["kind"] in ("concept", "idea"):
            seq += 1
        lines.append("")
        lines.append("【%s】%s" % (_kind_tag_text(it["kind"], seq), it["title"]))
        lines.append(it["what"])
        if it["why"]:
            lines.append("为什么重要：%s" % it["why"])

    lines.append("")
    lines.append("-" * 34)
    lines.append("就业与社会范式 · 本周信号")
    for p in data["signal"]["points"]:
        lines.append("· %s" % p)
    lines.append("")
    lines.append("=" * 34)
    lines.append("由 frontier-report 自动编排生成")
    lines.append("概念为主，路径为辅 · 第 %02d 期" % issue)
    return "\n".join(lines)


def _tag_span(kind, seq):
    name, bg, fg = KIND_STYLE[kind]
    label = name if kind == "path" else "%s · %02d" % (name, seq)
    return (
        '<span style="display:inline-block;font-size:11px;font-weight:600;'
        'letter-spacing:1px;padding:3px 10px;border-radius:99px;'
        'background:%s;color:%s;">%s</span>' % (bg, fg, wt.esc(label)))


def _item_card(item, seq):
    why = ""
    if item["why"]:
        why = (
            '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
            'style="margin-top:12px;"><tr>'
            '<td style="border-left:3px solid %s;padding:1px 0 1px 12px;">'
            '%s</td></tr></table>'
            % (wt.QUOTE_BAR, wt.paras(item["why"], wt.TEXT_MUTED, size="12.5px")))
    return (
        '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
        'style="background:%s;border-radius:12px;margin:0 0 12px;">'
        '<tr><td style="padding:16px 18px;">'
        '%s'
        '<div style="font-family:%s;font-size:16px;'
        'line-height:1.45;color:%s;margin:10px 0 0;">%s</div>'
        '%s%s'
        '</td></tr></table>'
        % (wt.BG_CARD, _tag_span(item["kind"], seq),
           wt.TITLE_FONT, wt.TEXT_CARD_TITLE, wt.esc(item["title"]),
           wt.paras(item["what"], wt.TEXT_BODY), why))


def render_weekly_html(data, issue, date_str):
    seq = 0
    cards = []
    for it in data["items"]:
        if it["kind"] in ("concept", "idea"):
            seq += 1
        cards.append(_item_card(it, seq))

    points = []
    for p in data["signal"]["points"]:
        points.append(
            '<table role="presentation" cellpadding="0" cellspacing="0" width="100%%">'
            '<tr><td width="14" valign="top" style="color:%s;font-size:13px;">·</td>'
            '<td style="padding:0 0 8px;font-size:12.5px;line-height:1.7;color:%s;">'
            '%s</td></tr></table>' % (wt.GOLD_SOFT, wt.TEXT_SIGNAL, wt.esc(p)))

    dot = date_str.replace("-", ".")
    inner = (
        wt.header_row("AI 技术与思路周报", "No.%02d" % issue)
        + wt.kicker("WEEKLY · %s" % dot)
        + ('<div style="margin-top:12px;font-family:%s;font-size:24px;'
           'line-height:1.35;color:%s;">%s</div>'
           % (wt.TITLE_FONT, wt.TEXT_TITLE, wt.esc(data["headline"]["title"])))
        + wt.paras(data["headline"]["summary"], wt.TEXT_LEAD,
                   size="13.5px", first_margin="12px 0 0")
        + wt.divider()
        + "".join(cards)
        + wt.dashed_card("就业与社会范式 · 本周信号", "".join(points))
        + wt.divider(margin="22px 0 14px", bg="rgba(255,255,255,.07)")
        + wt.footer(["由 frontier-report 自动编排生成",
                     "概念为主，路径为辅 · 第 %02d 期" % issue])
    )
    return wt.panel_page("AI 技术与思路周报 No.%02d" % issue, inner)


def build_weekly_prompt(digests, weekly_dir=None, template=None, fetched=None):
    """prompt 模板注入日报素材、聚合说明与本地一手素材缓存。

    template 未给定时按三级来源解析（config 覆盖 > weekly_dir/prompt.md >
    包内置模板）；weekly_dir 仅供测试与兼容显式指定。模板不可用抛
    WeeklyReportError。"""
    if template is None:
        if weekly_dir is not None:
            candidate = os.path.join(weekly_dir, "prompt.md")
            if os.path.exists(candidate):
                with open(candidate, encoding="utf-8") as f:
                    template = f.read()
        if template is None:
            template = _load_weekly_prompt_template()
    if template is None:
        raise WeeklyReportError("周报 prompt 模板不可用（覆盖路径/数据目录/包内置均失败）")
    tpl = template
    if digests:
        dates = "、".join(ds for ds, _ in digests)
        note = "本周共聚合到 %d 份日报（日期倒序）：%s。" % (len(digests), dates)
        blocks = ["## %s 的日报\n\n%s" % (ds, content) for ds, content in digests]
        digest = "\n\n---\n\n".join(blocks)
    else:
        note = "本周无可用日报素材，本期全部依赖你的增量联网深研。"
        digest = "（本周无可用日报素材）"
    fetched_s = fetched if fetched is not None else "（无本地素材）"
    return (tpl.replace("<<DIGEST_NOTE>>", note)
              .replace("<<DAILY_DIGEST>>", digest)
              .replace("<<FETCHED_SOURCES>>", fetched_s))


SAMPLE_DATA = {
    "headline": {
        "title": "从提示词到上下文：智能的新边界在哪里",
        "summary": "本周最值得记住的不是任何一个新模型，而是一种正在成型的共识——"
                   "管理“模型能看到什么”，正在成为新的核心能力。",
    },
    "items": [
        {"kind": "concept", "title": "上下文工程（Context Engineering）",
         "what": "从“写好一个提示词”转向“系统地管理模型在每一步能看到什么”——"
                 "上下文正在取代提示词，成为新的编程界面。",
         "why": "当 Agent 跨数十个工具与文件工作，决定智能上限的不再是模型参数，"
                "而是哪些信息在何时、以何种顺序进入上下文窗口。"},
        {"kind": "idea", "title": "延迟即界面",
         "what": "模型思考的数秒里，产品必须用流式输出与渐进披露把等待变成叙事——"
                 "停顿本身成为体验设计的材料。",
         "why": "若成立，所有实时产品的交互节奏与反馈设计都要重写。"},
        {"kind": "path", "title": "Agentic RAG",
         "what": "检索不再是问答前的一次性预处理，而是 Agent 在推理循环中自主发起、"
                 "自我批判、反复修正的动作；工程重点转向循环内工具编排与成本控制。",
         "why": ""},
        {"kind": "concept", "title": "记忆外化（Externalized Memory）",
         "what": "Agent 的长期记忆越来越多地由外部存储与检索承担，而非模型权重本身。",
         "why": "记忆架构成为产品差异化来源，也改变了“模型升级=产品升级”的旧等式。"},
        {"kind": "idea", "title": "按难度路由的小模型编排",
         "what": "简单意图交给小模型、困难推理才调用大模型的分流策略正在成为成本标配。",
         "why": "推理价格继续下探时，应用层成本结构与毛利模型随之改变。"},
    ],
    "signal": {
        "points": [
            "某头部客服外包商被报道裁员约 12%，同期“AI 训练师/标注质检”岗位挂牌量环比上升（信号需实跑时核验具体来源与数字）。",
            "多家云厂商把 Agent 编排能力并入标准企业套餐，采用动作从试点转向采购预算科目。",
            "监管侧继续围绕生成内容标识与训练数据合规细化，企业合规成本前移到产品设计阶段。",
        ]
    },
}


def _run_claude(prompt):
    """weekly 专有超时的薄封装。"""
    return rc.run_claude(prompt, _config.get_config().weekly_timeout)


def _one_attempt(prompt):
    """单次周报生成尝试。成功返回 (data, None)，失败返回 (None, 原因)。"""
    try:
        code, raw, err = _run_claude(prompt)
    except Exception as e:
        log("WARN claude 运行异常: %s" % e)
        return None, "claude 运行异常: %s" % e
    if err.strip():
        log("WARN claude stderr: %s"
            % err[:500].replace("\r", " ").replace("\n", " "))
    log("claude exit=%d output length=%d" % (code, len(raw)))
    if code != 0:
        log("ERROR: claude exit=%d。output head: %s"
            % (code, raw[:200].replace("\n", " ")))
        return None, "claude exit=%d" % code
    if not raw.strip():
        log("ERROR: claude 空输出")
        return None, "claude 空输出"
    try:
        data = parse_weekly_json(raw)
    except WeeklyReportError as e:
        fp = os.path.join(_weekly_dir(),
                          "failed-output-%s.txt" % time.strftime("%Y-%m-%d"))
        with open(fp, "w", encoding="utf-8") as f:
            f.write(raw)
        log("ERROR: JSON 校验失败（%s）；原始输出存 %s" % (e, fp))
        return None, "JSON 校验失败：%s" % e
    return data, None


def run_weekly():
    log("=== weekly report task start ===")
    cfg = _config.get_config()
    digests, missing = collect_daily_reports()
    if missing:
        log("WARN 缺失/损坏日报: %s" % ", ".join(missing))
    if not digests:
        log("WARN 本周无任何可用日报，完全依赖增量深研")

    fetched, fetched_date = "（无本地素材）", None
    if cfg.fetch_enabled:
        try:
            report, _ = fetch_sources.run_fetch(cfg.fetch_dir)
            ok = [k for k, v in report.items() if v[0]]
            log("素材预抓取完成: %s" % ("、".join(ok) if ok else "全部来源失败"))
        except Exception as e:  # noqa: BLE001
            log("WARN 素材预抓取异常: %s" % e)
        cached = fetch_sources.load_latest(cfg.fetch_dir)
        if cached:
            fetched, fetched_date = cached

    try:
        prompt = build_weekly_prompt(digests, fetched=fetched)
    except WeeklyReportError as e:
        log("ERROR: %s" % e)
        return 1
    log("prompt length: %d chars, digests: %d（素材 %s）"
        % (len(prompt), len(digests), "有" if fetched_date else "无"))

    data, last_err = None, "未尝试"
    attempts = max(1, cfg.max_attempts)
    for attempt in range(1, attempts + 1):
        log("生成尝试 %d/%d" % (attempt, attempts))
        data, last_err = _one_attempt(prompt)
        if data is not None:
            break
        if attempt < attempts:
            time.sleep(30 * attempt)

    ds = time.strftime("%Y-%m-%d")

    if data is None:
        log("ERROR: %d 次生成均失败，进入降级简报。原因：%s"
            % (attempts, last_err))
        return degrade.send_degraded_report(
            "weekly", last_err, fetched, _weekly_dir(), ds, log)

    issue = issue_number()
    text = render_weekly_text(data, issue, ds)
    htm = render_weekly_html(data, issue, ds)

    report_file = os.path.join(_weekly_dir(), "ai-weekly-ideas-%s.md" % ds)
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(text)
    log("saved report: %s (No.%02d)" % (report_file, issue))

    subject = "AI 技术与思路周报 - %s" % ds
    try:
        send_weekly_mail(subject, text, htm)
    except WeeklyReportError as e:
        log("ERROR: 发信失败: %s" % e)
        return 2
    log("=== weekly report task end ===")
    return 0


def send_weekly_mail(subject, text_body, html_body,
                     sleep_fn=time.sleep, smtp_factory=rc.smtplib.SMTP):
    """读凭据（本模块 get_env，可被 monkeypatch）+ 调共享发信。"""
    host = get_env("SMTP_HOST", "smtp.qq.com")
    user = get_env("SMTP_USER")
    pw = get_env("SMTP_PASS")
    to = get_env("REPORT_TO_EMAIL")
    if not (user and pw and to):
        raise WeeklyReportError("SMTP 凭据缺失（SMTP_USER/SMTP_PASS/REPORT_TO_EMAIL）")
    port = int(get_env("SMTP_PORT", "587"))
    try:
        rc.send_html_mail(host, port, user, pw, to, subject, text_body, html_body,
                          sleep_fn=sleep_fn, smtp_factory=smtp_factory, logger=log)
    except rc.ReportError as e:
        raise WeeklyReportError(str(e)) from e
