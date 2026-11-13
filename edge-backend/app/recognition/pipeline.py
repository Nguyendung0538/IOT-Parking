"""Unified Plate Recognition Pipeline integrating YOLOv8, EasyOCR, and post-processing."""

from dataclasses import dataclass
import logging
import time
from typing import List, Optional, Union
import cv2
import numpy as np

from app.recognition.detector import PlateDetector
from app.recognition.ocr import PlateOCR
from app.recognition.plate_utils import (
    PlateCandidate,
    normalize_plate,
    vote_best_candidate,
)

logger = logging.getLogger("plate_pipeline")


@dataclass
class PipelineResult:
    plate_raw: str
    plate_normalized: str
    plate_display: str
    confidence: float
    is_valid: bool
    crop_jpeg_bytes: Optional[bytes]
    best_frame_index: int
    latency_ms: float
    detector_used: str
    total_candidates: int


class PlateRecognitionPipeline:
    """Pipeline nhận diện biển số xe hoàn chỉnh (End-to-End)."""

    def __init__(
        self,
        model_path: str = "models/plate_yolo_s.pt",
        conf_threshold: float = 0.4,
        padding_ratio: float = 0.12,
        use_gpu: bool = True,
    ):
        logger.info("Khởi tạo PlateRecognitionPipeline (GPU=%s)...", use_gpu)
        self.detector = PlateDetector(
            model_path=model_path,
            conf_threshold=conf_threshold,
            padding_ratio=padding_ratio,
            use_gpu=use_gpu,
        )
        self.ocr = PlateOCR(use_gpu=use_gpu)
        self._warmup_done = False
        self._last_latency_ms: Optional[float] = None

    def warmup(self) -> None:
        """Chạy trước 1 ảnh giả lập để nạp CUDA kernels vào VRAM, loại bỏ độ trễ lần chạy đầu."""
        if self._warmup_done:
            return

        logger.info("Đang thực hiện GPU Warm-up cho AI Pipeline...")
        t0 = time.perf_counter()

        # Tạo ảnh giả lập biển số 51A-123.45
        dummy_img = np.full((120, 360, 3), 255, dtype=np.uint8)
        cv2.putText(
            dummy_img,
            "51A-123.45",
            (30, 75),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.5,
            (0, 0, 0),
            3,
            cv2.LINE_AA,
        )

        try:
            self.recognize_frames([dummy_img])
            self._warmup_done = True
            warmup_ms = (time.perf_counter() - t0) * 1000
            logger.info("GPU Warm-up THÀNH CÔNG trong %.1f ms! Pipeline sẵn sàng.", warmup_ms)
        except Exception as e:
            logger.warning("GPU Warm-up gặp lỗi (không nghiêm trọng): %s", e)

    def recognize_frames(
        self,
        frames: List[Union[bytes, np.ndarray]],
    ) -> PipelineResult:
        """Xử lý danh sách các frame ảnh (1 hoặc nhiều frame từ ESP32-CAM),
        áp dụng Multi-frame Voting để chọn biển số tốt nhất.
        """
        t_start = time.perf_counter()

        if not frames:
            return PipelineResult(
                plate_raw="",
                plate_normalized="",
                plate_display="",
                confidence=0.0,
                is_valid=False,
                crop_jpeg_bytes=None,
                best_frame_index=0,
                latency_ms=0.0,
                detector_used="none",
                total_candidates=0,
            )

        all_candidates: List[PlateCandidate] = []
        detector_sources = set()

        for f_idx, frame_data in enumerate(frames):
            # 1. Chuyển đổi sang np.ndarray BGR
            img_bgr = None
            if isinstance(frame_data, bytes):
                nparr = np.frombuffer(frame_data, np.uint8)
                img_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            elif isinstance(frame_data, np.ndarray):
                img_bgr = frame_data

            if img_bgr is None or img_bgr.size == 0:
                continue

            # 2. Phát hiện vùng biển số
            detected_plates = self.detector.detect_and_crop(img_bgr)
            for dp in detected_plates:
                detector_sources.add(dp.source)

                # 3. Đọc ký tự OCR
                raw_text, ocr_conf = self.ocr.read_crop(dp.crop_bgr)
                if not raw_text:
                    continue

                # 4. Chuẩn hóa & kiểm tra Regex
                norm_res = normalize_plate(raw_text)

                # Mã hóa crop sang JPEG bytes
                _, crop_buf = cv2.imencode(".jpg", dp.crop_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
                crop_bytes = crop_buf.tobytes() if crop_buf is not None else None

                # Điểm tự tin tổng hợp: kết hợp giữa detector conf và ocr conf
                combined_conf = round((dp.confidence * 0.4) + (ocr_conf * 0.6), 3)

                candidate = PlateCandidate(
                    plate_raw=norm_res.plate_raw,
                    plate_normalized=norm_res.plate_normalized,
                    plate_display=norm_res.plate_display,
                    confidence=combined_conf,
                    is_valid=norm_res.is_valid,
                    frame_index=f_idx,
                    crop_jpeg_bytes=crop_bytes,
                )
                all_candidates.append(candidate)

        # 5. Multi-frame Voting
        best_candidate = vote_best_candidate(all_candidates)

        latency_ms = round((time.perf_counter() - t_start) * 1000, 1)
        self._last_latency_ms = latency_ms

        detector_used_str = "+".join(sorted(detector_sources)) if detector_sources else "none"

        if best_candidate is not None:
            return PipelineResult(
                plate_raw=best_candidate.plate_raw,
                plate_normalized=best_candidate.plate_normalized,
                plate_display=best_candidate.plate_display,
                confidence=best_candidate.confidence,
                is_valid=best_candidate.is_valid,
                crop_jpeg_bytes=best_candidate.crop_jpeg_bytes,
                best_frame_index=best_candidate.frame_index,
                latency_ms=latency_ms,
                detector_used=detector_used_str,
                total_candidates=len(all_candidates),
            )
        else:
            return PipelineResult(
                plate_raw="",
                plate_normalized="",
                plate_display="",
                confidence=0.0,
                is_valid=False,
                crop_jpeg_bytes=None,
                best_frame_index=0,
                latency_ms=latency_ms,
                detector_used=detector_used_str,
                total_candidates=0,
            )

    @property
    def status(self) -> dict:
        """Báo cáo trạng thái AI phục vụ /health endpoint."""
        return {
            "gpuAvailable": self.detector.device == "cuda:0",
            "device": self.detector.device,
            "yoloLoaded": self.detector.yolo_model is not None,
            "warmupDone": self._warmup_done,
            "lastLatencyMs": self._last_latency_ms,
        }

