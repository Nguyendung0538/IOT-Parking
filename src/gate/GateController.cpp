#include "GateController.h"

#define GATE_SENSOR_DEBOUNCE_MS 80

static bool isDirectionOut(const char* direction) {
    if (!direction) return false;
    return (strcmp(direction, "OUT") == 0 || strcmp(direction, "out") == 0);
}

GateController::GateController()
    : entryState(GATE_IN_IDLE),
      exitState(GATE_OUT_IDLE),
      lastEvent(GATE_EVENT_NONE),
      barrierInOpen(false),
      barrierOutOpen(false),
      barrierInOpenTimestamp(0),
      barrierOutOpenTimestamp(0),
      flagJustEntered(false),
      flagJustExited(false),
      carsInLot(0),
      nextSeq(0),
      seqIn(0),
      seqOut(0),
      pendingInAuthPublish(false),
      inAuthWaitTimestamp(0),
      pendingInOfflineFallback(false),
      fallbackInSeq(0),
      pendingOutAuthPublish(false),
      outAuthWaitTimestamp(0),
      pendingOutOfflineFallback(false),
      fallbackOutSeq(0),
      rawEntryOut(false),
      confirmedEntryOut(false),
      lastDebounceEntryOut(0),
      rawEntryIn(false),
      confirmedEntryIn(false),
      lastDebounceEntryIn(0),
      rawExitIn(false),
      confirmedExitIn(false),
      lastDebounceExitIn(0),
      rawExitOut(false),
      confirmedExitOut(false),
      lastDebounceExitOut(0) {
    activePlate[0] = '\0';
    assignedSlot[0] = '\0';
}

void GateController::begin() {
    // Cổng VÀO: GPIO 34, 35 là input-only (không có pullup nội)
    pinMode(PIN_SENSOR_ENTRY_OUT, INPUT);
    pinMode(PIN_SENSOR_ENTRY_IN, INPUT);

    // Cổng RA: GPIO 32, 33 hỗ trợ INPUT_PULLUP tránh nhiễu khi hở chân
    pinMode(PIN_SENSOR_EXIT_IN, INPUT_PULLUP);
    pinMode(PIN_SENSOR_EXIT_OUT, INPUT_PULLUP);

    // Chuẩn bị 2 Servo SG90 trên 2 timer PWM riêng biệt
    ESP32PWM::allocateTimer(0);
    ESP32PWM::allocateTimer(1);

    gateServoIn.setPeriodHertz(50);
    gateServoIn.attach(PIN_SERVO_GATE_IN, 500, 2400);

    gateServoOut.setPeriodHertz(50);
    gateServoOut.attach(PIN_SERVO_GATE_OUT, 500, 2400);

    // Mặc định hạ cả 2 barie
    closeEntryBarrier();
    closeExitBarrier();

    // Đọc trạng thái ban đầu của 4 cảm biến cổng
    unsigned long now = millis();
    rawEntryOut = (digitalRead(PIN_SENSOR_ENTRY_OUT) == SENSOR_ACTIVE_LEVEL);
    confirmedEntryOut = rawEntryOut;
    lastDebounceEntryOut = now;

    rawEntryIn = (digitalRead(PIN_SENSOR_ENTRY_IN) == SENSOR_ACTIVE_LEVEL);
    confirmedEntryIn = rawEntryIn;
    lastDebounceEntryIn = now;

    rawExitIn = (digitalRead(PIN_SENSOR_EXIT_IN) == SENSOR_ACTIVE_LEVEL);
    confirmedExitIn = rawExitIn;
    lastDebounceExitIn = now;

    rawExitOut = (digitalRead(PIN_SENSOR_EXIT_OUT) == SENSOR_ACTIVE_LEVEL);
    confirmedExitOut = rawExitOut;
    lastDebounceExitOut = now;

    Serial.println(F("[GateController] Initialized Dual Gates (IN: 34/35 + GPIO18, OUT: 32/33 + GPIO19)."));
}

void GateController::updateSensors() {
    unsigned long now = millis();

    // 1. Cổng VÀO - ngoài bãi (GPIO 34)
    bool readEntryOut = (digitalRead(PIN_SENSOR_ENTRY_OUT) == SENSOR_ACTIVE_LEVEL);
    if (readEntryOut != rawEntryOut) {
        rawEntryOut = readEntryOut;
        lastDebounceEntryOut = now;
    }
    if ((now - lastDebounceEntryOut) >= GATE_SENSOR_DEBOUNCE_MS) {
        confirmedEntryOut = rawEntryOut;
    }

    // 2. Cổng VÀO - trong bãi (GPIO 35)
    bool readEntryIn = (digitalRead(PIN_SENSOR_ENTRY_IN) == SENSOR_ACTIVE_LEVEL);
    if (readEntryIn != rawEntryIn) {
        rawEntryIn = readEntryIn;
        lastDebounceEntryIn = now;
    }
    if ((now - lastDebounceEntryIn) >= GATE_SENSOR_DEBOUNCE_MS) {
        confirmedEntryIn = rawEntryIn;
    }

    // 3. Cổng RA - trong bãi (GPIO 32)
    bool readExitIn = (digitalRead(PIN_SENSOR_EXIT_IN) == SENSOR_ACTIVE_LEVEL);
    if (readExitIn != rawExitIn) {
        rawExitIn = readExitIn;
        lastDebounceExitIn = now;
    }
    if ((now - lastDebounceExitIn) >= GATE_SENSOR_DEBOUNCE_MS) {
        confirmedExitIn = rawExitIn;
    }

    // 4. Cổng RA - ngoài bãi (GPIO 33)
    bool readExitOut = (digitalRead(PIN_SENSOR_EXIT_OUT) == SENSOR_ACTIVE_LEVEL);
    if (readExitOut != rawExitOut) {
        rawExitOut = readExitOut;
        lastDebounceExitOut = now;
    }
    if ((now - lastDebounceExitOut) >= GATE_SENSOR_DEBOUNCE_MS) {
        confirmedExitOut = rawExitOut;
    }
}

void GateController::update(bool parkingFull) {
    updateSensors();
    unsigned long now = millis();

    updateEntryGate(parkingFull, now);
    updateExitGate(now);
}

void GateController::updateEntryGate(bool parkingFull, unsigned long now) {
    switch (entryState) {
        case GATE_IN_IDLE:
            if (confirmedEntryOut) {
#if ENABLE_WIFI
                if (isLotPhysicallyFull()) {
                    lastEvent = GATE_EVENT_BLOCKED_FULL;
                    entryState = GATE_IN_BLOCKED_FULL;
                    Serial.printf("[Gate IN] Tu choi xe vao: Bai da day %u/%u xe!\n", carsInLot, NUM_SLOTS);
                } else {
                    seqIn = ++nextSeq;
                    pendingInAuthPublish = true;
                    inAuthWaitTimestamp = now;
                    lastEvent = GATE_EVENT_WAITING_AUTH;
                    activePlate[0] = '\0';
                    assignedSlot[0] = '\0';
                    entryState = GATE_IN_WAIT_AUTH;
                    Serial.printf("[Gate IN] Xe den cong VAO -> Cho Edge AI auth (seq=%u, carsInLot=%u/%u)...\n",
                                  seqIn, carsInLot, NUM_SLOTS);
                }
#else
                if (parkingFull) {
                    lastEvent = GATE_EVENT_BLOCKED_FULL;
                    entryState = GATE_IN_BLOCKED_FULL;
                    Serial.println(F("[Gate IN] Tu choi xe vao: Bai do xe da DAY!"));
                } else {
                    strncpy(activePlate, "LOCAL", sizeof(activePlate));
                    assignedSlot[0] = '\0';
                    openEntryBarrier();
                    lastEvent = GATE_EVENT_CAR_ENTERING;
                    entryState = GATE_IN_OPEN_WAIT;
                    Serial.println(F("[Gate IN] Local Mode: Mo Barie VAO (90 deg)."));
                }
#endif
            }
            break;

        case GATE_IN_WAIT_AUTH:
            // Xe lùi ra khỏi cảm biến ngoài trước khi backend trả lời
            if (!confirmedEntryOut) {
                pendingInAuthPublish = false;
                if (lastEvent == GATE_EVENT_WAITING_AUTH && exitState != GATE_OUT_WAIT_AUTH) {
                    lastEvent = GATE_EVENT_NONE;
                }
                entryState = GATE_IN_IDLE;
                Serial.println(F("[Gate IN] Xe cong VAO da lui di -> Huy cho auth, ve IDLE."));
                break;
            }

            // Timeout 8s khi không nhận được phản hồi Edge -> Chuyển chờ Web Admin (không tự ý mở barie)
            if ((now - inAuthWaitTimestamp) >= GATE_AUTH_TIMEOUT_MS) {
                Serial.printf("[Gate IN] TIMEOUT 8s khong nhan duoc lenh Edge (seq=%u) -> Giu barie DONG, cho Web duyet!\n", seqIn);
                lastEvent = GATE_EVENT_MANUAL_REQUIRED;
                entryState = GATE_IN_WAIT_MANUAL;
            }
            break;

        case GATE_IN_WAIT_MANUAL:
            if (!confirmedEntryOut) {
                if (lastEvent == GATE_EVENT_MANUAL_REQUIRED) {
                    lastEvent = GATE_EVENT_NONE;
                }
                entryState = GATE_IN_IDLE;
                Serial.println(F("[Gate IN] Xe cho duyet da lui khoi cong VAO -> Ve IDLE."));
            }
            break;

        case GATE_IN_OPEN_WAIT:
            if ((now - barrierInOpenTimestamp) >= GATE_SAFETY_TIMEOUT_MS) {
                closeEntryBarrier();
                lastEvent = GATE_EVENT_TIMEOUT;
                entryState = GATE_IN_IDLE;
                Serial.println(F("[Gate IN] Timeout 10s: Xe vao khong qua cong -> Dong barie VAO!"));
                break;
            }

            if (confirmedEntryIn) {
                entryState = GATE_IN_PASSING;
                Serial.println(F("[Gate IN] Xe dang di qua cong VAO (cham S_ENTRY_IN)..."));
            }
            break;

        case GATE_IN_PASSING:
            if ((now - barrierInOpenTimestamp) >= GATE_SAFETY_TIMEOUT_MS) {
                closeEntryBarrier();
                lastEvent = GATE_EVENT_TIMEOUT;
                entryState = GATE_IN_IDLE;
                Serial.println(F("[Gate IN] Timeout 10s khi xe qua cong VAO -> Dong barie an toan!"));
                break;
            }

            if (!confirmedEntryOut && !confirmedEntryIn) {
                closeEntryBarrier();
                if (carsInLot < NUM_SLOTS) {
                    carsInLot++;
                }
                flagJustEntered = true;
                if (lastEvent == GATE_EVENT_CAR_ENTERING || lastEvent == GATE_EVENT_TIMEOUT) {
                    lastEvent = GATE_EVENT_NONE;
                }
                entryState = GATE_IN_IDLE;
                Serial.printf("[Gate IN] Xe da VAO BAI HOAN TAT -> Dong barie VAO (0 deg). [carsInLot=%u/%u]\n",
                              carsInLot, NUM_SLOTS);
            }
            break;

        case GATE_IN_BLOCKED_FULL:
            if (!confirmedEntryOut) {
                if (lastEvent == GATE_EVENT_BLOCKED_FULL) {
                    lastEvent = GATE_EVENT_NONE;
                }
                entryState = GATE_IN_IDLE;
                Serial.println(F("[Gate IN] Xe bi chan cong VAO da roi di -> Ve IDLE."));
            }
            break;
    }
}

void GateController::updateExitGate(unsigned long now) {
    switch (exitState) {
        case GATE_OUT_IDLE:
            if (confirmedExitIn) {
#if ENABLE_WIFI
                seqOut = ++nextSeq;
                pendingOutAuthPublish = true;
                outAuthWaitTimestamp = now;
                lastEvent = GATE_EVENT_WAITING_AUTH;
                activePlate[0] = '\0';
                assignedSlot[0] = '\0';
                exitState = GATE_OUT_WAIT_AUTH;
                Serial.printf("[Gate OUT] Xe den cong RA -> Cho Edge AI auth (seq=%u)...\n", seqOut);
#else
                strncpy(activePlate, "LOCAL", sizeof(activePlate));
                assignedSlot[0] = '\0';
                openExitBarrier();
                lastEvent = GATE_EVENT_CAR_EXITING;
                exitState = GATE_OUT_OPEN_WAIT;
                Serial.println(F("[Gate OUT] Local Mode: Mo Barie RA (90 deg)."));
#endif
            }
            break;

        case GATE_OUT_WAIT_AUTH:
            if (!confirmedExitIn) {
                pendingOutAuthPublish = false;
                if (lastEvent == GATE_EVENT_WAITING_AUTH && entryState != GATE_IN_WAIT_AUTH) {
                    lastEvent = GATE_EVENT_NONE;
                }
                exitState = GATE_OUT_IDLE;
                Serial.println(F("[Gate OUT] Xe cong RA da lui lai -> Huy cho auth, ve IDLE."));
                break;
            }

            // Timeout 8s khi không nhận được phản hồi Edge -> Chuyển chờ Web Admin (không tự ý mở barie)
            if ((now - outAuthWaitTimestamp) >= GATE_AUTH_TIMEOUT_MS) {
                Serial.printf("[Gate OUT] TIMEOUT 8s khong nhan duoc lenh Edge (seq=%u) -> Giu barie DONG, cho Web duyet!\n", seqOut);
                lastEvent = GATE_EVENT_MANUAL_REQUIRED;
                exitState = GATE_OUT_WAIT_MANUAL;
            }
            break;

        case GATE_OUT_WAIT_MANUAL:
            if (!confirmedExitIn) {
                if (lastEvent == GATE_EVENT_MANUAL_REQUIRED) {
                    lastEvent = GATE_EVENT_NONE;
                }
                exitState = GATE_OUT_IDLE;
                Serial.println(F("[Gate OUT] Xe cho duyet da lui khoi cong RA -> Ve IDLE."));
            }
            break;

        case GATE_OUT_OPEN_WAIT:
            if ((now - barrierOutOpenTimestamp) >= GATE_SAFETY_TIMEOUT_MS) {
                closeExitBarrier();
                lastEvent = GATE_EVENT_TIMEOUT;
                exitState = GATE_OUT_IDLE;
                Serial.println(F("[Gate OUT] Timeout 10s: Xe ra khong qua cong -> Dong barie RA!"));
                break;
            }

            if (confirmedExitOut) {
                exitState = GATE_OUT_PASSING;
                Serial.println(F("[Gate OUT] Xe dang di qua cong RA (cham S_EXIT_OUT)..."));
            }
            break;

        case GATE_OUT_PASSING:
            if ((now - barrierOutOpenTimestamp) >= GATE_SAFETY_TIMEOUT_MS) {
                closeExitBarrier();
                lastEvent = GATE_EVENT_TIMEOUT;
                exitState = GATE_OUT_IDLE;
                Serial.println(F("[Gate OUT] Timeout 10s khi xe qua cong RA -> Dong barie an toan!"));
                break;
            }

            if (!confirmedExitIn && !confirmedExitOut) {
                closeExitBarrier();
                if (carsInLot > 0) {
                    carsInLot--;
                }
                flagJustExited = true;
                if (lastEvent == GATE_EVENT_CAR_EXITING || lastEvent == GATE_EVENT_TIMEOUT) {
                    lastEvent = GATE_EVENT_NONE;
                }
                exitState = GATE_OUT_IDLE;
                Serial.printf("[Gate OUT] Xe da ROI BAI HOAN TAT -> Dong barie RA (0 deg). [carsInLot=%u/%u]\n",
                              carsInLot, NUM_SLOTS);
            }
            break;

        case GATE_OUT_BLOCKED:
            if (!confirmedExitIn) {
                if (lastEvent == GATE_EVENT_BLOCKED_FULL || lastEvent == GATE_EVENT_NO_EXIT_REQUEST) {
                    lastEvent = GATE_EVENT_NONE;
                }
                exitState = GATE_OUT_IDLE;
                Serial.println(F("[Gate OUT] Xe bi chan cong RA da roi di -> Ve IDLE."));
            }
            break;
    }
}

void GateController::handleBackendCommand(uint32_t seq, bool allowOpen, const char* plate, const char* slot) {
    const char* actionStr = (seq == 0 && !allowOpen) ? "CLOSE" : (allowOpen ? "OPEN" : "DENY");
    handleBackendCommand(seq, actionStr, "", plate, slot);
}

void GateController::handleBackendCommand(uint32_t seq, bool allowOpen, const char* direction, const char* plate, const char* slot) {
    const char* actionStr = (seq == 0 && !allowOpen) ? "CLOSE" : (allowOpen ? "OPEN" : "DENY");
    handleBackendCommand(seq, actionStr, direction, plate, slot);
}

void GateController::handleBackendCommand(uint32_t seq, const char* action, const char* direction, const char* plate, const char* slot) {
    const char* act = action ? action : "DENY";
    bool allowOpen = (strcmp(act, "OPEN") == 0);
    bool isWaitManual = (strcmp(act, "WAIT_MANUAL") == 0);

    // 1. Lệnh thủ công / duyệt từ Web Admin (seq == 0)
    if (seq == 0) {
        bool targetOut = isDirectionOut(direction);

        if (allowOpen) {
            if (plate && plate[0] != '\0') {
                strncpy(activePlate, plate, sizeof(activePlate) - 1);
                activePlate[sizeof(activePlate) - 1] = '\0';
            } else {
                strncpy(activePlate, "ADMIN_OPEN", sizeof(activePlate));
            }
            if (slot && slot[0] != '\0') {
                strncpy(assignedSlot, slot, sizeof(assignedSlot) - 1);
                assignedSlot[sizeof(assignedSlot) - 1] = '\0';
            } else {
                assignedSlot[0] = '\0';
            }

            if (targetOut) {
                openExitBarrier();
                lastEvent = GATE_EVENT_CAR_EXITING;
                exitState = GATE_OUT_OPEN_WAIT;
                Serial.printf("[Gate OUT] Nhan lenh ADMIN: Mo Barie RA (Plate=%s)!\n", activePlate);
            } else {
                openEntryBarrier();
                lastEvent = GATE_EVENT_CAR_ENTERING;
                entryState = GATE_IN_OPEN_WAIT;
                Serial.printf("[Gate IN] Nhan lenh ADMIN: Mo Barie VAO (Plate=%s, Slot=%s)!\n", activePlate, assignedSlot);
            }
        } else {
            if (targetOut) {
                closeExitBarrier();
                exitState = GATE_OUT_IDLE;
                Serial.println(F("[Gate OUT] Nhan lenh ADMIN: Dong Barie RA ngay lap tuc!"));
            } else {
                closeEntryBarrier();
                entryState = GATE_IN_IDLE;
                Serial.println(F("[Gate IN] Nhan lenh ADMIN: Dong Barie VAO ngay lap tuc!"));
            }
        }
        return;
    }

    // 2. Xử lý phản hồi cho Cổng VÀO
    if (entryState == GATE_IN_WAIT_AUTH && seq == seqIn) {
        if (plate && plate[0] != '\0') {
            strncpy(activePlate, plate, sizeof(activePlate) - 1);
            activePlate[sizeof(activePlate) - 1] = '\0';
        }
        if (slot && slot[0] != '\0') {
            strncpy(assignedSlot, slot, sizeof(assignedSlot) - 1);
            assignedSlot[sizeof(assignedSlot) - 1] = '\0';
        } else {
            assignedSlot[0] = '\0';
        }

        if (allowOpen) {
            openEntryBarrier();
            lastEvent = GATE_EVENT_CAR_ENTERING;
            entryState = GATE_IN_OPEN_WAIT;
            Serial.printf("[Gate IN] Backend CHO PHEP VAO (seq=%u, Plate=%s, Slot=%s) -> Mo Barie VAO!\n",
                          seq, activePlate, assignedSlot);
        } else if (isWaitManual) {
            lastEvent = GATE_EVENT_MANUAL_REQUIRED;
            entryState = GATE_IN_WAIT_MANUAL;
            Serial.printf("[Gate IN] Khong doc duoc bien (seq=%u) -> Giu Barie DONG, cho Web Admin!\n", seq);
        } else {
            lastEvent = GATE_EVENT_BLOCKED_FULL;
            entryState = GATE_IN_BLOCKED_FULL;
            Serial.printf("[Gate IN] Backend TU CHOI VAO (seq=%u, Plate=%s) -> Giu Barie VAO dong!\n",
                          seq, activePlate);
        }
        return;
    }

    // 3. Xử lý phản hồi cho Cổng RA
    if (exitState == GATE_OUT_WAIT_AUTH && seq == seqOut) {
        if (plate && plate[0] != '\0') {
            strncpy(activePlate, plate, sizeof(activePlate) - 1);
            activePlate[sizeof(activePlate) - 1] = '\0';
        }
        assignedSlot[0] = '\0';

        if (allowOpen) {
            openExitBarrier();
            lastEvent = GATE_EVENT_CAR_EXITING;
            exitState = GATE_OUT_OPEN_WAIT;
            Serial.printf("[Gate OUT] Backend CHO PHEP RA (seq=%u, Plate=%s) -> Mo Barie RA!\n",
                          seq, activePlate);
        } else if (isWaitManual) {
            lastEvent = GATE_EVENT_MANUAL_REQUIRED;
            exitState = GATE_OUT_WAIT_MANUAL;
            Serial.printf("[Gate OUT] Khong doc duoc bien (seq=%u) -> Giu Barie RA DONG, cho chon xe tren Web!\n", seq);
        } else {
            lastEvent = GATE_EVENT_NO_EXIT_REQUEST;
            exitState = GATE_OUT_BLOCKED;
            Serial.printf("[Gate OUT] Tu choi RA (seq=%u, Plate=%s): Chua co yeu cau ra tu App -> Giu Barie RA dong!\n",
                          seq, activePlate);
        }
        return;
    }

    Serial.printf("[GateController] Bo qua command khong khop: seq=%u (seqIn=%u [%s], seqOut=%u [%s])\n",
                  seq, seqIn, getEntryStateName(), seqOut, getExitStateName());
}

bool GateController::consumePendingAuthRequest(uint32_t& outSeq, const char*& outDirection) {
    if (pendingInAuthPublish) {
        pendingInAuthPublish = false;
        outSeq = seqIn;
        outDirection = "IN";
        return true;
    }
    if (pendingOutAuthPublish) {
        pendingOutAuthPublish = false;
        outSeq = seqOut;
        outDirection = "OUT";
        return true;
    }
    return false;
}

bool GateController::consumeOfflineFallbackEvent(uint32_t& outSeq, bool& outEntering) {
    if (pendingInOfflineFallback) {
        pendingInOfflineFallback = false;
        outSeq = fallbackInSeq;
        outEntering = true;
        return true;
    }
    if (pendingOutOfflineFallback) {
        pendingOutOfflineFallback = false;
        outSeq = fallbackOutSeq;
        outEntering = false;
        return true;
    }
    return false;
}

bool GateController::isBarrierOpen() const {
    return barrierInOpen || barrierOutOpen;
}

bool GateController::isBarrierInOpen() const {
    return barrierInOpen;
}

bool GateController::isBarrierOutOpen() const {
    return barrierOutOpen;
}

GateEvent GateController::getLastEvent() const {
    return lastEvent;
}

GateState GateController::getState() const {
    if (entryState == GATE_IN_OPEN_WAIT) return GATE_STATE_ENTERING_WAIT_IN;
    if (entryState == GATE_IN_PASSING) return GATE_STATE_ENTERING_PASSING;
    if (entryState == GATE_IN_WAIT_AUTH) return GATE_STATE_WAIT_AUTH_IN;
    if (entryState == GATE_IN_WAIT_MANUAL || entryState == GATE_IN_BLOCKED_FULL) return GATE_STATE_BLOCKED_FULL;

    if (exitState == GATE_OUT_OPEN_WAIT) return GATE_STATE_EXITING_WAIT_OUT;
    if (exitState == GATE_OUT_PASSING) return GATE_STATE_EXITING_PASSING;
    if (exitState == GATE_OUT_WAIT_AUTH) return GATE_STATE_WAIT_AUTH_OUT;
    if (exitState == GATE_OUT_WAIT_MANUAL || exitState == GATE_OUT_BLOCKED) return GATE_STATE_BLOCKED_FULL;

    return GATE_STATE_IDLE;
}

GateInState GateController::getEntryState() const {
    return entryState;
}

GateOutState GateController::getExitState() const {
    return exitState;
}

void GateController::clearLastEvent() {
    lastEvent = GATE_EVENT_NONE;
}

bool GateController::carJustEntered() {
    bool res = flagJustEntered;
    flagJustEntered = false;
    return res;
}

bool GateController::carJustExited() {
    bool res = flagJustExited;
    flagJustExited = false;
    return res;
}

void GateController::setCarsInLot(uint8_t count) {
    carsInLot = (count > NUM_SLOTS) ? NUM_SLOTS : count;
}

uint8_t GateController::getCarsInLot() const {
    return carsInLot;
}

uint8_t GateController::getAvailableByGate(uint8_t reservedCount) const {
    uint16_t occupiedAndReserved = static_cast<uint16_t>(carsInLot) + static_cast<uint16_t>(reservedCount);
    if (occupiedAndReserved >= NUM_SLOTS) {
        return 0;
    }
    return static_cast<uint8_t>(NUM_SLOTS - occupiedAndReserved);
}

bool GateController::isLotPhysicallyFull() const {
    return carsInLot >= NUM_SLOTS;
}

bool GateController::isLotFullForWalkIn(uint8_t reservedCount) const {
    return getAvailableByGate(reservedCount) == 0;
}

const char* GateController::getStateName() const {
    switch (getState()) {
        case GATE_STATE_IDLE:             return "IDLE";
        case GATE_STATE_WAIT_AUTH_IN:     return "WAIT_AUTH_IN";
        case GATE_STATE_WAIT_AUTH_OUT:    return "WAIT_AUTH_OUT";
        case GATE_STATE_ENTERING_WAIT_IN: return "ENTERING_WAIT_IN";
        case GATE_STATE_ENTERING_PASSING: return "ENTERING_PASSING";
        case GATE_STATE_EXITING_WAIT_OUT: return "EXITING_WAIT_OUT";
        case GATE_STATE_EXITING_PASSING:  return "EXITING_PASSING";
        case GATE_STATE_BLOCKED_FULL:     return "BLOCKED_FULL";
        default:                          return "UNKNOWN";
    }
}

const char* GateController::getEntryStateName() const {
    switch (entryState) {
        case GATE_IN_IDLE:         return "IDLE";
        case GATE_IN_WAIT_AUTH:    return "WAIT_AUTH";
        case GATE_IN_WAIT_MANUAL:  return "WAIT_MANUAL";
        case GATE_IN_OPEN_WAIT:    return "OPEN_WAIT";
        case GATE_IN_PASSING:      return "PASSING";
        case GATE_IN_BLOCKED_FULL: return "BLOCKED_FULL";
        default:                   return "UNKNOWN";
    }
}

const char* GateController::getExitStateName() const {
    switch (exitState) {
        case GATE_OUT_IDLE:        return "IDLE";
        case GATE_OUT_WAIT_AUTH:   return "WAIT_AUTH";
        case GATE_OUT_WAIT_MANUAL: return "WAIT_MANUAL";
        case GATE_OUT_OPEN_WAIT:   return "OPEN_WAIT";
        case GATE_OUT_PASSING:     return "PASSING";
        case GATE_OUT_BLOCKED:     return "BLOCKED";
        default:                   return "UNKNOWN";
    }
}

const char* GateController::getActivePlate() const {
    return activePlate;
}

const char* GateController::getAssignedSlot() const {
    return assignedSlot;
}

uint32_t GateController::getCurrentSeq() const {
    return nextSeq;
}

void GateController::openEntryBarrier() {
    gateServoIn.write(SERVO_ANGLE_OPEN);
    barrierInOpen = true;
    barrierInOpenTimestamp = millis();
}

void GateController::closeEntryBarrier() {
    gateServoIn.write(SERVO_ANGLE_CLOSED);
    barrierInOpen = false;
}

void GateController::openExitBarrier() {
    gateServoOut.write(SERVO_ANGLE_OPEN);
    barrierOutOpen = true;
    barrierOutOpenTimestamp = millis();
}

void GateController::closeExitBarrier() {
    gateServoOut.write(SERVO_ANGLE_CLOSED);
    barrierOutOpen = false;
}
