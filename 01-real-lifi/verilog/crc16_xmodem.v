`timescale 1ns / 1ps

module crc16_xmodem (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        init,
    input  wire        data_valid,
    input  wire [7:0]  data_in,
    output reg  [15:0] crc_out
);
    integer i;
    reg [15:0] crc_temp;
    
    always @(posedge clk) begin
        if (!rst_n)         crc_out <= 16'h0000;
        else if (init)      crc_out <= 16'h0000;
        else if (data_valid) begin
            crc_temp = crc_out ^ {data_in, 8'h00};
            for (i = 0; i < 8; i = i + 1) begin
                if (crc_temp[15]) crc_temp = (crc_temp << 1) ^ 16'h1021;
                else              crc_temp = crc_temp << 1;
            end
            crc_out <= crc_temp;
        end
    end
endmodule