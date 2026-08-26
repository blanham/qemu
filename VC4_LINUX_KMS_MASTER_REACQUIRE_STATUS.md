# VC4 explicit DRM-master handoff and atomic primary-plane flip

Validation passed: **false**

Frontier: **`vc4-kms-master-handoff-atomic-getresources-ids`**

- Probe return code: `1`
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
- Legacy flip-complete event received: `True`
- GETCRTC reports legacy-flipped framebuffer: `True`
- Atomic client capability enabled: `True`
- Active primary plane identified: `False`
- Active CRTC property identified: `False`
- Atomic replacement dumb buffer created: `False`
- Atomic replacement dumb buffer mapped: `False`
- Atomic replacement framebuffer created: `False`
- Atomic TEST_ONLY commit completed: `False`
- Atomic primary-plane ioctl started: `False`
- Atomic primary-plane update queued: `False`
- Atomic flip-complete event received: `False`
- GETCRTC reports atomic framebuffer: `False`
- Atomic visual-ready hold reached: `False`
- Atomic primary-plane witness completed: `False`
- Visual-ready hold reached: `False`
- Exact final pixels verified: `False`
- Child explicitly dropped master: `False`
- Child witness completed: `False`
- Original drm_file reacquired master: `True`
- Runtime reported handoff order: `False`
- Recorded marker order is valid: `False`
- Supervisor completed: `False`
- Timeout: `False`
- Failure stage: `atomic-getresources-ids`
- Failure errno: `14`

The child opens the primary DRM node while PID 1 still owns master and proves that SET_MASTER fails with EBUSY. PID 1 then drops master, and the same already-open child drm_file must explicitly acquire it. That new file independently enumerates a connector and mode, creates a first framebuffer, programs SETCRTC, and verifies the resulting CRTC state. It then creates a second framebuffer and completes a legacy event-driven page flip. While still master on the same drm_file, it enables atomic UAPI, identifies the active primary plane and its FB_ID property, TEST_ONLY-validates a third framebuffer, then queues a nonblocking atomic primary-plane update with a flip-complete event. GETCRTC and every captured XRGB8888 pixel must expose that third buffer. The child drops master before exiting, and PID 1 must reacquire it before the render witness continues.
