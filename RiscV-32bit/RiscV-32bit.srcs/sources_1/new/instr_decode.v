`timescale 1ns / 1ps

module instr_decode (
    input wire reset,
    input wire [31:0]instr,
    output reg [6:0]opcode,
    output reg [4:0]rd,
    output reg [4:0]rs1,
    output reg [4:0]rs2,
    output reg [2:0]funct3,
    output reg [6:0]funct7,
    output reg [31:0]imm
);

    reg [6:0]opc;

    always @* begin
        if(reset) begin
            opcode=7'd0;
            rd=5'd0;
            rs1=5'd0;
            rs2=5'd0;
            funct3=3'd0;
            funct7=7'd0;
            imm=32'd0;
        end else begin
            opc=instr[6:0];
            opcode=opc;
            rd=instr[11:7];
            funct3=instr[14:12];
            rs1=instr[19:15];
            rs2=instr[24:20];
            funct7=instr[31:25];
            imm=32'd0;

            case(opc)
                7'b0110011: begin
                    imm=32'd0;
                end
                7'b0010011,
                7'b0000011,
                7'b1100111: begin
                    imm={{20{instr[31]}},instr[31:20]};
                end
                7'b0100011: begin
                    imm={{20{instr[31]}},instr[31:25],instr[11:7]};
                end
                7'b1100011: begin
                    imm={{19{instr[31]}},instr[31],instr[7],instr[30:25],instr[11:8],1'b0};
                end
                7'b0110111,
                7'b0010111: begin
                    imm={instr[31:12],12'b0};
                end
                7'b1101111: begin
                    imm={{11{instr[31]}},instr[31],instr[19:12],instr[20],instr[30:21],1'b0};
                end
                default: begin
                    imm=32'd0;
                end
            endcase
        end
    end

endmodule