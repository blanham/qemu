# VC4 stock START.ELF transfer frontier

Classification: **`stock-startelf-transfer-complete-stop-seen-no-arm-witness`**

- Unchanged START.ELF size: `3022336` bytes / `5903` blocks
- Observed transfer: `755584` / `755584` words (100.00%)
- Captured prefix matches START.ELF: `True` (16 words)
- Exact transfer complete: `True`
- START.ELF CMD18 observed: `True`
- CMD12 after this transfer: `5`
- START.ELF SDHSTS values: `0x00000001`
- Accesses after transfer: `1561113`
- ARM witness reached: `False`

The loop at PCs `0x766a`–`0x7690` is the stock bootloader's bounded
128-word-per-block SDHOST reader. PC `0x544` is only a timer-delay helper.
Neither address is a valid target for a PC-specific emulator workaround.

## Next contract

trace start.elf execution and the first post-load firmware contract
