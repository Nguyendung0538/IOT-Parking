import os
from pathlib import Path
from typing import Any, Union
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.path.join(Path(__file__).resolve().parent.parent, ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # 1. MQTT Broker
    MQTT_HOST: str = "127.0.0.1"
    MQTT_PORT: int = 1883
    MQTT_USER: str = "edge_server"
    MQTT_PASS: str = "edge_secret_2026"
    MQTT_CLIENT_ID: str = "edge_server_01"

    # 2. HTTP Server
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    CAM_SHARED_API_KEY: str = "smart-parking-cam-secret-2026"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"

    # 3. Định danh Bãi đỗ & Thiết bị
    DEVICE_ID: str = "ESP32_PARKING_01"
    CAM_DEVICE_ID: str = "ESP32_CAM_01"
    LOT_ID: str = "lot_live_01"
    SLOT_IDS: Union[list[str], str] = Field(default=["A1", "A2", "B1", "B2"])

    @field_validator("SLOT_IDS", mode="before")
    @classmethod
    def parse_slot_ids(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            v_strip = v.strip()
            if v_strip.startswith("[") and v_strip.endswith("]"):
                try:
                    import json
                    parsed = json.loads(v_strip)
                    if isinstance(parsed, list):
                        return [str(s).strip() for s in parsed]
                except Exception:
                    pass
            return [s.strip() for s in v_strip.split(",") if s.strip()]
        if isinstance(v, (list, tuple)):
            return [str(s).strip() for s in v]
        return ["A1", "A2", "B1", "B2"]

    # 4. Timeout & TTL
    DEVICE_COMMAND_TTL_S: int = 60
    STATE_STALE_AFTER_S: int = 90

    # 5. Firebase / Cloud Bridge
    FIREBASE_ENABLED: bool = False
    GOOGLE_APPLICATION_CREDENTIALS: str = "serviceAccountKey.json"
    FIREBASE_STORAGE_BUCKET: str = ""

    # 6. Ngưỡng AI (Phase S4/S5)
    YOLO_MODEL_PATH: str = "models/plate_yolo_s.pt"
    YOLO_CONF_THRESHOLD: float = 0.4
    OCR_CONF_THRESHOLD: float = 0.5
    USE_GPU: bool = True
    PADDING_RATIO: float = 0.12

    # Generated Topic Properties (Không hardcode chuỗi rải rác)
    @property
    def topic_state(self) -> str:
        return f"parking/{self.DEVICE_ID}/state"

    @property
    def topic_gate_request(self) -> str:
        return f"parking/{self.DEVICE_ID}/gate/request"

    @property
    def topic_gate_command(self) -> str:
        return f"parking/{self.DEVICE_ID}/gate/command"

    @property
    def topic_reserved(self) -> str:
        return f"parking/{self.DEVICE_ID}/reserved"

    @property
    def topic_gate_status(self) -> str:
        return f"parking/{self.DEVICE_ID}/status"

    @property
    def topic_cam_status(self) -> str:
        return f"parking/{self.CAM_DEVICE_ID}/status"


settings = Settings()
