#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include "esp_camera.h"
#include "cam_config.h"

// Client kết nối Wi-Fi & MQTT
static WiFiClient camWifiClient;
static PubSubClient camMqttClient(camWifiClient);

static unsigned long lastMqttReconnectTime = 0;
static bool cameraReady = false;

// Khai báo trước các hàm
static void initCamera();
static void reconnectMqtt();
static void onMqttMessage(char* topic, byte* payload, unsigned int length);
static void captureAndUpload(uint32_t seq, const char* direction);

void setup() {
    // 1. Nháy nhanh đèn Flash LED 100ms ngay khi boot để báo hiệu phần cứng đang chạy
    pinMode(PIN_CAM_FLASH_LED, OUTPUT);
    digitalWrite(PIN_CAM_FLASH_LED, HIGH);
    delay(100);
    digitalWrite(PIN_CAM_FLASH_LED, LOW);

    Serial.begin(115200);
    delay(1000);
    Serial.println();
    Serial.println(F("=================================================="));
    Serial.println(F("   ESP32-CAM (OV2640) - Parking Camera Node      "));
    Serial.println(F("=================================================="));

    // 2. Khởi tạo phần cứng Camera OV2640
    initCamera();

    // 2. Cấu hình Wi-Fi & MQTT
#if ENABLE_WIFI
    Serial.printf("[CAM] Dang ket noi Wi-Fi SSID: %s ...\n", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    camMqttClient.setServer(MQTT_BROKER_HOST, MQTT_BROKER_PORT);
    camMqttClient.setBufferSize(MQTT_BUFFER_SIZE);
    camMqttClient.setCallback(onMqttMessage);
    Serial.printf("[CAM] Target Broker: %s:%d\n", MQTT_BROKER_HOST, MQTT_BROKER_PORT);
#else
    Serial.println(F("[CAM] ENABLE_WIFI = false. Camera dang o che do offline."));
#endif

    Serial.println(F("[CAM] Module san sang hoat dong!"));
}

void loop() {
#if ENABLE_WIFI
    if (WiFi.status() == WL_CONNECTED) {
        if (!camMqttClient.connected()) {
            reconnectMqtt();
        } else {
            camMqttClient.loop();
        }
    }
#endif
    delay(10); // Tránh watchdog reset khi rảnh rỗi
}

static void initCamera() {
    camera_config_t config;
    config.ledc_channel = LEDC_CHANNEL_0;
    config.ledc_timer = LEDC_TIMER_0;
    config.pin_d0 = Y2_GPIO_NUM;
    config.pin_d1 = Y3_GPIO_NUM;
    config.pin_d2 = Y4_GPIO_NUM;
    config.pin_d3 = Y5_GPIO_NUM;
    config.pin_d4 = Y6_GPIO_NUM;
    config.pin_d5 = Y7_GPIO_NUM;
    config.pin_d6 = Y8_GPIO_NUM;
    config.pin_d7 = Y9_GPIO_NUM;
    config.pin_xclk = XCLK_GPIO_NUM;
    config.pin_pclk = PCLK_GPIO_NUM;
    config.pin_vsync = VSYNC_GPIO_NUM;
    config.pin_href = HREF_GPIO_NUM;
    config.pin_sccb_sda = SIOD_GPIO_NUM;
    config.pin_sccb_scl = SIOC_GPIO_NUM;
    config.pin_pwdn = PWDN_GPIO_NUM;
    config.pin_reset = RESET_GPIO_NUM;
    config.xclk_freq_hz = 20000000;
    config.pixel_format = PIXFORMAT_JPEG;

    if (psramFound()) {
        Serial.println(F("[CAM] Tim thay PSRAM. Su dung FRAMESIZE_SVGA (800x600)."));
        config.frame_size = CAM_FRAME_SIZE;
        config.jpeg_quality = CAM_JPEG_QUALITY;
        config.fb_count = CAM_FB_COUNT;
        config.grab_mode = CAMERA_GRAB_LATEST;
    } else {
        Serial.println(F("[CAM] CANH BAO: Khong tim thay PSRAM! Ha xuong FRAMESIZE_VGA (640x480)."));
        config.frame_size = FRAMESIZE_VGA;
        config.jpeg_quality = 14;
        config.fb_count = 1;
        config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
    }

    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK) {
        Serial.printf("[CAM] Loi khoi tao Camera: 0x%x\n", err);
        cameraReady = false;
        return;
    }

    sensor_t* s = esp_camera_sensor_get();
    if (s) {
        // Tối ưu thông số quang học cho nhận diện biển số phản quang
        s->set_brightness(s, 0);     // -2 to 2
        s->set_contrast(s, 1);       // -2 to 2 (tăng nhẹ tương phản biển số)
        s->set_saturation(s, 0);     // -2 to 2
        s->set_gain_ctrl(s, 1);      // Bật AGC
        s->set_exposure_ctrl(s, 1);  // Bật AEC
        s->set_awb_gain(s, 1);       // Bật Auto White Balance
    }

    cameraReady = true;
    Serial.println(F("[CAM] Khoi tao Camera OV2640 THANH CONG."));
}

static void reconnectMqtt() {
#if ENABLE_WIFI
    unsigned long now = millis();
    if (now - lastMqttReconnectTime < MQTT_RECONNECT_MS) {
        return;
    }
    lastMqttReconnectTime = now;

    Serial.printf("[CAM MQTT] Dang ket noi toi Mosquitto (%s:%d)... ", MQTT_BROKER_HOST, MQTT_BROKER_PORT);

    // LWT: Tự động báo "offline" khi ESP32-CAM rớt mạng
    bool connected = camMqttClient.connect(
        DEVICE_ID_CAM,
        MQTT_USER_CAM,
        MQTT_PASS_CAM,
        TOPIC_CAM_STATUS,
        1,
        true,
        "offline"
    );

    if (connected) {
        Serial.println(F("THANH CONG!"));
        camMqttClient.publish(TOPIC_CAM_STATUS, "online", true);

        // Đăng ký nhận yêu cầu chụp từ cổng ESP32 Gate
        camMqttClient.subscribe(TOPIC_GATE_REQUEST, 1);
        Serial.printf("[CAM MQTT] Da subscribe: %s\n", TOPIC_GATE_REQUEST);
    } else {
        Serial.printf("THAT BAI, rc=%d. Thu lai sau 5s.\n", camMqttClient.state());
    }
#endif
}

static void onMqttMessage(char* topic, byte* payload, unsigned int length) {
    if (strcmp(topic, TOPIC_GATE_REQUEST) != 0) {
        return;
    }

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, payload, length);
    if (err) {
        Serial.printf("[CAM RX] Loi parse JSON gate request: %s\n", err.c_str());
        return;
    }

    // Bỏ qua nếu đây là gói tin đồng bộ bù offline (không có xe đang chờ chụp thật)
    bool isOfflineFallback = doc["offlineFallback"] | false;
    if (isOfflineFallback) {
        Serial.println(F("[CAM RX] Bo qua yeu cau do la Offline Fallback dong bo bu."));
        return;
    }

    uint32_t seq = doc["seq"] | 0;
    const char* dir = doc["direction"] | "IN";

    Serial.printf("\n[CAM RX] >>> NHAN LENH CHUP ANH: seq=%u, direction=%s <<<\n", seq, dir);
    captureAndUpload(seq, dir);
}

static void captureAndUpload(uint32_t seq, const char* direction) {
    if (!cameraReady) {
        Serial.println(F("[CAM] Loi: Camera chua san sang!"));
        return;
    }

    unsigned long startMs = millis();

    // 1. Bật đèn Flash LED & chờ cảm biến cân bằng sáng
    digitalWrite(PIN_CAM_FLASH_LED, HIGH);
    delay(CAM_FLASH_WARMUP_MS);

    // 2. Xả 1 frame cũ còn đọng trong FIFO buffer
    camera_fb_t* staleFb = esp_camera_fb_get();
    if (staleFb) {
        esp_camera_fb_return(staleFb);
    }

    // 3. Chụp Frame 1
    camera_fb_t* fb1 = esp_camera_fb_get();
    if (!fb1) {
        Serial.println(F("[CAM] Loi: Khong chup duoc Frame 1!"));
        digitalWrite(PIN_CAM_FLASH_LED, LOW);
        return;
    }

    // Nghỉ nhẹ để chụp Frame 2 (góc/ánh sáng khác biệt đôi chút)
    delay(CAM_FRAME_INTERVAL_MS);

    // 4. Chụp Frame 2
    camera_fb_t* fb2 = esp_camera_fb_get();

    // Tắt đèn Flash ngay sau khi chụp xong để chống nóng
    digitalWrite(PIN_CAM_FLASH_LED, LOW);

    Serial.printf("[CAM] Da chup xong (Frame 1: %u bytes%s%u bytes). Thoi gian chup: %lu ms\n",
                  fb1->len,
                  fb2 ? ", Frame 2: " : "",
                  fb2 ? fb2->len : 0,
                  millis() - startMs);

    // 5. Mở kết nối TCP tới Edge Server FastAPI (:8000)
    WiFiClient httpUploadClient;
    httpUploadClient.setTimeout(4000);

    Serial.printf("[CAM] Ket noi toi Edge Server %s:8000 ... ", EDGE_SERVER_IP);
    if (!httpUploadClient.connect(EDGE_SERVER_IP, 8000)) {
        Serial.println(F("THAT BAI (Khong the mo TCP Socket)!"));
        if (fb1) esp_camera_fb_return(fb1);
        if (fb2) esp_camera_fb_return(fb2);
        return;
    }
    Serial.println(F("OK"));

    // 6. Xây dựng gói tin multipart/form-data
    const char* boundary = "----ESP32CAMBoundary2026";
    char boundaryPrefix[64];
    snprintf(boundaryPrefix, sizeof(boundaryPrefix), "--%s\r\n", boundary);

    // Phần header metadata
    String partDeviceId = String(boundaryPrefix) +
        "Content-Disposition: form-data; name=\"device_id\"\r\n\r\n" +
        DEVICE_ID_CAM + "\r\n";

    String partSeq = String(boundaryPrefix) +
        "Content-Disposition: form-data; name=\"seq\"\r\n\r\n" +
        String(seq) + "\r\n";

    String partDir = String(boundaryPrefix) +
        "Content-Disposition: form-data; name=\"direction\"\r\n\r\n" +
        String(direction) + "\r\n";

    // Phần file frame_0.jpg
    String file1Head = String(boundaryPrefix) +
        "Content-Disposition: form-data; name=\"images\"; filename=\"frame_0.jpg\"\r\n" +
        "Content-Type: image/jpeg\r\n\r\n";
    String file1Tail = "\r\n";

    // Phần file frame_1.jpg (nếu có)
    String file2Head = "";
    String file2Tail = "";
    if (fb2) {
        file2Head = String(boundaryPrefix) +
            "Content-Disposition: form-data; name=\"images\"; filename=\"frame_1.jpg\"\r\n" +
            "Content-Type: image/jpeg\r\n\r\n";
        file2Tail = "\r\n";
    }

    String endBoundary = String("--") + boundary + "--\r\n";

    // Tính tổng Content-Length chuẩn xác
    size_t totalLen = partDeviceId.length() +
                      partSeq.length() +
                      partDir.length() +
                      file1Head.length() + fb1->len + file1Tail.length() +
                      (fb2 ? (file2Head.length() + fb2->len + file2Tail.length()) : 0) +
                      endBoundary.length();

    // 7. Gửi HTTP Request Headers
    httpUploadClient.printf("POST /recognize HTTP/1.1\r\n");
    httpUploadClient.printf("Host: %s:8000\r\n", EDGE_SERVER_IP);
    httpUploadClient.printf("User-Agent: ESP32-CAM/%s\r\n", DEVICE_ID_CAM);
    httpUploadClient.printf("X-API-Key: %s\r\n", CAM_SHARED_API_KEY);
    httpUploadClient.printf("Content-Type: multipart/form-data; boundary=%s\r\n", boundary);
    httpUploadClient.printf("Content-Length: %u\r\n", totalLen);
    httpUploadClient.printf("Connection: close\r\n\r\n");

    // 8. Stream dữ liệu multipart trực tiếp qua TCP (zero copy)
    httpUploadClient.print(partDeviceId);
    httpUploadClient.print(partSeq);
    httpUploadClient.print(partDir);

    // Frame 1
    httpUploadClient.print(file1Head);
    httpUploadClient.write(fb1->buf, fb1->len);
    httpUploadClient.print(file1Tail);

    // Frame 2
    if (fb2) {
        httpUploadClient.print(file2Head);
        httpUploadClient.write(fb2->buf, fb2->len);
        httpUploadClient.print(file2Tail);
    }

    httpUploadClient.print(endBoundary);
    httpUploadClient.flush();

    // 9. Đọc dòng HTTP status phản hồi từ Edge Server
    unsigned long waitStart = millis();
    bool gotResponse = false;
    while (httpUploadClient.connected() && (millis() - waitStart < 3000)) {
        if (httpUploadClient.available()) {
            String statusLine = httpUploadClient.readStringUntil('\n');
            Serial.printf("[CAM UPLOAD] Phan hoi: %s\n", statusLine.c_str());
            gotResponse = true;
            break;
        }
        delay(10);
    }

    if (!gotResponse) {
        Serial.println(F("[CAM UPLOAD] Timeout cho phan hoi tu Edge Server."));
    }

    httpUploadClient.stop();

    // 10. Giải phóng bộ đệm ảnh OV2640 an toàn
    if (fb1) esp_camera_fb_return(fb1);
    if (fb2) esp_camera_fb_return(fb2);

    Serial.printf("[CAM UPLOAD] Hoan tat toan bo luong upload trong %lu ms.\n", millis() - startMs);
}
