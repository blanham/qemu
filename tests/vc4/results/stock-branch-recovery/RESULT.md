# VC4 stock-firmware branch recovery

- Candidate branches scored: `37`
- Selected branch: `agent/vc4-stock-bootcode-flow-current`
- Selected SHA: `00e745d77b26cba710bede1eb4edd56e48f4afc3`
- Dual-frontend build/regressions: `True`

The selected tip is published only after Python validation, `git diff --check`, both QEMU frontend builds, and every available VC4 P-register and multicore-sync smoke test.

## Highest static scores

- `agent/vc4-stock-bootcode-flow-current` — score `2270`, SHA `00e745d77b26`
- `agent/vc4-linux-framebuffer-integration` — score `2230`, SHA `16050bc66ae3`
- `agent/vc4-linux-framebuffer-bringup` — score `80`, SHA `c6d7fee142e4`
- `agent/vc4-linux-bringup` — score `60`, SHA `c6c699897411`
- `agent/vc4-linux-boot-frontier` — score `60`, SHA `28cc4b4d4c11`
- `agent/vc4-startelf-repair` — score `40`, SHA `6c740a831de0`
- `agent/vc4-startelf-next-stage` — score `40`, SHA `043aa526be75`
- `agent/vc4-startelf-loader-diagnosis` — score `40`, SHA `4d23de931cf3`
- `agent/vc4-startelf-dma-trace` — score `40`, SHA `8e69abf56f55`
- `agent/vc4-startelf-entry-direct-report` — score `40`, SHA `44dc5788c6e1`
- `agent/vc4-startelf-entry-probe` — score `40`, SHA `1929d15d70a5`
- `agent/vc4-startelf-read-frontier` — score `40`, SHA `a0a8a527e3fd`
- `agent/vc4-startelf-volume` — score `40`, SHA `a55113b3757c`
- `agent/vc4-startelf-reporter-cleanup` — score `40`, SHA `52e0c601f37b`
- `agent/vc4-startelf-hetero-probe` — score `40`, SHA `a868d68e6962`
