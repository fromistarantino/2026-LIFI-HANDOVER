#define LED_TX_PIN  27
#define FPGA_RX_PIN 26
#define TX_BAUD    10000
#define RX_BAUD    9600

void setup() {
    Serial.begin(115200);
    Serial2.begin(TX_BAUD, SERIAL_8N1, -1, LED_TX_PIN, true);
    Serial1.begin(RX_BAUD, SERIAL_8N1, FPGA_RX_PIN, -1);

    Serial.println("===========================================");
    Serial.println("  LiFi 한 글자씩 송수신 표시");
    Serial.println("===========================================");
    Serial.println("보낼 텍스트 입력 후 엔터:");
}

void loop() {
    if (Serial.available()) {
        String s = Serial.readStringUntil('\n');
        s.trim();
        if (s.length() == 0) return;

        Serial.println();
        Serial.print("입력: ");
        Serial.println(s);
        Serial.print(">> ");

        // 수신 버퍼 비우기 (이전 데이터 잔존 방지)
        while (Serial1.available()) Serial1.read();

        // 한 글자씩 송수신
        for (int i = 0; i < s.length(); i++) {
            char tx_char = s[i];

            // 한 글자 송신
            Serial2.write(tx_char);
            Serial2.flush();  // 이 글자가 완전히 보내질 때까지 대기

            // 송신 표시
            Serial.print(tx_char);
            Serial.print("송신");

            // 수신 대기 (최대 200ms)
            unsigned long start = millis();
            while (!Serial1.available() && (millis() - start < 200)) {
                delay(1);
            }

            // 수신 결과 표시
            Serial.print(", ");
            if (Serial1.available()) {
                char rx_char = Serial1.read();
                Serial.print(rx_char);
                Serial.print("수신");
            } else {
                Serial.print("?수신(timeout)");
            }

            if (i < s.length() - 1) {
                Serial.print(", ");
            }
        }

        Serial.println();
        Serial.println("-------------------------------------");
    }
}