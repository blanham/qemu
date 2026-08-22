# VC4 Raspberry Pi HDMI DDC validation

Validation passed: **true**

- Both AArch64 and VC4 system frontends built.
- Firmware clock enumeration passed.
- Firmware power/domain/GPIO state passed.
- BSC2 returned a valid, checksummed monitor EDID.
- DDC pointer addressing and reset behavior passed.

```text
Raspberry Pi HDMI DDC smoke test passed: EDID 1.4, checksum=0x00
```
