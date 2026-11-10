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


class RecognizeResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool = True
    seq: int
    direction: str
    action: str  # "OPEN" | "DENY"
    status: str  # "ok" | "unrecognized" | "denied_full" | "denied_reserved_taken" | "duplicate_ignored"
    assignedSlot: str = ""
    plateRaw: str = ""
    plateNormalized: str = ""
    plateDisplay: str = ""
    confidence: float = 0.0
    latencyMs: float = 0.0
    duplicate: bool = False
    denyReason: Optional[str] = None
    flags: List[str] = Field(default_factory=list)


class GateEventRecord(BaseModel):
    """Schema chuẩn của document gateEvents/{autoId} khớp với Repo 2"""
    model_config = ConfigDict(extra="ignore")

    deviceId: str = "ESP32_PARKING_01"
    lotId: str = "lot_live_01"
    seq: int
    direction: str  # "IN" | "OUT"
    plateRaw: str = ""
    plateNormalized: str = ""
    plateDisplay: str = ""
    confidence: float = 0.0
    decision: str  # "OPEN" | "DENY"
    denyReason: Optional[str] = None
    assignedSlotId: Optional[str] = None
    matchedReservationId: Optional[str] = None
    flags: List[str] = Field(default_factory=list)
    status: str = "ok"
    imageUrl: Optional[str] = None
    cropImageUrl: Optional[str] = None
    latencyMs: float = 0.0
    source: str = "edge"
    serverTs: Optional[Any] = None


