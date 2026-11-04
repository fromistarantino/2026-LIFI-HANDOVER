`timescale 1ns / 1ps

module uart_tx #(
    parameter CLK_FREQ = 125_000_000,
    parameter BAUD = 115_200
)(
    input  wire        clk,
    input  wire        rst_n,
    input  wire        send,
    input  wire [7:0]  data_in,
    output reg         tx,
    output reg         busy
);
    localparam integer DIV = CLK_FREQ / BAUD;
    localparam S_IDLE = 2'd0, S_DATA = 2'd1;
    
    reg [1:0]  state;
    reg [10:0] clk_cnt;
    reg [3:0]  bit_idx;
    reg [9:0]  shift;
    
    always @(posedge clk) begin
        if (!rst_n) begin
            state <= S_IDLE; tx <= 1; busy <= 0;
            clk_cnt <= 0; bit_idx <= 0; shift <= 10'h3FF;
        end else begin
            case (state)
                S_IDLE: begin
                    tx <= 1; busy <= 0;
                    if (send) begin
                        shift <= {1'b1, data_in, 1'b0};
                        state <= S_DATA;
                        clk_cnt <= 0; bit_idx <= 0; busy <= 1;
                    end
                end
                S_DATA: begin
                    if (clk_cnt >= DIV - 1) begin
                        clk_cnt <= 0;
                        tx <= shift[0];
                        shift <= {1'b1, shift[9:1]};
                        if (bit_idx == 9) state <= S_IDLE;
                        else bit_idx <= bit_idx + 1;
                    end else clk_cnt <= clk_cnt + 1;
                end
                default: state <= S_IDLE;
            endcase
        end
    end
endmodule