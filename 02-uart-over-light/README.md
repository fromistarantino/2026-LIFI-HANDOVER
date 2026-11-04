# 02 · UART over Light (초기 구현)

ESP32의 하드웨어 UART 출력을 LED 게이트에 직접 연결한 방식입니다. 광 채널이 독자 프로토콜을 갖지 않고, **시리얼 케이블 한 가닥을 빛으로 대체**한 구조입니다.

## 구성

```
PC ──USB──► ESP32 ──GPIO26 UART 10,000 bps(반전)──► MOSFET ──► LED
                                                                │ 광
                                                                ▼
                                        FPGA ◄── 74HC14 ◄── TIA ◄── PD
                                          │
PC ◄──USB── ESP32 ◄──GPIO27, 115,200 전선──┘
```

**비대칭 구조**입니다. 하행(PC→FPGA)만 광이고, 상행(FPGA→PC)은 전선입니다. FPGA는 광으로 받은 UART를 디코딩해 전선으로 되돌려줍니다.

## 광 채널 파형

```
     START   D0 D1 D2 D3 D4 D5 D6 D7   STOP
LED:  ON    ─── 데이터 8비트 (반전) ───   OFF
```

바이트당 10비트(START 1 + 데이터 8 + STOP 1)이므로 회선 속도 10,000 bps에서 **실효 데이터율은 8,000 bps**입니다.

`Serial2.begin(TX_BAUD, SERIAL_8N1, -1, LED_TX_PIN, true)` 의 `invert=true`가 핵심입니다. UART는 유휴가 HIGH인데 반전을 걸어야 대기 중 LED가 꺼집니다.

## FPGA (`lifirx4.v`, 단일 모듈 198줄)

- UART 수신 4상태: `IDLE → START(중앙 재확인) → DATA(8비트 LSB 우선) → STOP(검증)`
- 125 MHz ÷ 10,000 = 12,500 클럭/비트, **오버샘플링 없이** 비트 중앙에서 1회 샘플
- **CRC 없음** — 무결성은 전적으로 Python의 CRC-8이 담당
- 1단 FIFO + 오버플로우 검출
- 수신 바이트를 115,200 bps UART로 ESP32에 재전송

보드 LED: `LD0` TX 완료 토글 / `LD1` RX 완료 토글 / `LD2` 프레임 오류·오버플로우 / `LD3` 하트비트

## 응용 계층 청크 (37 B)

```
[SYNC 0xAA][POS 4B ⊕0xA5][LEN 1B ⊕0xA5][DATA 30B][CRC-8 ⊕0xA5]
```

- CRC-8 다항식 `0x07`, 계산 범위는 헤더 + 데이터
- 헤더를 `0xA5`와 XOR — 연속된 `0x00`이 LED를 장시간 점등시켜 수신기를 포화시키는 것을 방지
- 파일 전체는 MD5로 검증

## 끊김 즉시 감지

ESP32가 **10 ms 주기로 광 링크를 감시**하다가 50 ms 이상 무신호면 PC에 매직 바이트를 즉시 송신합니다.

| 이름 | 값 |
|---|---|
| `LIFI_LOST` | `0xFF 0xFE 0xFD` |
| `LIFI_BACK` | `0xFF 0xFE 0xFC` |

Python은 수신 스트림에서 이 3바이트를 추출해 즉시 상태를 전이시킵니다. 감지에 가장 가까운 계층에서 판단하므로 PC 타임아웃보다 훨씬 빠릅니다.

## 5중 Ping-Pong 방지

| # | 메커니즘 | 값 | 목적 |
|---|---|---|---|
| 1 | `HANDOVER_COOLDOWN_MS` | 200 ms | 전환 직후 재전환 금지 (끊김 방향만) |
| 2 | `RECOVERY_STABLE_MS` | 200 ms | 복구 지속 확인 |
| 3 | `LIFI_PRIMARY_STABILIZE_MS` | 1,000 ms | 복귀 직후 과민 재전환 방지 |
| 4 | `pending_chunks.clear()` | 복귀 시 | 중복 수신 차단 |
| 5 | `send_pos` 재동기 | 복귀 시 첫 미수신 위치로 | 구간 누락 방지 |

**끊김은 신중하게(쿨다운 적용), 복귀는 즉시(쿨다운 없음)** 라는 비대칭 설계입니다.

추가 파라미터: `LIFI_TIMEOUT_MS` 300, `DEGRADE_TTT_MS` 50, 결정 엔진 주기 20 ms.

## 실행

```bash
cd python
python http_server.py        # 터미널 1
python handover1.py FILE     # 터미널 2
```

## 한계

프리앰블·SFD가 없어 프레임 시작을 알 수 없고, PHY 계층 오류 검출이 없으며, 패킷 경계 개념이 존재하지 않습니다. IEEE 802.15.7과 대조하면 대응되는 항목이 거의 없습니다. 이것이 [01 · Real LiFi](../01-real-lifi/)로 재설계한 이유입니다.
