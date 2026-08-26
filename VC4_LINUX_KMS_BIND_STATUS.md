# VC4 pinned Linux KMS frontier

Validation passed: **true**

Frontier: **`linux-vc4-kms-visible-scanout-clear`**

- Render submission preserved: `True`
- Native VC4 fbdev registered: `True`
- Native framebuffer write witnessed: `True`
- Native screenshot captured: `True`
- Native RGB quadrant scanout matched: `True`
- Native probe passed: `True`
- Flip-done timeouts: `0`
- Object commit-wait timeouts: `0`
- Generic commit timeout: `False`
- HDMI register wait timeouts: `0`
- CRTC discovered by witness: `True`
- Physical connector discovered: `True`
- Physical connector connected: `True`
- Display mode discovered: `True`
- Complete KMS topology: `True`

## Resource counts

- CRTCs: `None`
- Connector objects: `None`
- Physical connectors: `None`
- Connected physical connectors: `None`
- Modes on connected physical connectors: `None`

## Visible scanout samples

- `red`: RGB `[255, 0, 0]`, matched `True`
- `green`: RGB `[0, 255, 0]`, matched `True`
- `blue`: RGB `[0, 0, 255]`, matched `True`
- `white`: RGB `[255, 255, 255]`, matched `True`

## Bound VC4 components

- `3f400000.hvs` via `vc4_hvs_ops`
- `3f902000.hdmi` via `vc4_hdmi_ops`
- `3f004000.txp` via `vc4_txp_ops`
- `3f206000.pixelvalve` via `vc4_crtc_ops`
- `3f207000.pixelvalve` via `vc4_crtc_ops`
- `3f807000.pixelvalve` via `vc4_crtc_ops`
- `3fc00000.v3d` via `vc4_v3d_ops`
