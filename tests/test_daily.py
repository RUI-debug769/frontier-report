# -*- coding: utf-8 -*-
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from frontier_report import config as _config
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
        ],
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
        d = _base(); d["sections"] = d["sections"][:3]
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

    def test_xss_escaped(self):
        d = _base()
        d["briefs"][0] = "<script>x</script>"
        d["sections"][0]["items"][0]["text"] = '<img src=x onerror="a(1)">'
        data = dr.parse_daily_json(json.dumps(d))
        h = dr.render_daily_html(data, "2026-09-23")
        self.assertNotIn("<script>", h)
        self.assertNotIn('onerror="', h)
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


class RunDailyTests(unittest.TestCase):
    """run_daily 编排层：退出码契约（0/1/2）与"失败绝不发信"回归网。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        with open(os.path.join(self.tmp, "prompt.md"), "w", encoding="utf-8") as f:
            f.write("prompt body")
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
        # 隔离全局 config：指向临时目录，避免真实环境配置泄漏进测试
        mock.patch.object(_config, "get_config",
                          return_value=_config.Config(data_dir=self.tmp)).start()
        self.run_claude = mock.patch.object(dr, "_run_claude_daily").start()
        mock.patch.object(dr.rc, "get_env",
                          side_effect=lambda n, d="": self.env.get(n, d)).start()
        self.send_mail = mock.patch.object(dr.rc, "send_html_mail").start()

    def _claude_ok(self):
        self.run_claude.return_value = (0, json.dumps(_base()), "")

    def _failed_output_exists(self):
        fp = os.path.join(self.tmp,
                          "failed-output-%s.txt" % time.strftime("%Y-%m-%d"))
        return os.path.exists(fp)

    def _archive_exists(self):
        fp = os.path.join(self.tmp,
                          "ai-daily-report-%s.md" % time.strftime("%Y-%m-%d"))
        return os.path.exists(fp)

    def test_prompt_unavailable(self):
        mock.patch.object(dr, "_load_daily_prompt", return_value=None).start()
        self.assertEqual(dr.run_daily(), 1)
        self.run_claude.assert_not_called()
        self.send_mail.assert_not_called()

    def test_claude_raises(self):
        self.run_claude.side_effect = RuntimeError("boom")
        self.assertEqual(dr.run_daily(), 1)
        self.send_mail.assert_not_called()

    def test_claude_nonzero_exit(self):
        self.run_claude.return_value = (1, "some output", "")
        self.assertEqual(dr.run_daily(), 1)
        self.send_mail.assert_not_called()

    def test_claude_empty_output(self):
        self.run_claude.return_value = (0, "   \n  ", "")
        self.assertEqual(dr.run_daily(), 1)
        self.send_mail.assert_not_called()

    def test_json_invalid(self):
        self.run_claude.return_value = (0, "not a json at all", "")
        self.assertEqual(dr.run_daily(), 1)
        self.send_mail.assert_not_called()
        self.assertTrue(self._failed_output_exists())

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


if __name__ == "__main__":
    unittest.main()
