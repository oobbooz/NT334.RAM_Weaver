"""Các thước đo đánh giá được dùng trong bài báo (Mục 3 & 4).

Các thước đo trong bài:
        * **EMR** (Exact Match Rate) – tỷ lệ tin nhắn khôi phục khớp hoàn toàn
            với bản gốc. Dùng cho S2 (Bảng 2).
        * **CER** (Character Error Rate) – khoảng cách chỉnh sửa Levenshtein ở
            mức ký tự, chuẩn hoá theo độ dài tham chiếu. Dùng cho S1 & S2.
        * **SNR delta** – mức cải thiện SNR sau khi lọc AMC
            (tính trong ``ArtifactFilter.filter``).

Các hàm tiện ích này giúp người vận hành tái hiện quy trình đánh giá của bài
báo trên tập dữ liệu của họ.
"""

from __future__ import annotations


def snr_db(signal_bytes: int, total_bytes: int) -> float:
    r"""Tính SNR theo đơn vị dB như trong bài báo (Bảng 1).

    $$\text{SNR} = 10 \cdot \log_{10}(\tfrac{\text{signal}}{\text{total}})$$

    Trong đó ``signal_bytes`` là độ dài output hữu ích/sạch, còn ``total_bytes``
    là độ dài text thô được đưa vào bước lọc.

    Bài báo báo cáo:
      - Baseline 1 (strings đơn giản): SNR ≈ -47.33 dB
      - RAM-Weaver chạy đủ quy trình:  SNR ≈ -10.53 dB
      - Cải thiện:                           ≈ +36.8 dB (xấp xỉ 37 dB)

    Tham số:
        signal_bytes: Số byte (hoặc ký tự) của output hữu ích.
        total_bytes: Số byte (hoặc ký tự) của input thô.

    Trả về:
        SNR (dB). Giá trị thường âm; càng ít âm thì càng tốt.
    """
    import math
    if total_bytes <= 0:
        raise ValueError("total_bytes must be positive.")
    if signal_bytes <= 0:
        return float("-inf")
    return 10 * math.log10(signal_bytes / total_bytes)


def snr_delta_db(
    signal_bytes_after: int,
    total_bytes_after: int,
    signal_bytes_before: int,
    total_bytes_before: int,
) -> float:
    """Tính mức cải thiện SNR (delta) giữa 2 giai đoạn trong quy trình.

    Trả về ``snr_after - snr_before`` theo dB. Giá trị dương nghĩa là SNR
    được cải thiện (ít nhiễu hơn so với tín hiệu).

    Bài báo nêu ~37 dB cải thiện khi so sánh AMC chạy đầy đủ với baseline
    ``strings`` đơn giản.
    """
    return snr_db(signal_bytes_after, total_bytes_after) - snr_db(
        signal_bytes_before, total_bytes_before
    )



def character_error_rate(reference: str, hypothesis: str) -> float:
    """Tính CER (khoảng cách Levenshtein / len(reference)).

    CER = 0.0 nghĩa là khôi phục hoàn hảo; CER = 1.0 nghĩa là sai toàn bộ ký tự.
    CER > 1.0 có thể xảy ra nếu chuỗi dự đoán dài hơn chuỗi tham chiếu.

    Tham số:
        reference: Chuỗi ground-truth.
        hypothesis: Chuỗi khôi phục từ LLM.

    Trả về:
        CER (float). Trả về 0.0 nếu cả hai chuỗi đều rỗng.
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
    """Tính EMR trên danh sách cặp reference/hypothesis."""
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
    """Tính CER trung bình trên danh sách cặp (reference, hypothesis).

    Tham số:
        references: Chuỗi ground-truth.
        hypotheses: Chuỗi khôi phục.

    Trả về:
        CER trung bình trên tất cả cặp.

    Ngoại lệ:
        ValueError: Nếu 2 danh sách có độ dài khác nhau.
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
    """Tính gộp các thước đo trong bài báo trong một lần gọi.

    Tham số:
        references: Chuỗi tin nhắn ground-truth.
        hypotheses: Chuỗi tin nhắn khôi phục từ LLM.

    Trả về:
        Dict với các khoá ``"emr"``, ``"avg_cer"``.
    """
    return {
        "emr": exact_match_rate(references, hypotheses),
        "avg_cer": average_cer(references, hypotheses),
    }


def initial_cer(raw_text: str, ground_truth: str) -> float:
    if not ground_truth:
        return float("inf")
    if not raw_text:
        return float(len(ground_truth))
        
    ref_len = len(ground_truth)
    hyp_len = len(raw_text)

    # Nếu chuỗi rác quá lớn (> 500,000 ký tự), dùng phép xấp xỉ toán học 
    # để tránh bị WSL kill (OOM) do tràn RAM khi chạy mảng Levenshtein
    if hyp_len > 500_000:
        return float(hyp_len) / ref_len

    # Tính Levenshtein(gt, raw_text) / len(gt) bình thường cho chuỗi nhỏ
    return character_error_rate(ground_truth, raw_text)


def token_f1(reference: str, hypothesis: str) -> float:
    """Tính điểm F1 ở mức token (tách token theo khoảng trắng).

    Hữu ích khi đánh giá câu trả lời truy vấn: khớp tuyệt đối quá khắt khe,
    nhưng mức độ trùng token vẫn có ý nghĩa (S3 – truy vấn điều tra).

    Tham số:
        reference: Văn bản ground-truth.
        hypothesis: Văn bản mô hình sinh ra.

    Trả về:
        Điểm F1 (từ 0.0 đến 1.0).
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
