`timescale 1ns / 1ps

module inst_mem (
    input wire [31:0]addr,
    output reg [31:0]inst
);

    reg [31:0]mem[0:255];
    integer i;

    // 256 aligned words at byte addresses 0x00000000 through 0x000003fc.
    wire valid_word_addr;
    // #WARNING Fix : Lint-011
    assign valid_word_addr = (addr[31:10] == 22'd0) && (addr[1:0] == 2'b00);

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
        // #WARNING Fix : Lint-011
        if(valid_word_addr)
            inst = mem[addr[9:2]];
        else
            inst = 32'h00000013; // NOP for an invalid fetch in this core.
    end

// #WARNING Fix : Lint-004
endmodule
