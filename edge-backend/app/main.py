"""Main Entrypoint for Edge Backend Server.

Quản lý vòng đời ứng dụng (Lifespan):
- Khởi tạo RAM StateCache
- Khởi tạo AI Recognition Pipeline (YOLOv8 + EasyOCR GPU)
- Khởi tạo Cloud Bridge (FirestoreBridge hoặc NullCloudBridge)
- Khởi tạo StorageUploader (Lưu ảnh đĩa local / Firebase Cloud Storage)
- Khởi tạo Fast Gate Arbiter (Phân xử mở cổng < 5ms, Timer 5.0s, Idempotency)
- Khởi tạo và kết nối MQTT Bridge
- Đăng ký các REST API router (/health, /debug, /recognize)
"""

from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.debug import router as debug_router
from app.api.health import router as health_router
from app.api.recognize import router as recognize_router
from app.arbiter.gate_arbiter import GateArbiter
from app.cloud.base import CloudBridgeBase
from app.cloud.firestore_bridge import FirestoreBridge
from app.cloud.null_bridge import NullCloudBridge
from app.cloud.storage_uploader import StorageUploader
from app.config import settings
from app.logging_setup import setup_logging
from app.mqtt.bridge import MqttBridge
from app.recognition.pipeline import PlateRecognitionPipeline
from app.state_cache import StateCache

setup_logging(level=settings.LOG_LEVEL)
logger = logging.getLogger("edge_server.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Trong môi trường testing, bỏ qua việc kết nối phần cứng và MQTT thật
    if getattr(app.state, "test_mode", False):
        yield
        return

    logger.info("==================================================")
    logger.info("Khởi động Edge Backend Server (Phase S5 - Gate Arbiter & Recognize)...")
    logger.info("Device ID: %s | Cam ID: %s | Lot ID: %s", settings.DEVICE_ID, settings.CAM_DEVICE_ID, settings.LOT_ID)
    logger.info("MQTT Broker: %s:%d | Firebase Enabled: %s", settings.MQTT_HOST, settings.MQTT_PORT, settings.FIREBASE_ENABLED)
    logger.info("==================================================")

    # 1. Khởi tạo RAM StateCache
    cache = StateCache(slot_ids=["A1", "A2", "B1", "B2"])

    # 2. Khởi tạo AI Recognition Pipeline & GPU Warmup
    ai_pipeline = PlateRecognitionPipeline(
        model_path=settings.YOLO_MODEL_PATH,
        conf_threshold=settings.YOLO_CONF_THRESHOLD,
        padding_ratio=settings.PADDING_RATIO,
        use_gpu=settings.USE_GPU,
    )
    ai_pipeline.warmup()

    # 3. Khởi tạo CloudBridge (Firestore hoặc Null)
    cloud: CloudBridgeBase
    if settings.FIREBASE_ENABLED:
        logger.info("Chế độ Cloud: FIRESTORE (Đồng bộ thời gian thực với Cloud)")
        cloud = FirestoreBridge(cache=cache, settings=settings)
    else:
        logger.info("Chế độ Cloud: NULL BRIDGE (Chạy độc lập / Giả lập, không kết nối Firebase)")
        cloud = NullCloudBridge()

    # 4. Khởi tạo StorageUploader (Lưu ảnh nền local / cloud)
    storage_uploader = StorageUploader(cloud_bridge=cloud, max_workers=2)

    # 5. Khởi tạo GateArbiter (Phân xử mở barie < 5ms)
    gate_arbiter = GateArbiter(
        cache=cache,
        cloud_bridge=cloud,
        storage_uploader=storage_uploader,
    )

    # 6. Khởi tạo MQTT Bridge và liên kết chéo
    mqtt_bridge = MqttBridge(
        cache=cache,
        settings=settings,
        cloud=cloud,
        arbiter=gate_arbiter,
        ai_pipeline=ai_pipeline,
        storage_uploader=storage_uploader,
    )
    gate_arbiter.mqtt = mqtt_bridge
    cloud.mqtt = mqtt_bridge

    # Gán vào app state để các API endpoint truy cập
    app.state.cache = cache
    app.state.cloud = cloud
    app.state.mqtt = mqtt_bridge
    app.state.ai_pipeline = ai_pipeline
    app.state.arbiter = gate_arbiter
    app.state.storage_uploader = storage_uploader

    # 7. Khởi động các worker nền
    cloud.start()
    mqtt_bridge.start()

    logger.info("Edge Backend Server đã sẵn sàng phục vụ!")

    yield

    # 8. Dọn dẹp tài nguyên khi tắt server
    logger.info("Đang tắt Edge Backend Server...")
    gate_arbiter.stop()
    storage_uploader.stop()
    mqtt_bridge.stop()
    cloud.stop()
    logger.info("Edge Backend Server đã tắt an toàn.")


app = FastAPI(
    title="IOT Parking - Edge Backend Server",
    description="Edge Backend đồng bộ trạng thái bãi đỗ xe giữa ESP32 phần cứng và Cloud Firestore",
    version="1.0.0",
    lifespan=lifespan,
)

# Cấu hình CORS để phục vụ Web Dashboard hoặc Swagger UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Thư mục lưu ảnh captures tĩnh
captures_dir = Path(settings.LOCAL_CAPTURE_DIR)
if not captures_dir.is_absolute():
    captures_dir = Path(__file__).resolve().parent.parent / settings.LOCAL_CAPTURE_DIR
captures_dir.mkdir(parents=True, exist_ok=True)
app.mount("/captures", StaticFiles(directory=str(captures_dir)), name="captures")

# Đăng ký các Router
app.include_router(health_router)
app.include_router(debug_router)
app.include_router(recognize_router)


@app.get("/cam-test", include_in_schema=False)
def get_cam_test_page():
    static_file = Path(__file__).resolve().parent.parent / "static" / "cam-test.html"
    return FileResponse(str(static_file))


@app.get("/")
def get_root():
    return {
        "service": "iot-parking-edge-backend",
        "version": "1.0.0",
        "phase": "S5",
        "docsUrl": "/docs",
        "healthUrl": "/health",
        "debugUrl": "/debug/state",
        "recognizeUrl": "/recognize",
        "camTestUrl": "/cam-test",
    }
