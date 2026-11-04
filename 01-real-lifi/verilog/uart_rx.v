`timescale 1ns / 1ps

module uart_rx #(
    parameter CLK_FREQ = 125_000_000,
    parameter BAUD = 115_200
)(
    input  wire        clk,
    input  wire        rst_n,
    input  wire        rx,
    output reg         data_valid,
    output reg  [7:0]  data_out
);
    localparam integer DIV      = CLK_FREQ / BAUD;
    localparam integer DIV_HALF = DIV / 2;
    
    reg [2:0] rx_sync;
    wire rx_clean;
    
    always @(posedge clk) begin
        if (!rst_n) rx_sync <= 3'b111;
        else        rx_sync <= {rx_sync[1:0], rx};
    end
    assign rx_clean = rx_sync[2];
    
    localparam S_IDLE=2'd0, S_START=2'd1, S_DATA=2'd2, S_STOP=2'd3;
    
    reg [1:0]  state;
    reg [10:0] clk_cnt;
    reg [3:0]  bit_idx;
    reg [7:0]  shift;
    
    always @(posedge clk) begin
        if (!rst_n) begin
            state <= S_IDLE; data_valid <= 0; data_out <= 0;
            clk_cnt <= 0; bit_idx <= 0; shift <= 0;
        end else begin
            data_valid <= 0;
            case (state)
                S_IDLE: if (!rx_clean) begin state <= S_START; clk_cnt <= 0; end
                S_START: begin
                    if (clk_cnt >= DIV_HALF - 1) begin
                        clk_cnt <= 0;
                        if (!rx_clean) begin state <= S_DATA; bit_idx <= 0; end
                        else state <= S_IDLE;
                    end else clk_cnt <= clk_cnt + 1;
                end
                S_DATA: begin
                    if (clk_cnt >= DIV - 1) begin
                        clk_cnt <= 0;
                        shift <= {rx_clean, shift[7:1]};
                        if (bit_idx == 7) state <= S_STOP;
                        else bit_idx <= bit_idx + 1;
                    end else clk_cnt <= clk_cnt + 1;
                end
                S_STOP: begin
                    if (clk_cnt >= DIV - 1) begin
                        clk_cnt <= 0;
                        data_out <= shift;
                        data_valid <= 1;
                        state <= S_IDLE;
                    end else clk_cnt <= clk_cnt + 1;
                end
                default: state <= S_IDLE;
            endcase
        end
    end
endmodule