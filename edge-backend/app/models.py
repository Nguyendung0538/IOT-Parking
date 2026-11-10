from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class SlotState(BaseModel):
    model_config = ConfigDict(extra="ignore")
    occupied: bool
    reserved: bool = False


class StateSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")
    zone_A_available: Optional[int] = None
    zone_B_available: Optional[int] = None
    total_available: Optional[int] = None
    total_occupied: Optional[int] = None
    total_reserved: Optional[int] = None


class StatePayload(BaseModel):
    """Payload thực tế nhận từ topic parking/{DEVICE_ID}/state"""
    model_config = ConfigDict(extra="ignore")

    deviceId: str = "ESP32_PARKING_01"
    uptime: int = 0
    freeSlots: int = 0
    totalSlots: int = 4
    barrierOpen: bool = False
    gateState: str = "IDLE"
    slots: Dict[str, SlotState] = Field(default_factory=dict)
    summary: Optional[Dict[str, Any]] = None


class GateRequestPayload(BaseModel):
    """Payload nhận từ topic parking/{DEVICE_ID}/gate/request"""
    model_config = ConfigDict(extra="ignore")

    deviceId: str = "ESP32_PARKING_01"
    seq: int
    direction: str = "IN"  # "IN" | "OUT"
    freeSlots: int = 0
    timestamp: Optional[int] = None
    offlineFallback: bool = False
    clientTimestamp: Optional[int] = None


class GateCommandPayload(BaseModel):
    """Payload gửi xuống topic parking/{DEVICE_ID}/gate/command"""
    model_config = ConfigDict(extra="ignore")

    seq: int
    action: str  # "OPEN" | "DENY"
    plate: str = ""
    assignedSlot: str = ""
    reason: Optional[str] = None


class ReservedMaskPayload(BaseModel):
    """Payload gửi xuống topic parking/{DEVICE_ID}/reserved (Retained)"""
    model_config = ConfigDict(extra="ignore")

    A1: bool = False
    A2: bool = False
    B1: bool = False
    B2: bool = False


class ReservationItem(BaseModel):
    id: str
    slotId: str
    plate: str
    expiresAt: datetime


class PendingRequestItem(BaseModel):
    seq: int
    direction: str
    receivedAt: datetime

