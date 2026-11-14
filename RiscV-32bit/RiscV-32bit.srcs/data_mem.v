`timescale 1ns / 1ps

module data_mem (
    input wire clk,
    input wire reset,
    input wire mem_read,
    input wire mem_write,
    input wire [31:0]addr,
    input wire [31:0]write_data,
    output reg [31:0]read_data
);

    reg [31:0]mem[0:255];
    integer i;

    always @(posedge clk) begin
        if(reset) begin
            for(i=0;i<256;i=i+1)
                mem[i]<=32'd0;
        end else begin
            if(mem_write)
                mem[addr[9:2]]<=write_data;
        end
    end

    always @* begin
        if(mem_read)
            read_data=mem[addr[9:2]];
        else
            read_data=32'd0;
    end

endmodule
