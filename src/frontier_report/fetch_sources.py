#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一手素材预抓取：arXiv 新论文 / Hacker News 头条 / HuggingFace Daily Papers。

把"抓取"与"生成"解耦：本模块在生成前把一手来源落到本地 markdown 缓存，
生成时 claude 读缓存，即使生成那一刻外网不通也能出稿。纯标准库，
每个来源独立 try/except，一个失败不拖垮其余，结果诚实标注成败。

来源可达性（2026-10-28 实测）：arxiv.org / github.com 经代理可达；
hf-mirror.com / ghproxy.net 不可达。故默认只启 arXiv + HN，HF 为可选。
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

ARXIV_API = "https://export.arxiv.org/api/query"
HN_TOP = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{id}.json"
HF_DAILY = "https://huggingface.co/api/daily_papers"

ARXIV_CATS = ["cs.AI", "cs.CL", "cs.LG", "cs.CV"]
ARXIV_MAX = 25   # 每类最多取数
HN_MAX = 15      # 头条最多取数
HF_MAX = 10
FETCH_FILENAME = "fetched-{date}.md"
_TIMEOUT = 25
_RETRIES = 2


def _windows_registry_proxy():
    """读 Windows 系统代理（HKCU Internet Settings）。计划任务环境通常
    没有 HTTP_PROXY 环境变量，但系统代理（自由猫/吉连云在 127.0.0.1:7892）
    对交互式用户启用。返回 {'http': ..., 'https': ...} 或 {}。"""
    if os.name != "nt":
        return {}
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        try:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not enabled:
                return {}
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
        finally:
            winreg.CloseKey(key)
    except (OSError, FileNotFoundError):
        return {}
    out = {}
    server = (server or "").strip()
    if not server:
        return {}
    if "=" in server:  # 形如 "http=host:port;https=host:port"
        for part in server.split(";"):
            if "=" in part:
                proto, val = part.split("=", 1)
                out[proto.strip()] = val.strip()
    else:
        out["http"] = out["https"] = server
    return {k: ("http://%s" % v if not v.startswith("http") else v)
            for k, v in out.items()}


def _proxy_config():
    """代理发现：环境变量优先，否则回退 Windows 系统代理。"""
    env = {}
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        v = os.environ.get(var)
        if v:
            env["http"] = env.get("http", v)
            env["https"] = env.get("https", v)
    return env or _windows_registry_proxy()


def _build_opener():
    proxies = _proxy_config()
    if proxies:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler(proxies))
    return urllib.request.build_opener()


def _http_get(url, timeout=_TIMEOUT):
    """经环境/系统代理取数。境外源偶发 SSL EOF/重置（代理通道抖动），
    对幂等的 GET 做有限重试。"""
    last = None
    for attempt in range(_RETRIES + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "frontier-report/0.3"})
            with _build_opener().open(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < _RETRIES:
                time.sleep(2 * (attempt + 1))
    raise last


def fetch_arxiv(today=None):
    """arXiv 各分类最新提交：标题 + 摘要页链接。返回 (markdown, 条数)。"""
    today = today or date.today()
    out = []
    total = 0
    for cat in ARXIV_CATS:
        q = urllib.parse.urlencode({
            "search_query": "cat:%s" % cat,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": ARXIV_MAX,
        })
        xml = _http_get("%s?%s" % (ARXIV_API, q))
        entries = xml.split("<entry>")[1:]
        out.append("## arXiv %s（最新 %d 条）" % (cat, min(len(entries), ARXIV_MAX)))
        for e in entries[:ARXIV_MAX]:
            title = _tag(e, "title").strip().replace("\n", " ")
            link = _tag(e, "id").strip()
            summary = _tag(e, "summary").strip().replace("\n", " ")[:300]
            out.append("- %s\n  摘要：%s\n  链接：%s" % (title, summary, link))
            total += 1
        out.append("")
    return "\n".join(out), total


def _tag(xml, name):
    a = xml.find("<%s" % name)
    if a < 0:
        return ""
    a = xml.find(">", a) + 1
    b = xml.find("</%s>" % name, a)
    return xml[a:b] if b > a else ""


def fetch_hn(today=None):
    """Hacker News 头条：标题 + 外链。返回 (markdown, 条数)。"""
    ids = json.loads(_http_get(HN_TOP))[:HN_MAX]
    out = ["## Hacker News 头条（前 %d 条）" % len(ids)]
    n = 0
    for i in ids:
        try:
            it = json.loads(_http_get(HN_ITEM.format(id=i)))
        except (urllib.error.URLError, ValueError):
            continue
        title = it.get("title", "").strip()
        if not title:
            continue
        url = it.get("url") or ("https://news.ycombinator.com/item?id=%s" % i)
        score = it.get("score", 0)
        out.append("- %s（%d 分）\n  %s" % (title, score, url))
        n += 1
    out.append("")
    return "\n".join(out), n


def fetch_hf(today=None):
    """HuggingFace Daily Papers（需能访问 huggingface.co，否则失败）。"""
    data = json.loads(_http_get(HF_DAILY))[:HF_MAX]
    out = ["## HuggingFace Daily Papers（前 %d 条）" % len(data)]
    for p in data:
        p = p.get("paper", p)
        out.append("- %s\n  链接：https://huggingface.co/papers/%s"
                   % (p.get("title", "").strip(), p.get("id", "")))
    out.append("")
    return "\n".join(out), len(data)


def run_fetch(fetch_dir, include_hf=True):
    """抓取全部来源到本地缓存。返回 dict：{来源: (成功?, 条数或错误)}。
    抓取结果写入 <fetch_dir>/fetched-<日期>.md；部分失败不影响其他来源。"""
    os.makedirs(fetch_dir, exist_ok=True)
    ds = date.today().strftime("%Y-%m-%d")
    report = {}
    blocks = [
        "# 一手素材缓存 · %s\n（本文件由 frontier-report fetch 预抓取，"
        "供生成时离线读取；生成失败时也用作降级简报底料）\n" % ds,
    ]

    for name, fn in (("arxiv", fetch_arxiv), ("hn", fetch_hn)):
        try:
            md, n = fn()
            blocks.append(md)
            report[name] = (True, n)
        except Exception as e:  # noqa: BLE001 每源独立容错
            report[name] = (False, "%s: %s" % (type(e).__name__, e))

    if include_hf:
        try:
            md, n = fetch_hf()
            blocks.append(md)
            report["huggingface"] = (True, n)
        except Exception as e:  # noqa: BLE001
            report["huggingface"] = (False, "%s: %s" % (type(e).__name__, e))

    path = os.path.join(fetch_dir, FETCH_FILENAME.format(date=ds))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(blocks))
    return report, path


def load_latest(fetch_dir, today=None):
    """读取最近一份缓存（含历史），无任何缓存返回 None。"""
    today = today or date.today()
    # 从今天往回找最多 7 天
    for i in range(7):
        ds = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        p = os.path.join(fetch_dir, FETCH_FILENAME.format(date=ds))
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read(), ds
    return None
