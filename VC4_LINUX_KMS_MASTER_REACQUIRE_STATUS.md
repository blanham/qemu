# VC4 independent DRM-master reacquisition

Validation passed: **true**

Frontier: **`linux-vc4-kms-independent-master-modeset-pageflip-visual-clear`**

- Probe return code: `0`
- DDC supplier root present: `True`
- Module closure loaded: `True`
- Native topology clear: `True`
- Existing render submission preserved: `True`
- Initial modeset completed: `True`
- Inherited-file page flip completed: `True`
- Original drm_file dropped master: `True`
- Child closed the inherited descriptor: `True`
- Child reopened card0: `True`
- New drm_file acquired master: `True`
- Connector and mode selected on new file: `True`
- Baseline CRTC state read: `True`
- New drm_file programmed SETCRTC: `True`
- GETCRTC reports its initial FB: `True`
- Independent-file modeset completed: `True`
- Active CRTC reflects the new file: `True`
- Independent-file page flip queued: `True`
- Flip-complete event received: `True`
- GETCRTC reports the new FB: `True`
- Visual-ready hold reached: `True`
- Exact reacquired-master pixels verified: `True`
- Child explicitly dropped master: `True`
- Reacquisition witness completed: `True`
- Original drm_file reacquired master: `True`
- Supervisor completed: `True`
- Timeout: `False`
- Failure stage: `None`
- Failure errno: `None`

## Exact image witness

- Pattern: `vc4-native-kms-master-reacquire-xrgb8888-v1`
- Dimensions: `1280x800`
- Total pixels: `1024000`
- Mismatched pixels: `0`
- Maximum channel error: `0`
- Matching fraction: `1.0`
- SHA-256: `a25ac477ac4beff66504925905fa056dea2a75b4c48b77e8f81d5c8e80c33aa8`

After the established inherited-file modeset and page-flip baseline, PID 1 drops DRM master on its original card file. A bounded child closes that inherited descriptor before reopening /dev/dri/card0, explicitly acquires master on the new drm_file, enumerates the connector and mode, creates an initial framebuffer, programs it with DRM_IOCTL_MODE_SETCRTC, and verifies GETCRTC. It then creates a second framebuffer, queues an event-driven page flip, consumes DRM_EVENT_FLIP_COMPLETE, and verifies the final framebuffer ID. The host freezes QEMU at the independent-file visual-ready marker and requires every captured XRGB8888 pixel to match the final pattern. The child then drops master and exits, after which the original drm_file must reacquire master before the render witness continues.
