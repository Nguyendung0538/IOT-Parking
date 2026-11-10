"""License Plate Detection module using YOLOv8 (CUDA) with OpenCV fallback."""

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import List, Tuple
import cv2
import numpy as np

logger = logging.getLogger("plate_detector")


@dataclass
class DetectedPlate:
    bbox: Tuple[int, int, int, int]  # (x1, y1, x2, y2)
    confidence: float
    crop_bgr: np.ndarray
    source: str  # "yolo" | "opencv_fallback"


class PlateDetector:
    """Bộ phát hiện vùng biển số 2 tầng:
    Tầng 1: YOLOv8 CUDA (nhanh ~8ms, độ chính xác cao).
    Tầng 2: OpenCV Morphological Contour Fallback (dự phòng khi không phát hiện ra box).
    """

    def __init__(
        self,
        model_path: str = "models/plate_yolo_s.pt",
        conf_threshold: float = 0.4,
        padding_ratio: float = 0.12,
        use_gpu: bool = True,
    ):
        self.conf_threshold = conf_threshold
        self.padding_ratio = padding_ratio
        self.use_gpu = use_gpu
        self.yolo_model = None
        self.device = "cpu"

        # Kiểm tra CUDA
        if self.use_gpu:
            try:
                import torch
                if torch.cuda.is_available():
                    self.device = "cuda:0"
                    logger.info("PlateDetector: Khởi tạo trên GPU (%s)", torch.cuda.get_device_name(0))
                else:
                    logger.warning("PlateDetector: CUDA không khả dụng, chuyển sang CPU")
            except Exception as e:
                logger.warning("PlateDetector: Lỗi kiểm tra CUDA: %s", e)

        # Nạp model YOLO
        p = Path(model_path)
        if not p.is_absolute():
            # Tìm relative theo thư mục gốc của edge-backend
            base_dir = Path(__file__).resolve().parent.parent.parent
            p = base_dir / model_path

        if p.exists():
            try:
                from ultralytics import YOLO
                self.yolo_model = YOLO(str(p))
                logger.info("PlateDetector: Đã nạp thành công YOLO model từ %s", p)
            except Exception as e:
                logger.error("PlateDetector: Lỗi khi nạp YOLO model: %s. Sẽ dùng OpenCV fallback.", e)
                self.yolo_model = None
        else:
            logger.warning("PlateDetector: Không tìm thấy file model tại %s. Sẽ dùng OpenCV fallback.", p)

    def detect_and_crop(self, img_bgr: np.ndarray) -> List[DetectedPlate]:
        """Phát hiện vùng biển số trong ảnh và cắt ảnh con (crop) kèm padding."""
        if img_bgr is None or img_bgr.size == 0:
            return []

        h_img, w_img = img_bgr.shape[:2]
        detected: List[DetectedPlate] = []

        # TẦNG 1: Chạy YOLO nếu có model
        if self.yolo_model is not None:
            try:
                results = self.yolo_model.predict(
                    img_bgr,
                    conf=self.conf_threshold,
                    device=self.device,
                    verbose=False,
                )
                if results and len(results[0].boxes) > 0:
                    boxes = results[0].boxes
                    for box in boxes:
                        xyxy = box.xyxy[0].cpu().numpy()
                        conf = float(box.conf[0].cpu().numpy())

                        x1, y1, x2, y2 = xyxy
                        # Thêm padding
                        pad_w = (x2 - x1) * self.padding_ratio
                        pad_h = (y2 - y1) * self.padding_ratio

                        cx1 = max(0, int(x1 - pad_w))
                        cy1 = max(0, int(y1 - pad_h))
                        cx2 = min(w_img, int(x2 + pad_w))
                        cy2 = min(h_img, int(y2 + pad_h))

                        crop = img_bgr[cy1:cy2, cx1:cx2].copy()
                        if crop.size > 0:
                            detected.append(
                                DetectedPlate(
                                    bbox=(cx1, cy1, cx2, cy2),
                                    confidence=conf,
                                    crop_bgr=crop,
                                    source="yolo",
                                )
                            )
            except Exception as e:
                logger.error("PlateDetector: Lỗi trong quá trình YOLO inference: %s", e)

        # Nếu YOLO đã tìm thấy ít nhất 1 vùng biển, trả về ngay
        if detected:
            return detected

        # TẦNG 2: OpenCV Fallback nếu YOLO không tìm thấy hoặc chưa có model
        logger.debug("PlateDetector: Sử dụng OpenCV Morphological Fallback...")
        fallback_crops = self._opencv_fallback_detect(img_bgr)
        return fallback_crops

    def _opencv_fallback_detect(self, img_bgr: np.ndarray) -> List[DetectedPlate]:
        """Dùng toán tử hình thái học (Morphological BlackHat) để tìm vùng chữ nhật chứa chữ."""
        h_img, w_img = img_bgr.shape[:2]
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

        # BlackHat để làm nổi bật ký tự tối trên nền biển sáng
        rect_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (13, 5))
        blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, rect_kernel)

        # Sobel gradient theo trục X
        grad_x = cv2.Sobel(blackhat, ddepth=cv2.CV_32F, dx=1, dy=0, ksize=-1)
        grad_x = np.absolute(grad_x)
        min_val, max_val = np.min(grad_x), np.max(grad_x)
        if max_val > min_val:
            grad_x = (255 * ((grad_x - min_val) / (max_val - min_val))).astype("uint8")
        else:
            grad_x = grad_x.astype("uint8")

        # Làm mịn và đóng vùng
        grad_x = cv2.GaussianBlur(grad_x, (5, 5), 0)
        grad_x = cv2.morphologyEx(grad_x, cv2.MORPH_CLOSE, rect_kernel)
        _, thresh = cv2.threshold(grad_x, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

        # Tìm contours
        contours, _ = cv2.findContours(thresh.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []

        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            aspect_ratio = w / float(h)
            area = w * h

            # Tỷ lệ biển số VN (1 dòng hoặc 2 dòng): 1.1 đến 5.5, diện tích hợp lý
            if 1.1 <= aspect_ratio <= 5.5 and area > (w_img * h_img * 0.005) and area < (w_img * h_img * 0.4):
                candidates.append((area, (x, y, x + w, y + h)))

        # Sắp xếp theo diện tích lớn nhất
        candidates.sort(key=lambda c: c[0], reverse=True)
        results = []

        for _, (x1, y1, x2, y2) in candidates[:2]:
            pad_w = int((x2 - x1) * 0.08)
            pad_h = int((y2 - y1) * 0.08)
            cx1 = max(0, x1 - pad_w)
            cy1 = max(0, y1 - pad_h)
            cx2 = min(w_img, x2 + pad_w)
            cy2 = min(h_img, y2 + pad_h)

            crop = img_bgr[cy1:cy2, cx1:cx2].copy()
            if crop.size > 0:
                results.append(
                    DetectedPlate(
                        bbox=(cx1, cy1, cx2, cy2),
                        confidence=0.5,
                        crop_bgr=crop,
                        source="opencv_fallback",
                    )
                )

        # Nếu vẫn không tìm thấy box nào, lấy luôn vùng trung tâm 70% ảnh
        if not results:
            cx1, cy1 = int(w_img * 0.15), int(h_img * 0.2)
            cx2, cy2 = int(w_img * 0.85), int(h_img * 0.85)
            crop = img_bgr[cy1:cy2, cx1:cx2].copy()
            results.append(
                DetectedPlate(
                    bbox=(cx1, cy1, cx2, cy2),
                    confidence=0.3,
                    crop_bgr=crop,
                    source="opencv_center_roi",
                )
            )

        return results
