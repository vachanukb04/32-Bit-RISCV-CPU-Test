`timescale 1ns / 1ps

module alu (
    input wire [31:0]a,
    input wire [31:0]b,
    input wire [4:0]alu_op,
    output reg [31:0]result,
    output wire zero,
    output wire negative,
    output wire carry,
    output wire overflow
);

    reg c_r;
    reg v_r;
    reg [32:0]tmp;

    always @* begin
        result=32'd0;
        c_r=1'b0;
        v_r=1'b0;
        case(alu_op)
            5'b00000:result=32'd0;
            5'b00001,
            5'b00010,
            5'b01100,
            5'b01101,
            5'b01110:begin
                tmp={1'b0,a}+{1'b0,b};
                result=tmp[31:0];
                c_r=tmp[32];
                v_r=(a[31]==b[31])&&(result[31]!=a[31]);
            end
            5'b00011:begin
                tmp={1'b0,a}+{1'b0,~b}+33'd1;
                result=tmp[31:0];
                c_r=tmp[32];
                v_r=(a[31]!=b[31])&&(result[31]!=a[31]);
            end
            
            5'b00101:result=a&b;
            5'b00100:result=a|b;
            5'b00110:result=a^b;
            5'b00111:result=a<<b[4:0];
            5'b01000:result=a>>b[4:0];
            5'b01001:result=$signed(a)>>>b[4:0];
            5'b10100:result=($signed(a)<$signed(b))?32'd1:32'd0;
            5'b10101:result=(a<b)?32'd1:32'd0;
            5'b10001:result=a*b;
            5'b01010,
            5'b01011:begin
                
                tmp={1'b0,a}+{1'b0,~b}+33'd1;
                result=tmp[31:0];
                c_r=tmp[32];
                v_r=(a[31]!=b[31])&&(result[31]!=a[31]);
            end
            default:begin
                result=32'd0;
                c_r=1'b0;
                v_r=1'b0;
            end
        endcase
    end

    assign zero=(result==32'd0);
    assign negative=result[31];
    assign carry=c_r;
    assign overflow=v_r;

endmodule