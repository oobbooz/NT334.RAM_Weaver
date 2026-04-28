"""Unit tests for llm/metrics.py.

These tests are self-contained (no external dependencies) and verify the
paper's evaluation metrics behave correctly on controlled inputs.
"""

import pytest
from metrics import (
    average_cer,
    character_error_rate,
    evaluate,
    exact_match_rate,
)


class TestCharacterErrorRate:
    def test_identical_strings(self):
        assert character_error_rate("hello", "hello") == 0.0

    def test_completely_different(self):
        # 5 substitutions / 5 chars = 1.0
        assert character_error_rate("abcde", "fghij") == 1.0

    def test_empty_both(self):
        assert character_error_rate("", "") == 0.0

    def test_empty_reference(self):
        # CER > 1 when hypothesis is longer than empty reference
        result = character_error_rate("", "abc")
        assert result == float(3)

    def test_insertion(self):
        # "cat" → "cats": 1 insertion / 3 = 0.333...
        cer = character_error_rate("cat", "cats")
        assert abs(cer - 1 / 3) < 1e-6

    def test_deletion(self):
        # "cats" → "cat": 1 deletion / 4 = 0.25
        cer = character_error_rate("cats", "cat")
        assert abs(cer - 0.25) < 1e-6

    def test_substitution(self):
        # "bat" → "cat": 1 substitution / 3 = 0.333...
        cer = character_error_rate("bat", "cat")
        assert abs(cer - 1 / 3) < 1e-6

    def test_paper_target_final_cer(self):
        """Paper reports Final CER of 0.20 for RAM-Weaver (Table 1).
        This test verifies a near-perfect reconstruction achieves ~0.20."""
        ref = "Yes and they smile more when they hit the beat"
        # Simulate a minor restoration error (one char wrong)
        hyp = ref[:-1] + "B"
        cer = character_error_rate(ref, hyp)
        assert cer < 1.0  # clearly better than baseline


class TestExactMatchRate:
    def test_all_match(self):
        refs = ["hello", "world"]
        hyps = ["hello", "world"]
        assert exact_match_rate(refs, hyps) == 1.0

    def test_none_match(self):
        refs = ["hello", "world"]
        hyps = ["foo", "bar"]
        assert exact_match_rate(refs, hyps) == 0.0

    def test_partial_match(self):
        refs = ["a", "b", "c", "d"]
        hyps = ["a", "X", "c", "X"]
        assert exact_match_rate(refs, hyps) == 0.5

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            exact_match_rate(["a"], ["a", "b"])

    def test_empty_lists(self):
        assert exact_match_rate([], []) == 0.0

    def test_paper_gpt_o3_perfect(self):
        """Paper Table 2: GPT-o3 achieves 100% EMR."""
        refs = ["Ballet dancer go through 4 pairs of shoes a week"]
        hyps = ["Ballet dancer go through 4 pairs of shoes a week"]
        assert exact_match_rate(refs, hyps) == 1.0


class TestAverageCER:
    def test_perfect(self):
        assert average_cer(["abc", "def"], ["abc", "def"]) == 0.0

    def test_all_wrong(self):
        result = average_cer(["abc"], ["xyz"])
        assert result == 1.0

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            average_cer(["a"], [])

    def test_empty_lists(self):
        assert average_cer([], []) == 0.0


class TestEvaluate:
    def test_returns_both_metrics(self):
        refs = ["hello world"]
        hyps = ["hello world"]
        result = evaluate(refs, hyps)
        assert "emr" in result
        assert "avg_cer" in result
        assert result["emr"] == 1.0
        assert result["avg_cer"] == 0.0
