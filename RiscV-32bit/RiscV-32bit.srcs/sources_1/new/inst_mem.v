`timescale 1ns / 1ps

module inst_mem (
    input wire [31:0]addr,
    output reg [31:0]inst
);

    reg [31:0]mem[0:255];
    integer i;

    initial begin

        for(i=0; i<256; i=i+1) begin
            mem[i] = 32'h00000013;
        end

        mem[0] = 32'h000072B7; 
        mem[1] = 32'h00005337; 
        mem[2] = 32'h55530313; 
        mem[3] = 32'h0062A023; 
        mem[4] = 32'h0000006F; 
    end

    always @* begin
        inst = mem[addr[9:2]];
    end

endmodule