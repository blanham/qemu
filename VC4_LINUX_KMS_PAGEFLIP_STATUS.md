# VC4 inherited-master native KMS page flip

Validation passed: **true**

Frontier: **`linux-vc4-kms-pageflip-visual-clear`**

- Probe return code: `0`
- DDC supplier root present: `True`
- Module closure loaded: `True`
- Native topology clear: `True`
- Existing render submission preserved: `True`
- Initial modeset completed: `True`
- Active CRTC inherited: `True`
- Page flip queued: `True`
- Flip-complete event received: `True`
- GETCRTC reports the new FB: `True`
- Visual-ready hold reached: `True`
- Exact flipped pixels verified: `True`
- Page-flip witness completed: `True`
- Timeout: `False`
- Failure stage: `None`
- Failure errno: `None`

## Exact image witness

- Pattern: `vc4-native-kms-pageflip-xrgb8888-v1`
- Dimensions: `1280x800`
- Total pixels: `1024000`
- Mismatched pixels: `0`
- Maximum channel error: `0`
- Matching fraction: `1.0`
- SHA-256: `4ca981426505ccbd8d880da71ac1d582d5cdf95607a1ab8fd00f4b4f46e4a7c5`

The modeset child leaves the first framebuffer on the shared drm_file. A second child creates a distinct framebuffer, queues DRM_IOCTL_MODE_PAGE_FLIP with the event flag, consumes DRM_EVENT_FLIP_COMPLETE, and checks that GETCRTC exposes the new framebuffer. The host stops QEMU at the visual-ready marker and requires every captured XRGB8888 pixel to match the deterministic page-flip pattern.
