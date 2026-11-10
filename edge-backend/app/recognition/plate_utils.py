"""Utilities for Vietnamese license plate normalization, OCR correction, and validation."""

from dataclasses import dataclass
import re
from typing import List, Optional

# Biểu thức chính quy cho biển số xe Việt Nam:
# Mã tỉnh 2 số (11..99, không có 00..09) + 1 chữ cái seri + 0 hoặc 1 số/chữ seri phụ + 4 hoặc 5 số cuối
VN_PLATE_REGEX = re.compile(r"^[1-9][0-9][A-Z][A-Z0-9]?[0-9]{4,5}$")

# Bảng chuyển đổi ký tự chữ dễ bị OCR đọc nhầm thành số
CHAR_TO_DIGIT = {
    "O": "0",
    "Q": "0",
    "D": "0",
    "U": "0",
    "I": "1",
    "L": "1",
    "J": "1",
    "T": "1",
    "Z": "2",
    "S": "5",
    "G": "6",
    "B": "8",
    "A": "4",
}

# Bảng chuyển đổi số dễ bị OCR đọc nhầm thành chữ
DIGIT_TO_CHAR = {
    "0": "D",
    "8": "B",
    "5": "S",
    "1": "T",
    "2": "Z",
    "4": "A",
    "6": "G",
}


@dataclass
class PlateValidationResult:
    plate_raw: str
    plate_normalized: str
    plate_display: str
    is_valid: bool


@dataclass
class PlateCandidate:
    plate_raw: str
    plate_normalized: str
    plate_display: str
    confidence: float
    is_valid: bool
    frame_index: int = 0
    crop_jpeg_bytes: Optional[bytes] = None


def clean_raw_text(raw: str) -> str:
    """Lọc bỏ toàn bộ dấu cách và ký tự đặc biệt, chuyển thành chữ in hoa."""
    if not raw:
        return ""
    return re.sub(r"[^A-Za-z0-9]", "", str(raw)).upper()


def is_valid_vn_plate(plate: str) -> bool:
    """Kiểm tra biển số xe Việt Nam hợp lệ (dài từ 7 đến 9 ký tự, đúng mẫu)."""
    if not plate or len(plate) < 7 or len(plate) > 9:
        return False
    return bool(VN_PLATE_REGEX.match(plate))


def correct_ocr_confusions(cleaned: str) -> str:
    """Sửa lỗi ký tự OCR đọc nhầm theo vị trí ngữ cảnh của biển số xe Việt Nam."""
    length = len(cleaned)
    if length not in (7, 8, 9):
        return cleaned

    # Nếu chuỗi có ít hơn 2 chữ số thực sự, đây là chuỗi từ ngữ ngẫu nhiên (vd ABCDEFGH), không sửa
    num_digits = sum(c.isdigit() for c in cleaned)
    if num_digits < 2:
        return cleaned

    chars = list(cleaned)

    # 1. Hai ký tự đầu (Mã tỉnh) BẮT BUỘC LÀ SỐ (0-9)
    for i in (0, 1):
        if chars[i] in CHAR_TO_DIGIT:
            chars[i] = CHAR_TO_DIGIT[chars[i]]

    # 2. Ký tự thứ 3 (Seri chữ cái) BẮT BUỘC LÀ CHỮ (A-Z)
    if chars[2] in DIGIT_TO_CHAR:
        chars[2] = DIGIT_TO_CHAR[chars[2]]

    # 3. Ký tự thứ 4 (vị trí 3):
    if length == 7:
        # Biển 4 số (vd 29A1234): vị trí 3 là số đầu trong 4 số -> ép sang số
        if chars[3] in CHAR_TO_DIGIT:
            chars[3] = CHAR_TO_DIGIT[chars[3]]
    elif length == 8:
        # Biển 8 ký tự:
        # - Trường hợp 1: Biển ô tô 5 số (vd 51A12345): 5 ký tự cuối (3..7) là số.
        # - Trường hợp 2: Biển xe máy 4 số (vd 29B19999): vị trí 3 là số/chữ seri phụ, 4 số cuối (4..7) là số.
        # Nếu 4 ký tự cuối (4..7) đều là số hoặc có thể ép thành số, và vị trí 3 là chữ dễ nhầm thành số:
        tail_can_be_digits = all(
            chars[idx].isdigit() or chars[idx] in CHAR_TO_DIGIT for idx in range(4, 8)
        )
        if tail_can_be_digits and chars[3] in CHAR_TO_DIGIT:
            chars[3] = CHAR_TO_DIGIT[chars[3]]

    # 4. Các ký tự cuối:
    if length in (7, 8):
        # 4 ký tự cuối (vị trí -4..-1) bắt buộc là số
        for i in range(length - 4, length):
            if chars[i] in CHAR_TO_DIGIT:
                chars[i] = CHAR_TO_DIGIT[chars[i]]
    elif length == 9:
        # Biển 9 ký tự (xe máy 5 số, vd 59X112345 hoặc 43AA12345):
        # 5 ký tự cuối (vị trí -5..-1) bắt buộc là số
        for i in range(length - 5, length):
            if chars[i] in CHAR_TO_DIGIT:
                chars[i] = CHAR_TO_DIGIT[chars[i]]

    return "".join(chars)


def format_plate_display(plate_norm: str, raw_hint: str = "") -> str:
    """Định dạng biển số hiển thị có dấu gạch ngang và chấm phân cách chuẩn Việt Nam."""
    if not is_valid_vn_plate(plate_norm):
        return plate_norm

    length = len(plate_norm)
    if length == 8:
        # Kiểm tra xem raw có gợi ý biển xe máy 4 số không (vd '29-B1 9999' hoặc '29B1-9999')
        if raw_hint:
            m = re.match(r"^[0-9]{2}[\- ]?[A-Z][0-9A-Z][\s\-\.]+([0-9]{4})$", raw_hint.strip().upper())
            if m:
                return f"{plate_norm[:4]}-{plate_norm[4:]}"

        # Mặc định ô tô 5 số: 51A-123.45 nếu 5 ký tự cuối là số
        if plate_norm[3:].isdigit():
            return f"{plate_norm[:3]}-{plate_norm[3:6]}.{plate_norm[6:]}"
        else:
            return f"{plate_norm[:4]}-{plate_norm[4:]}"
    elif length == 7:
        # Biển ô tô 4 số cũ: 29A-1234
        return f"{plate_norm[:3]}-{plate_norm[3:]}"
    elif length == 9:
        # Biển xe máy 5 số: 59X1-123.45 hoặc 43AA-567.89
        return f"{plate_norm[:4]}-{plate_norm[4:7]}.{plate_norm[7:]}"

    return plate_norm


def normalize_plate(raw: str) -> PlateValidationResult:
    """Chuẩn hóa toàn diện biển số đầu vào."""
    cleaned = clean_raw_text(raw)
    corrected = correct_ocr_confusions(cleaned)
    valid = is_valid_vn_plate(corrected)
    display = format_plate_display(corrected, raw_hint=str(raw))

    return PlateValidationResult(
        plate_raw=str(raw),
        plate_normalized=corrected,
        plate_display=display,
        is_valid=valid,
    )


def vote_best_candidate(candidates: List[PlateCandidate]) -> Optional[PlateCandidate]:
    """Bầu chọn kết quả biển số tốt nhất giữa các frame hoặc các vùng phát hiện.
    
    Ưu tiên:
    1. Kết quả hợp lệ (is_valid == True).
    2. Tần suất xuất hiện nhiều hơn (đa số phiếu).
    3. Điểm tự tin (confidence) cao nhất.
    """
    if not candidates:
        return None

    # Tách nhóm hợp lệ và không hợp lệ
    valid_candidates = [c for c in candidates if c.is_valid]

    target_group = valid_candidates if valid_candidates else candidates

    # Đếm tần suất của từng plate_normalized
    freq_map = {}
    for c in target_group:
        freq_map[c.plate_normalized] = freq_map.get(c.plate_normalized, 0) + 1

    # Sắp xếp theo:
    # 1. is_valid (True > False)
    # 2. Tần suất xuất hiện (nhiều hơn tốt hơn)
    # 3. Confidence cao nhất
    best = max(
        target_group,
        key=lambda c: (
            1 if c.is_valid else 0,
            freq_map[c.plate_normalized],
            c.confidence,
        ),
    )

    return best
