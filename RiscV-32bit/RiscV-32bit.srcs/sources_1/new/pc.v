`timescale 1ns / 1ps

module pc (
    input wire clk,
    input wire reset,
    input wire [31:0]pc_next,
    output reg [31:0]pc
);

    // #WARNING Fix : Lint-014
    always @(posedge clk) begin
        if(reset)
            pc <= 32'b0;
        else
            pc <= pc_next;
    end

// #WARNING Fix : Lint-007
endmodule
