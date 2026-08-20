# VC4 heterogeneous Linux/framebuffer control proof

Source commit: `dac6323557f8b3535c7d6bac1c5dbfffc9a1eb01`

Workflow run: `32353720694`

Classification: `hetero-linux-framebuffer`

The final bare-metal and Linux host probes both passed. Those probes jointly require property-mailbox completion, guest framebuffer-RAM samples, and matching QMP display scanout. The Linux probe additionally requires the pinned kernel to reach the deterministic `/init` and paint `/dev/fb0`.
