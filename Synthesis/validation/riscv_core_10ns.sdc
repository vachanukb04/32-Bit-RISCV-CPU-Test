# VLSI Suite 101 baseline for the riscv_core ASIC top.
# Provisional clock only. Boundary delays and exceptions require design intent.
create_clock -name core_clk -period 10.000 -waveform {0.000 5.000} [get_ports clk]
