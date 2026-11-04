`timescale 1ns / 1ps

// 클럭 복구 포함 오버샘플러
// - 비트 transition마다 위상 정렬
// - 비트 중앙 (4번째 샘플)에서 결정

module oversampler (
    input  wire clk,
    input  wire rst_n,
    input  wire sample_tick,
    input  wire rx_in,
    output reg  bit_valid,
    output reg  bit_data
);
    reg [2:0] phase;
    reg       last_rx;
    reg       locked;
    
    always @(posedge clk) begin
        if (!rst_n) begin
            phase     <= 3'd0;
            last_rx   <= 1'b0;
            locked    <= 1'b0;
            bit_valid <= 1'b0;
            bit_data  <= 1'b0;
        end else begin
            bit_valid <= 1'b0;
            
            if (sample_tick) begin
                last_rx <= rx_in;
                
                if (rx_in != last_rx) begin
                    // Transition 검출: 비트 경계 → 위상 리셋
                    phase  <= 3'd1;
                    locked <= 1'b1;
                end else begin
                    // 비트 중앙 (phase==4)에서 결정
                    if (locked && phase == 3'd4) begin
                        bit_data  <= rx_in;
                        bit_valid <= 1'b1;
                    end
                    
                    if (phase == 3'd7) phase <= 3'd0;
                    else               phase <= phase + 3'd1;
                end
            end
        end
    end
endmodule