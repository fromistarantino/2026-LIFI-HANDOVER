`timescale 1ns / 1ps

module lifi_top (
    input  wire        CLK,
    input  wire        UART_RX_FROM_PC,
    output wire        UART_TX_TO_PC,
    output wire        LIFI_TX,
    input  wire        LIFI_RX,
    output wire [3:0]  LED
);

    // ─── 파워온 리셋 ───
    reg [3:0] por_count = 4'd0;
    wire rst_n = (por_count == 4'd15);
    always @(posedge CLK)
        if (por_count != 4'd15) por_count <= por_count + 4'd1;
    
    // ─── 신호 선언 ───
    wire bit_tick, sample_tick;
    wire pc_rx_valid;
    wire [7:0] pc_rx_data;
    reg last_was_crc_fail;
    
    reg [7:0]  tx_buffer [0:63];
    reg [7:0]  tx_length;
    reg        tx_trigger;
    reg [27:0] eof_timer;
    
    wire       lifi_busy;
    reg        lifi_busy_d;
    wire       lifi_busy_falling;
    wire       payload_ready;
    reg [7:0]  current_send_idx;
    reg [7:0]  payload_data_reg;
    
    reg [2:0]  lifi_rx_sync;
    wire       lifi_rx_clean;
    wire       rx_bit_valid, rx_bit_data;
    wire       rx_payload_valid;
    wire [7:0] rx_payload_data;
    wire       rx_frame_start, rx_frame_end, rx_crc_fail;
    wire [15:0] stat_ok, stat_fail;
    
    reg [7:0]  pc_tx_buffer [0:63];
    reg [7:0]  pc_tx_length;
    reg [7:0]  pc_tx_idx;
    reg [2:0]  pc_send_state;
    reg [7:0]  pc_data;
    reg        pc_send_pulse;
    wire       pc_busy;
    reg [7:0]  recv_idx;
    reg        send_pending;
    
    // 진단 LED
    reg [25:0] hb_cnt;
    reg        hb_led;
    reg [25:0] led_sync_blink;
    reg [25:0] led_ok_blink;
    reg [24:0] led_tx_blink;
    
    // ─── 비트/샘플 클럭 (★ 10 kbps ★) ───
    bit_clock_gen #(.CLK_FREQ(125_000_000), .BIT_RATE(1_000)) bclk (
        .clk(CLK), .rst_n(rst_n),
        .bit_tick(bit_tick), .sample_tick(sample_tick)
    );
    
    // ─── USB-UART RX ───
    uart_rx #(.CLK_FREQ(125_000_000), .BAUD(115_200)) urx (
        .clk(CLK), .rst_n(rst_n),
        .rx(UART_RX_FROM_PC),
        .data_valid(pc_rx_valid), .data_out(pc_rx_data)
    );
    
    // ─── lifi_busy 엣지 ───
    always @(posedge CLK) begin
        if (!rst_n) lifi_busy_d <= 0;
        else        lifi_busy_d <= lifi_busy;
    end
    assign lifi_busy_falling = lifi_busy_d && !lifi_busy;
    
    // ─── TX 버퍼 + 트리거 (100 ms EOF timeout) ───
    localparam EOF_TIMEOUT = 28'd12_500_000;
    
    always @(posedge CLK) begin
        if (!rst_n) begin
            tx_length <= 0; eof_timer <= 0; tx_trigger <= 0;
        end else begin
            tx_trigger <= 0;
            if (pc_rx_valid && tx_length < 64) begin
                tx_buffer[tx_length[5:0]] <= pc_rx_data;
                tx_length <= tx_length + 1;
                eof_timer <= 0;
            end else if (tx_length > 0 && !lifi_busy) begin
                if (eof_timer >= EOF_TIMEOUT) begin
                    tx_trigger <= 1; eof_timer <= 0;
                end else eof_timer <= eof_timer + 1;
            end
            if (lifi_busy_falling) tx_length <= 0;
        end
    end
    
    // ─── 송신 인덱스 ───
    always @(posedge CLK) begin
        if (!rst_n) begin
            current_send_idx <= 0; payload_data_reg <= 0;
        end else begin
            if (tx_trigger) begin
                current_send_idx <= 0;
                payload_data_reg <= tx_buffer[0];
            end else if (payload_ready) begin
                if (current_send_idx < tx_length - 1) begin
                    current_send_idx <= current_send_idx + 1;
                    payload_data_reg <= tx_buffer[(current_send_idx + 1) & 6'h3F];
                end
            end
        end
    end
    
    // ─── LiFi TX FSM ───
    lifi_tx_fsm tx_fsm (
        .clk(CLK), .rst_n(rst_n), .bit_tick(bit_tick),
        .payload_valid(tx_trigger), .payload_data(payload_data_reg),
        .payload_length(tx_length), .payload_ready(payload_ready),
        .lifi_out(LIFI_TX), .busy(lifi_busy)
    );
    
    // ─── LiFi RX 동기화 + 74HC14 반전 보정 ───
    always @(posedge CLK) begin
        if (!rst_n) lifi_rx_sync <= 0;
        else        lifi_rx_sync <= {lifi_rx_sync[1:0], LIFI_RX};
    end
    assign lifi_rx_clean = ~lifi_rx_sync[2];
    
    // ─── 오버샘플 ───
    oversampler osr (
        .clk(CLK), .rst_n(rst_n), .sample_tick(sample_tick),
        .rx_in(lifi_rx_clean),
        .bit_valid(rx_bit_valid), .bit_data(rx_bit_data)
    );
    
    // ─── LiFi RX FSM ───
    lifi_rx_fsm rx_fsm (
        .clk(CLK), .rst_n(rst_n),
        .bit_valid(rx_bit_valid), .bit_data(rx_bit_data),
        .payload_valid(rx_payload_valid), .payload_data(rx_payload_data),
        .frame_start(rx_frame_start), .frame_end(rx_frame_end),
        .crc_fail(rx_crc_fail),
        .stat_frames_ok(stat_ok), .stat_frames_fail(stat_fail)
    );
    
    // ─── RX 페이로드 수집 ───
    always @(posedge CLK) begin
        if (!rst_n) recv_idx <= 0;
        else begin
            if (rx_frame_start) recv_idx <= 0;
            else if (rx_payload_valid && recv_idx < 64) begin
                pc_tx_buffer[recv_idx[5:0]] <= rx_payload_data;
                recv_idx <= recv_idx + 1;
            end
        end
    end
    
    // ─── PC 송신 트리거 (CRC OK든 FAIL이든 보냄) ───
    always @(posedge CLK) begin
        if (!rst_n) begin
            send_pending <= 1'b0;
            pc_tx_length <= 8'd0;
            last_was_crc_fail <= 1'b0;
        end else begin
            if (rx_frame_end || rx_crc_fail) begin
                pc_tx_length <= recv_idx;
                send_pending <= 1'b1;
                last_was_crc_fail <= rx_crc_fail;
            end else if (pc_send_state != 3'd0) begin
                send_pending <= 1'b0;
            end
        end
    end
    
    // ─── PC 송신 FSM ([0xFE 0xFE][STATUS][LEN][DATA...]) ───
    localparam P_IDLE=3'd0, P_HDR1=3'd1, P_HDR2=3'd2,
               P_STATUS=3'd3, P_LEN=3'd4, P_DATA=3'd5;
    
    always @(posedge CLK) begin
        if (!rst_n) begin
            pc_send_state <= P_IDLE; pc_send_pulse <= 0;
            pc_tx_idx <= 0; pc_data <= 0;
        end else begin
            pc_send_pulse <= 0;
            case (pc_send_state)
                P_IDLE: if (send_pending) pc_send_state <= P_HDR1;
                P_HDR1: if (!pc_busy && !pc_send_pulse) begin
                    pc_data <= 8'hFE; pc_send_pulse <= 1; pc_send_state <= P_HDR2;
                end
                P_HDR2: if (!pc_busy && !pc_send_pulse) begin
                    pc_data <= 8'hFE; pc_send_pulse <= 1; pc_send_state <= P_STATUS;
                end
                P_STATUS: if (!pc_busy && !pc_send_pulse) begin
                    pc_data <= last_was_crc_fail ? 8'h02 : 8'h01;
                    pc_send_pulse <= 1'b1;
                    pc_send_state <= P_LEN;
                end
                P_LEN: if (!pc_busy && !pc_send_pulse) begin
                    pc_data <= pc_tx_length; pc_send_pulse <= 1; pc_tx_idx <= 0;
                    if (pc_tx_length == 0) pc_send_state <= P_IDLE;
                    else                   pc_send_state <= P_DATA;
                end
                P_DATA: if (!pc_busy && !pc_send_pulse) begin
                    pc_data <= pc_tx_buffer[pc_tx_idx[5:0]];
                    pc_send_pulse <= 1;
                    if (pc_tx_idx == pc_tx_length - 1) pc_send_state <= P_IDLE;
                    else pc_tx_idx <= pc_tx_idx + 1;
                end
                default: pc_send_state <= P_IDLE;
            endcase
        end
    end
    
    // ─── USB-UART TX ───
    uart_tx #(.CLK_FREQ(125_000_000), .BAUD(115_200)) utx (
        .clk(CLK), .rst_n(rst_n),
        .send(pc_send_pulse), .data_in(pc_data),
        .tx(UART_TX_TO_PC), .busy(pc_busy)
    );
    
    // ─── 진단 LED ───
    // LED[0]: LiFi 송신 / LED[1]: 광 수신 SYNC / LED[2]: CRC OK / LED[3]: 하트비트
    always @(posedge CLK) begin
        if (hb_cnt >= 26'd62_500_000) begin
            hb_cnt <= 0; hb_led <= ~hb_led;
        end else hb_cnt <= hb_cnt + 1;
    end
    
    always @(posedge CLK) begin
        if (!rst_n) begin
            led_tx_blink <= 0; led_sync_blink <= 0; led_ok_blink <= 0;
        end else begin
            if (lifi_busy)                   led_tx_blink <= 25'd25_000_000;
            else if (led_tx_blink > 0)       led_tx_blink <= led_tx_blink - 1;
            
            if (rx_frame_start)              led_sync_blink <= 26'd62_500_000;
            else if (led_sync_blink > 0)     led_sync_blink <= led_sync_blink - 1;
            
            if (rx_frame_end)                led_ok_blink <= 26'd62_500_000;
            else if (led_ok_blink > 0)       led_ok_blink <= led_ok_blink - 1;
        end
    end
    
    assign LED[0] = (led_tx_blink   > 0);
    assign LED[1] = (led_sync_blink > 0);
    assign LED[2] = (led_ok_blink   > 0);
    assign LED[3] = hb_led;
    
endmodule