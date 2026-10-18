#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 前沿日报（每天）链路。

claude 输出严格 JSON：今日速览 + 四栏目（人物/公司/事件/政策）+ 深读；
schema 校验后渲染为分栏紧凑式纯文本 + 暖陶暗色 HTML，SMTP 发出。
运行时/主题复用 common / theme。纯标准库。

路径解析：模块级 DAILY_DIR 默认为 None，运行时经 _daily_dir() 解析——
模块全局优先（测试可 patch），否则取 config 的 <data_dir>/daily。
log 默认为 no-op，由 frontier_report.cli 在运行时绑定真实 logger。
"""
import json
import os
import time
from importlib import resources

from . import common as rc
from . import config as _config
from . import theme as wt

DAILY_DIR = None  # None -> 运行时从 config 推导；测试可 patch 为临时目录

SECTION_IDS = ["people", "companies", "events", "policy"]
SECTION_TITLES = {
    "people": ("领军人物", "PEOPLE"),
    "companies": ("公司与产业链", "COMPANIES"),
    "events": ("重大事件", "EVENTS"),
    "policy": ("政策与监管", "POLICY"),
}


class DailyReportError(Exception):
    """日报链路可预期错误。"""


def _noop_log(msg):
    pass


log = _noop_log  # cli 启动时绑定 make_logger；测试可重赋


def _daily_dir():
    if DAILY_DIR is not None:
        return DAILY_DIR
    return _config.get_config().daily_dir


def _load_daily_prompt():
    """日报 prompt 三级来源：config.daily_prompt 覆盖 > 数据目录 prompt.md
    > 包内置 prompts/daily.md。全部不可用返回 None。"""
    cfg = _config.get_config()
    if cfg.daily_prompt:
        try:
            with open(cfg.daily_prompt, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None
    user_prompt = os.path.join(_daily_dir(), "prompt.md")
    if os.path.exists(user_prompt):
        with open(user_prompt, encoding="utf-8") as f:
            return f.read()
    try:
        return (resources.files("frontier_report.prompts")
                .joinpath("daily.md").read_text(encoding="utf-8"))
    except (OSError, FileNotFoundError, ModuleNotFoundError):
        return None


def _req_str(obj, key, label):
    v = obj.get(key)
    if not isinstance(v, str):
        raise DailyReportError("%s 必须是字符串" % label)
    v = v.strip()
    if not v:
        raise DailyReportError("%s 不可为空" % label)
    return v


def _validate_item(obj, idx):
    if not isinstance(obj, dict):
        raise DailyReportError("sections.items[%d] 不是对象" % idx)
    name = _req_str(obj, "name", "items[%d].name" % idx)
    text = _req_str(obj, "text", "items[%d].text" % idx)
    g = obj.get("group", "")
    if not isinstance(g, str):
        raise DailyReportError("items[%d].group 必须是字符串" % idx)
    return {"name": name, "text": text, "group": g.strip()}


def _validate_section(obj, idx):
    if not isinstance(obj, dict):
        raise DailyReportError("sections[%d] 不是对象" % idx)
    sid = obj.get("id")
    if idx >= len(SECTION_IDS) or sid != SECTION_IDS[idx]:
        expected = SECTION_IDS[idx] if idx < len(SECTION_IDS) else "<非法位置>"
        raise DailyReportError(
            "sections[%d].id 必须为 %s（顺序锁定），实际 %r"
            % (idx, expected, sid))
    items = obj.get("items")
    if not isinstance(items, list) or len(items) > 12:
        raise DailyReportError("sections[%d].items 必须为 0-12 条数组" % idx)
    return {"id": sid, "items": [_validate_item(it, j) for j, it in enumerate(items)]}


def _validate_deep(obj, idx):
    if not isinstance(obj, dict):
        raise DailyReportError("deep_reads[%d] 不是对象" % idx)
    title = _req_str(obj, "title", "deep_reads[%d].title" % idx)
    url = _req_str(obj, "url", "deep_reads[%d].url" % idx)
    why = _req_str(obj, "why", "deep_reads[%d].why" % idx)
    low = url.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        raise DailyReportError("deep_reads[%d].url 必须以 http(s):// 开头" % idx)
    return {"title": title, "url": url, "why": why}


def _validate_daily(data):
    if not isinstance(data, dict):
        raise DailyReportError("顶层不是 JSON 对象")

    briefs = data.get("briefs")
    if not isinstance(briefs, list) or not (3 <= len(briefs) <= 5):
        raise DailyReportError("briefs 必须为 3-5 条字符串")
    norm_briefs = []
    for i, b in enumerate(briefs):
        if not isinstance(b, str):
            raise DailyReportError("briefs[%d] 必须是字符串" % i)
        b = b.strip()
        if not b:
            raise DailyReportError("briefs[%d] 不可为空" % i)
        norm_briefs.append(b)

    sections = data.get("sections")
    if not isinstance(sections, list) or len(sections) != 4:
        raise DailyReportError("sections 必须为固定 4 个元素")
    norm_sections = [_validate_section(s, i) for i, s in enumerate(sections)]

    deeps = data.get("deep_reads")
    if not isinstance(deeps, list) or not (1 <= len(deeps) <= 2):
        raise DailyReportError("deep_reads 必须为 1-2 条")
    norm_deeps = [_validate_deep(d, i) for i, d in enumerate(deeps)]

    return {"briefs": norm_briefs, "sections": norm_sections,
            "deep_reads": norm_deeps}


def parse_daily_json(raw):
    """提取并校验日报；垃圾输入给泛化文案，schema 失败冒泡最近原因。"""
    try:
        return rc.extract_json(raw, validator=_validate_daily)
    except rc.JsonExtractError as e:
        if e.reason is not None:
            raise DailyReportError(
                "输出中找不到合法的日报 JSON 对象；最后一次校验：%s" % e.reason
            ) from e
        raise DailyReportError("输出中找不到合法的日报 JSON 对象") from e


_CIRCLED = ["①", "②", "③", "④", "⑤"]


def render_daily_text(data, date_str):
    L = []
    L.append("AI 前沿日报 · %s" % date_str)
    L.append("DAILY · %s" % date_str.replace("-", "."))
    L.append("=" * 34)
    L.append("")
    L.append("◆ 今日速览")
    for i, b in enumerate(data["briefs"]):
        L.append("%s %s" % (_CIRCLED[i], b))

    for sec in data["sections"]:
        if not sec["items"]:
            continue
        cn, en = SECTION_TITLES[sec["id"]]
        L.append("")
        L.append("-" * 34)
        L.append("%s · %s" % (cn, en))
        last_group = None
        for it in sec["items"]:
            if sec["id"] == "companies" and it["group"] and it["group"] != last_group:
                L.append("【%s】" % it["group"])
                last_group = it["group"]
            L.append("· %s：%s" % (it["name"], it["text"]))

    L.append("")
    L.append("-" * 34)
    L.append("值得深读")
    for d in data["deep_reads"]:
        L.append("· %s" % d["title"])
        L.append("  %s" % d["url"])
        L.append("  推荐：%s" % d["why"])

    L.append("")
    L.append("=" * 34)
    L.append("由 frontier-report 自动编排生成")
    return "\n".join(L)


def _brief_html(briefs):
    rows = []
    for i, b in enumerate(briefs):
        rows.append(
            '<table role="presentation" cellpadding="0" cellspacing="0" width="100%%">'
            '<tr><td width="24" valign="top" '
            'style="font-size:12px;font-weight:700;color:%s;">%02d</td>'
            '<td style="padding:0 0 8px;font-size:12.5px;line-height:1.7;color:%s;">%s</td>'
            '</tr></table>'
            % (wt.ACCENT, i + 1, wt.TEXT_SIGNAL, wt.esc(b)))
    return "".join(rows)


def _section_html(sec):
    items = sec["items"]
    if not items:
        return ""
    cn, en = SECTION_TITLES[sec["id"]]
    head = (
        '<div style="margin:22px 0 10px;">'
        '<span style="font-family:%s;font-size:15px;color:%s;">%s</span>'
        '<span style="font-size:10px;letter-spacing:2px;color:%s;margin-left:8px;">%s</span>'
        '</div>'
        % (wt.TITLE_FONT, wt.TEXT_CARD_TITLE, wt.esc(cn), wt.TEXT_RIGHT, en))

    rows = []
    last_group = None
    for it in items:
        if sec["id"] == "companies" and it["group"] and it["group"] != last_group:
            rows.append(
                '<div style="font-size:10.5px;letter-spacing:1px;color:%s;margin:8px 0 2px;">%s</div>'
                % (wt.GOLD_SOFT, wt.esc(it["group"])))
            last_group = it["group"]
        rows.append(
            '<table role="presentation" cellpadding="0" cellspacing="0" width="100%%">'
            '<tr><td style="padding:6px 0;border-top:1px solid rgba(255,255,255,.05);">'
            '<span style="font-size:12.5px;font-weight:600;color:%s;">%s：</span>'
            '<span style="font-size:12.5px;color:%s;">%s</span>'
            '</td></tr></table>'
            % (wt.ACCENT_SOFT, wt.esc(it["name"]), wt.TEXT_BODY, wt.esc(it["text"])))
    return head + "".join(rows)


def _deep_html(deeps):
    rows = []
    for d in deeps:
        rows.append(
            '<div style="margin:0 0 12px;">'
            '<a href="%s" style="font-size:12.5px;font-weight:600;color:%s;'
            'text-decoration:none;">%s</a>'
            '<div style="font-size:12px;line-height:1.65;color:%s;margin-top:3px;">%s</div>'
            '</div>'
            % (wt.esc(d["url"]), wt.GOLD, wt.esc(d["title"]),
               wt.TEXT_MUTED, wt.esc(d["why"])))
    return "".join(rows)


def render_daily_html(data, date_str):
    dot = date_str.replace("-", ".")
    briefs_block = (
        '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
        'style="background:%s;border-radius:10px;">'
        '<tr><td style="padding:13px 16px 6px;">'
        '<div style="font-size:11px;letter-spacing:2px;color:%s;margin-bottom:8px;">今日速览</div>'
        '%s</td></tr></table>'
        % (wt.BG_SIGNAL, wt.ACCENT, _brief_html(data["briefs"])))

    sections = "".join(_section_html(s) for s in data["sections"])
    deep_card = wt.dashed_card("值得深读 · DEEP READ", _deep_html(data["deep_reads"]))

    inner = (
        wt.header_row("AI 前沿日报", "DAILY")
        + wt.kicker("DAILY · %s" % dot)
        + '<div style="height:14px;"></div>'
        + briefs_block
        + sections
        + '<div style="height:18px;"></div>'
        + deep_card
        + wt.divider(margin="22px 0 14px")
        + wt.footer(["由 frontier-report 自动编排生成"]))
    return wt.panel_page("AI 前沿日报 %s" % dot, inner)


SAMPLE_DATA = {
    "briefs": ["示例速览甲：今日最重要的变化", "示例速览乙", "示例速览丙", "示例速览丁"],
    "sections": [
        {"id": "people", "items": [
            {"name": "Amodei", "text": "发表长文呼吁行业放缓。", "group": ""},
            {"name": "Altman", "text": "确认今年不推进 IPO。", "group": ""}]},
        {"id": "companies", "items": [
            {"name": "英伟达", "text": "AI 加速器份额约 91%。", "group": "基础层"},
            {"name": "智谱", "text": "完成约 50 亿美元融资。", "group": "技术层"},
            {"name": "Figure", "text": "签约采购 Vera Rubin 平台。", "group": "应用层"}]},
        {"id": "events", "items": [
            {"name": "放缓联盟", "text": "巨头联名后遭反垄断集体诉讼。", "group": ""}]},
        {"id": "policy", "items": [
            {"name": "中国", "text": "发布安全治理框架 3.0。", "group": ""}]},
    ],
    "deep_reads": [
        {"title": "示例深读：Agent 自主性边界", "url": "https://example.com/a",
         "why": "一手观察样本，理解安全测试的披露伦理。"},
        {"title": "示例深读：行业自律机构", "url": "https://example.com/b",
         "why": "梳理行业出资监管模式的独立性争议。"}],
}


def _run_claude_daily(prompt):
    return rc.run_claude(prompt, _config.get_config().daily_timeout)


def run_daily():
    log("=== daily report task start ===")
    prompt = _load_daily_prompt()
    if prompt is None:
        log("ERROR: 日报 prompt 不可用（覆盖路径/数据目录/包内置均失败）")
        return 1
    log("prompt length: %d chars" % len(prompt))

    try:
        code, raw, err = _run_claude_daily(prompt)
    except Exception as e:
        log("ERROR: claude 运行异常: %s" % e)
        return 1
    if err.strip():
        log("WARN claude stderr: %s"
            % err[:500].replace("\r", " ").replace("\n", " "))
    log("claude exit=%d output length=%d" % (code, len(raw)))
    if code != 0:
        log("ERROR: claude exit=%d，不存档不发信。output head: %s"
            % (code, raw[:200].replace("\n", " ")))
        return 1
    if not raw.strip():
        log("ERROR: claude 空输出")
        return 1

    try:
        data = parse_daily_json(raw)
    except DailyReportError as e:
        fp = os.path.join(_daily_dir(),
                          "failed-output-%s.txt" % time.strftime("%Y-%m-%d"))
        with open(fp, "w", encoding="utf-8") as f:
            f.write(raw)
        log("ERROR: JSON 校验失败（%s）；原始输出存 %s" % (e, fp))
        return 1

    ds = time.strftime("%Y-%m-%d")
    text = render_daily_text(data, ds)
    htm = render_daily_html(data, ds)

    report_file = os.path.join(_daily_dir(), "ai-daily-report-%s.md" % ds)
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(text)
    log("saved report: %s" % report_file)

    host = rc.get_env("SMTP_HOST", "smtp.qq.com")
    user = rc.get_env("SMTP_USER")
    pw = rc.get_env("SMTP_PASS")
    to = rc.get_env("REPORT_TO_EMAIL")
    if not (user and pw and to):
        log("ERROR: SMTP 凭据缺失（SMTP_USER/SMTP_PASS/REPORT_TO_EMAIL）")
        return 2
    try:
        port = int(rc.get_env("SMTP_PORT", "587"))
    except ValueError:
        log("ERROR: SMTP_PORT 非法")
        return 2

    subject = "AI前沿日报 - %s" % ds
    try:
        rc.send_html_mail(host, port, user, pw, to, subject, text, htm, logger=log)
    except rc.ReportError as e:
        log("ERROR: 发信失败: %s" % e)
        return 2
    log("=== daily report task end ===")
    return 0
