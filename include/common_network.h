#ifndef COMMON_NETWORK_H
#define COMMON_NETWORK_H

// ==========================================
// ĐỊNH DANH THIẾT BỊ TRONG HỆ THỐNG
// ==========================================
#define DEVICE_ID_GATE "ESP32_PARKING_01"
#define DEVICE_ID_CAM "ESP32_CAM_01"

// ==========================================
// CẤU HÌNH WI-FI DÙNG CHUNG (GATE & CAM)
// ==========================================
#define ENABLE_WIFI true // Đổi thành true khi kết nối Wi-Fi thực tế
#define WIFI_SSID "Thanh Tan"
#define WIFI_PASSWORD "Abc123456"

// ==========================================
// ĐỊA CHỈ MÁY TRẠM EDGE SERVER (REPO 1)
// ==========================================
#define EDGE_SERVER_IP "192.168.1.8"
#define MQTT_BROKER_HOST EDGE_SERVER_IP
#define MQTT_BROKER_PORT 1883
#define MQTT_BUFFER_SIZE 1024
#define MQTT_RECONNECT_MS 5000

// Endpoint HTTP nhận ảnh biển số trên Edge Server (FastAPI)
#define EDGE_RECOGNIZE_URL "http://" EDGE_SERVER_IP ":8000/recognize"
#define CAM_SHARED_API_KEY "smart-parking-cam-secret-2026"

// ==========================================
// TÀI KHOẢN XÁC THỰC MQTT BROKER (MOSQUITTO)
// ==========================================
#define MQTT_USER_GATE "esp32_gate_01"
#define MQTT_PASS_GATE "gate_secret_2026"

#define MQTT_USER_CAM "esp32_cam_01"
#define MQTT_PASS_CAM "cam_secret_2026"

// ==========================================
// HỢP ĐỒNG 6 MQTT TOPICS CHUẨN
// ==========================================
#define TOPIC_STATE "parking/ESP32_PARKING_01/state"
#define TOPIC_GATE_REQUEST "parking/ESP32_PARKING_01/gate/request"
#define TOPIC_GATE_COMMAND "parking/ESP32_PARKING_01/gate/command"
#define TOPIC_RESERVED "parking/ESP32_PARKING_01/reserved"
#define TOPIC_GATE_STATUS "parking/ESP32_PARKING_01/status"
#define TOPIC_CAM_STATUS "parking/ESP32_CAM_01/status"

#endif // COMMON_NETWORK_H
