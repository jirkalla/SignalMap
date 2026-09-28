"""Unit tests for the pure functions in compare_peec.py (T4, design decision 14).

Runs on the developer PC, stdlib `unittest` only, no DB and no Peec export files:

    python -m unittest tools/local/test_compare_peec.py

`tools/` is not copied into the app's Docker image (see the Dockerfile), so this
cannot live in the app's own pytest suite — it is a separate, host-only run.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

# `python -m unittest tools/local/test_compare_peec.py` does not add the file's own
# directory to sys.path the way running `python tools/local/compare_peec.py` directly
# does — so this needs the same sibling-import bootstrap _dbtools.py relies on, done
# explicitly here instead.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import compare_peec as cp  # noqa: E402


# ---------------------------------------------------------------------------
# Domain normalization (design decision 9)
# ---------------------------------------------------------------------------


class NormalizeDomainTests(unittest.TestCase):
    def test_full_url_with_utm_and_locale_path(self):
        domain, flagged = cp.normalize_domain("https://www.knauf.com/de-DE?utm_source=openai")
        self.assertEqual(domain, "knauf.com")
        self.assertFalse(flagged)

    def test_already_bare_domain_is_unchanged(self):
        domain, flagged = cp.normalize_domain("knauf.com")
        self.assertEqual(domain, "knauf.com")
        self.assertFalse(flagged)

    def test_case_and_www_are_stripped(self):
        domain, flagged = cp.normalize_domain("WWW.Knauf.COM")
        self.assertEqual(domain, "knauf.com")
        self.assertFalse(flagged)

    def test_subdomain_and_multiple_query_params_survive(self):
        domain, flagged = cp.normalize_domain("http://sub.example.co.uk/path?a=b&utm_medium=x")
        self.assertEqual(domain, "sub.example.co.uk")
        self.assertFalse(flagged)

    def test_unparseable_text_is_flagged_not_dropped(self):
        domain, flagged = cp.normalize_domain("not a domain at all")
        self.assertTrue(flagged)
        self.assertEqual(domain, "not a domain at all")  # still returned, never silently discarded

    def test_gemini_redirect_shape_is_flagged(self):
        domain, flagged = cp.normalize_domain("https://vertexaisearch.googleapis.com/redirect/abc123")
        self.assertTrue(flagged)


# ---------------------------------------------------------------------------
# The brand ruler (design decision 6)
# ---------------------------------------------------------------------------


class BrandRulerTests(unittest.TestCase):
    def setUp(self):
        self.rules = cp.build_brand_rules(
            "Knauf", [],
            [("Saint Gobain", []), ("Sto", []), ("Rockwool", [])],
        )

    def test_saint_gobain_and_hyphenated_variant_are_the_same_brand(self):
        hits_space = cp.detect_brands("Der Wettbewerber Saint Gobain ist aktiv.", self.rules)
        hits_hyphen = cp.detect_brands("Der Wettbewerber Saint-Gobain ist aktiv.", self.rules)
        self.assertTrue(hits_space["Saint Gobain"].mentioned)
        self.assertTrue(hits_hyphen["Saint Gobain"].mentioned)

    def test_sto_matches_as_its_own_word(self):
        hits = cp.detect_brands("Sto GmbH liefert Fassadensysteme.", self.rules)
        self.assertTrue(hits["Sto"].mentioned)

    def test_stoff_is_not_sto(self):
        hits = cp.detect_brands("Wir liefern Stoffe und Materialien.", self.rules)
        self.assertFalse(hits["Sto"].mentioned)

    def test_sto_is_case_sensitive(self):
        hits = cp.detect_brands("wir haben sto material bestellt", self.rules)
        self.assertFalse(hits["Sto"].mentioned)

    def test_long_name_matches_any_case(self):
        hits = cp.detect_brands("Der Hersteller ROCKWOOL liefert Daemmstoffe.", self.rules)
        self.assertTrue(hits["Rockwool"].mentioned)


# ---------------------------------------------------------------------------
# Peec 'created' -> Europe/Prague local day (design decision 5)
# ---------------------------------------------------------------------------


class LocalDateTests(unittest.TestCase):
    def test_2200z_is_next_days_midnight_in_prague(self):
        tz = cp.prague_zone()
        created = cp.parse_peec_created("2026-09-22T22:00:00Z")
        self.assertEqual(cp.local_date_of(created, tz), date(2026, 9, 23))


# ---------------------------------------------------------------------------
# Pure statistics helpers
# ---------------------------------------------------------------------------


class JaccardTests(unittest.TestCase):
    def test_both_empty_is_full_agreement(self):
        self.assertEqual(cp.jaccard(set(), set()), 1.0)

    def test_disjoint_sets(self):
        self.assertEqual(cp.jaccard({"a"}, {"b"}), 0.0)

    def test_partial_overlap(self):
        self.assertAlmostEqual(cp.jaccard({"a", "b"}, {"b", "c"}), 1 / 3)


class CohensKappaTests(unittest.TestCase):
    def test_perfect_agreement(self):
        pairs = [(True, True), (True, True), (False, False), (False, False)]
        self.assertAlmostEqual(cp.cohens_kappa(pairs), 1.0)

    def test_chance_level_agreement(self):
        pairs = [(True, True), (True, False), (False, True), (False, False)]
        self.assertAlmostEqual(cp.cohens_kappa(pairs), 0.0)

    def test_perfect_disagreement(self):
        pairs = [(True, False), (False, True)]
        self.assertAlmostEqual(cp.cohens_kappa(pairs), -1.0)

    def test_empty_is_none(self):
        self.assertIsNone(cp.cohens_kappa([]))


class SpearmanTests(unittest.TestCase):
    def test_perfect_positive_correlation(self):
        self.assertAlmostEqual(cp.spearman([(1, 1), (2, 2), (3, 3), (4, 4)]), 1.0)

    def test_perfect_negative_correlation(self):
        self.assertAlmostEqual(cp.spearman([(1, 4), (2, 3), (3, 2), (4, 1)]), -1.0)

    def test_ties_use_midranks(self):
        # x has a tie (ranks 1.5, 1.5, 3); hand-computed expected value is sqrt(3)/2.
        result = cp.spearman([(1, 1), (1, 2), (2, 3)])
        self.assertAlmostEqual(result, 3 ** 0.5 / 2, places=6)

    def test_too_short_is_none(self):
        self.assertIsNone(cp.spearman([(1, 1)]))


class PercentileTests(unittest.TestCase):
    def test_median_of_odd_count(self):
        self.assertEqual(cp.percentile([1, 2, 3, 4, 5], 50), 3.0)

    def test_median_of_even_count_interpolates(self):
        self.assertEqual(cp.percentile([1, 2, 3, 4], 50), 2.5)


class BootstrapTests(unittest.TestCase):
    def test_same_seed_gives_identical_result(self):
        values = [0.1, 0.4, 0.6, 0.9, 0.2, 0.7]
        first = cp.bootstrap_ci(values, seed=42, iterations=500)
        second = cp.bootstrap_ci(values, seed=42, iterations=500)
        self.assertEqual(first, second)

    def test_constant_values_give_a_degenerate_ci(self):
        low, high = cp.bootstrap_ci([5.0, 5.0, 5.0], seed=1, iterations=200)
        self.assertAlmostEqual(low, 5.0)
        self.assertAlmostEqual(high, 5.0)

    def test_diff_same_seed_gives_identical_result(self):
        cross, noise = [0.9, 0.8, 0.95], [0.5, 0.6, 0.55]
        first = cp.bootstrap_ci_diff(cross, noise, seed=7, iterations=500)
        second = cp.bootstrap_ci_diff(cross, noise, seed=7, iterations=500)
        self.assertEqual(first, second)


# ---------------------------------------------------------------------------
# Pairing and group-averaging (design decision 5) — a day with 3 Peec repeats
# must contribute exactly ONE data point wherever it competes with a day that
# has only 1, not three times the weight.
# ---------------------------------------------------------------------------


def peec_answer(peec_answer_id, local_date, *, client_mentioned, client_rank, client_sov, brands, domains, text_length):
    return {
        "peec_answer_id": peec_answer_id, "signalmap_prompt_id": "p1", "prompt_set_name": "Set 1",
        "peec_model": "gpt-5-6-terra", "signalmap_model": "gpt-5.6-terra", "local_date": local_date,
        "compared": "true",
        "_client_mentioned": client_mentioned, "_client_rank": client_rank, "_client_sov": client_sov,
        "_mentioned_brands": brands, "_domain_set": domains, "_text_length": text_length,
    }


def signalmap_answer(run_id, local_date, *, client_mentioned, client_rank, client_sov, brands, domains, text_length):
    return {
        "run_id": run_id, "prompt_id": "p1", "signalmap_model": "gpt-5.6-terra", "local_date": local_date,
        "_client_mentioned": client_mentioned, "_client_rank": client_rank, "_client_sov": client_sov,
        "_mentioned_brands": brands, "_domain_set": domains, "_text_length": text_length,
    }


class GroupWeightTests(unittest.TestCase):
    """One prompt/model, day A with 3 Peec repeats (one of which doesn't mention
    the client) and day B with a single Peec answer; one SignalMap run per day.
    """

    def setUp(self):
        self.peec_answers = [
            peec_answer("a1", "2026-09-23", client_mentioned=True, client_rank=1, client_sov=0.5,
                        brands={"Knauf", "Sto"}, domains={"a.com", "b.com"}, text_length=100),
            peec_answer("a2", "2026-09-23", client_mentioned=True, client_rank=2, client_sov=0.3,
                        brands={"Knauf"}, domains={"a.com"}, text_length=200),
            peec_answer("a3", "2026-09-23", client_mentioned=False, client_rank=None, client_sov=0.0,
                        brands={"Sto"}, domains={"c.com"}, text_length=300),
            peec_answer("a4", "2026-09-24", client_mentioned=True, client_rank=1, client_sov=1.0,
                        brands={"Knauf"}, domains={"a.com"}, text_length=150),
        ]
        self.signalmap_answers = [
            signalmap_answer("r1", "2026-09-23", client_mentioned=True, client_rank=1, client_sov=0.4,
                              brands={"Knauf", "Sto"}, domains={"a.com", "d.com"}, text_length=250),
            signalmap_answer("r2", "2026-09-24", client_mentioned=True, client_rank=1, client_sov=0.9,
                              brands={"Knauf"}, domains={"a.com"}, text_length=180),
        ]

    def test_build_pairs_has_one_row_per_day_not_per_repeat(self):
        pairs = cp.build_pairs(self.peec_answers, self.signalmap_answers)
        self.assertEqual(len(pairs), 2)

        day_a = next(p for p in pairs if p["local_date"] == "2026-09-23")
        self.assertEqual(day_a["peec_answer_count"], 3)
        self.assertEqual(day_a["signalmap_run_count"], 1)
        self.assertAlmostEqual(day_a["knauf_in_peec_share"], 2 / 3)
        self.assertAlmostEqual(day_a["knauf_first_mention_rank_peec"], 1.5)  # mean(1, 2), a3 excluded (never mentioned)
        self.assertAlmostEqual(day_a["knauf_share_of_voice_peec"], (0.5 + 0.3 + 0.0) / 3)
        self.assertAlmostEqual(day_a["peec_text_length"], 200.0)
        self.assertAlmostEqual(day_a["peec_domain_count"], 4 / 3)
        # brand_jaccard/domain_jaccard: mean over all 3 (peec repeat x 1 signalmap run) combinations.
        self.assertAlmostEqual(day_a["brand_jaccard"], (1.0 + 0.5 + 0.5) / 3)
        self.assertAlmostEqual(day_a["domain_jaccard"], (1 / 3 + 0.5 + 0.0) / 3)

        day_b = next(p for p in pairs if p["local_date"] == "2026-09-24")
        self.assertEqual(day_b["peec_answer_count"], 1)
        self.assertEqual(day_b["signalmap_run_count"], 1)
        self.assertAlmostEqual(day_b["brand_jaccard"], 1.0)
        self.assertAlmostEqual(day_b["domain_jaccard"], 1.0)

    def test_metric_series_gives_the_3_repeat_day_the_same_weight_as_any_other(self):
        series = cp.compute_metric_series(self.peec_answers, self.signalmap_answers)
        model_series = series["gpt-5.6-terra"]

        # cross: one value per day (2 days), never one value per repeat (would be 4).
        self.assertEqual(len(model_series["cross"]["knauf_agreement"]), 2)
        self.assertEqual(len(model_series["cross"]["brand_jaccard"]), 2)

        # peec_same_day: only day A qualifies (>=2 repeats) -> exactly 1 entry, not 3.
        self.assertEqual(len(model_series["peec_same_day"]["knauf_agreement"]), 1)
        self.assertAlmostEqual(model_series["peec_same_day"]["knauf_agreement"][0], 1 / 3)
        self.assertAlmostEqual(model_series["peec_same_day"]["brand_jaccard"][0], (0.5 + 0.5 + 0.0) / 3)

        # peec_diff_day / signalmap_diff_day: exactly one day-pair (A, B) each.
        self.assertEqual(len(model_series["peec_diff_day"]["knauf_agreement"]), 1)
        self.assertAlmostEqual(model_series["peec_diff_day"]["knauf_agreement"][0], 2 / 3)
        self.assertEqual(len(model_series["signalmap_diff_day"]["knauf_agreement"]), 1)
        self.assertAlmostEqual(model_series["signalmap_diff_day"]["knauf_agreement"][0], 1.0)


if __name__ == "__main__":
    unittest.main()
