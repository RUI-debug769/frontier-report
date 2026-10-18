# -*- coding: utf-8 -*-
import json
import os
import subprocess
import sys
import tempfile
import unittest

from frontier_report import __version__
from frontier_report import cli


class PythonMEntryTests(unittest.TestCase):
    def test_module_entry_version(self):
        # `python -m frontier_report --version` 是 skill 与脚本的首选调用形态，
        # 不依赖 Scripts 目录在 PATH 中
        proc = subprocess.run(
            [sys.executable, "-m", "frontier_report", "--version"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        self.assertIn("frontier-report", proc.stdout.decode("utf-8", "replace"))

    def test_module_entry_help(self):
        proc = subprocess.run(
            [sys.executable, "-m", "frontier_report", "-h"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(proc.returncode, 0)
        out = proc.stdout.decode("utf-8", "replace")
        for cmd in ("daily", "weekly", "sample", "doctor"):
            self.assertIn(cmd, out)


class SampleCommandTests(unittest.TestCase):
    def test_sample_writes_both_kinds(self):
        with tempfile.TemporaryDirectory() as d:
            rc = cli.main(["sample", "--out", d])
            self.assertEqual(rc, 0)
            for name in ("daily-sample.html", "daily-sample.txt",
                         "weekly-sample.html", "weekly-sample.txt"):
                p = os.path.join(d, name)
                self.assertTrue(os.path.isfile(p), name)
                self.assertGreater(os.path.getsize(p), 200, name)

    def test_sample_kind_daily_only(self):
        with tempfile.TemporaryDirectory() as d:
            rc = cli.main(["sample", "--kind", "daily", "--out", d])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.isfile(os.path.join(d, "daily-sample.html")))
            self.assertFalse(os.path.exists(os.path.join(d, "weekly-sample.html")))


class DoctorCommandTests(unittest.TestCase):
    def test_doctor_runs_and_returns_exit_code(self):
        # doctor 的结果依赖机器环境（claude/SMTP 是否就绪），
        # 这里只验证它能完整跑完并返回合法退出码
        with tempfile.TemporaryDirectory() as d:
            rc = cli.main(["doctor", "--data-dir", d])
            self.assertIn(rc, (0, 1))


class VersionConsistencyTests(unittest.TestCase):
    def test_version_format(self):
        parts = __version__.split(".")
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(p.isdigit() for p in parts))


if __name__ == "__main__":
    unittest.main()
