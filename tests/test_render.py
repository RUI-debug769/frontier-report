# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from datetime import date

from frontier_report import weekly as wr

# 测试卫生：FakeSMTP 等用例会触发模块级 log()，单测期间重定向为 no-op，
# 绝不往真实 run.log 写假记录。
wr.log = lambda msg: None


class CollectDailyReportsTests(unittest.TestCase):
    def _write(self, d, ds, content):
        p = os.path.join(d, "ai-daily-report-%s.md" % ds)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)

    def test_collects_last_7_valid_files_newest_first(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "2026-09-19", "X" * 250)
            self._write(d, "2026-09-18", "Y" * 250)
            self._write(d, "2026-09-13", "Z" * 250)  # 第 7 天前
            digests, missing = wr.collect_daily_reports(
                today=date(2026, 9, 19), daily_dir=d)
            dates = [ds for ds, _ in digests]
            self.assertEqual(dates, ["2026-09-19", "2026-09-18", "2026-09-13"])
            self.assertEqual(digests[0][1], "X" * 250)
            # 窗口内其余 4 天缺失
            self.assertEqual(len(missing), 4)

    def test_tiny_file_counted_as_corrupt_missing(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "2026-09-19", "x" * 49)   # 类似坏文件
            digests, missing = wr.collect_daily_reports(
                today=date(2026, 9, 19), daily_dir=d, count=1)
            self.assertEqual(digests, [])
            self.assertEqual(missing, ["2026-09-19(损坏)"])

    def test_non_utf8_file_counted_as_corrupt_missing(self):
        # >200 字节但含非 UTF-8 坏字节：按损坏计入 missing，不中断整周聚合
        import shutil
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "ai-daily-report-2026-09-19.md")
            with open(p, "wb") as f:
                f.write(b"\xff\xfe" + b"x" * 250)
            digests, missing = wr.collect_daily_reports(
                today=date(2026, 9, 19), daily_dir=d, count=1)
            self.assertEqual(digests, [])
            self.assertEqual(missing, ["2026-09-19(损坏)"])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_no_files_at_all(self):
        with tempfile.TemporaryDirectory() as d:
            digests, missing = wr.collect_daily_reports(
                today=date(2026, 9, 19), daily_dir=d, count=2)
            self.assertEqual(digests, [])
            self.assertEqual(len(missing), 2)


GOOD = {
    "headline": {"title": "上下文工程", "summary": "管理模型能看到什么成为新核心能力。"},
    "items": [
        {"kind": "concept", "title": "上下文工程", "what": "系统管理上下文。", "why": "决定智能上限。"},
        {"kind": "idea", "title": "延迟即界面", "what": "停顿是设计材料。", "why": "交互范式重写。"},
        {"kind": "path", "title": "Agentic RAG", "what": "循环内自主检索。", "why": ""},
        {"kind": "concept", "title": "记忆外化", "what": "记忆是产品能力。", "why": "差异化来源。"},
        {"kind": "idea", "title": "小模型路由", "what": "按难度分流。", "why": "成本结构变化。"},
    ],
    "signal": {"points": ["信号甲", "信号乙", "信号丙"]},
}


class ParseWeeklyJsonTests(unittest.TestCase):
    def test_plain_json(self):
        import json as _json
        data = wr.parse_weekly_json(_json.dumps(GOOD, ensure_ascii=False))
        self.assertEqual(data["headline"]["title"], "上下文工程")
        self.assertEqual(len(data["items"]), 5)
        self.assertEqual(data["signal"]["points"][0], "信号甲")

    def test_fenced_with_ansi_and_prose(self):
        raw = ("前缀解释文字\n\x1b[31m```json\n"
               + __import__("json").dumps(GOOD, ensure_ascii=False)
                + "\n```\n后缀")
        data = wr.parse_weekly_json(raw)
        self.assertEqual(data["items"][0]["kind"], "concept")

    def test_bad_json_raises(self):
        with self.assertRaises(wr.WeeklyReportError):
            wr.parse_weekly_json("not a json at all")

    def test_items_count_out_of_range(self):
        import copy, json
        bad = copy.deepcopy(GOOD)
        bad["items"] = bad["items"][:4]  # 仅 4 条
        with self.assertRaises(wr.WeeklyReportError):
            wr.parse_weekly_json(json.dumps(bad, ensure_ascii=False))

    def test_too_many_paths(self):
        import copy, json
        bad = copy.deepcopy(GOOD)
        for it in bad["items"][:3]:
            it["kind"] = "path"
            it["why"] = ""
        with self.assertRaises(wr.WeeklyReportError):
            wr.parse_weekly_json(json.dumps(bad, ensure_ascii=False))

    def test_5_items_with_2_paths_rejected(self):
        import copy, json
        bad = copy.deepcopy(GOOD)  # 5 条、1 path
        bad["items"][1]["kind"] = "path"
        bad["items"][1]["why"] = ""  # 变成 5 条、2 path（概念/想法仅 60%）
        with self.assertRaises(wr.WeeklyReportError):
            wr.parse_weekly_json(json.dumps(bad, ensure_ascii=False))

    def test_6_items_with_2_paths_rejected(self):
        import copy, json
        bad = copy.deepcopy(GOOD)  # 5 条、1 path
        bad["items"][1]["kind"] = "path"
        bad["items"][1]["why"] = ""
        bad["items"].append(
            {"kind": "concept", "title": "新增概念", "what": "定义。", "why": "意义。"})
        self.assertEqual(len(bad["items"]), 6)  # 6 条、2 path，仍不满足联动比例
        with self.assertRaises(wr.WeeklyReportError):
            wr.parse_weekly_json(json.dumps(bad, ensure_ascii=False))

    def test_5_items_with_1_path_accepted(self):
        import copy, json
        good = copy.deepcopy(GOOD)  # 5 条、1 path（概念/想法 4/5 = 80%）
        data = wr.parse_weekly_json(json.dumps(good, ensure_ascii=False))
        self.assertEqual(len(data["items"]), 5)
        self.assertEqual(sum(1 for it in data["items"] if it["kind"] == "path"), 1)

    def test_7_items_with_2_paths_accepted(self):
        import copy, json
        good = copy.deepcopy(GOOD)  # 5 条、1 path
        good["items"].append(
            {"kind": "path", "title": "实现路径乙", "what": "工程路线。", "why": ""})
        good["items"].append(
            {"kind": "concept", "title": "新增概念", "what": "定义。", "why": "意义。"})
        self.assertEqual(len(good["items"]), 7)  # 7 条、2 path（5/7 ≈ 71%）
        data = wr.parse_weekly_json(json.dumps(good, ensure_ascii=False))
        self.assertEqual(len(data["items"]), 7)
        self.assertEqual(sum(1 for it in data["items"] if it["kind"] == "path"), 2)

    def test_concept_requires_why(self):
        import copy, json
        bad = copy.deepcopy(GOOD)
        bad["items"][0]["why"] = "   "
        with self.assertRaises(wr.WeeklyReportError):
            wr.parse_weekly_json(json.dumps(bad, ensure_ascii=False))

    def test_signal_count_must_be_3_to_5(self):
        import copy, json
        bad = copy.deepcopy(GOOD)
        bad["signal"]["points"] = ["只有两条", "之二"]
        with self.assertRaises(wr.WeeklyReportError):
            wr.parse_weekly_json(json.dumps(bad, ensure_ascii=False))

    def test_unknown_kind_rejected(self):
        import copy, json
        bad = copy.deepcopy(GOOD)
        bad["items"][0]["kind"] = "news"
        with self.assertRaises(wr.WeeklyReportError):
            wr.parse_weekly_json(json.dumps(bad, ensure_ascii=False))

    def test_headline_title_null_rejected(self):
        # 键存在但值为 JSON null：不得 fail-open 成字面 "None"
        import copy, json
        bad = copy.deepcopy(GOOD)
        bad["headline"]["title"] = None
        with self.assertRaises(wr.WeeklyReportError):
            wr.parse_weekly_json(json.dumps(bad, ensure_ascii=False))

    def test_item_title_number_rejected(self):
        import copy, json
        bad = copy.deepcopy(GOOD)
        bad["items"][0]["title"] = 123  # 数字不得被静默字符串化
        with self.assertRaises(wr.WeeklyReportError):
            wr.parse_weekly_json(json.dumps(bad, ensure_ascii=False))

    def test_item_what_bool_rejected(self):
        import copy, json
        bad = copy.deepcopy(GOOD)
        bad["items"][0]["what"] = True  # 布尔不得被静默字符串化成 "True"
        with self.assertRaises(wr.WeeklyReportError):
            wr.parse_weekly_json(json.dumps(bad, ensure_ascii=False))

    def test_concept_why_null_rejected(self):
        import copy, json
        bad = copy.deepcopy(GOOD)
        bad["items"][0]["why"] = None  # concept/idea 的 why 不允许 null
        with self.assertRaises(wr.WeeklyReportError):
            wr.parse_weekly_json(json.dumps(bad, ensure_ascii=False))

    def test_path_why_empty_string_accepted(self):
        # path 的 why 仍允许空字符串（仅 null/数字等非字符串不允许）
        import copy, json
        good = copy.deepcopy(GOOD)
        good["items"][2]["why"] = ""
        data = wr.parse_weekly_json(json.dumps(good, ensure_ascii=False))
        self.assertEqual(data["items"][2]["why"], "")

    def test_signal_points_non_string_rejected(self):
        import copy, json
        for val in (None, 123):
            bad = copy.deepcopy(GOOD)
            bad["signal"]["points"][0] = val
            with self.subTest(val=val):
                with self.assertRaises(wr.WeeklyReportError):
                    wr.parse_weekly_json(json.dumps(bad, ensure_ascii=False))

    def test_json_after_cli_noise(self):
        # 真实故障形态（旧链路 stderr 并入 stdout）：模型目录警告 + 遥测事件小 JSON
        # 位于正文之前。raw_decode 扫描必须先命中遥测 JSON、校验失败后跳过，
        # 定位到后面的合法双引号周报 JSON。
        import json
        noise = (
            '"ark-code-latest" isn\'t described by this version\'s model '
            'catalog; append [1m] to the model name for 1M.\n'
            '[claude-code:unrecognized_model] '
            '{"model":"ark-code-latest","query_source":"sdk"}\n'
        )
        raw = noise + "\n" + json.dumps(GOOD, ensure_ascii=False)
        data = wr.parse_weekly_json(raw)
        self.assertEqual(data["headline"]["title"], "上下文工程")
        self.assertEqual(len(data["items"]), 5)
        self.assertEqual(data["signal"]["points"][0], "信号甲")

    def test_python_repr_accepted(self):
        # 模型把周报序列化成 Python dict 字面量（单引号 repr）时仍须可解析；
        # path 的空 why、中文字段都要原样保留，严格类型校验不放松。
        d = {
            "headline": {"title": "中文头条", "summary": "中文摘要。"},
            "items": [
                {"kind": "concept", "title": "概念甲",
                 "what": "是什么。", "why": "为什么。"},
                {"kind": "idea", "title": "想法乙",
                 "what": "怎么做。", "why": "意义。"},
                {"kind": "path", "title": "路径丙",
                 "what": "工程路线。", "why": ""},
            ],
            "signal": {"points": ["信号一", "信号二", "信号三"]},
        }
        # 3 条 items 本不满足 5-7 条，补两条合法 concept 凑成最小合法周报
        d["items"].append(
            {"kind": "concept", "title": "概念丁", "what": "丁。", "why": "丁意义。"})
        d["items"].append(
            {"kind": "idea", "title": "想法戊", "what": "戊。", "why": "戊意义。"})
        raw = repr(d)
        self.assertTrue(raw.lstrip().startswith("{"))  # repr 为单引号 Python 字面量
        data = wr.parse_weekly_json(raw)
        self.assertEqual(data["headline"]["title"], "中文头条")
        self.assertEqual(len(data["items"]), 5)
        self.assertEqual(sum(1 for it in data["items"] if it["kind"] == "path"), 1)
        path_item = next(it for it in data["items"] if it["kind"] == "path")
        self.assertEqual(path_item["why"], "")  # path 空 why 保留为空字符串

    def test_real_failed_output_shape(self):
        # 复刻真实 failed-output 的三行形态：
        # 第1行模型目录警告（含 [1m]）、第2行遥测事件 JSON、
        # 第3行完整合法 Python repr 周报（7 items、2 path、5 points）。
        import copy
        d = copy.deepcopy(GOOD)  # 5 条、1 path
        d["items"].append(
            {"kind": "path", "title": "实现路径乙", "what": "工程路线。", "why": ""})
        d["items"].append(
            {"kind": "concept", "title": "新增概念", "what": "定义。", "why": "意义。"})
        d["signal"]["points"] = ["信号甲", "信号乙", "信号丙", "信号丁", "信号戊"]
        line1 = (
            '"ark-code-latest" isn\'t described by this version\'s model '
            "catalog; update Claude Code, or map it with behavesAs; "
            "append [1m] to the model name for 1M, "
            "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT=1.")
        line2 = ('[claude-code:unrecognized_model] '
                 '{"model":"ark-code-latest","query_source":"sdk"}')
        raw = line1 + "\n" + line2 + "\n" + repr(d)
        data = wr.parse_weekly_json(raw)
        self.assertEqual(len(data["items"]), 7)
        self.assertEqual(sum(1 for it in data["items"] if it["kind"] == "path"), 2)
        self.assertEqual(
            sum(1 for it in data["items"] if it["kind"] in ("concept", "idea")), 5)
        self.assertEqual(len(data["signal"]["points"]), 5)
        self.assertEqual(data["headline"]["title"], "上下文工程")

    def test_items_schema_error_reason_surfaced(self):
        # 语法合法的双引号 JSON 但仅 4 条 items：具体 schema 原因必须冒泡到异常消息，
        # 不能被三级回退吞成泛化文案（否则无法据以修 prompt）
        import copy, json, re
        bad = copy.deepcopy(GOOD)
        bad["items"] = bad["items"][:4]
        with self.assertRaisesRegex(
                wr.WeeklyReportError, re.escape("items 必须为 5-7 条")):
            wr.parse_weekly_json(json.dumps(bad, ensure_ascii=False))

    def test_signal_schema_error_reason_surfaced(self):
        # 合法 JSON 但 signal.points 仅 2 条：消息须含 signal.points 具体原因
        # 及"最后一次校验"前导
        import copy, json
        bad = copy.deepcopy(GOOD)
        bad["signal"]["points"] = ["只有两条", "之二"]
        try:
            wr.parse_weekly_json(json.dumps(bad, ensure_ascii=False))
        except wr.WeeklyReportError as e:
            msg = str(e)
            self.assertIn("signal.points", msg)
            self.assertIn("最后一次校验", msg)
        else:
            self.fail("应当抛出 WeeklyReportError")

    def test_garbage_keeps_generic_message_without_reason(self):
        # 纯垃圾、从未解出任何对象：保持泛化文案，不得附带"最后一次校验"
        try:
            wr.parse_weekly_json("garbage no braces")
        except wr.WeeklyReportError as e:
            self.assertEqual(str(e), "输出中找不到合法的周报 JSON 对象")
            self.assertNotIn("最后一次校验", str(e))
        else:
            self.fail("应当抛出 WeeklyReportError")


class IssueNumberTests(unittest.TestCase):
    def test_number_is_archive_count_plus_one(self):
        with tempfile.TemporaryDirectory() as d:
            for n in ("2026-09-05", "2026-09-12"):
                open(os.path.join(d, "ai-weekly-ideas-%s.md" % n),
                     "w", encoding="utf-8").close()
            open(os.path.join(d, "employment-weekly-2026-08-31.md"),
                 "w", encoding="utf-8").close()  # 旧命名不计
            self.assertEqual(wr.issue_number(d), 3)


class RenderTextTests(unittest.TestCase):
    def test_text_contains_all_sections_and_tag_numbers(self):
        data = wr.parse_weekly_json(__import__("json").dumps(GOOD, ensure_ascii=False))
        txt = wr.render_weekly_text(data, 7, "2026-09-26")
        self.assertIn("No.07", txt)
        self.assertIn("2026.09.26", txt)
        self.assertIn("上下文工程", txt)
        self.assertIn("【概念 01】", txt)
        self.assertIn("【想法 02】", txt)
        self.assertIn("【实现路径】Agentic RAG", txt)
        self.assertIn("【概念 03】记忆外化", txt)  # path 不占编号
        self.assertIn("为什么重要：决定智能上限。", txt)
        self.assertIn("就业与社会范式 · 本周信号", txt)
        self.assertIn("· 信号甲", txt)
        self.assertIn("第 07 期", txt)


class RenderHtmlTests(unittest.TestCase):
    def setUp(self):
        self.data = wr.parse_weekly_json(
            __import__("json").dumps(GOOD, ensure_ascii=False))
        self.html = wr.render_weekly_html(self.data, 1, "2026-09-26")

    def test_structure_and_palette(self):
        h = self.html
        self.assertTrue(h.lstrip().lower().startswith("<!doctype html"))
        self.assertIn('width="640"', h)
        self.assertIn("#26221F", h)   # 主面板
        self.assertIn("#322D29", h)   # 卡片
        self.assertIn("#CC785C", h)   # kicker 陶土橙
        self.assertIn("#E09A82", h)   # 概念字色
        self.assertIn("#D9B98A", h)   # 想法字色
        self.assertIn("No.01", h)
        self.assertIn("2026.09.26", h)
        self.assertIn("上下文工程", h)
        self.assertIn("就业与社会范式 · 本周信号", h)

    def test_tags_colored_and_numbered(self):
        h = self.html
        self.assertIn("概念 · 01", h)
        self.assertIn("想法 · 02", h)
        self.assertIn(">实现路径<", h)
        self.assertNotIn("实现路径 ·", h)

    def test_xss_is_escaped(self):
        import copy, json
        evil = copy.deepcopy(GOOD)
        evil["headline"]["title"] = "<script>alert(1)</script>"
        evil["items"][0]["what"] = '<img src=x onerror="alert(1)">'
        data = wr.parse_weekly_json(json.dumps(evil, ensure_ascii=False))
        h = wr.render_weekly_html(data, 1, "2026-09-26")
        self.assertNotIn("<script>", h)
        self.assertNotIn('onerror="', h)
        self.assertIn("&lt;script&gt;", h)
        self.assertIn("&lt;img", h)

    def test_empty_why_path_renders_no_quote_block(self):
        # GOOD 中 path 项 why 为空
        self.assertNotIn("为什么重要", self.html)
        # 非空 why 出现竖线引用样式
        self.assertIn("border-left:3px solid #8A5541", self.html)

    def test_multiline_what_becomes_paragraphs(self):
        import copy, json
        multi = copy.deepcopy(GOOD)
        multi["items"][0]["what"] = "第一段。\n\n第二段。"
        data = wr.parse_weekly_json(json.dumps(multi, ensure_ascii=False))
        h = wr.render_weekly_html(data, 1, "2026-09-26")
        self.assertEqual(h.count("第一段。"), 1)
        self.assertIn("第二段。", h)


class BuildPromptTests(unittest.TestCase):
    def test_digests_injected_with_date_headers(self):
        with tempfile.TemporaryDirectory() as d:
            tpl_path = os.path.join(d, "prompt.md")
            with open(tpl_path, "w", encoding="utf-8") as f:
                f.write("NOTE<<DIGEST_NOTE>>BODY<<DAILY_DIGEST>>END")
            prompt = wr.build_weekly_prompt(
                [("2026-09-19", "日报甲内容"), ("2026-09-18", "日报乙内容")],
                weekly_dir=d)
            self.assertIn("2 份日报", prompt)
            self.assertIn("## 2026-09-19 的日报", prompt)
            self.assertIn("日报甲内容", prompt)
            self.assertNotIn("<<DAILY_DIGEST>>", prompt)
            self.assertNotIn("<<DIGEST_NOTE>>", prompt)

    def test_empty_digests_message(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "prompt.md"), "w", encoding="utf-8") as f:
                f.write("<<DIGEST_NOTE>>|<<DAILY_DIGEST>>")
            prompt = wr.build_weekly_prompt([], weekly_dir=d)
            self.assertIn("无可用日报", prompt)

    def test_packaged_template_fallback(self):
        # weekly_dir 无 prompt.md 且 config 无覆盖时，回退包内置模板
        with tempfile.TemporaryDirectory() as d:
            prompt = wr.build_weekly_prompt(
                [("2026-09-19", "日报内容")], weekly_dir=d)
            self.assertIn("AI 技术与思路周报", prompt)
            self.assertIn("日报内容", prompt)
            self.assertNotIn("<<DAILY_DIGEST>>", prompt)

    def test_template_unavailable_raises(self):
        from unittest import mock
        with mock.patch.object(wr, "_load_weekly_prompt_template",
                               return_value=None):
            with self.assertRaises(wr.WeeklyReportError):
                wr.build_weekly_prompt([("2026-09-19", "x")])


class SendMailTests(unittest.TestCase):
    def _fake_factory(self, failures):
        calls = {"n": 0, "sleeps": [], "sent": [], "hosts": []}

        class FakeSMTP:
            # 签名对齐真实 smtplib.SMTP(host, port, timeout=...)：构造即连接
            def __init__(self, host="", port=0, timeout=30):
                calls["n"] += 1
                calls["hosts"].append(host)
                self._host = host  # smtplib 只在 __init__ 保存它，STARTTLS 的 SNI 取它
                if calls["n"] <= failures:
                    raise ConnectionError("unexpectedly closed")
            def ehlo(self): pass
            def starttls(self):
                # 对齐 ssl.SSLContext.wrap_socket：空 server_hostname 直接拒绝
                if not self._host:
                    raise ValueError(
                        "server_hostname cannot be an empty string or start with a leading dot")
            def login(self, u, p): pass
            def sendmail(self, frm, to, raw):
                calls["sent"].append(raw)
                self._raw = raw
            def quit(self): pass

        def fake_sleep(s):
            calls["sleeps"].append(s)
        return FakeSMTP, fake_sleep, calls

    def test_retry_then_success(self):
        import email as _email
        import os as _os
        _os.environ["SMTP_HOST"] = "smtp.qq.com"
        _os.environ["SMTP_PORT"] = "587"
        _os.environ["SMTP_USER"] = "a@example.com"
        _os.environ["SMTP_PASS"] = "code"
        _os.environ["REPORT_TO_EMAIL"] = "b@example.com"
        Fake, fake_sleep, calls = self._fake_factory(failures=2)
        wr.send_weekly_mail("主题", "纯文本", "<html></html>",
                            sleep_fn=fake_sleep, smtp_factory=Fake)
        self.assertEqual(calls["n"], 3)
        # 每次构造（含重试）都必须把 host 传进去，供 STARTTLS SNI 使用
        self.assertEqual(calls["hosts"], ["smtp.qq.com"] * 3)
        self.assertEqual(calls["sleeps"], [15, 45])
        self.assertEqual(len(calls["sent"]), 1)
        # 中文经 base64/quoted-printable 编码，必须 parse 后解码断言
        msg = _email.message_from_string(calls["sent"][0])
        self.assertEqual(msg.get_content_type(), "multipart/alternative")
        parts = msg.get_payload()
        self.assertEqual(parts[0].get_content_type(), "text/plain")
        self.assertEqual(parts[1].get_content_type(), "text/html")
        self.assertIn("纯文本", parts[0].get_payload(decode=True).decode("utf-8"))
        self.assertIn("<html></html>", parts[1].get_payload(decode=True).decode("utf-8"))

    def test_three_failures_raises(self):
        import os as _os
        _os.environ["SMTP_HOST"] = "smtp.qq.com"
        _os.environ["SMTP_PORT"] = "587"
        _os.environ["SMTP_USER"] = "a@example.com"
        _os.environ["SMTP_PASS"] = "code"
        _os.environ["REPORT_TO_EMAIL"] = "b@example.com"
        Fake, fake_sleep, calls = self._fake_factory(failures=9)
        with self.assertRaises(wr.WeeklyReportError):
            wr.send_weekly_mail("主题", "t", "<p>h</p>",
                                sleep_fn=fake_sleep, smtp_factory=Fake)
        self.assertEqual(calls["n"], 3)
        # 末次失败后不再白等：仅前两次失败后退避
        self.assertEqual(calls["sleeps"], [15, 45])

    def test_quit_failure_after_send_does_not_resend(self):
        # sendmail 已成功、quit 抛异常：函数不抛错，且绝不重试（防重复发信）
        import os as _os
        _os.environ["SMTP_USER"] = "a@example.com"
        _os.environ["SMTP_PASS"] = "code"
        _os.environ["REPORT_TO_EMAIL"] = "b@example.com"
        _os.environ["SMTP_HOST"] = "smtp.qq.com"
        _os.environ["SMTP_PORT"] = "587"
        calls = {"connect": 0, "sent": 0, "sleeps": [], "host": None}

        class FakeSMTP:
            def __init__(self, host="", port=0, timeout=30):
                calls["connect"] += 1
                calls["host"] = host
                self._host = host
            def ehlo(self): pass
            def starttls(self):
                if not self._host:
                    raise ValueError(
                        "server_hostname cannot be an empty string or start with a leading dot")
            def login(self, u, p): pass
            def sendmail(self, frm, to, raw):
                calls["sent"] += 1
            def quit(self):
                raise RuntimeError("connection closed on quit")

        def fake_sleep(s):
            calls["sleeps"].append(s)

        wr.send_weekly_mail("主题", "t", "<p>h</p>",
                            sleep_fn=fake_sleep, smtp_factory=FakeSMTP)
        self.assertEqual(calls["sent"], 1)
        self.assertEqual(calls["connect"], 1)
        self.assertEqual(calls["host"], "smtp.qq.com")
        self.assertEqual(calls["sleeps"], [])

    def test_factory_receives_host_for_starttls_sni(self):
        # 回归历史实跑事故：先 SMTP(timeout=30) 再手动 connect(host, port)
        # 不会设置 _host（smtplib 只在 __init__ 保存它），starttls 用空 _host 做 SNI
        # 被 ssl 拒绝（server_hostname cannot be an empty string）。host/port 必须构造时传入。
        import os as _os
        _os.environ["SMTP_HOST"] = "smtp.qq.com"
        _os.environ["SMTP_PORT"] = "587"
        _os.environ["SMTP_USER"] = "a@example.com"
        _os.environ["SMTP_PASS"] = "code"
        _os.environ["REPORT_TO_EMAIL"] = "b@example.com"
        seen = {}

        class FakeSMTP:
            def __init__(self, host="", port=0, timeout=30):
                seen["host"] = host
                seen["port"] = port
                self._host = host
            def ehlo(self): pass
            def starttls(self):
                if not self._host:
                    raise ValueError(
                        "server_hostname cannot be an empty string or start with a leading dot")
            def login(self, u, p): pass
            def sendmail(self, frm, to, raw): pass
            def quit(self): pass

        wr.send_weekly_mail("s", "t", "<p>h</p>",
                            sleep_fn=lambda s: None, smtp_factory=FakeSMTP)
        self.assertEqual(seen, {"host": "smtp.qq.com", "port": 587})

    def test_missing_credentials_raises(self):
        # 必须连注册表回退一起屏蔽，否则真实机器会从 HKCU\Environment 读到凭据
        orig = wr.get_env
        wr.get_env = lambda name, default="": ""
        try:
            with self.assertRaises(wr.WeeklyReportError):
                wr.send_weekly_mail("s", "t", "<p>h</p>")
        finally:
            wr.get_env = orig


class SampleSelfConsistencyTests(unittest.TestCase):
    def test_sample_data_passes_schema_and_renders(self):
        import json
        data = wr.parse_weekly_json(json.dumps(wr.SAMPLE_DATA, ensure_ascii=False))
        self.assertTrue(wr.render_weekly_html(data, 1, "2026-09-26"))
        self.assertTrue(wr.render_weekly_text(data, 1, "2026-09-26"))


if __name__ == "__main__":
    unittest.main()
