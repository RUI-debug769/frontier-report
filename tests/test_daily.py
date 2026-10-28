# -*- coding: utf-8 -*-
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import date
from unittest import mock

from frontier_report import config as _config
from frontier_report import concepts
from frontier_report import daily as dr
from frontier_report import theme as wt

dr.log = lambda msg: None  # 测试不写真 run.log


def _base():
    return {
        "briefs": ["速览甲", "速览乙", "速览丙"],
        "sections": [
            {"id": "people", "items": [
                {"name": "Altman", "text": "不推进 IPO"}]},
            {"id": "companies", "items": [
                {"name": "英伟达", "text": "占 91%", "group": "基础层"}]},
            {"id": "events", "items": [
                {"name": "放缓联盟", "text": "持续跟踪"}]},
            {"id": "policy", "items": [
                {"name": "中国", "text": "框架 3.0"}]},
            {"id": "hackathons", "items": [
                {"name": "示例黑客松", "text": "AI 应用开发赛", "group": "杭州",
                 "deadline": "2099-12-31", "url": "https://x/h"}]},
        ],
        "papers": [
            {"title": "论文甲", "source": "arXiv:2510.00001", "kind": "hot",
             "technique": "稀疏注意力", "why": "长文本新基线",
             "url": "https://x/p1"},
            {"title": "论文乙", "source": "arXiv:2510.00002", "kind": "theory",
             "technique": "平均场 PDE", "why": "超参迁移理论",
             "url": "https://x/p2"},
        ],
        "concept_new": {
            "name": "混合专家模型（MoE）", "one_liner": "门控路由到少数专家",
            "explanation": "先直觉后严谨的解释。", "news_hook": "今日架构新闻"},
        "concept_reviews": [],
        "deep_reads": [
            {"title": "深读甲", "url": "https://x/a", "why": "推荐理由"}],
    }


class ValidateBriefsTests(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(len(dr.parse_daily_json(json.dumps(_base()))["briefs"]), 3)

    def test_too_few(self):
        d = _base(); d["briefs"] = ["只两条", "之二"]
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d))

    def test_too_many(self):
        d = _base(); d["briefs"] = ["1", "2", "3", "4", "5", "6"]
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d))

    def test_non_string(self):
        d = _base(); d["briefs"][0] = 123
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d))

    def test_blank(self):
        d = _base(); d["briefs"][0] = "   "
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d))


class ValidateSectionsTests(unittest.TestCase):
    def test_wrong_count(self):
        d = _base(); d["sections"] = d["sections"][:4]
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d))

    def test_wrong_id_order(self):
        d = _base(); d["sections"][0]["id"] = "events"
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d))

    def test_illegal_id(self):
        d = _base(); d["sections"][2]["id"] = "news"
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d))

    def test_empty_items_valid(self):
        d = _base(); d["sections"][3]["items"] = []
        out = dr.parse_daily_json(json.dumps(d))
        self.assertEqual(out["sections"][3]["items"], [])

    def test_too_many_items(self):
        d = _base()
        d["sections"][0]["items"] = [
            {"name": "n%d" % i, "text": "t"} for i in range(13)]
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d))

    def test_item_missing_name(self):
        d = _base(); del d["sections"][0]["items"][0]["name"]
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d))

    def test_item_text_null(self):
        d = _base(); d["sections"][0]["items"][0]["text"] = None
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d))

    def test_group_default_empty(self):
        # _base() 的 people 条目本身就没有 group：缺省应规范化为 ""
        d = _base()
        out = dr.parse_daily_json(json.dumps(d))
        self.assertEqual(out["sections"][0]["items"][0]["group"], "")

    def test_group_null_rejected(self):
        d = _base(); d["sections"][1]["items"][0]["group"] = None
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d))

    def test_extra_position_safe_message(self):
        # idx 超出 SECTION_IDS 时防御分支不得再抛 IndexError
        with self.assertRaises(dr.DailyReportError):
            dr._validate_section({"id": "bogus", "items": []}, 9)


class ValidateHackathonsTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 10, 27)

    def test_ok(self):
        out = dr.parse_daily_json(json.dumps(_base()), today=self.today)
        h = next(s for s in out["sections"] if s["id"] == "hackathons")
        self.assertEqual(h["items"][0]["group"], "杭州")

    def test_empty_items_valid(self):
        d = _base(); d["sections"][4]["items"] = []
        out = dr.parse_daily_json(json.dumps(d), today=self.today)
        h = next(s for s in out["sections"] if s["id"] == "hackathons")
        self.assertEqual(h["items"], [])

    def test_too_many_items(self):
        d = _base()
        d["sections"][4]["items"] = [
            {"name": "n%d" % i, "text": "t", "group": "线上",
             "deadline": "2099-01-01", "url": ""} for i in range(7)]
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d), today=self.today)

    def test_expired_deadline_rejected(self):
        d = _base()
        d["sections"][4]["items"][0]["deadline"] = "2026-10-26"
        with self.assertRaisesRegex(dr.DailyReportError, "已过报名截止日"):
            dr.parse_daily_json(json.dumps(d), today=self.today)

    def test_today_deadline_accepted(self):
        d = _base()
        d["sections"][4]["items"][0]["deadline"] = "2026-10-27"
        out = dr.parse_daily_json(json.dumps(d), today=self.today)
        h = next(s for s in out["sections"] if s["id"] == "hackathons")
        self.assertEqual(h["items"][0]["deadline"], "2026-10-27")

    def test_bad_date_format(self):
        d = _base()
        d["sections"][4]["items"][0]["deadline"] = "2026/11/01"
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d), today=self.today)

    def test_empty_url_allowed_with_channel_in_text(self):
        d = _base()
        d["sections"][4]["items"][0]["url"] = ""
        out = dr.parse_daily_json(json.dumps(d), today=self.today)
        self.assertEqual(out["sections"][4]["items"][0]["url"], "")

    def test_non_http_url_rejected(self):
        d = _base()
        d["sections"][4]["items"][0]["url"] = "javascript:x"
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d), today=self.today)

    def test_news_link_does_not_count_as_url(self):
        # 新闻报道链接冒充官方报名页：schema 不判语义，但 prompt 禁止；
        # 这里保证 url 为空时 text 必须带渠道（由 prompt 约束，校验只保证形态）
        d = _base()
        d["sections"][4]["items"][0] = {
            "name": "公众号赛", "text": "在公众号报名", "group": "线上",
            "deadline": "2099-01-01", "url": ""}
        out = dr.parse_daily_json(json.dumps(d), today=self.today)
        self.assertEqual(out["sections"][4]["items"][0]["text"], "在公众号报名")

    def test_items_sorted_by_deadline(self):
        d = _base()
        d["sections"][4]["items"] = [
            {"name": "晚", "text": "t", "group": "线上",
             "deadline": "2099-12-31", "url": ""},
            {"name": "早", "text": "t", "group": "杭州",
             "deadline": "2099-01-01", "url": ""},
        ]
        out = dr.parse_daily_json(json.dumps(d), today=self.today)
        names = [it["name"] for it in out["sections"][4]["items"]]
        self.assertEqual(names, ["早", "晚"])

    def test_missing_group_rejected(self):
        d = _base()
        del d["sections"][4]["items"][0]["group"]
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d), today=self.today)


class ValidateDeepTests(unittest.TestCase):
    def test_too_few(self):
        d = _base(); d["deep_reads"] = []
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d))

    def test_too_many(self):
        d = _base()
        d["deep_reads"].append({"title": "乙", "url": "https://x/b", "why": "w"})
        d["deep_reads"].append({"title": "丙", "url": "https://x/c", "why": "w"})
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d))

    def test_bad_url_scheme(self):
        d = _base(); d["deep_reads"][0]["url"] = "javascript:alert(1)"
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d))

    def test_missing_why(self):
        d = _base(); del d["deep_reads"][0]["why"]
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d))

    def test_uppercase_scheme_ok(self):
        d = _base(); d["deep_reads"][0]["url"] = "HTTPS://x/a"
        out = dr.parse_daily_json(json.dumps(d))
        self.assertEqual(out["deep_reads"][0]["url"], "HTTPS://x/a")


class ValidatePapersTests(unittest.TestCase):
    def test_ok(self):
        out = dr.parse_daily_json(json.dumps(_base()))
        self.assertEqual(len(out["papers"]), 2)
        self.assertEqual(out["papers"][0]["kind"], "hot")

    def test_too_few(self):
        d = _base(); d["papers"] = d["papers"][:1]
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d))

    def test_too_many(self):
        d = _base()
        d["papers"] = d["papers"] * 3  # 6 篇
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d))

    def test_missing_papers_key(self):
        d = _base(); del d["papers"]
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d))

    def test_bad_kind(self):
        d = _base(); d["papers"][0]["kind"] = "survey"
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d))

    def test_bad_url(self):
        d = _base(); d["papers"][0]["url"] = "ftp://x/p"
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d))

    def test_missing_technique(self):
        d = _base(); del d["papers"][0]["technique"]
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d))


class ValidateConceptTests(unittest.TestCase):
    def test_missing_concept_new(self):
        d = _base(); del d["concept_new"]
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d))

    def test_empty_explanation(self):
        d = _base(); d["concept_new"]["explanation"] = "  "
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d))

    def test_news_hook_empty_ok(self):
        d = _base(); d["concept_new"]["news_hook"] = ""
        out = dr.parse_daily_json(json.dumps(d))
        self.assertEqual(out["concept_new"]["news_hook"], "")

    def test_reviews_too_many(self):
        d = _base()
        d["concept_reviews"] = [
            {"name": "n%d" % i, "recall_cue": "c", "essence": "e"}
            for i in range(4)]
        with self.assertRaises(dr.DailyReportError):
            dr.parse_daily_json(json.dumps(d))

    def test_review_name_must_match_due(self):
        due = [{"name": "KL 散度", "last_review": "2026-10-15",
                "half_life_days": 1.0, "review_count": 1,
                "one_liner": "", "explanation": ""}]
        d = _base()
        d["concept_reviews"] = [
            {"name": "KL 散度", "recall_cue": "先自测", "essence": "精要"}]
        out = dr.parse_daily_json(json.dumps(d), due=due)
        r = out["concept_reviews"][0]
        self.assertEqual(r["name"], "KL 散度")
        self.assertEqual(r["review_no"], 2)  # review_count 1 + 本次

    def test_review_name_fuzzy_match(self):
        due = [{"name": "KL 散度", "last_review": "2026-10-15",
                "half_life_days": 1.0, "review_count": 0,
                "one_liner": "", "explanation": ""}]
        d = _base()
        d["concept_reviews"] = [
            {"name": "kl散度", "recall_cue": "c", "essence": "e"}]
        out = dr.parse_daily_json(json.dumps(d), due=due)
        self.assertEqual(out["concept_reviews"][0]["name"], "KL 散度")  # 规范化

    def test_review_name_not_in_due_rejected(self):
        due = [{"name": "KL 散度", "last_review": "2026-10-15",
                "half_life_days": 1.0, "review_count": 0,
                "one_liner": "", "explanation": ""}]
        d = _base()
        d["concept_reviews"] = [
            {"name": "交叉熵", "recall_cue": "c", "essence": "e"}]
        try:
            dr.parse_daily_json(json.dumps(d), due=due)
        except dr.DailyReportError as e:
            self.assertIn("KL 散度", str(e))  # 报错列出合法名称
        else:
            self.fail("应抛错")

    def test_review_without_due_skips_matching(self):
        d = _base()
        d["concept_reviews"] = [
            {"name": "任意名称", "recall_cue": "c", "essence": "e"}]
        out = dr.parse_daily_json(json.dumps(d))  # due=None
        self.assertEqual(out["concept_reviews"][0]["name"], "任意名称")
        self.assertIsNone(out["concept_reviews"][0]["review_no"])


class ParseRobustnessTests(unittest.TestCase):
    def test_noise_then_json(self):
        d = _base()
        raw = 'warn\n{"model":"x"}\n' + json.dumps(d)
        self.assertEqual(dr.parse_daily_json(raw)["briefs"], d["briefs"])

    def test_python_repr(self):
        d = _base()
        self.assertEqual(dr.parse_daily_json(repr(d))["briefs"], d["briefs"])

    def test_schema_reason_surfaced(self):
        d = _base(); d["briefs"] = ["只两条", "之二"]
        try:
            dr.parse_daily_json(json.dumps(d))
        except dr.DailyReportError as e:
            self.assertIn("最后一次校验", str(e))
        else:
            self.fail("应抛错")

    def test_garbage_generic(self):
        try:
            dr.parse_daily_json("no braces")
        except dr.DailyReportError as e:
            self.assertEqual(str(e), "输出中找不到合法的日报 JSON 对象")
        else:
            self.fail("应抛错")


class RenderTextTests(unittest.TestCase):
    def setUp(self):
        self.data = dr.parse_daily_json(json.dumps(_base()))

    def test_basic_sections(self):
        t = dr.render_daily_text(self.data, "2026-09-23")
        self.assertIn("AI 前沿日报", t)
        self.assertIn("2026.09.23", t)
        self.assertIn("① 速览甲", t)
        self.assertIn("领军人物 · PEOPLE", t)
        self.assertIn("Altman：不推进 IPO", t)
        self.assertIn("【基础层】", t)
        self.assertIn("值得深读", t)
        self.assertIn("https://x/a", t)

    def test_papers_and_concept_blocks(self):
        t = dr.render_daily_text(self.data, "2026-09-23")
        self.assertIn("热门 Paper 技术 · PAPERS", t)
        self.assertIn("【热门】论文甲（arXiv:2510.00001）", t)
        self.assertIn("【理论】论文乙", t)
        self.assertIn("每日一概念 · 混合专家模型（MoE）", t)
        self.assertIn("门控路由到少数专家", t)
        self.assertIn("关联：今日架构新闻", t)

    def test_hackathon_block_with_countdown_and_link(self):
        t = dr.render_daily_text(self.data, "2026-10-27")
        self.assertIn("赛事机会 · HACKATHONS", t)
        self.assertIn("示例黑客松（杭州｜截止 2099-12-31，剩", t)
        self.assertIn("报名：https://x/h", t)

    def test_hackathon_today_deadline_wording(self):
        d = _base()
        d["sections"][4]["items"][0]["deadline"] = "2026-10-27"
        data = dr.parse_daily_json(json.dumps(d), today=date(2026, 10, 27))
        t = dr.render_daily_text(data, "2026-10-27")
        self.assertIn("今日截止", t)

    def test_reviews_block_only_when_present(self):
        t = dr.render_daily_text(self.data, "2026-09-23")
        self.assertNotIn("间隔复习", t)  # _base 的 reviews 为空
        d = _base()
        d["concept_reviews"] = [
            {"name": "KL 散度", "recall_cue": "先自测", "essence": "精要"}]
        due = [{"name": "KL 散度", "last_review": "2026-10-15",
                "half_life_days": 1.0, "review_count": 1,
                "one_liner": "", "explanation": ""}]
        data = dr.parse_daily_json(json.dumps(d), due=due)
        t2 = dr.render_daily_text(data, "2026-09-23")
        self.assertIn("间隔复习 · SPACED REVIEW", t2)
        self.assertIn("【复习】KL 散度（第 2 次）", t2)
        self.assertIn("先回忆：先自测", t2)

    def test_empty_section_omitted(self):
        d = _base(); d["sections"][3]["items"] = []
        data = dr.parse_daily_json(json.dumps(d))
        t = dr.render_daily_text(data, "2026-09-23")
        self.assertNotIn("政策与监管", t)


class RenderHtmlTests(unittest.TestCase):
    def setUp(self):
        self.data = dr.parse_daily_json(json.dumps(_base()))
        self.h = dr.render_daily_html(self.data, "2026-09-23")

    def test_shell_and_palette(self):
        h = self.h
        self.assertTrue(h.lstrip().lower().startswith("<!doctype html"))
        self.assertIn('width="640"', h)
        self.assertIn(wt.BG_PANEL, h)
        self.assertIn(wt.ACCENT, h)
        self.assertIn("2026.09.23", h)
        self.assertIn("今日速览", h)
        self.assertIn("值得深读", h)

    def test_papers_html(self):
        h = self.h
        self.assertIn("热门 Paper 技术", h)
        self.assertIn('href="https://x/p1"', h)
        self.assertIn(">热门<", h)
        self.assertIn(">理论<", h)
        self.assertIn("稀疏注意力", h)

    def test_hackathon_html(self):
        data = dr.parse_daily_json(json.dumps(_base()),
                                   today=date(2026, 10, 27))
        h = dr.render_daily_html(data, "2026-10-27")
        self.assertIn("赛事机会", h)
        self.assertIn("示例黑客松", h)
        self.assertIn("杭州", h)
        self.assertIn("2099-12-31", h)
        self.assertIn('href="https://x/h"', h)

    def test_hackathon_empty_omitted_html(self):
        d = _base(); d["sections"][4]["items"] = []
        data = dr.parse_daily_json(json.dumps(d))
        h = dr.render_daily_html(data, "2026-09-23")
        self.assertNotIn("赛事机会", h)

    def test_concept_html(self):
        h = self.h
        self.assertIn("每日一概念", h)
        self.assertIn("混合专家模型（MoE）", h)
        self.assertIn("先直觉后严谨的解释。", h)
        self.assertNotIn("间隔复习", h)  # reviews 为空不出卡

    def test_xss_escaped(self):
        d = _base()
        d["briefs"][0] = "<script>x</script>"
        d["sections"][0]["items"][0]["text"] = '<img src=x onerror="a(1)">'
        data = dr.parse_daily_json(json.dumps(d))
        h = dr.render_daily_html(data, "2026-09-23")
        self.assertNotIn("<script>", h)
        self.assertNotIn('onerror="', h)
        self.assertIn("&lt;script&gt;", h)

    def test_xss_in_new_blocks_escaped(self):
        d = _base()
        d["papers"][0]["technique"] = '<svg onload="a(1)">'
        d["concept_new"]["explanation"] = "<script>evil</script>"
        data = dr.parse_daily_json(json.dumps(d))
        h = dr.render_daily_html(data, "2026-09-23")
        self.assertNotIn("<svg", h)
        self.assertNotIn("<script>", h)
        self.assertIn("&lt;script&gt;", h)

    def test_url_attr_escaped(self):
        d = _base()
        d["deep_reads"][0]["url"] = 'https://x/"><bad>'
        data = dr.parse_daily_json(json.dumps(d))
        h = dr.render_daily_html(data, "2026-09-23")
        self.assertNotIn('"><bad>', h)
        self.assertIn("&lt;bad&gt;", h)

    def test_empty_section_omitted(self):
        d = _base(); d["sections"][3]["items"] = []
        data = dr.parse_daily_json(json.dumps(d))
        h = dr.render_daily_html(data, "2026-09-23")
        self.assertNotIn("政策与监管", h)

    def test_companies_group_kicker(self):
        self.assertIn("基础层", self.h)

    def test_deep_link_present(self):
        self.assertIn('href="https://x/a"', self.h)


class LoadPromptTests(unittest.TestCase):
    def test_packaged_prompt_fallback(self):
        # 数据目录无 prompt.md、config 无覆盖时，回退包内置 prompt
        with tempfile.TemporaryDirectory() as d:
            cfg = _config.Config(data_dir=d)
            with mock.patch.object(dr, "DAILY_DIR", d), \
                 mock.patch.object(_config, "get_config", return_value=cfg):
                prompt = dr._load_daily_prompt()
            self.assertIsNotNone(prompt)
            self.assertIn("AI 前沿情报官", prompt)

    def test_data_dir_prompt_overrides_packaged(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "prompt.md"), "w", encoding="utf-8") as f:
                f.write("自定义 prompt")
            cfg = _config.Config(data_dir=d)
            with mock.patch.object(dr, "DAILY_DIR", d), \
                 mock.patch.object(_config, "get_config", return_value=cfg):
                self.assertEqual(dr._load_daily_prompt(), "自定义 prompt")

    def test_config_prompt_path_wins(self):
        with tempfile.TemporaryDirectory() as d:
            custom = os.path.join(d, "my-prompt.md")
            with open(custom, "w", encoding="utf-8") as f:
                f.write("config 指定 prompt")
            with open(os.path.join(d, "prompt.md"), "w", encoding="utf-8") as f:
                f.write("数据目录 prompt")
            cfg = _config.Config(data_dir=d, daily_prompt=custom)
            with mock.patch.object(dr, "DAILY_DIR", d), \
                 mock.patch.object(_config, "get_config", return_value=cfg):
                self.assertEqual(dr._load_daily_prompt(), "config 指定 prompt")


class BuildPromptTests(unittest.TestCase):
    def test_placeholders_injected(self):
        store = {"concepts": [
            {"name": "KL 散度", "first_seen": "2026-10-01",
             "last_review": "2026-10-15", "half_life_days": 1.0,
             "review_count": 0, "one_liner": "分布差异", "explanation": "解释"}]}
        due = concepts.due_reviews(store, today=None)
        prompt = dr.build_daily_prompt(
            "R=<<CONCEPT_REVIEW>> K=<<CONCEPT_KNOWN>>", store, due)
        self.assertNotIn("<<CONCEPT_REVIEW>>", prompt)
        self.assertNotIn("<<CONCEPT_KNOWN>>", prompt)
        self.assertIn("KL 散度", prompt)  # 复习清单与已知名单均含

    def test_empty_store_prompt(self):
        prompt = dr.build_daily_prompt(
            "R=<<CONCEPT_REVIEW>> K=<<CONCEPT_KNOWN>>", {"concepts": []}, [])
        self.assertIn("无到期复习", prompt)
        self.assertIn("为空", prompt)

    def test_today_placeholder_injected(self):
        prompt = dr.build_daily_prompt(
            "今天是 {{TODAY}}", {"concepts": []}, [],
            today=date(2026, 10, 27))
        self.assertIn("今天是 2026-10-27", prompt)
        self.assertNotIn("{{TODAY}}", prompt)

    def test_fetched_sources_injected(self):
        prompt = dr.build_daily_prompt(
            "F=<<FETCHED_SOURCES>>", {"concepts": []}, [],
            fetched="## arXiv 素材")
        self.assertIn("## arXiv 素材", prompt)
        self.assertNotIn("<<FETCHED_SOURCES>>", prompt)

    def test_fetched_sources_default_placeholder(self):
        prompt = dr.build_daily_prompt(
            "F=<<FETCHED_SOURCES>>", {"concepts": []}, [])
        self.assertIn("无本地素材", prompt)


class RunDailyTests(unittest.TestCase):
    """run_daily 编排层：退出码契约、重试、降级简报、概念调度状态更新时机。

    0.3.x 起语义：生成失败经重试后不再静默空缺，而是发降级简报（仍返回 0，
    因为邮件已发出）；1 仅保留给 prompt 不可用；2 为发信/凭据失败。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        with open(os.path.join(self.tmp, "prompt.md"), "w", encoding="utf-8") as f:
            f.write("R=<<CONCEPT_REVIEW>> K=<<CONCEPT_KNOWN>>")
        self.env = {
            "SMTP_HOST": "smtp.qq.com",
            "SMTP_USER": "user",
            "SMTP_PASS": "pass",
            "REPORT_TO_EMAIL": "to@example.com",
            "SMTP_PORT": "587",
        }
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(dr, "DAILY_DIR", self.tmp).start()
        # 隔离全局 config：禁用抓取（避免真实网络）、单次尝试（测试确定性）
        mock.patch.object(_config, "get_config",
                          return_value=_config.Config(
                              data_dir=self.tmp,
                              fetch_enabled=False, max_attempts=1)).start()
        mock.patch.object(dr.time, "sleep", return_value=None).start()
        self.run_claude = mock.patch.object(dr, "_run_claude_daily").start()
        mock.patch.object(dr.rc, "get_env",
                          side_effect=lambda n, d="": self.env.get(n, d)).start()
        self.send_mail = mock.patch.object(dr.rc, "send_html_mail").start()

    def _claude_ok(self, data=None):
        self.run_claude.return_value = (0, json.dumps(data or _base()), "")

    def _failed_output_exists(self):
        fp = os.path.join(self.tmp,
                          "failed-output-%s.txt" % time.strftime("%Y-%m-%d"))
        return os.path.exists(fp)

    def _degraded_exists(self):
        fp = os.path.join(
            self.tmp,
            "ai-daily-report-%s.degraded.md" % time.strftime("%Y-%m-%d"))
        return os.path.exists(fp)

    def _archive_exists(self):
        fp = os.path.join(self.tmp,
                          "ai-daily-report-%s.md" % time.strftime("%Y-%m-%d"))
        return os.path.exists(fp)

    def _store_path(self):
        return os.path.join(self.tmp, concepts.STORE_FILENAME)

    def _load_store(self):
        with open(self._store_path(), encoding="utf-8") as f:
            return json.load(f)

    def test_prompt_unavailable(self):
        mock.patch.object(dr, "_load_daily_prompt", return_value=None).start()
        self.assertEqual(dr.run_daily(), 1)
        self.run_claude.assert_not_called()
        self.send_mail.assert_not_called()

    def test_claude_raises(self):
        self.run_claude.side_effect = RuntimeError("boom")
        self.assertEqual(dr.run_daily(), 0)  # 降级后照常发信
        self.send_mail.assert_called_once()
        self.assertTrue(self._degraded_exists())

    def test_claude_nonzero_exit(self):
        self.run_claude.return_value = (1, "some output", "")
        self.assertEqual(dr.run_daily(), 0)
        self.send_mail.assert_called_once()
        self.assertTrue(self._degraded_exists())

    def test_claude_empty_output(self):
        self.run_claude.return_value = (0, "   \n  ", "")
        self.assertEqual(dr.run_daily(), 0)
        self.send_mail.assert_called_once()
        self.assertTrue(self._degraded_exists())

    def test_json_invalid(self):
        self.run_claude.return_value = (0, "not a json at all", "")
        self.assertEqual(dr.run_daily(), 0)
        self.send_mail.assert_called_once()  # 降级简报也发信
        self.assertTrue(self._failed_output_exists())
        self.assertTrue(self._degraded_exists())

    def test_smtp_credentials_missing(self):
        self._claude_ok()
        self.env["SMTP_USER"] = ""
        self.assertEqual(dr.run_daily(), 2)
        self.send_mail.assert_not_called()

    def test_smtp_port_non_numeric(self):
        self._claude_ok()
        self.env["SMTP_PORT"] = "abc"
        self.assertEqual(dr.run_daily(), 2)
        self.send_mail.assert_not_called()

    def test_send_raises_report_error(self):
        self._claude_ok()
        self.send_mail.side_effect = dr.rc.ReportError("smtp down")
        self.assertEqual(dr.run_daily(), 2)
        self.send_mail.assert_called_once()

    def test_success(self):
        self._claude_ok()
        self.assertEqual(dr.run_daily(), 0)
        self.send_mail.assert_called_once()
        self.assertTrue(self._archive_exists())
        args = self.send_mail.call_args
        self.assertEqual(args[0][1], 587)  # port 为 int
        self.assertEqual(args[0][5],
                         "AI前沿日报 - %s" % time.strftime("%Y-%m-%d"))

    def test_new_concept_saved_on_success(self):
        self._claude_ok()
        self.assertEqual(dr.run_daily(), 0)
        store = self._load_store()
        self.assertEqual(len(store["concepts"]), 1)
        c = store["concepts"][0]
        self.assertEqual(c["name"], "混合专家模型（MoE）")
        self.assertEqual(c["half_life_days"], 1.0)
        self.assertEqual(c["last_review"], time.strftime("%Y-%m-%d"))

    def test_store_not_updated_on_send_failure(self):
        self._claude_ok()
        self.send_mail.side_effect = dr.rc.ReportError("smtp down")
        self.assertEqual(dr.run_daily(), 2)
        self.assertFalse(os.path.exists(self._store_path()))

    def test_store_not_updated_on_validation_failure(self):
        self.run_claude.return_value = (0, "garbage", "")
        self.assertEqual(dr.run_daily(), 0)  # 降级路径
        self.assertFalse(os.path.exists(self._store_path()))
        self.assertTrue(self._degraded_exists())

    def test_review_flow_end_to_end(self):
        # 预置到期概念 → prompt 注入复习清单 → 模型复推 → 半衰期增长
        store = {"concepts": [
            {"name": "KL 散度", "first_seen": "2026-10-01",
             "last_review": "2026-10-10", "half_life_days": 1.0,
             "review_count": 0, "one_liner": "分布差异",
             "explanation": "首次解释", "news_hook": ""}]}
        with open(self._store_path(), "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False)
        d = _base()
        d["concept_reviews"] = [
            {"name": "kl散度", "recall_cue": "先自测", "essence": "精要"}]
        self._claude_ok(d)
        self.assertEqual(dr.run_daily(), 0)
        # prompt 注入了复习清单与已知名单
        prompt_sent = self.run_claude.call_args[0][0]
        self.assertIn("KL 散度", prompt_sent)
        # 调度状态推进：复习计数 +1、半衰期 1.0 → 2.3
        c = self._load_store()["concepts"][0]
        self.assertEqual(c["review_count"], 1)
        self.assertEqual(c["half_life_days"], 2.3)
        self.assertEqual(c["last_review"], time.strftime("%Y-%m-%d"))

    def test_review_name_mismatch_fails_validation(self):
        store = {"concepts": [
            {"name": "KL 散度", "first_seen": "2026-10-01",
             "last_review": "2026-10-10", "half_life_days": 1.0,
             "review_count": 0, "one_liner": "", "explanation": "", "news_hook": ""}]}
        with open(self._store_path(), "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False)
        d = _base()
        d["concept_reviews"] = [
            {"name": "交叉熵", "recall_cue": "c", "essence": "e"}]
        self._claude_ok(d)
        self.assertEqual(dr.run_daily(), 0)  # 校验失败 → 降级
        self.assertTrue(self._failed_output_exists())
        self.assertTrue(self._degraded_exists())

    def test_retry_then_success(self):
        # max_attempts=3：前两次失败、第三次成功
        mock.patch.object(_config, "get_config",
                          return_value=_config.Config(
                              data_dir=self.tmp, fetch_enabled=False,
                              max_attempts=3)).start()
        self.run_claude.side_effect = [
            (1, "fail1", ""), (1, "fail2", ""), (0, json.dumps(_base()), "")]
        self.assertEqual(dr.run_daily(), 0)
        self.assertEqual(self.run_claude.call_count, 3)
        self.assertTrue(self._archive_exists())

    def test_degrade_after_all_attempts_fail(self):
        mock.patch.object(_config, "get_config",
                          return_value=_config.Config(
                              data_dir=self.tmp, fetch_enabled=False,
                              max_attempts=2)).start()
        self.run_claude.side_effect = RuntimeError("down")
        self.assertEqual(dr.run_daily(), 0)
        self.assertEqual(self.run_claude.call_count, 2)
        self.send_mail.assert_called_once()
        # 降级主题带 [降级] 标记
        self.assertIn("[降级]", self.send_mail.call_args[0][5])


if __name__ == "__main__":
    unittest.main()
