# -*- coding: utf-8 -*-
import os
import tempfile
import unittest

from frontier_report import common as rc
from frontier_report import theme as wt


class StripAnsiTests(unittest.TestCase):
    def test_strips(self):
        self.assertEqual(rc.strip_ansi("x\x1b[31m红\x1b[0my"), "x红y")


class GetEnvTests(unittest.TestCase):
    def test_env_var_wins(self):
        os.environ["FRONTIER_TEST_ENV"] = "v1"
        try:
            self.assertEqual(rc.get_env("FRONTIER_TEST_ENV"), "v1")
        finally:
            del os.environ["FRONTIER_TEST_ENV"]

    def test_missing_returns_default(self):
        self.assertEqual(rc.get_env("FRONTIER_TEST_MISSING_XYZ", "d"), "d")


class MakeLoggerTests(unittest.TestCase):
    def test_appends_with_timestamp(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "run.log")
            lg = rc.make_logger(p)
            lg("hello")
            with open(p, encoding="utf-8") as fh:
                txt = fh.read()
            self.assertIn("hello", txt)
            self.assertRegex(txt, r"\[\d{4}-\d{2}-\d{2} ")


class ExtractJsonTests(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(rc.extract_json('{"a": 1}'), {"a": 1})

    def test_noise_then_json_with_validator(self):
        raw = ('warn line\n{"model":"x"}\n{"a": 1}')
        def validator(o):
            if "a" not in o:
                raise ValueError("缺 a")
            return o
        self.assertEqual(rc.extract_json(raw, validator), {"a": 1})

    def test_python_repr(self):
        self.assertEqual(rc.extract_json("{'a': 1}"), {"a": 1})

    def test_fenced(self):
        self.assertEqual(rc.extract_json("```json\n{'a': 1}\n```"), {"a": 1})

    def test_validator_reason_kept(self):
        def validator(o):
            raise ValueError("schema-原因")
        try:
            rc.extract_json('{"a": 1}', validator)
        except rc.JsonExtractError as e:
            self.assertEqual(e.reason, "schema-原因")
        else:
            self.fail("应抛 JsonExtractError")

    def test_garbage_no_reason(self):
        try:
            rc.extract_json("no braces here")
        except rc.JsonExtractError as e:
            self.assertIsNone(e.reason)
        else:
            self.fail("应抛 JsonExtractError")


class RunClaudeTests(unittest.TestCase):
    def test_stderr_split_three_tuple(self):
        class FakeProc:
            returncode = 0
            stdout = b"out"
            stderr = b"err"
        orig = rc.subprocess.run
        rc.subprocess.run = lambda *a, **k: FakeProc()
        try:
            code, out, err = rc.run_claude("p", 100, extra_tools=(),
                                           claude_exe="claude")
            self.assertEqual((code, out, err), (0, "out", "err"))
        finally:
            rc.subprocess.run = orig

    def test_claude_exe_auto_resolution_never_empty(self):
        # 未传 claude_exe 时经 config.resolve_claude_exe 解析，
        # 任何环境都必须返回非空字符串（最终回退裸命令名）
        seen = {}

        class FakeProc:
            returncode = 0
            stdout = b"o"
            stderr = b"e"

        def _fake_run(*a, **k):
            seen["args"] = a[0]
            return FakeProc()
        orig = rc.subprocess.run
        rc.subprocess.run = _fake_run
        try:
            rc.run_claude("p", 100, extra_tools=())
            self.assertTrue(seen["args"][0])  # 可执行名非空
            self.assertEqual(seen["args"][1], "-p")
        finally:
            rc.subprocess.run = orig


class SendHtmlMailTests(unittest.TestCase):
    def _factory(self, fails):
        calls = {"n": 0, "sent": 0, "sleeps": []}

        class FakeSMTP:
            def __init__(self, host="", port=0, timeout=30):
                calls["n"] += 1
                self._host = host
                if calls["n"] <= fails:
                    raise ConnectionError("closed")
            def ehlo(self): pass
            def starttls(self):
                if not self._host:
                    raise ValueError("empty host")
            def login(self, *a): pass
            def sendmail(self, *a): calls["sent"] += 1
            def quit(self): pass
        return FakeSMTP, calls

    def test_success_first_attempt(self):
        Fake, calls = self._factory(0)
        rc.send_html_mail("h", 1, "u", "p", "t", "s", "tx", "<i>",
                          sleep_fn=lambda s: calls["sleeps"].append(s),
                          smtp_factory=Fake)
        self.assertEqual((calls["n"], calls["sent"]), (1, 1))

    def test_retry_then_success(self):
        Fake, calls = self._factory(1)
        sleeps = []
        rc.send_html_mail("h", 1, "u", "p", "t", "s", "tx", "<i>",
                          sleep_fn=sleeps.append, smtp_factory=Fake)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(calls["sent"], 1)
        self.assertEqual(sleeps, [15])

    def test_three_failures_raise(self):
        Fake, calls = self._factory(9)
        with self.assertRaises(rc.ReportError):
            rc.send_html_mail("h", 1, "u", "p", "t", "s", "tx", "<i>",
                              sleep_fn=lambda s: None, smtp_factory=Fake)
        self.assertEqual(calls["n"], 3)

    def test_quit_fail_after_send_no_resend(self):
        calls = {"sent": 0, "n": 0}
        class FakeSMTP:
            def __init__(self, *a, **k): calls["n"] += 1
            def ehlo(self): pass
            def starttls(self): pass
            def login(self, *a): pass
            def sendmail(self, *a): calls["sent"] += 1
            def quit(self): raise RuntimeError("quit closed")
        rc.send_html_mail("h", 1, "u", "p", "t", "s", "tx", "<i>",
                          sleep_fn=lambda s: None, smtp_factory=FakeSMTP)
        self.assertEqual((calls["sent"], calls["n"]), (1, 1))

    def test_connect_stage_smtp_error_retried_then_success(self):
        # (a) 连接/握手阶段的 SMTP 异常仍按 [15,45] 重试，最终成功
        calls = {"n": 0, "sent": 0}

        class FakeSMTP:
            def __init__(self, host="", port=0, timeout=30):
                calls["n"] += 1
            def ehlo(self):
                if calls["n"] == 1:
                    raise rc.smtplib.SMTPConnectError(421, b"connect busy")
            def starttls(self): pass
            def login(self, *a): pass
            def sendmail(self, *a): calls["sent"] += 1
            def quit(self): pass
        sleeps = []
        rc.send_html_mail("h", 1, "u", "p", "t", "s", "tx", "<i>",
                          sleep_fn=sleeps.append, smtp_factory=FakeSMTP)
        self.assertEqual((calls["n"], calls["sent"]), (2, 1))
        self.assertEqual(sleeps, [15])

    def test_quit_fail_warns_mail_maybe_sent_no_resend(self):
        # (b) sendmail 成功后 quit 抛异常：不二次调用、正常返回，
        # 且 warning 说明"邮件可能已发出"
        calls = {"n": 0, "sent": 0}
        logs = []

        class FakeSMTP:
            def __init__(self, *a, **k): calls["n"] += 1
            def ehlo(self): pass
            def starttls(self): pass
            def login(self, *a): pass
            def sendmail(self, *a): calls["sent"] += 1
            def quit(self): raise OSError("connection reset")
        rc.send_html_mail("h", 1, "u", "p", "t", "s", "tx", "<i>",
                          sleep_fn=lambda s: None, smtp_factory=FakeSMTP,
                          logger=logs.append)
        self.assertEqual((calls["n"], calls["sent"]), (1, 1))
        self.assertTrue(any("可能已发出" in m for m in logs), logs)

    def test_unexpected_error_no_retry_raises_report_error(self):
        # 未预期异常（非可重试类型）立即按 ReportError 失败，不重试、不等待
        calls = {"n": 0}
        sleeps = []

        class FakeSMTP:
            def __init__(self, *a, **k): calls["n"] += 1
            def ehlo(self): raise TypeError("bug in fake handshake")
            def starttls(self): pass
            def login(self, *a): pass
            def sendmail(self, *a): pass
            def quit(self): pass
        with self.assertRaises(rc.ReportError):
            rc.send_html_mail("h", 1, "u", "p", "t", "s", "tx", "<i>",
                              sleep_fn=sleeps.append, smtp_factory=FakeSMTP)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(sleeps, [])

    def test_recipients_refused_retried_then_success(self):
        # sendmail 阶段的 SMTPRecipientsRefused 属可重试类型（服务端未接收）
        calls = {"n": 0, "sent": 0}

        class FakeSMTP:
            def __init__(self, *a, **k): calls["n"] += 1
            def ehlo(self): pass
            def starttls(self): pass
            def login(self, *a): pass
            def sendmail(self, *a):
                if calls["n"] < 3:
                    raise rc.smtplib.SMTPRecipientsRefused(
                        {"t": (550, b"mailbox unavailable")})
                calls["sent"] += 1
            def quit(self): pass
        sleeps = []
        rc.send_html_mail("h", 1, "u", "p", "t", "s", "tx", "<i>",
                          sleep_fn=sleeps.append, smtp_factory=FakeSMTP)
        self.assertEqual((calls["n"], calls["sent"]), (3, 1))
        self.assertEqual(sleeps, [15, 45])


class DividerBgTests(unittest.TestCase):
    def test_default_bg_08_custom_bg_07(self):
        self.assertIn("rgba(255,255,255,.08)", wt.divider())
        self.assertIn("rgba(255,255,255,.07)",
                      wt.divider(bg="rgba(255,255,255,.07)"))
        self.assertNotIn("rgba(255,255,255,.08)",
                         wt.divider(bg="rgba(255,255,255,.07)"))


if __name__ == "__main__":
    unittest.main()
