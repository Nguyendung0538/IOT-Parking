"""Benchmark script đo hiệu năng và độ chính xác của AI Pipeline trên GPU NVIDIA."""

import glob
import logging
import os
from pathlib import Path
import sys
import time
import cv2
import numpy as np

# Thêm đường dẫn app vào sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.recognition.pipeline import PlateRecognitionPipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("benchmark")

DATASET_DIR = BASE_DIR.parent / "yolo_plate_dataset"


def run_benchmark(num_samples: int = 30):
    print("=" * 70)
    print("🚀 BẮT ĐẦU BENCHMARK PIPELINE AI NHẬN DIỆN BIỂN SỐ TRÊN GPU NVIDIA")
    print("=" * 70)

    if not DATASET_DIR.exists():
        print(f"❌ Không tìm thấy thư mục dataset tại: {DATASET_DIR}")
        return

    # Lấy mẫu ảnh ô tô (CarLongPlate) và xe máy (xemay)
    car_images = sorted(glob.glob(str(DATASET_DIR / "CarLongPlate*.jpg")))
    moto_images = sorted(glob.glob(str(DATASET_DIR / "xemay*.jpg")))

    samples_per_type = num_samples // 2
    selected_cars = car_images[:samples_per_type]
    selected_motos = moto_images[:samples_per_type]
    test_files = selected_cars + selected_motos

    print(f"📁 Tổng số ảnh kiểm thử: {len(test_files)} ({len(selected_cars)} ô tô, {len(selected_motos)} xe máy)")

    # Khởi tạo Pipeline AI
    pipeline = PlateRecognitionPipeline(use_gpu=True)
    pipeline.warmup()

    latencies = []
    detected_count = 0
    valid_count = 0

    print("\n" + "-" * 88)
    print(f"{'STT':<4} | {'Loại':<7} | {'Tên File':<22} | {'Biển Nhận Diện':<14} | {'Hợp Lệ':<7} | {'Conf':<6} | {'Độ Trễ':<8}")
    print("-" * 88)

    for idx, fpath in enumerate(test_files, 1):
        fname = Path(fpath).name
        vtype = "Ô tô" if "Car" in fname else "Xe máy"

        img = cv2.imread(fpath)
        if img is None:
            continue

        res = pipeline.recognize_frames([img])
        latencies.append(res.latency_ms)

        if res.plate_raw:
            detected_count += 1
        if res.is_valid:
            valid_count += 1

        disp = res.plate_display if res.plate_display else (res.plate_raw if res.plate_raw else "---")
        valid_mark = "✅ Có" if res.is_valid else "❌ Không"

        print(f"{idx:<4} | {vtype:<7} | {fname[:22]:<22} | {disp:<14} | {valid_mark:<7} | {res.confidence:<6.2f} | {res.latency_ms:>5.1f} ms")

    total = len(latencies)
    if total == 0:
        print("Không có ảnh nào được xử lý.")
        return

    detection_rate = (detected_count / total) * 100
    valid_rate = (valid_count / total) * 100
    mean_lat = float(np.mean(latencies))
    median_lat = float(np.median(latencies))
    p95_lat = float(np.percentile(latencies, 95))
    min_lat = float(np.min(latencies))
    max_lat = float(np.max(latencies))

    print("-" * 88)
    print("\n📊 TỔNG KẾT HIỆU NĂNG & ĐỘ CHÍNH XÁC:")
    print(f"  • Thiết bị thực thi: {pipeline.detector.device.upper()}")
    print(f"  • Model YOLO: {Path(pipeline.detector.yolo_model.model_name).name if pipeline.detector.yolo_model else 'OpenCV Fallback'}")
    print(f"  • Tỷ lệ phát hiện biển số (Detection Rate): {detection_rate:.1f}% ({detected_count}/{total})")
    print(f"  • Tỷ lệ biển số hợp lệ (Valid VN Plate Rate): {valid_rate:.1f}% ({valid_count}/{total})")
    print(f"  • Độ trễ trung bình (Mean Latency):          {mean_lat:.1f} ms  (Mục tiêu < 400 ms)")
    print(f"  • Độ trễ trung vị (Median Latency):            {median_lat:.1f} ms")
    print(f"  • Độ trễ phân vị 95 (P95 Latency):            {p95_lat:.1f} ms")
    print(f"  • Độ trễ nhỏ nhất / lớn nhất:                  {min_lat:.1f} ms / {max_lat:.1f} ms")
    print("=" * 70)


if __name__ == "__main__":
    count = 30
    if len(sys.argv) > 1:
        try:
            count = int(sys.argv[1])
        except ValueError:
            pass
    run_benchmark(num_samples=count)

