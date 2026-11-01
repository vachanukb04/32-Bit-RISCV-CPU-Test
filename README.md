# 💻 RISC-V 32-bit Single-Cycle Processor

![Language](https://img.shields.io/badge/Language-Verilog-blue)
![Platform](https://img.shields.io/badge/Platform-Xilinx%20Vivado-red)
![Target](https://img.shields.io/badge/Target-Artix--7%20(Nexys%204)-orange)
![License](https://img.shields.io/badge/License-MIT-green)
![Status](https://img.shields.io/badge/Status-Synthesis%20Ready-brightgreen)

## 📌 Overview

This project implements a highly optimized **32-bit RISC-V single-cycle soft-core** in pure Verilog. Originally based on [Design and Implementation of 32-bit RISC-V Processor Using Verilog](docs/Reference_Paper.pdf), the architecture has been heavily modified and constrained for physical silicon synthesis on the **Xilinx Artix-7 FPGA (Nexys 4 DDR)**.

The CPU supports the **RV32I** base integer instruction set and features a custom **Memory-Mapped I/O (MMIO)** architecture, allowing compiled RISC-V assembly to natively control physical board peripherals.

---

## 📊 Hardware Utilization & Metrics
Synthesized and implemented using Xilinx Vivado for the `xc7a100tcsg324-1` part. The core is highly optimized for area and easily meets 100 MHz onboard timing constraints.

* **Look-Up Tables (LUTs):** 300 (< 1%)
* **Registers (Flip-Flops):** 117 (< 1%)
* **Timing (WNS):** +8.161 ns (Met constraints)
* **Total On-Chip Power:** 1.888 W

---

## ✨ Architectural Features

### ✔ RV32I Base Integer Set
* **Arithmetic / Logic:** `ADD`, `SUB`, `AND`, `OR`, `XOR`
* **Shifts:** `SLL`, `SRL`, `SRA`
* **Compare:** `SLT`, `SLTU`
* **Immediate:** `ADDI`, `LUI`, `AUIPC`
* **Branches:** `BEQ`, `BNE`
* **Jumps:** `JAL`, `JALR`
* **Memory:** `LW`, `SW`
*(Note: Complex combinational instructions like DIV/REM were removed to meet strict single-cycle synthesis timing paths).*

### ✔ Hardware-Software Interface
* **Memory-Mapped I/O (MMIO):** Custom I/O register mapped to address `0x00007000` to drive 16 onboard LEDs directly via Store Word (`sw`) instructions.
* **Clock Management:** Integrated clock divider scaling the 100 MHz physical board oscillator to a stable 12.5 MHz system clock.
* **Standalone Execution:** Instruction memory is initialized natively in the RTL (`inst_mem.v`), requiring no external hex files during the Vivado build process.

---

## 🚀 Running the Hardware Test Program

The instruction memory comes pre-loaded with a machine-code payload that tests the MMIO by writing an alternating pattern (`0x5555` / `0101010101010101`) to the physical LEDs on the Nexys 4 board.

**Assembly Payload:**
```assembly
lui x5, 0x00007      # Load MMIO base address (0x00007000)
lui x6, 0x00005      # Load upper LED pattern
addi x6, x6, 0x555   # Add lower LED pattern (x6 = 0x5555)
sw x6, 0(x5)         # Store pattern to physical LEDs
jal x0, 0            # Infinite loop to maintain state
```

---

## 📂 Directory Structure
```
RiscV-32bit/
│
├── src/
│   ├── nexys4_top.v          # Top-level wrapper with clock divider & MMIO
│   ├── riscv_core.v          # CPU datapath and integration
│   ├── alu.v                 # Arithmetic Logic Unit
│   ├── control_unit.v        # Instruction decoding and control signals
│   ├── instr_decode.v        # Immediate generation
│   ├── reg_file.v            # 32×32 Register File
│   ├── pc.v                  # Program Counter
│   ├── inst_mem.v            # Instruction Memory (Hardcoded test program)
│   └── data_mem.v            # Data Memory
│
├── constraints/
│   └── nexys4_ddr.xdc        # Physical pin mapping and timing constraints
│
├── sim/
│   └── tb_riscv_core.v       # Testbench for datapath and MMIO verification
│
├── images/                   # Architecture diagrams + waveforms
│   ├── Micro-Arch.png        
│   ├── Schematic.png         
│   └── Test_Bench_Result.png 
│
└── docs/
    └── Reference_Paper.pdf   # Included architectural reference
