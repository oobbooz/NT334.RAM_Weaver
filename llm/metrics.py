"""Evaluation metrics used in the paper (Section 3 & 4).

Paper metrics:
    * **EMR** (Exact Match Rate)  – percentage of reconstructed messages
      identical to the original.  Used in S2 (Table 2).
    * **CER** (Character Error Rate) – Levenshtein edit distance at the
      character level, normalised by reference length.  Used in S1 & S2.
    * **SNR delta** – improvement in signal-to-noise ratio after AMC
      filtering (computed in ``ArtifactFilter.filter``).

These helpers allow operators to reproduce the paper's evaluation
pipeline on their own datasets.
"""

from __future__ import annotations


def character_error_rate(reference: str, hypothesis: str) -> float:
    """Compute CER (Levenshtein distance / len(reference)).

    A CER of 0.0 means perfect reconstruction; 1.0 means every character
    is wrong.  Values > 1.0 are possible when the hypothesis is longer than
    the reference.

    Args:
        reference:  Ground-truth message string.
        hypothesis: Reconstructed message string from the LLM.

    Returns:
        CER as a float.  Returns 0.0 if both strings are empty.
    """
    if not reference and not hypothesis:
        return 0.0
    if not reference:
        return float(len(hypothesis))

    ref_len = len(reference)
    hyp_len = len(hypothesis)

    # Dynamic-programming Levenshtein
    prev = list(range(hyp_len + 1))
    for i, r_char in enumerate(reference, start=1):
        curr = [i] + [0] * hyp_len
        for j, h_char in enumerate(hypothesis, start=1):
            if r_char == h_char:
                curr[j] = prev[j - 1]
            else:
                curr[j] = 1 + min(prev[j], curr[j - 1], prev[j - 1])
        prev = curr

    return prev[hyp_len] / ref_len


def exact_match_rate(references: list[str], hypotheses: list[str]) -> float:
    """Compute EMR across a list of reference/hypothesis pairs.

    Args:
        references:  Ground-truth message strings.
        hypotheses:  Reconstructed strings (same order as references).

    Returns:
        Fraction of pairs that are character-for-character identical.

    Raises:
        ValueError: If the lists have different lengths.
    """
    if len(references) != len(hypotheses):
        raise ValueError(
            f"Length mismatch: {len(references)} references vs "
            f"{len(hypotheses)} hypotheses."
        )
    if not references:
        return 0.0
    matches = sum(r == h for r, h in zip(references, hypotheses))
    return matches / len(references)


def average_cer(references: list[str], hypotheses: list[str]) -> float:
    """Compute mean CER over a paired list.

    Args:
        references:  Ground-truth strings.
        hypotheses:  Reconstructed strings.

    Returns:
        Mean CER across all pairs.

    Raises:
        ValueError: If the lists have different lengths.
    """
    if len(references) != len(hypotheses):
        raise ValueError(
            f"Length mismatch: {len(references)} references vs "
            f"{len(hypotheses)} hypotheses."
        )
    if not references:
        return 0.0
    return sum(
        character_error_rate(r, h) for r, h in zip(references, hypotheses)
    ) / len(references)


def evaluate(
    references: list[str],
    hypotheses: list[str],
) -> dict[str, float]:
    """Compute all paper metrics in one call.

    Args:
        references:  Ground-truth message strings.
        hypotheses:  LLM-reconstructed strings.

    Returns:
        Dictionary with keys ``"emr"``, ``"avg_cer"``.
    """
    return {
        "emr": exact_match_rate(references, hypotheses),
        "avg_cer": average_cer(references, hypotheses),
    }
