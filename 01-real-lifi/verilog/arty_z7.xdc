# Arty Z7-10 핀 제약 (LiFi + ESP32 브리지)

# 시스템 클럭
set_property -dict {PACKAGE_PIN H16 IOSTANDARD LVCMOS33} [get_ports CLK]
create_clock -period 8.000 -name sys_clk [get_ports CLK]

# UART (Pmod JB - ESP32 브리지)
set_property -dict {PACKAGE_PIN W14 IOSTANDARD LVCMOS33} [get_ports UART_RX_FROM_PC]
set_property -dict {PACKAGE_PIN Y14 IOSTANDARD LVCMOS33} [get_ports UART_TX_TO_PC]

# LiFi PHY (Pmod JA)
set_property -dict {PACKAGE_PIN Y18 IOSTANDARD LVCMOS33} [get_ports LIFI_RX]
set_property -dict {PACKAGE_PIN Y19 IOSTANDARD LVCMOS33} [get_ports LIFI_TX]

# 보드 LED
set_property -dict {PACKAGE_PIN R14 IOSTANDARD LVCMOS33} [get_ports {LED[0]}]
set_property -dict {PACKAGE_PIN P14 IOSTANDARD LVCMOS33} [get_ports {LED[1]}]
set_property -dict {PACKAGE_PIN N16 IOSTANDARD LVCMOS33} [get_ports {LED[2]}]
set_property -dict {PACKAGE_PIN M14 IOSTANDARD LVCMOS33} [get_ports {LED[3]}]