# VC4 stock-firmware temporal VPU frontier

Classification: **`stock-vpu-lowpc-loop-is-progressing`**

- ARM witness reached: `False`
- Samples: `399`
- Unique PCs: `8`
- Unique selected register states: `50`
- State transitions: `50`
- PC 0x544 sample fraction: `0.8771929824561403`
- System timer advance: `20002170` us
- ARM_CONTROL0: `0x00000000`
- ARM_CONTROL1: `0x00000000`
- ARM_STATUS: `0x00000000`
- PM_PROC: `0x00000000`

## Sampled PCs

- `0x00000542`: `2` samples
- `0x00000544`: `350` samples
- `0x00006af8`: `1` samples
- `0x0000766a`: `8` samples
- `0x00007670`: `11` samples
- `0x0000767c`: `6` samples
- `0x0000768a`: `20` samples
- `0x00008308`: `1` samples

## Translation context around 0x544

```text
--- lines 1444-1483 ---
----------------
IN: 
0x00000542:  
OBJD-T: 122132460083fd4b

----------------
IN: 
0x00000542:  
OBJD-T: 1221

----------------
IN: 
0x00000544:  
OBJD-T: 32460083fd4b

----------------
IN: 
0x00000540:  
OBJD-T: 0100122132460083fd4b

----------------
IN: 
0x0000054a:  
OBJD-T: 5a00

----------------
IN: 
0x000080de:  
OBJD-T: 0260716022e62090ff371061613262346009ff9fccfe

----------------
IN: 
0x000080ea:  
OBJD-T: 6132

----------------
IN: 
0x000080ec:  
OBJD-T: 62346009ff9fccfe

```
