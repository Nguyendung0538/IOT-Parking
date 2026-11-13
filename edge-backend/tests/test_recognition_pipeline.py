"""Unit & Integration tests for PlateRecognitionPipeline and OCR box merging."""

import cv2
import numpy as np
import pytest

from app.recognition.ocr import PlateOCR
from app.recognition.pipeline import PlateRecognitionPipeline


def test_ocr_merge_boxes_single_line():
    # Giả lập 2 box nằm trên cùng 1 dòng: "51A" ở bên trái, "123.45" ở bên phải
    # Format easyocr: (bbox, text, conf)
    # bbox: [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
    box1 = ([[10, 20], [60, 20], [60, 50], [10, 50]], "51A", 0.95)
    box2 = ([[70, 22], [150, 22], [150, 52], [70, 52]], "12345", 0.90)

    text, conf = PlateOCR.merge_ocr_boxes([box1, box2])
    assert text == "51A12345"
    assert conf > 0.85


def test_ocr_merge_boxes_two_lines():
    # Giả lập biển 2 dòng: dòng 1 "59H1" ở trên, dòng 2 "549.86" ở dưới
    # Dòng 1: y từ 10..40
    box_top = ([[20, 10], [90, 10], [90, 40], [20, 40]], "59H1", 0.92)
    # Dòng 2: y từ 50..80
    box_bot = ([[15, 50], [100, 50], [100, 80], [15, 80]], "54986", 0.94)

    text, conf = PlateOCR.merge_ocr_boxes([box_bot, box_top])  # Đảo thứ tự đầu vào
    # Thuật toán phải tự sắp xếp dòng trên trước, dòng dưới sau
    assert text == "59H154986"
    assert conf > 0.90


def test_pipeline_synthetic_frame():
    # Tạo 1 ảnh biển số giả lập trong RAM
    img = np.full((150, 400, 3), 255, dtype=np.uint8)
    cv2.putText(
        img,
        "51A-123.45",
        (30, 90),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.6,
        (0, 0, 0),
        3,
        cv2.LINE_AA,
    )

    # Chạy pipeline (sử dụng model YOLO nếu có hoặc OpenCV fallback)
    pipeline = PlateRecognitionPipeline(use_gpu=True)
    res = pipeline.recognize_frames([img])

    assert res.latency_ms > 0
    assert res.detector_used in ("yolo", "opencv_fallback", "opencv_center_roi")
    assert res.total_candidates >= 0


def test_pipeline_empty_frames():
    pipeline = PlateRecognitionPipeline(use_gpu=False)
    res = pipeline.recognize_frames([])
    assert res.plate_normalized == ""
    assert res.is_valid is False
    assert res.total_candidates == 0

