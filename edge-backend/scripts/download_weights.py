"""Script tải trọng số YOLOv8 biển số xe lưu vào edge-backend/models/plate_yolo_s.pt."""

import os
from pathlib import Path
import urllib.request
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("download_weights")

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
TARGET_PATH = MODELS_DIR / "plate_yolo_s.pt"

# Danh sách URL tải trọng số dự phòng (ưu tiên model chuyên biển số Việt Nam)
URL_CANDIDATES = [
    {
        "name": "VN Plate Benchmark (YOLOv8 Edge)",
        "url": "https://huggingface.co/neitpm/vn-plate-detection-benchmark/resolve/main/weights/yolov8n_edge.pt",
    },
    {
        "name": "Koushim License Plate Detection (YOLOv8n)",
        "url": "https://huggingface.co/Koushim/yolov8-license-plate-detection/resolve/main/best.pt",
    },
    {
        "name": "Keremberke License Plate Object Detection (YOLOv8n)",
        "url": "https://huggingface.co/keremberke/yolov8n-license-plate-detection/resolve/main/best.pt",
    },
]


def download_model(force: bool = False) -> bool:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    if TARGET_PATH.exists() and not force:
        size_mb = TARGET_PATH.stat().st_size / (1024 * 1024)
        if size_mb > 1.0:
            logger.info("File trọng số đã tồn tại: %s (%.2f MB). Bỏ qua tải lại.", TARGET_PATH, size_mb)
            return True
        else:
            logger.warning("File trọng số bị hỏng (< 1MB). Tiến hành tải lại...")

    for candidate in URL_CANDIDATES:
        name = candidate["name"]
        url = candidate["url"]
        logger.info("Đang thử tải model '%s' từ: %s ...", name, url)
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp, open(TARGET_PATH, "wb") as out_file:
                total_size = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                chunk_size = 64 * 1024
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    out_file.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        percent = (downloaded / total_size) * 100
                        print(f"\r  -> Tiến độ: {downloaded / (1024*1024):.2f}/{total_size / (1024*1024):.2f} MB ({percent:.1f}%)", end="")
            print()
            size_mb = TARGET_PATH.stat().st_size / (1024 * 1024)
            if size_mb > 1.0:
                logger.info("TẢI THÀNH CÔNG: %s (%.2f MB)!", TARGET_PATH, size_mb)
                return True
            else:
                logger.warning("File tải về quá nhỏ (%.2f MB), thử nguồn tiếp theo...", size_mb)
                TARGET_PATH.unlink(missing_ok=True)
        except Exception as e:
            logger.warning("Tải từ nguồn '%s' thất bại: %s", name, e)
            TARGET_PATH.unlink(missing_ok=True)

    logger.error("Không thể tải trọng số từ bất kỳ nguồn nào.")
    return False


if __name__ == "__main__":
    download_model()

