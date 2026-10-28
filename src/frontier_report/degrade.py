#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""降级简报：模型多次生成失败时，用本地一手素材缓存发一封诚实标注的简报，
保证"每天有邮件"。不冒充实时整理，明确标注降级与失败原因。"""
import os
import time

from . import theme as wt


def build_degraded(kind, reason, fetched_md, date_str=None):
    """kind: 'daily' | 'weekly'。返回 (text, html, subject)。"""
    date_str = date_str or time.strftime("%Y-%m-%d")
    kind_cn = "AI 前沿日报" if kind == "daily" else "AI 技术与思路周报"
    kind_en = "DAILY" if kind == "daily" else "WEEKLY"
    label = "降级简报"

    # 素材截断，避免邮件过长
    material = (fetched_md or "（无本地素材缓存）")[:1500]

    text = "\n".join([
        "%s · %s  [%s]" % (kind_cn, date_str, label),
        "%s · %s" % (kind_en, date_str.replace("-", ".")),
        "=" * 34,
        "",
        "⚠ 本期新鲜内容生成失败，以下为本地一手素材摘要（非实时整理）。",
        "失败原因：%s" % reason,
        "",
        "-" * 34,
        material,
        "",
        "=" * 34,
        "由 frontier-report 自动编排（降级模式）· 下期自动重试",
    ])

    esc_material = wt.esc(material).replace("\n", "<br>")
    inner = (
        wt.header_row(kind_cn, label)
        + wt.kicker("%s · %s" % (kind_en, date_str.replace("-", ".")))
        + ('<div style="margin-top:14px;font-size:13px;color:%s;">'
           '⚠ 本期新鲜内容生成失败，以下为本地一手素材摘要（非实时整理）。</div>'
           % wt.ACCENT)
        + ('<div style="font-size:12px;color:%s;margin-top:6px;">失败原因：%s</div>'
           % (wt.TEXT_MUTED, wt.esc(reason)))
        + wt.divider()
        + ('<div style="font-size:12px;line-height:1.7;color:%s;">%s</div>'
           % (wt.TEXT_BODY, esc_material))
        + wt.divider(margin="22px 0 14px")
        + wt.footer(["由 frontier-report 自动编排（降级模式）· 下期自动重试"])
    )
    html = wt.panel_page("%s %s 降级简报" % (kind_cn, date_str), inner)
    subject = "[降级]%s - %s" % (kind_cn, date_str)
    return text, html, subject


def send_degraded_report(kind, reason, fetched, out_dir, date_str, log):
    """生成降级简报、存档并发信。返回 0（已发信）/ 2（发信或凭据失败）。
    供 daily / weekly 的降级路径共用。"""
    from . import common as rc
    text, htm, subject = build_degraded(kind, reason, fetched, date_str)
    fn = "ai-daily-report" if kind == "daily" else "ai-weekly-ideas"
    report_file = os.path.join(out_dir, "%s-%s.degraded.md" % (fn, date_str))
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(text)
    log("saved degraded report: %s" % report_file)
    host = rc.get_env("SMTP_HOST", "smtp.qq.com")
    user = rc.get_env("SMTP_USER")
    pw = rc.get_env("SMTP_PASS")
    to = rc.get_env("REPORT_TO_EMAIL")
    if not (user and pw and to):
        log("ERROR: 降级简报因 SMTP 凭据缺失无法发出")
        return 2
    try:
        port = int(rc.get_env("SMTP_PORT", "587"))
        rc.send_html_mail(host, port, user, pw, to, subject, text, htm, logger=log)
        log("降级简报已发信")
        return 0
    except (rc.ReportError, ValueError) as e:
        log("ERROR: 降级简报发信失败: %s" % e)
        return 2
