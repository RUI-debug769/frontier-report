#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日一概念的间隔重复调度（简化半衰期模型）。

思想提炼自墨墨背单词团队 KDD 2022 间隔重复调度论文：每个记忆项有一个
半衰期 h，间隔 Δ 后的回忆概率约 p = 2^(-Δ/h)；当 Δ ≥ h（回忆概率降到约
50%、即将遗忘）时安排复习，每次复习后半衰期按系数 GROWTH 增长——
间隔自然拉宽：1 → 2.3 → 5.3 → 12 → 28 → 64 天。

与主动背词场景的差异：日报是被动阅读，没有回忆测试结果可回归，
因此不估计个体差异，只用确定性规则调度；模型只负责生成文本，
调度决策全部在本模块内完成（可测试、可复现）。

状态文件：<数据目录>/daily/concepts.json。纯标准库。
"""
import json
import os
import time
from datetime import date

GROWTH = 2.3            # 每次复习后半衰期增长系数
INITIAL_HALF_LIFE = 1.0  # 新概念首次复习间隔（天）
MAX_REVIEWS_PER_DAY = 3  # 每日最多复推概念数（防刷屏）
STORE_FILENAME = "concepts.json"


class ConceptStoreError(Exception):
    """概念库可预期错误。"""


def _today_str(today=None):
    return (today or date.today()).strftime("%Y-%m-%d")


def load_store(daily_dir):
    """读概念库；不存在或损坏时返回空库（损坏文件改名留底，不丢证据）。"""
    path = os.path.join(daily_dir, STORE_FILENAME)
    if not os.path.exists(path):
        return {"concepts": []}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data.get("concepts"), list):
            raise ValueError("concepts 不是数组")
    except (OSError, ValueError) as e:
        backup = path + ".corrupt-%s" % time.strftime("%Y%m%d-%H%M%S")
        try:
            os.replace(path, backup)
        except OSError:
            pass
        raise ConceptStoreError("概念库损坏（已备份到 %s）: %s" % (backup, e))
    return data


def save_store(daily_dir, store):
    """原子写入：先写临时文件再 os.replace，避免中途断电留半截 JSON。"""
    path = os.path.join(daily_dir, STORE_FILENAME)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _parse_day(s):
    return date(*[int(x) for x in s.split("-")])


def due_reviews(store, today=None, limit=MAX_REVIEWS_PER_DAY):
    """返回今日到期概念（按紧迫度 = 已隔天数/半衰期 降序），至多 limit 个。

    到期判据：today - last_review >= half_life_days（回忆概率降至约 50%）。
    """
    today = today or date.today()
    due = []
    for c in store["concepts"]:
        try:
            elapsed = (today - _parse_day(c["last_review"])).days
        except (KeyError, ValueError):
            continue
        h = float(c.get("half_life_days", INITIAL_HALF_LIFE))
        if elapsed >= h:
            due.append((elapsed / h, c))
    due.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in due[:limit]]


def format_review_block(due, today=None):
    """注入 prompt 的复习清单文本。无到期时给明确说明。"""
    today = today or date.today()
    if not due:
        return "（今日无到期复习概念，concept_reviews 输出空数组 []）"
    lines = []
    for c in due:
        lines.append(
            "- name: %s\n  距上次推送 %d 天前（第 %d 次复习）\n  一句话：%s\n  首次解释：%s"
            % (c["name"],
               (today - _parse_day(c["last_review"])).days,
               c.get("review_count", 0) + 1,
               c.get("one_liner", ""),
               c.get("explanation", "")[:400]))
    return "\n".join(lines)


def format_known_names(store, limit=60):
    """注入 prompt 的已知概念名清单（防重复教学），超限时只给最近 limit 个。"""
    names = [c["name"] for c in store["concepts"]]
    if not names:
        return "（概念库为空，任何新概念都可以讲）"
    shown = names[-limit:]
    suffix = "……等共 %d 个" % len(names) if len(names) > limit else ""
    return "、".join(shown) + suffix


def _norm_name(s):
    """名称归一化：去空白与括号变体，用于复习条目与到期清单的匹配。"""
    return (s.replace(" ", "").replace("（", "(").replace("）", ")")
             .replace("(", "").replace(")", "").lower())


def match_review_name(review_name, due):
    """复习条目 name 匹配到期清单：精确 → 归一化相等 → 归一化互相包含。
    返回匹配到的概念 dict，无匹配返回 None。"""
    rn = _norm_name(review_name)
    for c in due:
        if review_name == c["name"]:
            return c
    for c in due:
        if rn == _norm_name(c["name"]):
            return c
    for c in due:
        cn = _norm_name(c["name"])
        if rn and cn and (rn in cn or cn in rn):
            return c
    return None


def apply_issue(store, concept_new, reviews, due, today=None):
    """一期成功投递后更新状态：登记新概念 + 刷新被复推概念的半衰期。
    reviews 中无法匹配到期清单的条目忽略（不影响渲染，仅不参与调度）。
    同名新概念视为复习（防模型无视已知清单重复教学）。"""
    today_s = _today_str(today)

    if concept_new:
        new_name = concept_new["name"]
        existing = next(
            (c for c in store["concepts"] if _norm_name(c["name"]) == _norm_name(new_name)),
            None)
        if existing is None:
            store["concepts"].append({
                "name": new_name,
                "first_seen": today_s,
                "last_review": today_s,
                "half_life_days": INITIAL_HALF_LIFE,
                "review_count": 0,
                "one_liner": concept_new.get("one_liner", ""),
                "explanation": concept_new.get("explanation", ""),
                "news_hook": concept_new.get("news_hook", ""),
            })
        else:
            existing["last_review"] = today_s
            existing["review_count"] = existing.get("review_count", 0) + 1
            existing["half_life_days"] = round(
                float(existing.get("half_life_days", INITIAL_HALF_LIFE)) * GROWTH, 2)

    for r in reviews:
        c = match_review_name(r.get("name", ""), due)
        if c is None:
            continue
        c["last_review"] = today_s
        c["review_count"] = c.get("review_count", 0) + 1
        c["half_life_days"] = round(
            float(c.get("half_life_days", INITIAL_HALF_LIFE)) * GROWTH, 2)
    return store
