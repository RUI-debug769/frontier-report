# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest
from datetime import date, timedelta

from frontier_report import concepts


def _concept(name, last_review, half_life=1.0, review_count=0):
    return {"name": name, "first_seen": "2026-10-01",
            "last_review": last_review, "half_life_days": half_life,
            "review_count": review_count, "one_liner": "一句话",
            "explanation": "解释", "news_hook": ""}


def _days_ago(n, base=date(2026, 10, 18)):
    return (base - timedelta(days=n)).strftime("%Y-%m-%d")


class LoadSaveTests(unittest.TestCase):
    def test_missing_file_returns_empty_store(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(concepts.load_store(d), {"concepts": []})

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            store = {"concepts": [_concept("MoE", "2026-10-18")]}
            concepts.save_store(d, store)
            self.assertEqual(concepts.load_store(d), store)

    def test_corrupt_file_backed_up_and_raises(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, concepts.STORE_FILENAME)
            with open(path, "w", encoding="utf-8") as f:
                f.write("{not json")
            with self.assertRaises(concepts.ConceptStoreError):
                concepts.load_store(d)
            leftovers = os.listdir(d)
            self.assertEqual(len(leftovers), 1)
            self.assertTrue(leftovers[0].startswith("concepts.json.corrupt-"))


class DueReviewsTests(unittest.TestCase):
    def test_new_concept_due_next_day(self):
        store = {"concepts": [_concept("MoE", _days_ago(1), half_life=1.0)]}
        due = concepts.due_reviews(store, today=date(2026, 10, 18))
        self.assertEqual([c["name"] for c in due], ["MoE"])

    def test_same_day_not_due(self):
        store = {"concepts": [_concept("MoE", _days_ago(0), half_life=1.0)]}
        self.assertEqual(concepts.due_reviews(store, today=date(2026, 10, 18)), [])

    def test_long_half_life_not_due_yet(self):
        store = {"concepts": [_concept("MoE", _days_ago(2), half_life=5.29)]}
        self.assertEqual(concepts.due_reviews(store, today=date(2026, 10, 18)), [])

    def test_urgency_sort_and_limit(self):
        store = {"concepts": [
            _concept("轻微逾期", _days_ago(3), half_life=2.3),    # 紧迫度 1.30
            _concept("严重逾期", _days_ago(30), half_life=1.0),   # 紧迫度 30
            _concept("中度逾期", _days_ago(6), half_life=2.3),    # 紧迫度 2.61
            _concept("未到期", _days_ago(1), half_life=5.29),
        ]}
        due = concepts.due_reviews(store, today=date(2026, 10, 18), limit=2)
        self.assertEqual([c["name"] for c in due], ["严重逾期", "中度逾期"])

    def test_bad_record_skipped(self):
        store = {"concepts": [{"name": "坏记录"}, _concept("好记录", _days_ago(2))]}
        due = concepts.due_reviews(store, today=date(2026, 10, 18))
        self.assertEqual([c["name"] for c in due], ["好记录"])


class FormatBlockTests(unittest.TestCase):
    def test_empty_due_message(self):
        self.assertIn("无到期复习", concepts.format_review_block([]))

    def test_block_contains_scheduling_facts(self):
        c = _concept("KL 散度", _days_ago(4), half_life=1.0, review_count=1)
        block = concepts.format_review_block([c], today=date(2026, 10, 18))
        self.assertIn("KL 散度", block)
        self.assertIn("4 天前", block)
        self.assertIn("第 2 次复习", block)  # review_count 1 + 本次 = 2

    def test_known_names_empty(self):
        self.assertIn("为空", concepts.format_known_names({"concepts": []}))

    def test_known_names_listed(self):
        store = {"concepts": [_concept("MoE", "2026-10-01"),
                              _concept("KL 散度", "2026-10-02")]}
        text = concepts.format_known_names(store)
        self.assertIn("MoE", text)
        self.assertIn("KL 散度", text)


class MatchNameTests(unittest.TestCase):
    def setUp(self):
        self.due = [_concept("KL 散度", _days_ago(2))]

    def test_exact(self):
        self.assertIsNotNone(concepts.match_review_name("KL 散度", self.due))

    def test_normalized(self):
        self.assertIsNotNone(concepts.match_review_name("kl散度", self.due))

    def test_containment(self):
        self.assertIsNotNone(
            concepts.match_review_name("KL 散度（Kullback-Leibler）", self.due))

    def test_no_match(self):
        self.assertIsNone(concepts.match_review_name("交叉熵", self.due))


class ApplyIssueTests(unittest.TestCase):
    def test_new_concept_registered_with_initial_half_life(self):
        store = {"concepts": []}
        concepts.apply_issue(
            store,
            {"name": "MoE", "one_liner": "一", "explanation": "解", "news_hook": ""},
            [], [], today=date(2026, 10, 18))
        c = store["concepts"][0]
        self.assertEqual(c["name"], "MoE")
        self.assertEqual(c["half_life_days"], 1.0)
        self.assertEqual(c["review_count"], 0)
        self.assertEqual(c["first_seen"], "2026-10-18")
        self.assertEqual(c["last_review"], "2026-10-18")

    def test_half_life_growth_sequence(self):
        # 半衰期增长序列：1 → 2.3 → 5.29 → 12.17（墨墨式拉宽间隔）
        store = {"concepts": [_concept("MoE", _days_ago(1), half_life=1.0)]}
        today = date(2026, 10, 18)
        due = concepts.due_reviews(store, today=today)
        concepts.apply_issue(store, None,
                             [{"name": "MoE"}], due, today=today)
        c = store["concepts"][0]
        self.assertEqual((c["review_count"], c["half_life_days"]), (1, 2.3))
        # 再复习一次
        c["last_review"] = _days_ago(3)
        due = concepts.due_reviews(store, today=today)
        concepts.apply_issue(store, None, [{"name": "MoE"}], due, today=today)
        self.assertEqual((c["review_count"], c["half_life_days"]), (2, 5.29))

    def test_duplicate_new_concept_treated_as_review(self):
        # 模型无视已知清单重复教学同名概念：不新增，按复习处理
        store = {"concepts": [_concept("MoE", _days_ago(5), half_life=2.3)]}
        concepts.apply_issue(
            store,
            {"name": "moe", "one_liner": "新说法", "explanation": "解", "news_hook": ""},
            [], [], today=date(2026, 10, 18))
        self.assertEqual(len(store["concepts"]), 1)
        c = store["concepts"][0]
        self.assertEqual(c["review_count"], 1)
        self.assertEqual(c["half_life_days"], round(2.3 * concepts.GROWTH, 2))

    def test_unmatched_review_ignored(self):
        store = {"concepts": [_concept("MoE", _days_ago(5), half_life=1.0)]}
        due = concepts.due_reviews(store, today=date(2026, 10, 18))
        concepts.apply_issue(store, None, [{"name": "不相关概念"}], due,
                             today=date(2026, 10, 18))
        self.assertEqual(store["concepts"][0]["review_count"], 0)


if __name__ == "__main__":
    unittest.main()
