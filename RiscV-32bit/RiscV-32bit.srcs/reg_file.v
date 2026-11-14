`timescale 1ns / 1ps

module reg_file (
    input wire clk,
    input wire reset,
    input wire we,
    input wire [4:0]rs1,
    input wire [4:0]rs2,
    input wire [4:0]rd,
    input wire [31:0]wd,
    output reg [31:0]rd1,
    output reg [31:0]rd2
);

    reg [31:0]regs[0:31];
    integer i;

    always @(posedge clk) begin
        if(reset) begin
            for(i=0;i<32;i=i+1)
                regs[i]<=32'd0;
        end else begin
            if(we&&(rd!=5'd0))
                regs[rd]<=wd;
        end
    end

    always @* begin
        rd1=(rs1==5'd0)?32'd0:regs[rs1];
        rd2=(rs2==5'd0)?32'd0:regs[rs2];
    end
endmodule
