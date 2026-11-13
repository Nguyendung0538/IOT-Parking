"""Optical Character Recognition module using EasyOCR with Vietnamese plate layout parsing."""

import logging
from typing import List, Tuple
import cv2
import numpy as np

logger = logging.getLogger("plate_ocr")


class PlateOCR:
    """Module nhận diện ký tự trên vùng ảnh biển số xe."""

    def __init__(self, use_gpu: bool = True):
        self.use_gpu = use_gpu
        self.reader = None

        try:
            import easyocr
            self.reader = easyocr.Reader(
                ["en"],
                gpu=self.use_gpu,
                verbose=False,
            )
            logger.info("PlateOCR: Khởi tạo EasyOCR thành công (GPU=%s)", self.use_gpu)
        except Exception as e:
            logger.error("PlateOCR: Lỗi khi khởi tạo EasyOCR: %s", e)
            self.reader = None

    def preprocess(self, crop_bgr: np.ndarray) -> np.ndarray:
        """Tiền xử lý nâng cao tương phản và làm nét ký tự."""
        if crop_bgr is None or crop_bgr.size == 0:
            return crop_bgr

        # 1. Phóng đại nếu ảnh quá nhỏ
        h, w = crop_bgr.shape[:2]
        if h < 70:
            scale = 70.0 / h
            crop_bgr = cv2.resize(
                crop_bgr,
                (int(w * scale), 70),
                interpolation=cv2.INTER_CUBIC,
            )

        # 2. Chuyển sang ảnh mức xám
        gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)

        # 3. Cân bằng tương phản cục bộ (CLAHE)
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        contrast = clahe.apply(gray)

        # 4. Lọc mịn song phương (Bilateral Filter) giữ sắc cạnh ký tự
        filtered = cv2.bilateralFilter(contrast, d=5, sigmaColor=50, sigmaSpace=50)

        return filtered

    def read_crop(self, crop_bgr: np.ndarray) -> Tuple[str, float]:
        """Đọc ký tự từ vùng ảnh crop biển số, tự động ghép đúng biển 1 dòng và 2 dòng."""
        if self.reader is None or crop_bgr is None or crop_bgr.size == 0:
            return "", 0.0

        processed = self.preprocess(crop_bgr)

        try:
            # 1. Thử nhận diện trên ảnh đã tiền xử lý CLAHE + Bilateral
            results = self.reader.readtext(
                processed,
                allowlist="0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                detail=1,
                paragraph=False,
            )

            # 2. Nếu tiền xử lý không ra kết quả, thử trên ảnh gốc crop_bgr
            if not results:
                results = self.reader.readtext(
                    crop_bgr,
                    allowlist="0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                    detail=1,
                    paragraph=False,
                )

            if not results:
                return "", 0.0

            raw_text, conf = self.merge_ocr_boxes(results)
            return raw_text, conf
        except Exception as e:
            logger.error("PlateOCR: Lỗi nhận diện chữ: %s", e)
            return "", 0.0

    @staticmethod
    def merge_ocr_boxes(ocr_results: list) -> Tuple[str, float]:
        """Thuật toán phân cụm dòng theo trục Y và sắp xếp theo trục X.
        
        Giải quyết chuẩn xác cả biển 1 dòng (ô tô dài) và biển 2 dòng (ô tô vuông, xe máy).
        """
        parsed_boxes = []

        for bbox, text, conf in ocr_results:
            clean_text = "".join(c for c in text.upper() if c.isalnum())
            if not clean_text or conf < 0.2:
                continue

            # Tọa độ 4 đỉnh của box: [[x1, y1], [x2, y2], [x3, y3], [x4, y4]]
            pts = np.array(bbox)
            cy = float(np.mean(pts[:, 1]))
            cx = float(np.mean(pts[:, 0]))
            h = float(np.max(pts[:, 1]) - np.min(pts[:, 1]))

            parsed_boxes.append({
                "cx": cx,
                "cy": cy,
                "h": max(h, 10.0),
                "text": clean_text,
                "conf": float(conf),
            })

        if not parsed_boxes:
            return "", 0.0

        if len(parsed_boxes) == 1:
            return parsed_boxes[0]["text"], parsed_boxes[0]["conf"]

        # Sắp xếp các box theo chiều dọc (cy tăng dần)
        parsed_boxes.sort(key=lambda b: b["cy"])
        mean_h = np.mean([b["h"] for b in parsed_boxes])

        # Phân cụm các box thành các dòng
        lines: List[List[dict]] = []
        for box in parsed_boxes:
            placed = False
            for line in lines:
                line_mean_cy = np.mean([b["cy"] for b in line])
                if abs(box["cy"] - line_mean_cy) <= (0.6 * mean_h):
                    line.append(box)
                    placed = True
                    break
            if not placed:
                lines.append([box])

        # Sắp xếp các dòng từ trên xuống dưới theo cy trung bình
        lines.sort(key=lambda line: np.mean([b["cy"] for b in line]))

        # Trong từng dòng, sắp xếp các box từ trái sang phải theo cx
        final_text_parts = []
        total_chars = 0
        weighted_conf_sum = 0.0

        for line in lines:
            line.sort(key=lambda b: b["cx"])
            for b in line:
                final_text_parts.append(b["text"])
                chars_count = len(b["text"])
                total_chars += chars_count
                weighted_conf_sum += b["conf"] * chars_count

        full_text = "".join(final_text_parts)
        avg_conf = (weighted_conf_sum / total_chars) if total_chars > 0 else 0.0

        return full_text, round(avg_conf, 3)
