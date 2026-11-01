`timescale 1ns / 1ps

module riscv_core (
    input wire clk,
    input wire reset,
    output wire [31:0]io_out,
    output wire [31:0]dbg_pc,
    output wire [31:0]dbg_instr,
    output wire [31:0]dbg_alu_result,
    output wire dbg_zero,
    output wire dbg_negative,
    output wire dbg_carry,
    output wire dbg_overflow
);

    wire [31:0]pc,pc_next,pc_plus4,branch_target;

    pc u_pc (
        .clk(clk),
        .reset(reset),
        .pc_next(pc_next),
        .pc(pc)
    );

    assign pc_plus4 = pc + 32'd4;

    wire [31:0]instr;
    inst_mem u_imem (
        .addr(pc),
        .inst(instr)
    );

    wire [6:0]opcode;
    wire [4:0]rd,rs1,rs2;
    wire [2:0]funct3;
    wire [6:0]funct7;
    wire [31:0]imm;
    instr_decode u_dec (
        .reset(reset),
        .instr(instr),
        .opcode(opcode),
        .rd(rd),
        .rs1(rs1),
        .rs2(rs2),
        .funct3(funct3),
        .funct7(funct7),
        .imm(imm)
    );

    wire reg_write,mem_read,mem_write,mem_to_reg;
    wire alu_src,branch,jal;
    wire [4:0]alu_op;
    control_unit u_ctrl (
        .reset(reset),
        .opcode(opcode),
        .funct3(funct3),
        .funct7(funct7),
        .reg_write(reg_write),
        .mem_read(mem_read),
        .mem_write(mem_write),
        .mem_to_reg(mem_to_reg),
        .alu_src(alu_src),
        .branch(branch),
        .jal(jal),
        .alu_op(alu_op)
    );

    wire [31:0]rs1_data,rs2_data,wb_data;
    reg_file u_rf (
        .clk(clk),
        .reset(reset),
        .we(reg_write),
        .rs1(rs1),
        .rs2(rs2),
        .rd(rd),
        .wd(wb_data),
        .rd1(rs1_data),
        .rd2(rs2_data)
    );

    wire [31:0]alu_a;
    wire [31:0]alu_b;
    assign alu_a = (jal|(opcode==7'b0010111))?pc:rs1_data;
    assign alu_b = alu_src?imm:rs2_data;

    wire [31:0]alu_result;
    wire zero,negative,carry,overflow;
    alu u_alu (
        .a(alu_a),
        .b(alu_b),
        .alu_op(alu_op),
        .result(alu_result),
        .zero(zero),
        .negative(negative),
        .carry(carry),
        .overflow(overflow)
    );

    wire is_io_write;
    wire dmem_write;
    reg [31:0] io_reg;

    assign is_io_write = mem_write & (alu_result == 32'h00007000);
    assign dmem_write = mem_write & ~is_io_write;

    always @(posedge clk or posedge reset) begin
        if(reset)
            io_reg <= 32'd0;
        else if(is_io_write)
            io_reg <= rs2_data;
    end

    assign io_out = io_reg;

    wire [31:0]mem_data;
    data_mem u_dmem (
        .clk(clk),
        .reset(reset),
        .mem_read(mem_read),
        .mem_write(dmem_write),
        .addr(alu_result),
        .write_data(rs2_data),
        .read_data(mem_data)
    );

    assign wb_data = (opcode==7'b0110111)?imm:
                     (mem_to_reg)?mem_data:
                     alu_result;

    wire branch_taken;
    assign branch_taken = branch&&((funct3==3'b000&&zero)||(funct3==3'b001&&!zero));
    assign branch_target = pc+imm;

    assign pc_next = jal?(pc+imm):
                     branch_taken?branch_target:
                     pc_plus4;

    assign dbg_pc = pc;
    assign dbg_instr = instr;
    assign dbg_alu_result = alu_result;
    assign dbg_zero = zero;
    assign dbg_negative = negative;
    assign dbg_carry = carry;
    assign dbg_overflow = overflow;

endmodule