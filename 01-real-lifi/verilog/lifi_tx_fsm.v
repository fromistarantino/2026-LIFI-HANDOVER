`timescale 1ns / 1ps

module lifi_tx_fsm (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        bit_tick,
    input  wire        payload_valid,
    input  wire [7:0]  payload_data,
    input  wire [7:0]  payload_length,
    output reg         payload_ready,
    output reg         lifi_out,
    output reg         busy
);
    localparam S_IDLE=4'd0, S_PREAMBLE=4'd1, S_SFD=4'd2, S_LENGTH=4'd3,
               S_PAYLOAD=4'd4, S_CRC_HI=4'd5, S_CRC_LO=4'd6, S_IDLE_GAP=4'd7;
    localparam [15:0] PREAMBLE_VAL = 16'hAAAA;
    localparam [7:0]  SFD_VAL = 8'h7E;
    
    reg [3:0] state;
    reg [4:0] bit_idx;
    reg [7:0] byte_idx, cur_byte, saved_length;
    wire [15:0] crc_val;
    reg crc_init, crc_data_valid;
    reg [7:0] crc_data;
    
    crc16_xmodem crc_inst (
        .clk(clk), .rst_n(rst_n), .init(crc_init),
        .data_valid(crc_data_valid), .data_in(crc_data), .crc_out(crc_val)
    );
    
    always @(posedge clk) begin
        if (!rst_n) begin
            state <= S_IDLE; lifi_out <= 0; busy <= 0;
            payload_ready <= 0; crc_init <= 0; crc_data_valid <= 0;
            crc_data <= 0; bit_idx <= 0; byte_idx <= 0;
            cur_byte <= 0; saved_length <= 0;
        end else begin
            payload_ready <= 0; crc_init <= 0; crc_data_valid <= 0;
            
            case (state)
                S_IDLE: begin
                    busy <= 0; lifi_out <= 0;
                    if (payload_valid) begin
                        saved_length <= payload_length;
                        cur_byte <= PREAMBLE_VAL[15:8];
                        bit_idx <= 0; crc_init <= 1;
                        state <= S_PREAMBLE; busy <= 1;
                    end
                end
                S_PREAMBLE: if (bit_tick) begin
                    lifi_out <= cur_byte[7];
                    bit_idx <= bit_idx + 1;
                    if (bit_idx == 7) cur_byte <= PREAMBLE_VAL[7:0];
                    else if (bit_idx == 15) begin
                        state <= S_SFD; cur_byte <= SFD_VAL; bit_idx <= 0;
                    end else cur_byte <= {cur_byte[6:0], 1'b0};
                end
                S_SFD: if (bit_tick) begin
                    lifi_out <= cur_byte[7];
                    bit_idx <= bit_idx + 1;
                    if (bit_idx == 7) begin
                        state <= S_LENGTH; cur_byte <= saved_length; bit_idx <= 0;
                        crc_data <= saved_length; crc_data_valid <= 1;
                    end else cur_byte <= {cur_byte[6:0], 1'b0};
                end
                S_LENGTH: if (bit_tick) begin
                    lifi_out <= cur_byte[7];
                    bit_idx <= bit_idx + 1;
                    if (bit_idx == 7) begin
                        if (saved_length == 0) begin
                            state <= S_CRC_HI; cur_byte <= crc_val[15:8]; bit_idx <= 0;
                        end else begin
                            state <= S_PAYLOAD; cur_byte <= payload_data;
                            payload_ready <= 1; byte_idx <= 0; bit_idx <= 0;
                            crc_data <= payload_data; crc_data_valid <= 1;
                        end
                    end else cur_byte <= {cur_byte[6:0], 1'b0};
                end
                S_PAYLOAD: if (bit_tick) begin
                    lifi_out <= cur_byte[7];
                    bit_idx <= bit_idx + 1;
                    if (bit_idx == 7) begin
                        bit_idx <= 0;
                        if (byte_idx == saved_length - 1) begin
                            state <= S_CRC_HI; cur_byte <= crc_val[15:8];
                        end else begin
                            byte_idx <= byte_idx + 1;
                            cur_byte <= payload_data; payload_ready <= 1;
                            crc_data <= payload_data; crc_data_valid <= 1;
                        end
                    end else cur_byte <= {cur_byte[6:0], 1'b0};
                end
                S_CRC_HI: if (bit_tick) begin
                    lifi_out <= cur_byte[7];
                    bit_idx <= bit_idx + 1;
                    if (bit_idx == 7) begin
                        state <= S_CRC_LO; cur_byte <= crc_val[7:0]; bit_idx <= 0;
                    end else cur_byte <= {cur_byte[6:0], 1'b0};
                end
                S_CRC_LO: if (bit_tick) begin
                    lifi_out <= cur_byte[7];
                    bit_idx <= bit_idx + 1;
                    if (bit_idx == 7) begin
                        state <= S_IDLE_GAP; bit_idx <= 0;
                    end else cur_byte <= {cur_byte[6:0], 1'b0};
                end
                S_IDLE_GAP: begin
                    if (bit_tick) begin
                        lifi_out <= 0;
                        bit_idx <= bit_idx + 1;
                        if (bit_idx == 7) state <= S_IDLE;
                    end
                end
                default: state <= S_IDLE;
            endcase
        end
    end
endmodule