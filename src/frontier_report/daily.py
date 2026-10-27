#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 前沿日报（每天）链路。

claude 输出严格 JSON：今日速览 + 四栏目（人物/公司/事件/政策）+ 热门 Paper
技术（2-4 篇，hot/theory）+ 每日一概念（新概念 + 间隔复习）+ 深读；
schema 校验后渲染为分栏紧凑式纯文本 + 暖陶暗色 HTML，SMTP 发出。
概念调度（简化半衰期模型）在 concepts 模块；运行时/主题复用 common / theme。
纯标准库。

路径解析：模块级 DAILY_DIR 默认为 None，运行时经 _daily_dir() 解析——
模块全局优先（测试可 patch），否则取 config 的 <data_dir>/daily。
log 默认为 no-op，由 frontier_report.cli 在运行时绑定真实 logger。
"""
import json
import os
import time
from datetime import date, datetime
from importlib import resources

from . import common as rc
from . import concepts
from . import config as _config
from . import theme as wt

DAILY_DIR = None  # None -> 运行时从 config 推导；测试可 patch 为临时目录

SECTION_IDS = ["people", "companies", "events", "policy", "hackathons"]
SECTION_TITLES = {
    "people": ("领军人物", "PEOPLE"),
    "companies": ("公司与产业链", "COMPANIES"),
    "events": ("重大事件", "EVENTS"),
    "policy": ("政策与监管", "POLICY"),
    "hackathons": ("赛事机会", "HACKATHONS"),
}
HACKATHON_MAX_ITEMS = 6

PAPER_KINDS = ("hot", "theory")
PAPER_KIND_STYLE = {
    "hot": ("热门", "#3D2C23", "#E09A82"),
    "theory": ("理论", "#3A3226", "#D9B98A"),
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


def build_daily_prompt(template, store, due, today=None):
    """模板注入概念调度信息、复习清单、已知概念名单与今日日期。"""
    today_s = (today or date.today()).strftime("%Y-%m-%d")
    return (template
            .replace("{{TODAY}}", today_s)
            .replace("<<CONCEPT_REVIEW>>",
                     concepts.format_review_block(due, today=today))
            .replace("<<CONCEPT_KNOWN>>", concepts.format_known_names(store)))


def _req_str(obj, key, label, allow_empty=False):
    v = obj.get(key)
    if not isinstance(v, str):
        raise DailyReportError("%s 必须是字符串" % label)
    v = v.strip()
    if not allow_empty and not v:
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


def _validate_section(obj, idx, today=None):
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
    if sid == "hackathons":
        if len(items) > HACKATHON_MAX_ITEMS:
            raise DailyReportError(
                "hackathons items 至多 %d 条" % HACKATHON_MAX_ITEMS)
        return {"id": sid,
                "items": [_validate_hackathon(it, j, today)
                          for j, it in enumerate(items)]}
    return {"id": sid, "items": [_validate_item(it, j) for j, it in enumerate(items)]}


def _validate_hackathon(obj, idx, today=None):
    if not isinstance(obj, dict):
        raise DailyReportError("hackathons.items[%d] 不是对象" % idx)
    name = _req_str(obj, "name", "hackathons.items[%d].name" % idx)
    text = _req_str(obj, "text", "hackathons.items[%d].text" % idx)
    group = _req_str(obj, "group", "hackathons.items[%d].group" % idx)
    deadline_s = _req_str(
        obj, "deadline", "hackathons.items[%d].deadline" % idx)
    url = _req_str(obj, "url", "hackathons.items[%d].url" % idx,
                   allow_empty=True)
    try:
        deadline = datetime.strptime(deadline_s, "%Y-%m-%d").date()
    except ValueError:
        raise DailyReportError(
            "hackathons.items[%d].deadline 必须为 YYYY-MM-DD，实际 %r"
            % (idx, deadline_s))
    today = today or date.today()
    if deadline < today:
        raise DailyReportError(
            "hackathons.items[%d] 已过报名截止日（%s < %s），不得收录"
            % (idx, deadline_s, today.strftime("%Y-%m-%d")))
    if url and not url.lower().startswith(("http://", "https://")):
        raise DailyReportError(
            "hackathons.items[%d].url 必须以 http(s):// 开头或为空字符串"
            % idx)
    return {"name": name, "text": text, "group": group,
            "deadline": deadline_s, "url": url}


def _check_url(url, label):
    low = url.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        raise DailyReportError("%s 必须以 http(s):// 开头" % label)


def _validate_deep(obj, idx):
    if not isinstance(obj, dict):
        raise DailyReportError("deep_reads[%d] 不是对象" % idx)
    title = _req_str(obj, "title", "deep_reads[%d].title" % idx)
    url = _req_str(obj, "url", "deep_reads[%d].url" % idx)
    why = _req_str(obj, "why", "deep_reads[%d].why" % idx)
    _check_url(url, "deep_reads[%d].url" % idx)
    return {"title": title, "url": url, "why": why}


def _validate_paper(obj, idx):
    if not isinstance(obj, dict):
        raise DailyReportError("papers[%d] 不是对象" % idx)
    title = _req_str(obj, "title", "papers[%d].title" % idx)
    source = _req_str(obj, "source", "papers[%d].source" % idx)
    kind = obj.get("kind")
    if kind not in PAPER_KINDS:
        raise DailyReportError(
            "papers[%d].kind 必须为 hot 或 theory，实际 %r" % (idx, kind))
    technique = _req_str(obj, "technique", "papers[%d].technique" % idx)
    why = _req_str(obj, "why", "papers[%d].why" % idx)
    url = _req_str(obj, "url", "papers[%d].url" % idx)
    _check_url(url, "papers[%d].url" % idx)
    return {"title": title, "source": source, "kind": kind,
            "technique": technique, "why": why, "url": url}


def _validate_concept_new(obj):
    if not isinstance(obj, dict):
        raise DailyReportError("concept_new 缺失或不是对象")
    name = _req_str(obj, "name", "concept_new.name")
    one_liner = _req_str(obj, "one_liner", "concept_new.one_liner")
    explanation = _req_str(obj, "explanation", "concept_new.explanation")
    news_hook = _req_str(obj, "news_hook", "concept_new.news_hook",
                         allow_empty=True)
    return {"name": name, "one_liner": one_liner,
            "explanation": explanation, "news_hook": news_hook}


def _validate_concept_reviews(obj, due):
    """复习条目：name 必须能匹配到期清单（due 为 None 时跳过匹配，
    仅做形态校验）。匹配成功则规范化为清单中的原始名称并附复习序号。"""
    if not isinstance(obj, list) or len(obj) > concepts.MAX_REVIEWS_PER_DAY:
        raise DailyReportError(
            "concept_reviews 必须为 0-%d 条数组" % concepts.MAX_REVIEWS_PER_DAY)
    norm = []
    for i, r in enumerate(obj):
        if not isinstance(r, dict):
            raise DailyReportError("concept_reviews[%d] 不是对象" % i)
        name = _req_str(r, "name", "concept_reviews[%d].name" % i)
        cue = _req_str(r, "recall_cue", "concept_reviews[%d].recall_cue" % i)
        essence = _req_str(r, "essence", "concept_reviews[%d].essence" % i)
        entry = {"name": name, "recall_cue": cue, "essence": essence,
                 "review_no": None}
        if due is not None:
            matched = concepts.match_review_name(name, due)
            if matched is None:
                valid = "、".join(c["name"] for c in due) or "（今日无到期概念）"
                raise DailyReportError(
                    "concept_reviews[%d].name %r 不在今日复习清单内（应为：%s）"
                    % (i, name, valid))
            entry["name"] = matched["name"]
            entry["review_no"] = matched.get("review_count", 0) + 1
        norm.append(entry)
    return norm


def _validate_daily(data, due=None, today=None):
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
    if not isinstance(sections, list) or len(sections) != 5:
        raise DailyReportError("sections 必须为固定 5 个元素")
    norm_sections = [_validate_section(s, i, today=today)
                     for i, s in enumerate(sections)]
    # 赛事按截止日升序（prompt 已要求模型排序；此处兜底保证）
    for s in norm_sections:
        if s["id"] == "hackathons":
            s["items"].sort(key=lambda it: it["deadline"])

    papers = data.get("papers")
    if not isinstance(papers, list) or not (2 <= len(papers) <= 4):
        raise DailyReportError("papers 必须为 2-4 条")
    norm_papers = [_validate_paper(p, i) for i, p in enumerate(papers)]

    norm_concept_new = _validate_concept_new(data.get("concept_new"))
    norm_reviews = _validate_concept_reviews(data.get("concept_reviews"), due)

    deeps = data.get("deep_reads")
    if not isinstance(deeps, list) or not (1 <= len(deeps) <= 2):
        raise DailyReportError("deep_reads 必须为 1-2 条")
    norm_deeps = [_validate_deep(d, i) for i, d in enumerate(deeps)]

    return {"briefs": norm_briefs, "sections": norm_sections,
            "papers": norm_papers, "concept_new": norm_concept_new,
            "concept_reviews": norm_reviews, "deep_reads": norm_deeps}


def parse_daily_json(raw, due=None, today=None):
    """提取并校验日报；垃圾输入给泛化文案，schema 失败冒泡最近原因。
    due 为当日到期复习清单；today 为日期锚点（date 对象，默认今天），
    用于赛事截止日校验。"""
    try:
        return rc.extract_json(
            raw, validator=lambda obj: _validate_daily(obj, due, today))
    except rc.JsonExtractError as e:
        if e.reason is not None:
            raise DailyReportError(
                "输出中找不到合法的日报 JSON 对象；最后一次校验：%s" % e.reason
            ) from e
        raise DailyReportError("输出中找不到合法的日报 JSON 对象") from e


_CIRCLED = ["①", "②", "③", "④", "⑤"]


def _days_remaining(deadline_s, date_str):
    try:
        d0 = datetime.strptime(date_str, "%Y-%m-%d").date()
        d1 = datetime.strptime(deadline_s, "%Y-%m-%d").date()
        return (d1 - d0).days
    except ValueError:
        return None


def _countdown_text(deadline_s, date_str):
    n = _days_remaining(deadline_s, date_str)
    if n is None:
        return "截止日见官方页"
    if n == 0:
        return "今日截止"
    return "剩%d天" % n


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
        if sec["id"] == "hackathons":
            for it in sec["items"]:
                L.append("· %s（%s｜截止 %s，%s）"
                         % (it["name"], it["group"], it["deadline"],
                            _countdown_text(it["deadline"], date_str)))
                L.append("  %s" % it["text"])
                if it["url"]:
                    L.append("  报名：%s" % it["url"])
            continue
        last_group = None
        for it in sec["items"]:
            if sec["id"] == "companies" and it["group"] and it["group"] != last_group:
                L.append("【%s】" % it["group"])
                last_group = it["group"]
            L.append("· %s：%s" % (it["name"], it["text"]))

    L.append("")
    L.append("-" * 34)
    L.append("热门 Paper 技术 · PAPERS")
    for p in data["papers"]:
        tag = PAPER_KIND_STYLE[p["kind"]][0]
        L.append("【%s】%s（%s）" % (tag, p["title"], p["source"]))
        L.append("· 技术：%s" % p["technique"])
        L.append("· 价值：%s" % p["why"])
        L.append("  %s" % p["url"])

    cn = data["concept_new"]
    L.append("")
    L.append("-" * 34)
    L.append("每日一概念 · %s" % cn["name"])
    L.append(cn["one_liner"])
    L.append(cn["explanation"])
    if cn["news_hook"]:
        L.append("关联：%s" % cn["news_hook"])

    if data["concept_reviews"]:
        L.append("")
        L.append("-" * 34)
        L.append("间隔复习 · SPACED REVIEW")
        for r in data["concept_reviews"]:
            no = "（第 %d 次）" % r["review_no"] if r["review_no"] else ""
            L.append("【复习】%s%s" % (r["name"], no))
            L.append("先回忆：%s" % r["recall_cue"])
            L.append("精要：%s" % r["essence"])

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


def _section_head(cn, en):
    return (
        '<div style="margin:22px 0 10px;">'
        '<span style="font-family:%s;font-size:15px;color:%s;">%s</span>'
        '<span style="font-size:10px;letter-spacing:2px;color:%s;margin-left:8px;">%s</span>'
        '</div>'
        % (wt.TITLE_FONT, wt.TEXT_CARD_TITLE, wt.esc(cn), wt.TEXT_RIGHT, en))


def _hackathon_item_html(it, date_str):
    countdown = _countdown_text(it["deadline"], date_str)
    link = ""
    if it["url"]:
        link = ('<a href="%s" style="font-size:12px;color:%s;text-decoration:none;">报名链接 →</a>'
                % (wt.esc(it["url"]), wt.ACCENT))
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" width="100%%">'
        '<tr><td style="padding:8px 0;border-top:1px solid rgba(255,255,255,.05);">'
        '<div style="font-size:13px;font-weight:600;color:%s;">%s '
        '<span style="font-size:10.5px;font-weight:400;color:%s;margin-left:6px;">%s · 截止 %s · %s</span>'
        '</div>'
        '<div style="font-size:12.5px;line-height:1.65;color:%s;margin-top:3px;">%s</div>'
        '<div style="margin-top:4px;">%s</div>'
        '</td></tr></table>'
        % (wt.ACCENT_SOFT, wt.esc(it["name"]), wt.TEXT_RIGHT,
           wt.esc(it["group"]), wt.esc(it["deadline"]), countdown,
           wt.TEXT_BODY, wt.esc(it["text"]), link))


def _section_html(sec, date_str=None):
    items = sec["items"]
    if not items:
        return ""
    cn, en = SECTION_TITLES[sec["id"]]
    head = _section_head(cn, en)

    if sec["id"] == "hackathons":
        return head + "".join(_hackathon_item_html(it, date_str) for it in items)

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


def _kind_tag(kind):
    name, bg, fg = PAPER_KIND_STYLE[kind]
    return (
        '<span style="display:inline-block;font-size:10.5px;font-weight:600;'
        'letter-spacing:1px;padding:2px 8px;border-radius:99px;'
        'background:%s;color:%s;">%s</span>' % (bg, fg, wt.esc(name)))


def _papers_html(papers):
    rows = []
    for p in papers:
        rows.append(
            '<table role="presentation" cellpadding="0" cellspacing="0" width="100%%">'
            '<tr><td style="padding:8px 0;border-top:1px solid rgba(255,255,255,.05);">'
            '%s <a href="%s" style="font-size:13px;font-weight:600;color:%s;'
            'text-decoration:none;">%s</a>'
            '<span style="font-size:11px;color:%s;">（%s）</span>'
            '<div style="font-size:12.5px;line-height:1.65;color:%s;margin-top:4px;">'
            '<b style="color:%s;">技术：</b>%s</div>'
            '<div style="font-size:12px;line-height:1.6;color:%s;margin-top:2px;">'
            '价值：%s</div>'
            '</td></tr></table>'
            % (_kind_tag(p["kind"]), wt.esc(p["url"]), wt.GOLD, wt.esc(p["title"]),
               wt.TEXT_RIGHT, wt.esc(p["source"]),
               wt.TEXT_BODY, wt.ACCENT_SOFT, wt.esc(p["technique"]),
               wt.TEXT_MUTED, wt.esc(p["why"])))
    return _section_head("热门 Paper 技术", "PAPERS") + "".join(rows)


def _concept_new_html(cn):
    hook = ""
    if cn["news_hook"]:
        hook = ('<div style="font-size:11.5px;line-height:1.6;color:%s;margin-top:8px;">'
                '关联：%s</div>' % (wt.TEXT_RIGHT, wt.esc(cn["news_hook"])))
    return (
        '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
        'style="background:%s;border-radius:12px;margin:18px 0 0;">'
        '<tr><td style="padding:15px 18px;">'
        '<div style="font-size:11px;letter-spacing:2px;color:%s;margin-bottom:6px;">每日一概念</div>'
        '<div style="font-family:%s;font-size:16px;line-height:1.45;color:%s;">%s</div>'
        '<div style="font-size:12.5px;font-weight:600;color:%s;margin-top:6px;">%s</div>'
        '%s%s'
        '</td></tr></table>'
        % (wt.BG_CARD, wt.ACCENT,
           wt.TITLE_FONT, wt.TEXT_CARD_TITLE, wt.esc(cn["name"]),
           wt.ACCENT_SOFT, wt.esc(cn["one_liner"]),
           wt.paras(cn["explanation"], wt.TEXT_BODY, size="12.5px"), hook))


def _reviews_html(reviews):
    if not reviews:
        return ""
    rows = []
    for r in reviews:
        no = "第 %d 次" % r["review_no"] if r["review_no"] else "复习"
        rows.append(
            '<div style="margin:0 0 12px;">'
            '<div style="font-size:12.5px;font-weight:600;color:%s;">%s'
            '<span style="font-size:10.5px;font-weight:400;color:%s;margin-left:6px;">%s</span></div>'
            '<div style="font-size:12px;line-height:1.65;color:%s;margin-top:3px;">'
            '<i>先回忆：%s</i></div>'
            '<div style="font-size:12.5px;line-height:1.7;color:%s;margin-top:3px;">%s</div>'
            '</div>'
            % (wt.GOLD, wt.esc(r["name"]), wt.TEXT_RIGHT, wt.esc(no),
               wt.TEXT_MUTED, wt.esc(r["recall_cue"]),
               wt.TEXT_BODY, wt.esc(r["essence"])))
    return ('<div style="height:18px;"></div>'
            + wt.dashed_card("间隔复习 · SPACED REVIEW", "".join(rows)))


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

    sections = "".join(_section_html(s, date_str) for s in data["sections"])
    deep_card = wt.dashed_card("值得深读 · DEEP READ", _deep_html(data["deep_reads"]))

    inner = (
        wt.header_row("AI 前沿日报", "DAILY")
        + wt.kicker("DAILY · %s" % dot)
        + '<div style="height:14px;"></div>'
        + briefs_block
        + sections
        + _papers_html(data["papers"])
        + _concept_new_html(data["concept_new"])
        + _reviews_html(data["concept_reviews"])
        + '<div style="height:18px;"></div>'
        + deep_card
        + wt.divider(margin="22px 0 14px")
        + wt.footer(["由 frontier-report 自动编排生成 · 概念按半衰期模型间隔复推"]))
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
        {"id": "hackathons", "items": [
            {"name": "示例 AI 黑客松", "text": "基于具身基座模型完成仿真任务，"
                                            "线上初赛可选报真机赛道。",
             "group": "杭州", "deadline": "2099-01-31",
             "url": "https://example.com/hack"}]},
    ],
    "papers": [
        {"title": "示例论文：稀疏注意力的新纪元", "source": "arXiv:2510.00001",
         "kind": "hot",
         "technique": "用可学习的块稀疏掩码把长上下文注意力成本降到近线性。",
         "why": "长文本推理成本的新基线。", "url": "https://example.com/paper-a"},
        {"title": "示例论文：梯度流的平均场刻画", "source": "arXiv:2510.00002",
         "kind": "theory",
         "technique": "把宽度趋无穷的残差网络训练动力学归结为平均场 PDE。",
         "why": "为超参数迁移提供理论解释。", "url": "https://example.com/paper-b"},
    ],
    "concept_new": {
        "name": "混合专家模型（MoE）",
        "one_liner": "用门控网络把每个输入路由到少数专家子网络的大模型架构。",
        "explanation": "MoE 的核心直觉是“分科问诊”：不必让每个参数参与每次计算。"
        "门控网络为每个 token 选出 top-k 个专家，只激活这些专家的参数，"
        "于是总参数可以很大而单次计算量可控。最小例子：128 个专家每次激活 8 个，"
        "计算量约为稠密模型的 1/16。代价是路由均衡与显存占用的工程复杂度。",
        "news_hook": "今日某旗舰模型公布采用细粒度 MoE 架构。"},
    "concept_reviews": [
        {"name": " KL 散度 ",
         "recall_cue": "为什么说 KL(p‖q) ≠ KL(q‖p)？用“避重就轻 vs 求全”回忆。",
         "essence": "KL 散度度量两个分布的差异，但不满足对称性，不是距离。"
         "前向 KL 倾向覆盖 p 的所有支撑集（求全），反向 KL 倾向锁住众数（避重就轻），"
         "这一不对称性决定了蒸馏与变分推断的行为。", "review_no": 2}],
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
    daily_dir = _daily_dir()
    try:
        store = concepts.load_store(daily_dir)
    except concepts.ConceptStoreError as e:
        log("WARN %s；以空概念库继续" % e)
        store = {"concepts": []}
    due = concepts.due_reviews(store)
    if due:
        log("到期复习概念: %s" % "、".join(c["name"] for c in due))

    template = _load_daily_prompt()
    if template is None:
        log("ERROR: 日报 prompt 不可用（覆盖路径/数据目录/包内置均失败）")
        return 1
    prompt = build_daily_prompt(template, store, due)
    log("prompt length: %d chars（到期复习 %d 个）" % (len(prompt), len(due)))

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
        data = parse_daily_json(raw, due=due, today=date.today())
    except DailyReportError as e:
        fp = os.path.join(daily_dir,
                          "failed-output-%s.txt" % time.strftime("%Y-%m-%d"))
        with open(fp, "w", encoding="utf-8") as f:
            f.write(raw)
        log("ERROR: JSON 校验失败（%s）；原始输出存 %s" % (e, fp))
        return 1

    ds = time.strftime("%Y-%m-%d")
    text = render_daily_text(data, ds)
    htm = render_daily_html(data, ds)

    report_file = os.path.join(daily_dir, "ai-daily-report-%s.md" % ds)
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

    # 投递成功后才推进概念调度：发信失败不消耗复习、不登记新概念
    concepts.apply_issue(store, data["concept_new"], data["concept_reviews"], due)
    try:
        concepts.save_store(daily_dir, store)
        log("概念库已更新（共 %d 个概念）" % len(store["concepts"]))
    except OSError as e:
        log("WARN 概念库保存失败（不影响本期投递）: %s" % e)
    log("=== daily report task end ===")
    return 0
