# -*- coding: utf-8 -*-
"""fetch_sources 离线单测：HTTP 全部 mock，不依赖网络与代理。"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

from frontier_report import fetch_sources as fs


ARXIV_ATOM = """<?xml version="1.0"?>
<feed><entry>
<title>Test Paper About Sparse Attention</title>
<id>https://arxiv.org/abs/2610.99999</id>
<summary>A short summary for testing purposes.</summary>
</entry></feed>"""


def _fake_get_factory():
    def _fake(url, timeout=fs._TIMEOUT):
        if url == fs.HN_TOP:
            return json.dumps([101, 102])
        if "item/101" in url:
            return json.dumps({"title": "HN Alpha", "url": "https://x/a",
                               "score": 100})
        if "item/102" in url:
            return json.dumps({"title": "HN Beta", "score": 50})
        return ARXIV_ATOM
    return _fake


class RunFetchTests(unittest.TestCase):
    def test_fetch_all_sources_to_cache(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(fs, "_http_get", _fake_get_factory()):
            report, path = fs.run_fetch(d, include_hf=False)
            self.assertTrue(report["arxiv"][0])
            self.assertEqual(report["arxiv"][1], 4)  # 4 分类各 1 条
            self.assertTrue(report["hn"][0])
            self.assertEqual(report["hn"][1], 2)
            self.assertTrue(os.path.isfile(path))
            md, day = fs.load_latest(d)
            self.assertIn("arXiv", md)
            self.assertIn("Sparse Attention", md)
            self.assertIn("HN Alpha", md)
            self.assertEqual(day, date.today().strftime("%Y-%m-%d"))

    def test_source_failure_isolated(self):
        # arxiv 失败不应拖垮 hn
        def fake(url, timeout=fs._TIMEOUT):
            if "arxiv" in url:
                raise OSError("arxiv down")
            return _fake_get_factory()(url, timeout)
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(fs, "_http_get", fake):
            report, _ = fs.run_fetch(d, include_hf=False)
            self.assertFalse(report["arxiv"][0])
            self.assertTrue(report["hn"][0])

    def test_load_latest_fallback_to_previous_day(self):
        with tempfile.TemporaryDirectory() as d:
            yest = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
            p = os.path.join(d, fs.FETCH_FILENAME.format(date=yest))
            with open(p, "w", encoding="utf-8") as f:
                f.write("yesterday cache")
            md, day = fs.load_latest(d)
            self.assertEqual(md, "yesterday cache")
            self.assertEqual(day, yest)

    def test_load_latest_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(fs.load_latest(d))


class RetryTests(unittest.TestCase):
    @staticmethod
    def _mock_opener(seq):
        idx = {"i": 0}

        class Resp:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def read(self_):
                v = seq[idx["i"]]
                idx["i"] += 1
                if isinstance(v, Exception):
                    raise v
                return v

        opener = mock.MagicMock()
        opener.open.return_value = Resp()
        return mock.patch.object(fs, "_build_opener", return_value=opener), idx

    def test_retry_succeeds_after_failures(self):
        patcher, idx = self._mock_opener([OSError("e1"), OSError("e2"), b"ok"])
        with patcher, mock.patch.object(fs.time, "sleep"):
            self.assertEqual(fs._http_get("https://example.com"), "ok")
            self.assertEqual(idx["i"], 3)

    def test_retry_exhausts_and_raises(self):
        patcher, idx = self._mock_opener([OSError("down")] * 3)
        with patcher, mock.patch.object(fs.time, "sleep"):
            with self.assertRaises(OSError):
                fs._http_get("https://example.com")
            self.assertEqual(idx["i"], 3)


class ProxyDiscoveryTests(unittest.TestCase):
    def test_env_proxy_wins(self):
        env = {"HTTPS_PROXY": "http://127.0.0.1:9999"}
        with mock.patch.dict(os.environ, env, clear=False):
            cfg = fs._proxy_config()
            self.assertIn("127.0.0.1:9999", cfg["https"])

    def test_fallback_to_windows_registry(self):
        env = {k: "" for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                               "http_proxy", "https_proxy", "all_proxy")}
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(fs, "_windows_registry_proxy",
                                  return_value={"http": "http://127.0.0.1:7892",
                                                "https": "http://127.0.0.1:7892"}):
            cfg = fs._proxy_config()
            self.assertEqual(cfg["https"], "http://127.0.0.1:7892")

    def test_registry_parse_protocol_pairs(self):
        # ProxyServer 形如 "http=a:1;https=a:2" 应被拆开；局部 import winreg
        # 故 patch sys.modules['winreg']
        fake_reg = mock.MagicMock()
        fake_key = mock.MagicMock()
        fake_reg.OpenKey.return_value = fake_key

        def query(key, name):
            values = {"ProxyEnable": 1,
                      "ProxyServer": "http=127.0.0.1:7892;https=127.0.0.1:7892"}
            return values[name], 0
        fake_reg.QueryValueEx.side_effect = query
        fake_reg.HKEY_CURRENT_USER = "HKCU"
        with mock.patch.dict(sys.modules, {"winreg": fake_reg}), \
                mock.patch.object(os, "name", "nt"):
            cfg = fs._windows_registry_proxy()
            self.assertIn("http", cfg)
            self.assertIn("https", cfg)
            self.assertEqual(cfg["https"], "http://127.0.0.1:7892")

    def test_registry_disabled_returns_empty(self):
        fake_reg = mock.MagicMock()
        fake_key = mock.MagicMock()
        fake_reg.OpenKey.return_value = fake_key
        fake_reg.QueryValueEx.side_effect = lambda k, n: ((0, 0)
                                                          if n == "ProxyEnable"
                                                          else ("", 0))
        fake_reg.HKEY_CURRENT_USER = "HKCU"
        with mock.patch.dict(sys.modules, {"winreg": fake_reg}), \
                mock.patch.object(os, "name", "nt"):
            self.assertEqual(fs._windows_registry_proxy(), {})


if __name__ == "__main__":
    unittest.main()
