#define LED_TX_PIN  26
#define FPGA_RX_PIN 27
#define TX_BAUD    10000
#define RX_BAUD    115200

#define RX_BUF_SIZE 1024
uint8_t  rx_buf[RX_BUF_SIZE];
size_t   rx_buf_idx = 0;
bool          rx_active   = false;
unsigned long rx_last_ms  = 0;  
unsigned long rx_byte_cnt = 0;
unsigned long tx_byte_cnt = 0;
unsigned long tx_start_ms = 0;
const unsigned long RX_TIMEOUT_MS = 100;

// 송신 완료 후 마지막 비트 drain 시간
const unsigned long TX_DRAIN_MS = 200;

// ★ 추가: LiFi 끊김/복구 즉시 감지용
const unsigned long LIFI_SILENT_THRESHOLD_MS = 50;
const uint8_t LIFI_LOST_MAGIC[3] = {0xFF, 0xFE, 0xFD};  // LiFi 끊김 알림
const uint8_t LIFI_BACK_MAGIC[3] = {0xFF, 0xFE, 0xFC};  // LiFi 복구 알림
bool lifi_alive = true;
unsigned long last_lifi_check = 0;

bool   file_mode = false;
size_t file_bytes_to_send = 0;
size_t file_bytes_received = 0;
unsigned long file_last_rx_ms = 0;

void setup() {
    Serial.begin(115200);
    Serial1.setRxBufferSize(8192);
    Serial2.begin(TX_BAUD, SERIAL_8N1, -1, LED_TX_PIN, true);
    Serial1.begin(RX_BAUD, SERIAL_8N1, FPGA_RX_PIN, -1);
    Serial.println();
    Serial.println("LiFi 송수신 (파일 전송 + 즉시 끊김 감지)");
    Serial.println("일반 텍스트: 입력 + 엔터");
    Serial.println("파일 전송:   :FILE <크기>");
}

void forwardSerial1Raw() {
    while (Serial1.available()) {
        Serial.write(Serial1.read());
        file_bytes_received++;
        file_last_rx_ms = millis();
    }
}

void readSerial1Buffered() {
    while (Serial1.available()) {
        uint8_t c = Serial1.read();
        if (rx_buf_idx < RX_BUF_SIZE) rx_buf[rx_buf_idx++] = c;
        rx_byte_cnt++;
        rx_last_ms = millis();
        rx_active = true;
        if (rx_byte_cnt % 50 == 0) Serial.print(".");
    }
}

void loop() {
    if (file_mode) {
        // ─── 파일 모드: 투명 포워딩 ───
        
        // PC → ESP32 → LED (한 바이트씩 보내며 RX도 처리)
        while (Serial.available() && file_bytes_to_send > 0) {
            while (Serial2.availableForWrite() < 1) {
                forwardSerial1Raw();
            }
            Serial2.write(Serial.read());
            file_bytes_to_send--;
            forwardSerial1Raw();
        }
        
        // 송신이 막 끝났을 때 LED 비트 drain 보장
        if (file_bytes_to_send == 0) {
            Serial2.flush();
        }
        
        forwardSerial1Raw();
        
        // ★★★ 추가: LiFi 끊김/복구 즉시 감지 ★★★
        // 10ms마다 체크하여 50ms 이상 신호 없으면 즉시 PC에 알림
        // file_last_rx_ms > 0 이어야 (한 번이라도 데이터 받았어야) 체크함
        unsigned long now = millis();
        if (now - last_lifi_check > 10) {
            last_lifi_check = now;
            
            if (file_last_rx_ms > 0) {
                bool now_alive = (now - file_last_rx_ms < LIFI_SILENT_THRESHOLD_MS);
                
                if (!now_alive && lifi_alive) {
                    // LiFi 끊김 감지 → PC에 즉시 알림 (3바이트 magic)
                    Serial.write(LIFI_LOST_MAGIC, 3);
                    lifi_alive = false;
                } else if (now_alive && !lifi_alive) {
                    // LiFi 복구 → PC에 알림 (3바이트 magic)
                    Serial.write(LIFI_BACK_MAGIC, 3);
                    lifi_alive = true;
                }
            }
        }
        // ★★★ 추가 끝 ★★★
        
        // 송신 완료 + 일정 시간 수신 없으면 모드 종료
        if (file_bytes_to_send == 0 && 
            file_last_rx_ms > 0 &&
            millis() - file_last_rx_ms > 500) {
            file_mode = false;
            Serial.println();
            Serial.print("[FILE_END] received=");
            Serial.println(file_bytes_received);
            lifi_alive = true;  // ★ 추가: 다음 file_mode를 위해 리셋
        }
        
    } else {
        // ─── 일반 모드 ───
        if (Serial.available()) {
            String s = Serial.readStringUntil('\n');
            s.trim();
            
            // 파일 모드 명령
            if (s.startsWith(":FILE ")) {
                size_t size = s.substring(6).toInt();
                if (size > 0 && size < 100000) {
                    file_mode = true;
                    file_bytes_to_send = size;
                    file_bytes_received = 0;
                    file_last_rx_ms = 0;
                    lifi_alive = true;          // ★ 추가: 시작은 alive 가정
                    last_lifi_check = millis(); // ★ 추가
                    Serial.print("[FILE_START] size=");
                    Serial.println(size);
                    return;
                }
            }
            
            if (s.length() == 0) return;
            
            // 일반 텍스트 모드 (기존 코드 그대로)
            Serial.println();
            Serial.print("┌─[송신] ");
            Serial.print(s.length());
            Serial.print("바이트  진행: ");
            
            tx_byte_cnt = s.length();
            tx_start_ms = millis();
            rx_byte_cnt = 0;
            rx_buf_idx = 0;
            rx_active = true;
            rx_last_ms = millis();
            
            for (size_t i = 0; i < s.length(); i++) {
                while (Serial2.availableForWrite() < 1) {
                    readSerial1Buffered();
                }
                Serial2.write(s[i]);
                readSerial1Buffered();
            }
            
            // 송신 버퍼의 마지막 비트까지 LED로 다 송신될 때까지 대기
            Serial2.flush();
            
            // FPGA → ESP32 UART RX 마지막 데이터 받기 위해 추가 대기
            unsigned long drain_start = millis();
            while (millis() - drain_start < TX_DRAIN_MS) {
                readSerial1Buffered();
                delay(1);
            }
        }
        
        readSerial1Buffered();
        
        if (rx_active && (millis() - rx_last_ms > RX_TIMEOUT_MS)) {
            unsigned long actual_ms = rx_last_ms - tx_start_ms;
            unsigned long bps = actual_ms > 0 ? (rx_byte_cnt * 10UL * 1000UL) / actual_ms : 0;
            
            Serial.println();
            Serial.print("├─[수신] ");
            for (size_t i = 0; i < rx_buf_idx; i++) {
                uint8_t c = rx_buf[i];
                if (c >= 0x20 && c <= 0x7E) Serial.write(c);
                else {
                    Serial.print("\\x");
                    if (c < 0x10) Serial.print("0");
                    Serial.print(c, HEX);
                }
            }
            Serial.println();
            Serial.print("└─ 받음: ");
            Serial.print(rx_byte_cnt);
            Serial.print("/");
            Serial.print(tx_byte_cnt);
            Serial.print("바이트  ");
            Serial.print(actual_ms);
            Serial.print("ms  ");
            Serial.print(bps);
            Serial.print("bps  ");
            Serial.println(rx_byte_cnt == tx_byte_cnt ? "[OK]" : "[손실]");
            Serial.println();
            
            rx_active = false;
            rx_byte_cnt = 0;
            rx_buf_idx = 0;
        }
    }
}