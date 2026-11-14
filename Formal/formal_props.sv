module riscv_core_formal_top (
    input wire clk,
    input wire reset
);
    localparam [31:0] STORE_PC = 32'h0000_000c;
    localparam [31:0] STORE_INSTR = 32'h0062_a023;
    localparam [31:0] IO_ADDR = 32'h0000_7000;
    localparam [31:0] BOOT_VALUE = 32'h0000_5555;

    wire [31:0] io_out, dbg_pc, dbg_instr, dbg_alu_result;
    riscv_core u_dut (
        .clk(clk),
        .reset(reset),
        .io_out(io_out),
        .dbg_pc(dbg_pc),
        .dbg_instr(dbg_instr),
        .dbg_alu_result(dbg_alu_result),
        .dbg_zero(),
        .dbg_negative(),
        .dbg_carry(),
        .dbg_overflow()
    );

    wire at_store = dbg_pc == STORE_PC;
    reg past_valid = 1'b0;
    always @(posedge clk) begin
        past_valid <= 1'b1;
        // Sample one reset at boot. Later reset pulses are unconstrained.
        if (!past_valid)
            boot_starts_in_reset: assume(reset);

        if (past_valid) begin
            word_aligned_pc: assert(dbg_pc[1:0] == 2'b00);

            if ($past(reset)) begin
                reset_pc: assert(dbg_pc == 32'd0);
                reset_mmio: assert(io_out == 32'd0);
            end else if ($past(at_store)) begin
                store_updates_mmio: assert(io_out == BOOT_VALUE);
            end else begin
                mmio_holds: assert(io_out == $past(io_out));
            end

            if (!reset && at_store) begin
                store_instruction: assert(dbg_instr == STORE_INSTR);
                store_address: assert(dbg_alu_result == IO_ADDR);
            end

            reach_boot_store: cover(!reset && at_store);
            reach_boot_value: cover(!$past(reset) && $past(at_store) && io_out == BOOT_VALUE);
        end
    end
endmodule
