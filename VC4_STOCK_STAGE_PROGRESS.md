# VC4 stock firmware stage-progress measurement

- Classification: `start-elf-copy-incomplete`
- ARM witness: `False`
- START.ELF size: `3022336` bytes
- START.ELF entry: `0xcec00200`
- Copy complete by independent sentinels: `False`
- First high VPU PC: `None`
- First exact START.ELF entry PC: `None`
- Final SDHOST state: `{'cmd': '0x0000000d', 'arg': '0x288a0000', 'timeout': '0x00a00000', 'cdiv': '0x00000009', 'status': '0x00000000', 'vdd': '0x00000001', 'edm': '0x0000c601', 'config': '0x0000000a', 'hbct': '0x00000400', 'hblc': '0x00000000'}`

This is a diagnostic measurement only. It runs unchanged pinned firmware under normal single-threaded TCG and does not alter firmware, guest memory, timing, interrupts, or SDHOST behavior.
