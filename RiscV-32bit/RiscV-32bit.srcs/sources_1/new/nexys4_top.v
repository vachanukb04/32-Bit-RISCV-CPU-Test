`timescale 1ns / 1ps

module nexys4_top (
    input wire clk_100mhz,
    input wire reset_n,
    output wire [15:0] led
);

    reg [2:0] clk_div;
    wire clk_sys;
    reg reset_sync_1, reset_sync_2;
    wire sys_reset;

    always @(posedge clk_100mhz) begin
        clk_div <= clk_div + 1;
    end
    assign clk_sys = clk_div[2];

    always @(posedge clk_sys) begin
        reset_sync_1 <= ~reset_n;
        reset_sync_2 <= reset_sync_1;
    end
    assign sys_reset = reset_sync_2;

    wire [31:0] io_out;

    riscv_core u_core (
        .clk(clk_sys),
        .reset(sys_reset),
        .io_out(io_out),
        .dbg_pc(),
        .dbg_instr(),
        .dbg_alu_result(),
        .dbg_zero(),
        .dbg_negative(),
        .dbg_carry(),
        .dbg_overflow()
    );

    assign led = io_out[15:0];

endmodule