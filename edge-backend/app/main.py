"""Main Entrypoint for Edge Backend Server.

Quản lý vòng đời ứng dụng (Lifespan):
- Khởi tạo RAM StateCache
- Khởi tạo và kết nối MQTT Bridge
- Khởi tạo Cloud Bridge (FirestoreBridge hoặc NullCloudBridge)
- Đăng ký các REST API router (/health, /debug)
"""

from contextlib import asynccontextmanager
import logging
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.debug import router as debug_router
from app.api.health import router as health_router
from app.cloud.base import CloudBridgeBase
from app.cloud.firestore_bridge import FirestoreBridge
from app.cloud.null_bridge import NullCloudBridge
from app.config import settings
from app.logging_setup import setup_logging
from app.mqtt.bridge import MqttBridge
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
    logger.info("Khởi động Edge Backend Server (Phase S3)...")
    logger.info("Device ID: %s | Cam ID: %s | Lot ID: %s", settings.DEVICE_ID, settings.CAM_DEVICE_ID, settings.LOT_ID)
    logger.info("MQTT Broker: %s:%d | Firebase Enabled: %s", settings.MQTT_HOST, settings.MQTT_PORT, settings.FIREBASE_ENABLED)
    logger.info("==================================================")

    # 1. Khởi tạo RAM StateCache
    cache = StateCache(slot_ids=["A1", "A2", "B1", "B2"])

    # 2. Khởi tạo CloudBridge (Firestore hoặc Null)
    cloud: CloudBridgeBase
    if settings.FIREBASE_ENABLED:
        logger.info("Chế độ Cloud: FIRESTORE (Đồng bộ thời gian thực với Cloud)")
        cloud = FirestoreBridge(cache=cache, settings=settings)
    else:
        logger.info("Chế độ Cloud: NULL BRIDGE (Chạy độc lập / Giả lập, không kết nối Firebase)")
        cloud = NullCloudBridge()

    # 3. Khởi tạo MQTT Bridge
    mqtt_bridge = MqttBridge(cache=cache, settings=settings, cloud=cloud)

    # 4. Liên kết 2 chiều giữa Cloud Bridge và MQTT Bridge
    cloud.mqtt = mqtt_bridge

    # Gán vào app state để các API endpoint truy cập
    app.state.cache = cache
    app.state.cloud = cloud
    app.state.mqtt = mqtt_bridge

    # 5. Khởi động các worker nền
    cloud.start()
    mqtt_bridge.start()

    logger.info("Edge Backend Server đã sẵn sàng phục vụ!")

    yield

    # 6. Dọn dẹp tài nguyên khi tắt server
    logger.info("Đang tắt Edge Backend Server...")
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

# Đăng ký các Router
app.include_router(health_router)
app.include_router(debug_router)


@app.get("/")
def get_root():
    return {
        "service": "iot-parking-edge-backend",
        "version": "1.0.0",
        "phase": "S3",
        "docsUrl": "/docs",
        "healthUrl": "/health",
        "debugUrl": "/debug/state",
    }

