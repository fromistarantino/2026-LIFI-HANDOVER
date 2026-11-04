#define LED_TX_PIN  27
#define FPGA_RX_PIN 26
#define TX_BAUD    10000
#define RX_BAUD    115200

bool          rx_active   = false;
unsigned long rx_last_ms  = 0;
unsigned long rx_byte_cnt = 0;
unsigned long tx_byte_cnt = 0;
unsigned long tx_start_ms = 0;

const unsigned long RX_TIMEOUT_MS = 300;

// ★ 메모리 버퍼: 받은 바이트를 일단 저장만
#define RX_BUF_SIZE 8192
uint8_t  rx_buf[RX_BUF_SIZE];
size_t   rx_buf_idx = 0;

// ─── Serial1에서 받은 바이트를 메모리에 저장 (출력 X) ───
void readSerial1() {
    while (Serial1.available()) {
        uint8_t c = Serial1.read();
        if (rx_buf_idx < RX_BUF_SIZE) {
            rx_buf[rx_buf_idx++] = c;
        }
        rx_byte_cnt++;
        rx_last_ms = millis();
        rx_active = true;
        
        // 진행 표시 (50바이트마다 점 하나)
        if (rx_byte_cnt % 50 == 0) {
            Serial.print(".");
        }
    }
}

void setup() {
    Serial.begin(115200);
    
    // ★ 양쪽 버퍼 모두 확장
    Serial1.setRxBufferSize(8192);
    
    Serial2.begin(TX_BAUD, SERIAL_8N1, -1, LED_TX_PIN, true);
    Serial1.begin(RX_BAUD, SERIAL_8N1, FPGA_RX_PIN, -1);

    Serial.println();
    Serial.println("LiFi 송수신 (긴 메시지 안정화 v2)");
    Serial.print("광채널: "); Serial.print(TX_BAUD); Serial.print(" bps  ");
    Serial.print("와이어: "); Serial.print(RX_BAUD); Serial.println(" bps");
    Serial.println();
}

void loop() {
    // ─── PC → ESP32 → LED ───
    if (Serial.available()) {
        String s = Serial.readStringUntil('\n');
        s.trim();
        if (s.length() > 0) {
            Serial.println();
            Serial.print("┌─[송신] ");
            Serial.print(s.length());
            Serial.print("바이트  진행: ");
            
            tx_byte_cnt = s.length();
            tx_start_ms = millis();
            rx_byte_cnt = 0;
            rx_buf_idx  = 0;
            rx_active = true;
            rx_last_ms = millis();
            
            // 비블로킹 송신 + 동시 RX 처리
            for (size_t i = 0; i < s.length(); i++) {
                while (Serial2.availableForWrite() < 1) {
                    readSerial1();
                }
                Serial2.write(s[i]);
                readSerial1();
            }
        }
    }

    readSerial1();

    // ─── 수신 타임아웃 시 결과 일괄 출력 ───
    if (rx_active && (millis() - rx_last_ms > RX_TIMEOUT_MS)) {
        unsigned long actual_ms = rx_last_ms - tx_start_ms;
        unsigned long bps = 0;
        unsigned long data_bps = 0;
        if (actual_ms > 0) {
            bps = (rx_byte_cnt * 10UL * 1000UL) / actual_ms;
            data_bps = (rx_byte_cnt * 8UL * 1000UL) / actual_ms;
        }
        
        // 받은 데이터 일괄 출력
        Serial.println();
        Serial.print("├─[수신] ");
        for (size_t i = 0; i < rx_buf_idx; i++) {
            uint8_t c = rx_buf[i];
            if (c >= 0x20 && c <= 0x7E) {
                Serial.write(c);
            } else {
                Serial.print("\\x");
                if (c < 0x10) Serial.print("0");
                Serial.print(c, HEX);
            }
        }
        Serial.println();
        
        // 통계
        Serial.print("└─ 받음: ");
        Serial.print(rx_byte_cnt);
        Serial.print("/");
        Serial.print(tx_byte_cnt);
        Serial.print("바이트  소요: ");
        Serial.print(actual_ms);
        Serial.print("ms  속도: ");
        Serial.print(bps);
        Serial.print("bps (데이터 ");
        Serial.print(data_bps);
        Serial.print("bps)");
        
        if (rx_byte_cnt == tx_byte_cnt) {
            Serial.println("  [OK]");
        } else if (rx_byte_cnt < tx_byte_cnt) {
            Serial.print("  [손실 ");
            Serial.print(tx_byte_cnt - rx_byte_cnt);
            Serial.println("]");
        } else {
            Serial.println("  [초과수신]");
        }
        Serial.println();
        
        rx_active = false;
        rx_byte_cnt = 0;
        rx_buf_idx = 0;
    }
}