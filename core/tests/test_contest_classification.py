from django.test import TestCase

from core.services.contest_classification import (
    classify_atcoder_category,
    classify_codeforces_division,
)


class ContestClassificationTests(TestCase):
    def test_classify_atcoder_category_by_id(self):
        self.assertEqual(classify_atcoder_category("abc350", "AtCoder Beginner Contest 350"), "ABC")
        self.assertEqual(classify_atcoder_category("arc180", "AtCoder Regular Contest 180"), "ARC")
        self.assertEqual(classify_atcoder_category("agc070", "AtCoder Grand Contest 070"), "AGC")
        self.assertEqual(classify_atcoder_category("ahc030", "AtCoder Heuristic Contest 030"), "AHC")

    def test_classify_atcoder_category_by_title(self):
        self.assertEqual(classify_atcoder_category("future001", "AtCoder Beginner Contest X"), "ABC")
        self.assertEqual(classify_atcoder_category("future002", "AtCoder Regular Contest X"), "ARC")
        self.assertEqual(classify_atcoder_category("future003", "AtCoder Grand Contest X"), "AGC")
        self.assertEqual(classify_atcoder_category("future004", "AtCoder Heuristic Contest X"), "AHC")
        self.assertEqual(classify_atcoder_category("future005", "Some Other Contest"), "Other")

    def test_classify_codeforces_division(self):
        self.assertEqual(
            classify_codeforces_division("Educational Codeforces Round 165 (Rated for Div. 2)"),
            "Educational",
        )
        self.assertEqual(
            classify_codeforces_division("Codeforces Global Round 27"),
            "Global",
        )
        self.assertEqual(
            classify_codeforces_division("Codeforces Round 949 (Div. 4)"),
            "Div4",
        )
        self.assertEqual(
            classify_codeforces_division("Codeforces Round 950 (Div. 3)"),
            "Div3",
        )
        self.assertEqual(
            classify_codeforces_division("Codeforces Round 951 (Div. 1 + Div. 2)"),
            "Div2",
        )
        self.assertEqual(
            classify_codeforces_division("Codeforces Round 952 (Div. 2)"),
            "Div2",
        )
        self.assertEqual(
            classify_codeforces_division("Codeforces Round 953 (Div. 1)"),
            "Div1",
        )
        self.assertEqual(
            classify_codeforces_division("Codeforces Round XYZ"),
            "Other",
        )
