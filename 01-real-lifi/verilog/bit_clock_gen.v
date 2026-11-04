`timescale 1ns / 1ps

module bit_clock_gen #(
    parameter CLK_FREQ = 125_000_000,
    parameter BIT_RATE = 10_000
)(
    input  wire clk,
    input  wire rst_n,
    output reg  bit_tick,
    output reg  sample_tick
);
    localparam integer DIV_BIT    = CLK_FREQ / BIT_RATE;
    localparam integer DIV_SAMPLE = CLK_FREQ / (BIT_RATE * 8);
    
    reg [23:0] bit_cnt;
    reg [20:0] sample_cnt;
    
    always @(posedge clk) begin
        if (!rst_n) begin
            bit_cnt  <= 24'd0;
            bit_tick <= 1'b0;
        end else if (bit_cnt >= DIV_BIT - 1) begin
            bit_cnt  <= 24'd0;
            bit_tick <= 1'b1;
        end else begin
            bit_cnt  <= bit_cnt + 24'd1;
            bit_tick <= 1'b0;
        end
    end
    
    always @(posedge clk) begin
        if (!rst_n) begin
            sample_cnt  <= 21'd0;
            sample_tick <= 1'b0;
        end else if (sample_cnt >= DIV_SAMPLE - 1) begin
            sample_cnt  <= 21'd0;
            sample_tick <= 1'b1;
        end else begin
            sample_cnt  <= sample_cnt + 21'd1;
            sample_tick <= 1'b0;
        end
    end
endmodule