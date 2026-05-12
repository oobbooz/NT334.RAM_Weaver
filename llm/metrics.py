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


def initial_cer(raw_text: str, ground_truth: str) -> float:
    """Compute Initial CER — measure of noise in raw extraction.
    
    Compares the full raw extraction against ground truth to assess
    how much noise/garbage is present before LLM processing.
    
    Args:
        raw_text: Full noisy text from extraction (e.g., all strings).
        ground_truth: Ground-truth message to compare against.
    
    Returns:
        CER score (can be > 1.0 if raw_text is much longer).
    """
    if not ground_truth:
        return float("inf")
    if not raw_text:
        return float(len(ground_truth))
    
    # Use standard CER but limit hypothesis length to avoid extreme values
    # (raw extractions can be thousands of times longer than ground truth)
    limited_raw = raw_text[: max(len(ground_truth) * 10, 1000)]
    return character_error_rate(ground_truth, limited_raw)


def token_f1(reference: str, hypothesis: str) -> float:
    """Compute token-level F1 score (whitespace-based tokenization).
    
    Useful for evaluating query answers where exact match is too strict
    but token overlap is meaningful (S3 - Forensic Querying).
    
    Args:
        reference: Ground-truth text.
        hypothesis: Model-generated text.
    
    Returns:
        F1 score (0.0 to 1.0).
    """
    from collections import Counter
    
    ref_tokens = reference.strip().split()
    hyp_tokens = hypothesis.strip().split()
    
    if not ref_tokens and not hyp_tokens:
        return 1.0
    if not ref_tokens or not hyp_tokens:
        return 0.0
    
    ref_counter = Counter(ref_tokens)
    hyp_counter = Counter(hyp_tokens)
    
    common = 0
    for token, count in ref_counter.items():
        common += min(count, hyp_counter.get(token, 0))
    
    if common == 0:
        return 0.0
    
    precision = common / sum(hyp_counter.values())
    recall = common / sum(ref_counter.values())
    
    if precision + recall == 0:
        return 0.0
    
    return 2 * precision * recall / (precision + recall)
