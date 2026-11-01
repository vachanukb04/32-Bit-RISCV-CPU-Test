`timescale 1ns / 1ps

module tb_riscv_core;

    reg clk = 0;
    reg reset = 1;

    wire [31:0] io_out;
    wire [31:0] dbg_pc;
    wire [31:0] dbg_instr;
    wire [31:0] dbg_alu_result;
    wire dbg_zero;
    wire dbg_negative;
    wire dbg_carry;
    wire dbg_overflow;

    riscv_core uut (
        .clk(clk),
        .reset(reset),
        .io_out(io_out),
        .dbg_pc(dbg_pc),
        .dbg_instr(dbg_instr),
        .dbg_alu_result(dbg_alu_result),
        .dbg_zero(dbg_zero),
        .dbg_negative(dbg_negative),
        .dbg_carry(dbg_carry),
        .dbg_overflow(dbg_overflow)
    );

    always #5 clk = ~clk;

    initial begin
        $dumpfile("riscv_core.vcd");
        $dumpvars(0, tb_riscv_core);

        reset = 1;
        #20 reset = 0;
        
        #2000 $finish;
    end

    always @(io_out) begin
        if (!reset) begin
            $display("Time: %0t | IO Output changed to: %h", $time, io_out);
        end
    end

endmodule