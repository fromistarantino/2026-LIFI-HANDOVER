`timescale 1ns / 1ps

module lifi_rx_fsm (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        bit_valid,
    input  wire        bit_data,
    output reg         payload_valid,
    output reg  [7:0]  payload_data,
    output reg         frame_start,
    output reg         frame_end,
    output reg         crc_fail,
    output reg  [15:0] stat_frames_ok,
    output reg  [15:0] stat_frames_fail
);
    localparam S_IDLE=3'd0, S_LENGTH=3'd1, S_PAYLOAD=3'd2,
               S_CRC_HI=3'd3, S_CRC_LO=3'd4, S_VERIFY=3'd5;
    
    reg [2:0]  state;
    reg [23:0] shift24;
    reg [7:0]  byte_buf;
    reg [2:0]  bit_cnt;
    reg        byte_ready;
    reg [7:0]  frame_length, data_count;
    reg [15:0] crc_rx;
    
    wire [15:0] crc_calc;
    reg crc_init, crc_data_valid;
    reg [7:0] crc_data;
    
    crc16_xmodem crc_inst (
        .clk(clk), .rst_n(rst_n), .init(crc_init),
        .data_valid(crc_data_valid), .data_in(crc_data), .crc_out(crc_calc)
    );
    
    wire sfd_detected = (shift24 == 24'hAAAA7E);
    
    always @(posedge clk) begin
        if (!rst_n) shift24 <= 0;
        else if (bit_valid) shift24 <= {shift24[22:0], bit_data};
    end
    
    always @(posedge clk) begin
        if (!rst_n) begin
            bit_cnt <= 0; byte_buf <= 0; byte_ready <= 0;
        end else begin
            byte_ready <= 0;
            if (state == S_IDLE) bit_cnt <= 0;
            else if (bit_valid) begin
                byte_buf <= {byte_buf[6:0], bit_data};
                if (bit_cnt == 7) begin bit_cnt <= 0; byte_ready <= 1; end
                else bit_cnt <= bit_cnt + 1;
            end
        end
    end
    
    always @(posedge clk) begin
        if (!rst_n) begin
            state <= S_IDLE; payload_valid <= 0; payload_data <= 0;
            frame_start <= 0; frame_end <= 0; crc_fail <= 0;
            stat_frames_ok <= 0; stat_frames_fail <= 0;
            crc_init <= 0; crc_data_valid <= 0; crc_data <= 0;
            frame_length <= 0; data_count <= 0; crc_rx <= 0;
        end else begin
            payload_valid <= 0; frame_start <= 0; frame_end <= 0;
            crc_fail <= 0; crc_init <= 0; crc_data_valid <= 0;
            
            case (state)
                S_IDLE: if (sfd_detected) begin
                    state <= S_LENGTH; frame_start <= 1;
                    crc_init <= 1; data_count <= 0;
                end
                S_LENGTH: if (byte_ready) begin
                    if (byte_buf == 0 || byte_buf > 64) state <= S_IDLE;
                    else begin
                        frame_length <= byte_buf;
                        crc_data <= byte_buf; crc_data_valid <= 1;
                        state <= S_PAYLOAD;
                    end
                end
                S_PAYLOAD: if (byte_ready) begin
                    payload_data <= byte_buf; payload_valid <= 1;
                    crc_data <= byte_buf; crc_data_valid <= 1;
                    if (data_count == frame_length - 1) state <= S_CRC_HI;
                    else data_count <= data_count + 1;
                end
                S_CRC_HI: if (byte_ready) begin
                    crc_rx[15:8] <= byte_buf; state <= S_CRC_LO;
                end
                S_CRC_LO: if (byte_ready) begin
                    crc_rx[7:0] <= byte_buf; state <= S_VERIFY;
                end
                S_VERIFY: begin
                    if (crc_calc == crc_rx) begin
                        frame_end <= 1; stat_frames_ok <= stat_frames_ok + 1;
                    end else begin
                        crc_fail <= 1; stat_frames_fail <= stat_frames_fail + 1;
                    end
                    state <= S_IDLE;
                end
                default: state <= S_IDLE;
            endcase
        end
    end
endmodule