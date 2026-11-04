# 하드웨어 결선 및 핀맵

아래 핀 배치는 **XDC 제약 파일과 펌웨어 소스에서 직접 확인한 값**입니다. 회로도(KiCad)의 네트 라벨 일부가 실제 결선과 어긋나 있으므로, 결선은 이 문서를 기준으로 하십시오.

---

## 1. Real LiFi 방식 (최종)

### 전체 결선

```
  PC ──USB──┐
            │
      ┌─────▼──────────────┐
      │   ESP32-WROOM-32   │   순수 USB-UART 브리지 (광 채널 무관)
      │  GPIO33 ─────────────────► FPGA JB pin 1 (W14)   115,200 bps
      │  GPIO32 ◄───────────────── FPGA JB pin 2 (Y14)
      │  GND    ──────────────────  공통 GND
      └────────────────────┘

      ┌────────────────────────────────┐
      │   Arty Z7-10  (PL 전용)         │
      │  JA pin 2 (Y19) ─ LIFI_TX ──────► [R1 100Ω] ─ MOSFET Gate
      │  JA pin 1 (Y18) ─ LIFI_RX ◄───── 74HC14 출력
      │  JA pin 6 ─ 3.3V ───────────────► MCP6022 VDD, 74HC14 VCC
      │  JA pin 5 ─ GND  ───────────────► 공통 GND
      └────────────────────────────────┘
```

**ESP32가 남아 있는 이유**: Arty Z7의 내장 USB-UART는 Zynq의 PS(프로세서) 측에 연결되어 있어 PL 전용 설계에서는 직접 사용할 수 없습니다. ESP32를 Pmod JB에 붙여 우회했습니다.

### FPGA 핀맵 (`01-real-lifi/verilog/arty_z7.xdc`)

| 신호 | PACKAGE_PIN | Pmod 위치 | 방향 | 연결 대상 |
|---|---|---|---|---|
| `CLK` | H16 | 온보드 | In | 125 MHz 발진기 |
| `LIFI_TX` | **Y19** | **JA pin 2** | Out | R1 100 Ω → MOSFET 게이트 |
| `LIFI_RX` | **Y18** | **JA pin 1** | In | 74HC14 출력 |
| `UART_RX_FROM_PC` | **W14** | **JB pin 1** | In | ESP32 GPIO33 |
| `UART_TX_TO_PC` | **Y14** | **JB pin 2** | Out | ESP32 GPIO32 |
| `LED[0]` | R14 | 보드 LD0 | Out | LiFi 송신 중 |
| `LED[1]` | P14 | 보드 LD1 | Out | 광 수신 SYNC 검출 |
| `LED[2]` | N16 | 보드 LD2 | Out | CRC-16 통과 |
| `LED[3]` | M14 | 보드 LD3 | Out | 하트비트 (1초 주기) |

Pmod 핀 배열 (보드 정면, 상단 행)
```
JA : [6=3.3V][5=GND][4=Y17][3=Y16][2=Y19][1=Y18]
JB : [6=3.3V][5=GND][4    ][3    ][2=Y14][1=W14]
```

**리셋 버튼이 없습니다.** `lifi_top.v`가 4비트 파워온 카운터(15클럭)로 자동 리셋합니다. 리셋하려면 비트스트림을 다시 프로그램하거나 전원을 재인가해야 합니다.

### ESP32 핀맵 (`01-real-lifi/esp32/lifi_tx3/lifi_tx3.ino`)

| GPIO | 방향 | 연결 |
|---|---|---|
| GPIO33 | Out (Serial2 TX) | FPGA JB pin 1 (W14) |
| GPIO32 | In (Serial2 RX) | FPGA JB pin 2 (Y14) |
| GND | — | FPGA GND + 브레드보드 GND |

---

## 2. UART over Light 방식 (초기)

### FPGA 핀맵 (`02-uart-over-light/verilog/lifirx.xdc`)

| 신호 | PACKAGE_PIN | Pmod 위치 | 방향 | 연결 대상 |
|---|---|---|---|---|
| `clk` | H16 | 온보드 | In | 125 MHz |
| `rst_btn` | **D19** | BTN0 | In | 리셋 (active-high) |
| `light_in` | **Y18** | **JA pin 1** | In | 74HC14 출력 |
| `uart_tx_out` | **Y16** | **JA pin 3** | Out | ESP32 GPIO27 |
| `led[0:3]` | R14/P14/N16/M14 | LD0~LD3 | Out | TX 완료 / RX 완료 / 오류 / 하트비트 |

### ESP32 핀맵 (`02-uart-over-light/esp32/uartlifi1_esp32/uartlifi1_esp32.ino`)

| GPIO | 방향 | 연결 | 속도 |
|---|---|---|---|
| **GPIO26** | Out (Serial2 TX) | R1 100 Ω → MOSFET 게이트 | 10,000 bps (반전) |
| **GPIO27** | In (Serial1 RX) | FPGA Y16 (JA pin 3) | 115,200 bps |

`Serial2.begin(10000, SERIAL_8N1, -1, 26, true)` 의 마지막 인자 `invert=true`가 중요합니다. UART는 유휴 상태가 HIGH인데, 반전을 걸어야 아무것도 보내지 않을 때 LED가 꺼집니다.

### 두 방식 전환 시 작업

1. MOSFET 게이트 점퍼선을 `ESP32 GPIO26` ↔ `FPGA JA pin 2 (Y19)` 로 이동
2. `FPGA Y16 → ESP32 GPIO27` 선 제거 / 추가
3. `FPGA JB1·JB2 ↔ ESP32 GPIO33·GPIO32` 2선 연결 / 제거

---

## 3. 아날로그 회로

### 송신부

```
        +5V
         │
      [R3 150Ω]
         │
       [LED]
         │ Drain
    ┌────┴─────┐
    │  N-ch    │◄──[R1 100Ω]── FPGA JA2 (또는 ESP32 GPIO26)
    │ MOSFET   │       │
    │(로직레벨) │   [R2 1kΩ]
    └────┬─────┘       │
       Source ─────────┴─── GND
```

- 비트 1 → 게이트 HIGH → MOSFET ON → LED 점등
- MOSFET이 LED의 **저측(low-side)을 스위칭**하므로 5 V가 FPGA·ESP32 I/O에 올라가지 않습니다
- 게이트 구동 전압이 3.3 V이므로 **로직레벨 MOSFET이 필수**입니다
- 전류 = (5 − V_f) / 150 Ω ≈ 20 mA (적색 LED 기준)

### 수신부

```
                 ┌──[R4 1MΩ]───┐
                 ├──[C_f      ]┤
  Photodiode ────┤(−) MCP6022  ├──► V_out ──[74HC14]──► FPGA JA1
   캐소드         │   (U3A)     │
                 │             │
  Photodiode ─┐  │             │
   애노드      │  │             │
              GND┤(+)          │
                 └─────────────┘
```

- **포토다이오드 캐소드가 op-amp 반전 입력(2번 핀)**, 애노드가 GND
- **비반전 입력(3번 핀)은 GND** — 가상 접지를 따로 만들지 않습니다
- 무광 시 출력 ≈ 0 V, 빛이 들어오면 출력이 **상승**합니다
- `V_out = I_pd × R_4`
- 74HC14는 **반전** 소자이므로 LED ON 시 FPGA 입력은 LOW가 됩니다. 논리 반전은 `lifi_top.v`의 입력 동기화단에서 보정합니다

```verilog
assign lifi_rx_clean = ~lifi_rx_sync[2];   // 74HC14 반전 보정
```

이 보정 때문에 **포토다이오드 방향이 반대면 프리앰블 `0xAAAA7E` 검출이 영원히 실패**합니다.

### 부품표

| 구분 | 부품 | 값 | 비고 |
|---|---|---|---|
| 송신 | MOSFET Q1 | N-ch 로직레벨 | 3.3 V 게이트 구동 |
| | R1 게이트 직렬 | 100 Ω | 링잉 억제 |
| | R2 게이트 풀다운 | 1 kΩ | 부팅 중 게이트 플로팅 방지 |
| | R3 LED 직렬 | 150 Ω | +5 V 기준 |
| 수신 | 포토다이오드 D2 | PIN형 | 캐소드 → op-amp (−) |
| | Op-amp U3A | MCP6022 | 듀얼, GBW 10 MHz, 레일투레일 |
| | R4 피드백 | 1 MΩ | 트랜스임피던스 |
| | C_f 피드백 | 10 pF 권장 | f_c ≈ 15.9 kHz |
| | 슈미트 U2A | 74HC14 | 반전 소자 |

### 전원

- MCP6022 V_DD, 74HC14 V_CC → **Pmod JA pin 6 (3.3 V)**
- LED용 +5 V → ESP32 개발보드의 `5V`/`VIN` 핀 또는 별도 5 V 공급
- **모든 GND를 한 점에 묶을 것** — ESP32 GND, Arty Z7 GND, 브레드보드 GND, 5 V 전원 GND
- 각 IC 전원 핀 옆에 100 nF 세라믹, LED 전원 노드에 10 µF 권장

---

## 4. 알려진 이슈

| 항목 | 내용 |
|---|---|
| **C_f 누락** | KiCad 회로도에 피드백 커패시터가 그려져 있지 않습니다. 실물에는 장착되어 있으나 값이 미확인 상태입니다. 100 pF 이상이면 대역이 1.6 kHz 이하로 떨어져 10 kbps 전송이 불가능합니다. |
| **R2 = 1 kΩ** | R1 100 Ω과 분압되어 게이트 전압이 3.3 V → 약 3.0 V로 떨어집니다. 10 kΩ으로 교체하면 3.27 V가 걸리고 소모 전류도 줄어듭니다. |
| **회로도 네트 라벨** | 회로도에서 송신부와 수신부 모두 `JA2(FPGA)`로 표기되어 있으나, 실제로는 수신이 JA1, 송신이 JA2입니다. XDC가 정확합니다. |
| **저항 값 불일치** | 위 부품표는 KiCad 회로도 기준(R2 = 1 kΩ, R3 = 150 Ω)입니다. 그러나 초기 펌웨어 `03-firmware-history/02_test2_1kbps.ino`의 회로 주석은 **풀다운 10 kΩ, LED 직렬 100 Ω**으로 적혀 있습니다. 실물 측정으로 확정이 필요합니다. |
| **오버샘플러 글리치 내성** | `oversampler.v`는 천이가 검출되면 조건 없이 위상을 리셋하고 비트 중앙 1샘플로 판정합니다. 다수결이 없어 잡음 글리치 하나가 위상을 밀 수 있습니다. 3샘플 다수결 + 위상 리셋 구간 제한이 개선 방향입니다. |
