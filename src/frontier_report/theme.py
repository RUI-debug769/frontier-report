#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日报/周报共享暖陶主题：语义化色板、字体、640px 外壳与通用 HTML 积木。
所有入模字符串经 esc/paras 转义。纯标准库。"""
import html as _html

# 色板（与已上线周报逐字一致）
BG_OUTER = "#211E1B"
BG_PANEL = "#26221F"
BG_CARD = "#322D29"
BG_SIGNAL = "#2E2925"
TEXT_BRAND = "#EDE6DB"
TEXT_TITLE = "#F2EDE5"
TEXT_CARD_TITLE = "#F0EAE0"
TEXT_BODY = "#C6BFB3"
TEXT_LEAD = "#BEB7AC"
TEXT_MUTED = "#9C9488"
TEXT_SIGNAL = "#B7B0A4"
TEXT_RIGHT = "#87807A"
TEXT_FOOTER = "#7D766C"
ACCENT = "#CC785C"
ACCENT_SOFT = "#E09A82"
GOLD = "#D9B98A"
GOLD_SOFT = "#DDB391"
QUOTE_BAR = "#8A5541"

TITLE_FONT = "Georgia,'Songti SC',serif"
BODY_FONT = "-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif"


def esc(s):
    return _html.escape(str(s), quote=True)


def paras(s, color, size="13px", first_margin="4px 0 0"):
    """非空行 -> 若干 <p>。"""
    out = []
    for line in str(s).splitlines():
        line = line.strip()
        if line:
            out.append(
                '<p style="margin:%s;font-size:%s;line-height:1.75;color:%s;">%s</p>'
                % (first_margin, size, color, esc(line)))
    return "".join(out)


def header_row(brand, right=""):
    return (
        '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0"><tr>'
        '<td style="font-family:%s;font-size:14px;letter-spacing:.5px;color:%s;">%s</td>'
        '<td align="right" style="font-size:11px;color:%s;">%s</td>'
        '</tr></table>'
        % (TITLE_FONT, TEXT_BRAND, esc(brand), TEXT_RIGHT, esc(right)))


def kicker(text):
    return (
        '<div style="margin-top:16px;font-size:10.5px;letter-spacing:3px;color:%s;">%s</div>'
        % (ACCENT, esc(text)))


def divider(margin="22px 0", bg="rgba(255,255,255,.08)"):
    """1px 隔线。bg/margin 原样拼入 HTML/CSS，不做转义，仅接受内部可信
    常量（rgba 颜色、像素 margin 字面量）；禁止传入用户/模型来源数据，
    否则构成 CSS/HTML 注入。"""
    return '<div style="height:1px;background:%s;margin:%s;"></div>' % (bg, margin)


def footer(lines):
    return ('<div style="font-size:10.5px;line-height:1.7;color:%s;">%s</div>'
            % (TEXT_FOOTER, "<br>".join(esc(x) for x in lines)))


def dashed_card(title, body_html, title_color=GOLD_SOFT):
    """虚线容器：周报信号卡 / 日报深读卡共用。"""
    return (
        '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
        'style="background:%s;border:1px dashed rgba(255,255,255,.14);border-radius:12px;">'
        '<tr><td style="padding:15px 18px;">'
        '<div style="font-size:12.5px;color:%s;font-weight:600;margin-bottom:8px;">%s</div>'
        '%s</td></tr></table>'
        % (BG_SIGNAL, title_color, esc(title), body_html))


def panel_page(doc_title, inner_html):
    """最外层：doctype/head/body + 640 主面板。"""
    return (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<title>%s</title></head>\n'
        '<body style="margin:0;padding:24px 12px;background:%s;font-family:%s;">\n'
        '<table role="presentation" width="640" align="center" cellpadding="0" cellspacing="0" '
        'style="width:100%%;max-width:640px;background:%s;border-radius:16px;">\n'
        '<tr><td style="padding:30px 30px 26px;">%s</td></tr>\n</table>\n'
        '</body></html>'
        % (esc(doc_title), BG_OUTER, BODY_FONT, BG_PANEL, inner_html))
