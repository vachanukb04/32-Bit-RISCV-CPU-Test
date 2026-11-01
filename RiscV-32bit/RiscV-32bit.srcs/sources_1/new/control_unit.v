`timescale 1ns / 1ps

module control_unit (
    input wire reset,
    input wire [6:0]opcode,
    input wire [2:0]funct3,
    input wire [6:0]funct7,
    output reg reg_write,
    output reg mem_read,
    output reg mem_write,
    output reg mem_to_reg,
    output reg alu_src,
    output reg branch,
    output reg jal,
    output reg [4:0]alu_op
);

    localparam OPC_RTYPE=7'b0110011;
    localparam OPC_ITYPE=7'b0010011;
    localparam OPC_LOAD=7'b0000011;
    localparam OPC_STORE=7'b0100011;
    localparam OPC_BRANCH=7'b1100011;
    localparam OPC_LUI=7'b0110111;
    localparam OPC_AUIPC=7'b0010111;
    localparam OPC_JAL=7'b1101111;
    localparam OPC_JALR=7'b1100111;

    always @* begin
        reg_write=1'b0;
        mem_read=1'b0;
        mem_write=1'b0;
        mem_to_reg=1'b0;
        alu_src=1'b0;
        branch=1'b0;
        jal=1'b0;
        alu_op=5'b00000;

        if(!reset) begin
            case(opcode)
                OPC_RTYPE: begin
                    reg_write=1'b1;
                    alu_src=1'b0;
                    case({funct7,funct3})
                        {7'b0000000,3'b000}:alu_op=5'b00010;
                        {7'b0100000,3'b000}:alu_op=5'b00011;
                        {7'b0000000,3'b111}:alu_op=5'b00101;
                        {7'b0000000,3'b110}:alu_op=5'b00100;
                        {7'b0000000,3'b100}:alu_op=5'b00110;
                        {7'b0000000,3'b001}:alu_op=5'b00111;
                        {7'b0000000,3'b101}:alu_op=5'b01000;
                        {7'b0100000,3'b101}:alu_op=5'b01001;
                        {7'b0000000,3'b010}:alu_op=5'b10100;
                        {7'b0000000,3'b011}:alu_op=5'b10101;
                        {7'b0000001,3'b000}:alu_op=5'b10001;
                        default:alu_op=5'b00000;
                    endcase
                end
                OPC_ITYPE: begin
                    reg_write=1'b1;
                    alu_src=1'b1;
                    case(funct3)
                        3'b000:alu_op=5'b00001;
                        3'b010:alu_op=5'b10100;
                        3'b011:alu_op=5'b10101;
                        3'b100:alu_op=5'b00110;
                        3'b110:alu_op=5'b00100;
                        3'b111:alu_op=5'b00101;
                        3'b001:alu_op=5'b00111;
                        3'b101:alu_op=(funct7==7'b0000000)?5'b01000:5'b01001;
                        default:alu_op=5'b00000;
                    endcase
                end
                OPC_LOAD: begin
                    reg_write=1'b1;
                    mem_read=1'b1;
                    mem_to_reg=1'b1;
                    alu_src=1'b1;
                    alu_op=5'b01100;
                end
                OPC_STORE: begin
                    mem_write=1'b1;
                    alu_src=1'b1;
                    alu_op=5'b01101;
                end
                OPC_BRANCH: begin
                    branch=1'b1;
                    alu_src=1'b0;
                    case(funct3)
                        3'b000:alu_op=5'b01010;
                        3'b001:alu_op=5'b01011;
                        default:alu_op=5'b01010;
                    endcase
                end
                OPC_JAL,OPC_JALR: begin
                    jal=1'b1;
                    reg_write=1'b1;
                    alu_src=1'b1;
                    alu_op=5'b01110;
                end
                OPC_LUI: begin
                    reg_write=1'b1;
                    alu_src=1'b1;
                    alu_op=5'b00000;
                end
                OPC_AUIPC: begin
                    reg_write=1'b1;
                    alu_src=1'b1;
                    alu_op=5'b01110;
                end
                default:;
            endcase
        end
    end
endmodule