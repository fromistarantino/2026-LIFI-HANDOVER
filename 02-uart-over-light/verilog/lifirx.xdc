## Arty Z7-10용 LiFi 수신부 XDC
## Pmod JA 핀 배치 (보드 정면 기준):
##   [Pin6=3.3V] [Pin5=GND] [Pin4] [Pin3] [Pin2] [Pin1]

## ─── 시스템 클럭 (125 MHz) ───
set_property -dict { PACKAGE_PIN H16   IOSTANDARD LVCMOS33 } [get_ports { clk }];
create_clock -add -name sys_clk_pin -period 8.000 -waveform {0 4} [get_ports { clk }];

## ─── 리셋 버튼 (BTN0, active-high) ───
set_property -dict { PACKAGE_PIN D19   IOSTANDARD LVCMOS33 } [get_ports { rst_btn }];

## ─── Pmod JA ───
## Pin 1 (Y18): 74HC14 출력 → FPGA (광신호 수신)
set_property -dict { PACKAGE_PIN Y18   IOSTANDARD LVCMOS33 } [get_ports { light_in }];

## Pin 3 (Y16): FPGA UART TX → ESP32 GPIO 27 (수신 데이터 전달)
set_property -dict { PACKAGE_PIN Y16   IOSTANDARD LVCMOS33 } [get_ports { uart_tx_out }];

## (JA2, JA4 미사용)

## ─── 보드 내장 LED (LD0~LD3) ───
## LD0: 송신 성공 토글 (FPGA→ESP32 UART TX 완료)
## LD1: 수신 성공 토글 (LiFi 광 → FPGA UART RX 완료)
## LD2: 오류 발생 토글 (프레임 오류 또는 FIFO 오버플로우)
## LD3: 시스템 하트비트 (1초 주기)
set_property -dict { PACKAGE_PIN R14   IOSTANDARD LVCMOS33 } [get_ports { led[0] }];
set_property -dict { PACKAGE_PIN P14   IOSTANDARD LVCMOS33 } [get_ports { led[1] }];
set_property -dict { PACKAGE_PIN N16   IOSTANDARD LVCMOS33 } [get_ports { led[2] }];
set_property -dict { PACKAGE_PIN M14   IOSTANDARD LVCMOS33 } [get_ports { led[3] }];