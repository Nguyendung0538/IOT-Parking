from typing import Any, Dict
from fastapi import APIRouter, Request

from app.config import settings

router = APIRouter(tags=["Health"])


@router.get("/health")
def get_health(request: Request) -> Dict[str, Any]:
    cache = request.app.state.cache
    mqtt = request.app.state.mqtt
    cloud = request.app.state.cloud

    snap = cache.snapshot()
    mqtt_connected = mqtt.is_connected
    last_state_ago = snap.get("lastStateAgoS")

    state_stale = (
        last_state_ago is not None and last_state_ago > settings.STATE_STALE_AFTER_S
    )

    cloud_status = cloud.status() if cloud else {"mode": "none", "connected": False}
    cloud_ok = cloud_status.get("connected", False)

    is_degraded = (not mqtt_connected) or state_stale or (not cloud_ok)
    overall_status = "degraded" if is_degraded else "ok"

    return {
        "status": overall_status,
        "mqtt": {
            "connected": mqtt_connected,
            "lastMessageAgoS": mqtt.last_message_ago_s,
            "messageCount": mqtt.message_count,
            "parseErrors": mqtt.parse_errors,
        },
        "cloud": cloud_status,
        "devices": {
            "gateOnline": snap.get("gateOnline", False),
            "camOnline": snap.get("camOnline", False),
            "lastStateAgoS": last_state_ago,
            "stateStale": state_stale,
        },
        "lot": {
            "sensorOccupied": snap.get("sensorOccupied", {}),
            "reservedMask": snap.get("reservedMask", {}),
            "freeSlotsPublic": snap.get("freeSlotsPublic", 0),
        },
    }

