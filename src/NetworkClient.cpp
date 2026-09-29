#include "NetworkClient.h"

NetworkClient::NetworkClient()
    : lastTelemetryTime(0) {
}

void NetworkClient::begin() {
#if ENABLE_WIFI
    Serial.printf("[NetworkClient] Connecting to Wi-Fi SSID: %s ...\n", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
#else
    Serial.println(F("[NetworkClient] Wi-Fi disabled in config.h (ENABLE_WIFI = false). Running in local mode."));
#endif
}

void NetworkClient::update(const ParkingMonitor& parking, const GateController& gate) {
#if ENABLE_WIFI
    if (WiFi.status() != WL_CONNECTED) {
        return; // Chưa có kết nối, không block loop()
    }

    unsigned long now = millis();
    if (now - lastTelemetryTime >= TELEMETRY_INTERVAL_MS) {
        lastTelemetryTime = now;
        sendTelemetry(parking, gate);
    }
#else
    (void)parking;
    (void)gate;
#endif
}

bool NetworkClient::isConnected() const {
#if ENABLE_WIFI
    return (WiFi.status() == WL_CONNECTED);
#else
    return false;
#endif
}

void NetworkClient::sendTelemetry(const ParkingMonitor& parking, const GateController& gate) {
#if ENABLE_WIFI
    JsonDocument doc;

    doc["device_id"] = DEVICE_ID;
    doc["timestamp"] = millis() / 1000;

    JsonObject gateObj = doc["gate"].to<JsonObject>();
    gateObj["barrier_open"] = gate.isBarrierOpen();
    gateObj["last_action"] = (gate.getLastEvent() == GATE_EVENT_CAR_ENTERING) ? "ENTER" :
                             (gate.getLastEvent() == GATE_EVENT_CAR_EXITING)  ? "EXIT"  : "NONE";

    JsonObject slotsObj = doc["slots"].to<JsonObject>();
    for (uint8_t i = 0; i < NUM_SLOTS; i++) {
        slotsObj[parking.getSlotName(i)] = parking.isOccupied(i);
    }

    JsonObject summaryObj = doc["summary"].to<JsonObject>();
    summaryObj["total_slots"] = NUM_SLOTS;
    summaryObj["total_occupied"] = parking.getOccupiedCount();
    summaryObj["total_available"] = parking.getTotalAvailable();
    summaryObj["zone_A_available"] = parking.getAvailableA();
    summaryObj["zone_B_available"] = parking.getAvailableB();

    String jsonString;
    serializeJson(doc, jsonString);

    HTTPClient http;
    http.begin(BACKEND_HTTP_URL);
    http.addHeader("Content-Type", "application/json");
    http.setTimeout(HTTP_TIMEOUT_MS);

    int httpResponseCode = http.POST(jsonString);
    if (httpResponseCode > 0) {
        Serial.printf("[NetworkClient] Telemetry sent, HTTP Code: %d\n", httpResponseCode);
    } else {
        Serial.printf("[NetworkClient] HTTP POST failed, error: %s\n", http.errorToString(httpResponseCode).c_str());
    }
    http.end();
#else
    (void)parking;
    (void)gate;
#endif
}
