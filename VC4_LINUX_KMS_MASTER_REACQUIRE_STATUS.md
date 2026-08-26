# VC4 explicit DRM-master handoff, modeset, and page flip

Validation passed: **true**

Frontier: **`linux-vc4-kms-explicit-master-handoff-modeset-pageflip-visual-clear`**

- Probe return code: `0`
- DDC supplier root present: `True`
- Module closure loaded: `True`
- Native topology clear: `True`
- Existing render submission preserved: `True`
- Initial inherited-file modeset completed: `True`
- Inherited-file page flip completed: `True`
- Child closed inherited descriptor: `True`
- Child opened card0 before the drop: `True`
- Pre-drop SET_MASTER returned EBUSY: `True`
- Child reached the handoff gate: `True`
- Original drm_file dropped master: `True`
- Same new drm_file acquired master: `True`
- New drm_file selected connector/mode: `True`
- Pre-modeset CRTC state read: `True`
- Independent modeset dumb buffer created: `True`
- Independent modeset dumb buffer mapped: `True`
- Independent modeset framebuffer created: `True`
- Independent SETCRTC started: `True`
- Independent SETCRTC completed: `True`
- GETCRTC verified independent modeset: `True`
- Independent modeset witness completed: `True`
- Page-flip dumb buffer created: `True`
- Page-flip dumb buffer mapped: `True`
- Page-flip framebuffer created: `True`
- Independent page-flip ioctl started: `True`
- Independent page flip queued: `True`
- Flip-complete event received: `True`
- GETCRTC reports flipped framebuffer: `True`
- Visual-ready hold reached: `True`
- Exact final pixels verified: `True`
- Child explicitly dropped master: `True`
- Child witness completed: `True`
- Original drm_file reacquired master: `True`
- Runtime reported handoff order: `True`
- Recorded marker order is valid: `True`
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

The child opens the primary DRM node while PID 1 still owns master and proves that SET_MASTER fails with EBUSY. PID 1 then drops master, and the same already-open child drm_file must explicitly acquire it. That new file independently enumerates a connector and mode, creates a first framebuffer, programs SETCRTC, and verifies the resulting CRTC state. It then creates a second framebuffer, queues an event-driven page flip, consumes DRM_EVENT_FLIP_COMPLETE, and verifies both GETCRTC and every captured XRGB8888 pixel. The child drops master before exiting, and PID 1 must reacquire it before the render witness continues.
