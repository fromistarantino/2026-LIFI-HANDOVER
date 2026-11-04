`timescale 1ns / 1ps

module lifi_top (
    input  wire       clk,
    input  wire       rst_btn,
    input  wire       light_in,
    output wire       uart_tx_out,
    output wire [3:0] led
);

    wire rst_n = ~rst_btn;

    localparam CLK_FREQ    = 125_000_000;
    localparam RX_BAUD     = 10000;
    localparam TX_BAUD     = 115200;
    localparam RX_CLKS_BIT = CLK_FREQ / RX_BAUD;  
    localparam TX_CLKS_BIT = CLK_FREQ / TX_BAUD;   

    // ─── 하트비트 (LD3 표시용) ───
    reg hb = 1'b0;
    reg [26:0] hb_cnt = 0;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin hb_cnt <= 0; hb <= 1'b0; end
        else begin
            if (hb_cnt == (CLK_FREQ/2) - 1) begin
                hb_cnt <= 0; hb <= ~hb;
            end else hb_cnt <= hb_cnt + 1;
        end
    end

    // ─── 입력 동기화 (메타스테이블 방지) ───
    reg sync1 = 1'b1, sync2 = 1'b1;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin sync1 <= 1'b1; sync2 <= 1'b1; end
        else begin sync1 <= light_in; sync2 <= sync1; end
    end

    // ─── UART RX (10000bps, 8N1, STOP 검증) ───
    reg [2:0]  rx_state = 0;
    reg [20:0] rx_cnt = 0;
    reg [2:0]  rx_bit_idx = 0;
    reg [7:0]  rx_buf = 0;
    reg [7:0]  rx_data = 0;
    reg        rx_done = 1'b0;       // 수신 성공 펄스
    reg        rx_frame_err = 1'b0;  // 수신 오류 펄스

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rx_state <= 0; rx_cnt <= 0; rx_bit_idx <= 0;
            rx_data <= 0; rx_done <= 1'b0; rx_frame_err <= 1'b0;
        end else begin
            rx_done <= 1'b0;
            rx_frame_err <= 1'b0;
            case (rx_state)
                0: begin
                    rx_cnt <= 0; rx_bit_idx <= 0;
                    if (sync2 == 1'b0) rx_state <= 1;
                end
                1: begin
                    if (rx_cnt == RX_CLKS_BIT/2) begin
                        if (sync2 == 1'b0) begin
                            rx_cnt <= 0; rx_state <= 2;
                        end else rx_state <= 0;
                    end else rx_cnt <= rx_cnt + 1;
                end
                2: begin
                    if (rx_cnt == RX_CLKS_BIT - 1) begin
                        rx_cnt <= 0;
                        rx_buf[rx_bit_idx] <= sync2;
                        if (rx_bit_idx == 7) begin
                            rx_bit_idx <= 0; rx_state <= 3;
                        end else rx_bit_idx <= rx_bit_idx + 1;
                    end else rx_cnt <= rx_cnt + 1;
                end
                3: begin
                    if (rx_cnt == RX_CLKS_BIT - 1) begin
                        if (sync2 == 1'b1) begin
                            rx_data <= rx_buf;
                            rx_done <= 1'b1;
                        end else begin
                            rx_frame_err <= 1'b1;
                        end
                        rx_state <= 0;
                    end else rx_cnt <= rx_cnt + 1;
                end
                default: rx_state <= 0;
            endcase
        end
    end

    // ─── FIFO 버퍼 (1단계 깊이) ───
    reg [7:0] fifo_data = 0;
    reg       fifo_valid = 1'b0;
    reg       fifo_overflow = 1'b0;   // 오버플로우 펄스

    wire tx_load_req = (tx_state == 0) && fifo_valid;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            fifo_data <= 0;
            fifo_valid <= 1'b0;
            fifo_overflow <= 1'b0;
        end else begin
            fifo_overflow <= 1'b0;
            if (rx_done) begin
                if (!fifo_valid) begin
                    fifo_data <= rx_data;
                    fifo_valid <= 1'b1;
                end else if (tx_load_req) begin
                    fifo_data <= rx_data;
                end else begin
                    fifo_overflow <= 1'b1;
                end
            end else if (tx_load_req) begin
                fifo_valid <= 1'b0;
            end
        end
    end

    // ─── UART TX (115200bps) - FIFO에서 읽어서 송신 ───
    reg [2:0]  tx_state = 0;
    reg [13:0] tx_cnt = 0;
    reg [2:0]  tx_bit_idx = 0;
    reg [7:0]  tx_byte = 0;
    reg        tx_pin = 1'b1;
    reg        tx_done = 1'b0;        // ★ 추가: 송신 성공 펄스

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            tx_state <= 0; tx_cnt <= 0; tx_bit_idx <= 0;
            tx_byte <= 0; tx_pin <= 1'b1;
            tx_done <= 1'b0;
        end else begin
            tx_done <= 1'b0;  // 매 사이클 기본값 0 (1 사이클 펄스)
            case (tx_state)
                0: begin
                    tx_pin <= 1'b1;
                    if (fifo_valid) begin
                        tx_byte <= fifo_data;
                        tx_cnt <= 0;
                        tx_state <= 1;
                    end
                end
                1: begin
                    tx_pin <= 1'b0;
                    if (tx_cnt == TX_CLKS_BIT - 1) begin
                        tx_cnt <= 0;
                        tx_bit_idx <= 0;
                        tx_state <= 2;
                    end else tx_cnt <= tx_cnt + 1;
                end
                2: begin
                    tx_pin <= tx_byte[tx_bit_idx];
                    if (tx_cnt == TX_CLKS_BIT - 1) begin
                        tx_cnt <= 0;
                        if (tx_bit_idx == 7) tx_state <= 3;
                        else tx_bit_idx <= tx_bit_idx + 1;
                    end else tx_cnt <= tx_cnt + 1;
                end
                3: begin
                    tx_pin <= 1'b1;
                    if (tx_cnt == TX_CLKS_BIT - 1) begin
                        tx_cnt <= 0; 
                        tx_state <= 0;
                        tx_done <= 1'b1;  // ★ 송신 완료 펄스!
                    end else tx_cnt <= tx_cnt + 1;
                end
                default: tx_state <= 0;
            endcase
        end
    end
    assign uart_tx_out = tx_pin;

    // ─── LED 표시: 동작 상태 ───
    // LD0: 송신 성공 토글 (FPGA → ESP32 UART TX 완료마다 토글)
    // LD1: 수신 성공 토글 (LiFi 광 → FPGA UART RX 완료마다 토글)
    // LD2: 오류 발생 토글 (프레임 오류 또는 FIFO 오버플로우)
    // LD3: 시스템 하트비트 (1초 주기)
    reg led0_toggle = 1'b0;
    reg led1_toggle = 1'b0;
    reg led2_toggle = 1'b0;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            led0_toggle <= 0;
            led1_toggle <= 0;
            led2_toggle <= 0;
        end else begin
            if (tx_done)       led0_toggle <= ~led0_toggle;
            if (rx_done)       led1_toggle <= ~led1_toggle;
            if (rx_frame_err | fifo_overflow) led2_toggle <= ~led2_toggle;
        end
    end
    
    assign led[0] = led0_toggle;
    assign led[1] = led1_toggle;
    assign led[2] = led2_toggle;
    assign led[3] = hb;

endmodule