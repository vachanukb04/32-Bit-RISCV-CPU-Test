# Illustrative synchronous I/O budgets for pre-layout constraint coverage.
create_clock -name core_clk -period 10.000 -waveform {0.000 5.000} [get_ports clk]
set_input_delay -clock core_clk -max 2.000 [get_ports reset]
set_input_delay -clock core_clk -min 0.000 [get_ports reset]
set_input_transition 0.100 [get_ports reset]
set_output_delay -clock core_clk -max 2.000 [all_outputs]
set_output_delay -clock core_clk -min 0.000 [all_outputs]
set_load 0.005 [all_outputs]
