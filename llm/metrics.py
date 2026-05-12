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


def snr_db(signal_bytes: int, total_bytes: int) -> float:
    """Compute SNR in dB as used in the paper (Table 1).

    SNR = 20 * log10(signal_bytes / total_bytes)

    where ``signal_bytes`` is the length of useful/clean output and
    ``total_bytes`` is the length of the raw extracted text fed to the filter.
    The paper reports:
      - Baseline 1 (naïve strings): SNR ≈ -47.33 dB
      - RAM-Weaver full pipeline:   SNR ≈ -10.53 dB
      - Improvement:                        ≈ +36.8 dB  (≈ 37 dB)

    Args:
        signal_bytes: Number of bytes (or chars) of useful output.
        total_bytes:  Number of bytes (or chars) of raw input.

    Returns:
        SNR in dB (negative value; less negative = better).
    """
    import math
    if total_bytes <= 0:
        raise ValueError("total_bytes must be positive.")
    if signal_bytes <= 0:
        return float("-inf")
    return 20 * math.log10(signal_bytes / total_bytes)


def snr_delta_db(
    signal_bytes_after: int,
    total_bytes_after: int,
    signal_bytes_before: int,
    total_bytes_before: int,
) -> float:
    """Compute SNR improvement (delta) between two pipeline stages.

    Returns ``snr_after - snr_before`` in dB.  A positive value means
    the SNR improved (less noise relative to signal).

    The paper claims ~37 dB improvement when comparing the full AMC
    pipeline to naïve ``strings`` baseline.
    """
    return snr_db(signal_bytes_after, total_bytes_after) - snr_db(
        signal_bytes_before, total_bytes_before
    )



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
