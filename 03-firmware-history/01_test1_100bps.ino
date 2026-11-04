#define LED_TX_PIN 27

void setup() {
    Serial.begin(115200);
    Serial2.begin(100, SERIAL_8N1, -1, LED_TX_PIN, true);
    Serial.println("==================================");
    Serial.println("  LiFi 송신부 준비 완료 (1Kbps)");
    Serial.println("==================================");
}

void loop() {
    if (Serial.available()) {
        String inputData = Serial.readStringUntil('\n');
        inputData.trim();  // \r, 공백 제거
        
        if (inputData.length() == 0) return;  // 빈 입력 무시
        
        // PC에 표시
        Serial.print("전송: \"");
        Serial.print(inputData);
        Serial.print("\" (");
        Serial.print(inputData.length());
        Serial.println(" bytes)");
        
        // 각 문자의 hex 값 출력 (디버깅용)
        Serial.print("HEX: ");
        for (int i = 0; i < inputData.length(); i++) {
            if (inputData[i] < 0x10) Serial.print("0");
            Serial.print(inputData[i], HEX);
            Serial.print(" ");
        }
        Serial.println();
        
        // LED로 전송
        Serial2.print(inputData);
        Serial2.print('\n');
        Serial2.flush();  // 전송 완료 대기
        
        // 예상 소요 시간 계산 (1Kbps 기준)
        unsigned long ms = (inputData.length() + 1) * 10;  // 10비트/문자
        Serial.print("전송 완료 (예상 ");
        Serial.print(ms);
        Serial.println(" ms)");
        Serial.println("----------------------------------");
    }
}