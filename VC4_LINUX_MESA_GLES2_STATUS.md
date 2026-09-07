# VC4 Linux Mesa GLES2 frontier

Validation passed: **false**

Harness valid: **true**

Frontier: **`vc4-v3d-unsupported-gl-array-primitive-0x21`**

- Module closure preserved: `True`
- Handwritten DRM submit preserved: `True`
- Mesa process started: `True`
- VC4 hardware frontier reached: `True`
- Last stage: `VC4_LINUX_MESA_GLES2_FINISH_START`
- Next missing stage: `VC4_LINUX_MESA_GLES2_FINISH_OK`
- Renderer: `VC4 V3D 2.1`
- GL version: `OpenGL ES 2.0 Mesa 24.0.2`
- Timed out: `True`
- Child exit: `124`
- Child signal: `None`
- Probe return code: `0`
- Shader/QPU witness lines: `91`

## First unsupported V3D packet

- Opcode: `0x21`
- Name: `gl-array-primitive`
- Command-list address: `0xc82be050`

## Bounded shader/QPU witness

```text
bcm2835-v3d: frontier primitive thread=0 packet=0x21 mode=4:triangles length=3 first=0 bin=1x1 flags=0x44 valid=1 alloc=0xf8201000+0x0007f000 state=0xf8200000
bcm2835-v3d: frontier shader record=0xc82be060 raw=0xc82be061 attrs=1 extended=0 flags=0x05 varyings=0 fs=0xc28c4000 fs-uniforms=0xc82be09c vs=0xc55a0000 vs-uniforms=0xc82be0a0 cs=0xc547e000 cs-uniforms=0xc82be0b0 vs-select=0x01 vs-size=8 cs-select=0x01 cs-size=8
bcm2835-v3d: frontier attribute index=0 address=0xf9200000 bytes=8 stride=8 vs-vpm=0 cs-vpm=0
bcm2835-v3d: frontier attribute-data index=0 vertex=0 address=0xf9200000 size=8 words=bf800000,bf800000,00000000,00000000
bcm2835-v3d: frontier attribute-data index=0 vertex=1 address=0xf9200008 size=8 words=40400000,bf800000,00000000,00000000
bcm2835-v3d: frontier attribute-data index=0 vertex=2 address=0xf9200010 size=8 words=bf800000,40400000,00000000,00000000
bcm2835-v3d: qpu frontier stage=fs index=0 address=0xc28c4000 word=0x100009e7009e7000 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=0 sf=0 ws=0 wa=39 wm=39 add=0 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=fs index=1 address=0xc28c4008 word=0x100009e7009e7000 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=0 sf=0 ws=0 wa=39 wm=39 add=0 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=fs index=2 address=0xc28c4010 word=0x10020ba715827d80 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=0 wa=46 wm=39 add=21 mul=0 ra=32 rb=39 aa=6 ab=6 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=fs index=3 address=0xc28c4018 word=0x300009e7009e7000 sig=3:program-end unpack=0 pm=0 pack=0 ca=0 cm=0 sf=0 ws=0 wa=39 wm=39 add=0 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=fs index=4 address=0xc28c4020 word=0x100009e7009e7000 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=0 sf=0 ws=0 wa=39 wm=39 add=0 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=fs index=5 address=0xc28c4028 word=0x500009e7009e7000 sig=5:scoreboard-unlock unpack=0 pm=0 pack=0 ca=0 cm=0 sf=0 ws=0 wa=39 wm=39 add=0 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: frontier uniform stage=fs index=0 address=0xc82be09c value=0xff2080df
bcm2835-v3d: frontier uniform stage=fs index=1 address=0xc82be0a0 value=0x3f800000
bcm2835-v3d: frontier uniform stage=fs index=2 address=0xc82be0a4 value=0x44000000
bcm2835-v3d: frontier uniform stage=fs index=3 address=0xc82be0a8 value=0xc4000000
bcm2835-v3d: frontier uniform stage=fs index=4 address=0xc82be0ac value=0x3f000000
bcm2835-v3d: frontier uniform stage=fs index=5 address=0xc82be0b0 value=0x3f800000
bcm2835-v3d: frontier uniform stage=fs index=6 address=0xc82be0b4 value=0x44000000
bcm2835-v3d: frontier uniform stage=fs index=7 address=0xc82be0b8 value=0xc4000000
bcm2835-v3d: frontier uniform stage=fs index=8 address=0xc82be0bc value=0x3f000000
bcm2835-v3d: frontier uniform stage=fs index=9 address=0xc82be0c0 value=0x00000000
bcm2835-v3d: frontier uniform stage=fs index=10 address=0xc82be0c4 value=0x00000000
bcm2835-v3d: frontier uniform stage=fs index=11 address=0xc82be0c8 value=0x00000000
bcm2835-v3d: frontier uniform stage=fs index=12 address=0xc82be0cc value=0x00000000
bcm2835-v3d: frontier uniform stage=fs index=13 address=0xc82be0d0 value=0x00000000
bcm2835-v3d: frontier uniform stage=fs index=14 address=0xc82be0d4 value=0x00000000
bcm2835-v3d: frontier uniform stage=fs index=15 address=0xc82be0d8 value=0x00000000
bcm2835-v3d: qpu frontier stage=vs index=0 address=0xc55a0000 word=0xd002102702821f80 sig=13:small-immediate unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=1 wa=0 wm=39 add=2 mul=0 ra=32 rb=33 aa=7 ab=6 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=vs index=1 address=0xc55a0008 word=0xe0024c6700201a00 sig=14:load-immediate unpack=0 pm=0 pack=0 ca=1 cm=1 sf=0 ws=0 wa=49 wm=39 add=0 mul=0 ra=8 rb=1 aa=5 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=vs index=2 address=0xc55a0010 word=0x100049e020c20037 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=1 sf=0 ws=0 wa=39 wm=32 add=0 mul=1 ra=48 rb=32 aa=0 ab=0 ma=6 mb=7
bcm2835-v3d: qpu frontier stage=vs index=3 address=0xc55a0018 word=0x100049e1209c0007 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=1 sf=0 ws=0 wa=39 wm=33 add=0 mul=1 ra=39 rb=0 aa=0 ab=0 ma=0 mb=7
bcm2835-v3d: qpu frontier stage=vs index=4 address=0xc55a0020 word=0x1012402227c20277 sig=1:none unpack=0 pm=0 pack=1 ca=1 cm=1 sf=0 ws=0 wa=0 wm=34 add=7 mul=1 ra=48 rb=32 aa=1 ab=1 ma=6 mb=7
bcm2835-v3d: qpu frontier stage=vs index=5 address=0xc55a0028 word=0x100049e3209c0017 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=1 sf=0 ws=0 wa=39 wm=35 add=0 mul=1 ra=39 rb=0 aa=0 ab=0 ma=2 mb=7
bcm2835-v3d: qpu frontier stage=vs index=6 address=0xc55a0030 word=0x10220027079e76c0 sig=1:none unpack=0 pm=0 pack=2 ca=1 cm=0 sf=0 ws=0 wa=0 wm=39 add=7 mul=0 ra=39 rb=39 aa=3 ab=3 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=vs index=7 address=0xc55a0038 word=0xe0025c6700001a00 sig=14:load-immediate unpack=0 pm=0 pack=0 ca=1 cm=1 sf=0 ws=1 wa=49 wm=39 add=0 mul=0 ra=0 rb=1 aa=5 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=vs index=8 address=0xc55a0040 word=0x10020c2715027d80 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=0 wa=48 wm=39 add=21 mul=0 ra=0 rb=39 aa=6 ab=6 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=vs index=9 address=0xc55a0048 word=0x10020c2715827d80 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=0 wa=48 wm=39 add=21 mul=0 ra=32 rb=39 aa=6 ab=6 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=vs index=10 address=0xc55a0050 word=0x10020c27159c0fc0 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=0 wa=48 wm=39 add=21 mul=0 ra=39 rb=0 aa=7 ab=7 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=vs index=11 address=0xc55a0058 word=0x300009e7009e7000 sig=3:program-end unpack=0 pm=0 pack=0 ca=0 cm=0 sf=0 ws=0 wa=39 wm=39 add=0 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=vs index=12 address=0xc55a0060 word=0x100009e7009e7000 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=0 sf=0 ws=0 wa=39 wm=39 add=0 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=vs index=13 address=0xc55a0068 word=0x100009e7009e7000 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=0 sf=0 ws=0 wa=39 wm=39 add=0 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: frontier uniform stage=vs index=0 address=0xc82be0a0 value=0x3f800000
bcm2835-v3d: frontier uniform stage=vs index=1 address=0xc82be0a4 value=0x44000000
bcm2835-v3d: frontier uniform stage=vs index=2 address=0xc82be0a8 value=0xc4000000
bcm2835-v3d: frontier uniform stage=vs index=3 address=0xc82be0ac value=0x3f000000
bcm2835-v3d: frontier uniform stage=vs index=4 address=0xc82be0b0 value=0x3f800000
bcm2835-v3d: frontier uniform stage=vs index=5 address=0xc82be0b4 value=0x44000000
bcm2835-v3d: frontier uniform stage=vs index=6 address=0xc82be0b8 value=0xc4000000
bcm2835-v3d: frontier uniform stage=vs index=7 address=0xc82be0bc value=0x3f000000
bcm2835-v3d: frontier uniform stage=vs index=8 address=0xc82be0c0 value=0x00000000
bcm2835-v3d: frontier uniform stage=vs index=9 address=0xc82be0c4 value=0x00000000
bcm2835-v3d: frontier uniform stage=vs index=10 address=0xc82be0c8 value=0x00000000
bcm2835-v3d: frontier uniform stage=vs index=11 address=0xc82be0cc value=0x00000000
bcm2835-v3d: frontier uniform stage=vs index=12 address=0xc82be0d0 value=0x00000000
bcm2835-v3d: frontier uniform stage=vs index=13 address=0xc82be0d4 value=0x00000000
bcm2835-v3d: frontier uniform stage=vs index=14 address=0xc82be0d8 value=0x00000000
bcm2835-v3d: frontier uniform stage=vs index=15 address=0xc82be0dc value=0x00000000
bcm2835-v3d: qpu frontier stage=cs index=0 address=0xc547e000 word=0xe0024c6700201a00 sig=14:load-immediate unpack=0 pm=0 pack=0 ca=1 cm=1 sf=0 ws=0 wa=49 wm=39 add=0 mul=0 ra=8 rb=1 aa=5 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=cs index=1 address=0xc547e008 word=0xe0025c6700001a00 sig=14:load-immediate unpack=0 pm=0 pack=0 ca=1 cm=1 sf=0 ws=1 wa=49 wm=39 add=0 mul=0 ra=0 rb=1 aa=5 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=cs index=2 address=0xc547e010 word=0xd002102702821f80 sig=13:small-immediate unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=1 wa=0 wm=39 add=2 mul=0 ra=32 rb=33 aa=7 ab=6 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=cs index=3 address=0xc547e018 word=0x1002086715c27d80 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=0 wa=33 wm=39 add=21 mul=0 ra=48 rb=39 aa=6 ab=6 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=cs index=4 address=0xc547e020 word=0x10024c233582724e sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=1 sf=0 ws=0 wa=48 wm=35 add=21 mul=1 ra=32 rb=39 aa=1 ab=1 ma=1 mb=6
bcm2835-v3d: qpu frontier stage=cs index=5 address=0xc547e028 word=0x100248a135c00d9f sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=1 sf=0 ws=0 wa=34 wm=33 add=21 mul=1 ra=48 rb=0 aa=6 ab=6 ma=3 mb=7
bcm2835-v3d: qpu frontier stage=cs index=6 address=0xc547e030 word=0x1012402027827256 sig=1:none unpack=0 pm=0 pack=1 ca=1 cm=1 sf=0 ws=0 wa=0 wm=32 add=7 mul=1 ra=32 rb=39 aa=1 ab=1 ma=2 mb=6
bcm2835-v3d: qpu frontier stage=cs index=7 address=0xc547e038 word=0x10024c20359c0487 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=1 sf=0 ws=0 wa=48 wm=32 add=21 mul=1 ra=39 rb=0 aa=2 ab=2 ma=0 mb=7
bcm2835-v3d: qpu frontier stage=cs index=8 address=0xc547e040 word=0xd0020c27159c0fc0 sig=13:small-immediate unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=0 wa=48 wm=39 add=21 mul=0 ra=39 rb=0 aa=7 ab=7 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=cs index=9 address=0xc547e048 word=0x10220027079e7000 sig=1:none unpack=0 pm=0 pack=2 ca=1 cm=0 sf=0 ws=0 wa=0 wm=39 add=7 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=cs index=10 address=0xc547e050 word=0xd0020c27159e0fc0 sig=13:small-immediate unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=0 wa=48 wm=39 add=21 mul=0 ra=39 rb=32 aa=7 ab=7 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=cs index=11 address=0xc547e058 word=0x10020c2715027d80 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=0 wa=48 wm=39 add=21 mul=0 ra=0 rb=39 aa=6 ab=6 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=cs index=12 address=0xc547e060 word=0x10020c2715827d80 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=0 wa=48 wm=39 add=21 mul=0 ra=32 rb=39 aa=6 ab=6 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=cs index=13 address=0xc547e068 word=0x10020c27159c0fc0 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=0 wa=48 wm=39 add=21 mul=0 ra=39 rb=0 aa=7 ab=7 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=cs index=14 address=0xc547e070 word=0x300009e7009e7000 sig=3:program-end unpack=0 pm=0 pack=0 ca=0 cm=0 sf=0 ws=0 wa=39 wm=39 add=0 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=cs index=15 address=0xc547e078 word=0x100009e7009e7000 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=0 sf=0 ws=0 wa=39 wm=39 add=0 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=cs index=16 address=0xc547e080 word=0x100009e7009e7000 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=0 sf=0 ws=0 wa=39 wm=39 add=0 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: frontier uniform stage=cs index=0 address=0xc82be0b0 value=0x3f800000
bcm2835-v3d: frontier uniform stage=cs index=1 address=0xc82be0b4 value=0x44000000
bcm2835-v3d: frontier uniform stage=cs index=2 address=0xc82be0b8 value=0xc4000000
bcm2835-v3d: frontier uniform stage=cs index=3 address=0xc82be0bc value=0x3f000000
bcm2835-v3d: frontier uniform stage=cs index=4 address=0xc82be0c0 value=0x00000000
bcm2835-v3d: frontier uniform stage=cs index=5 address=0xc82be0c4 value=0x00000000
bcm2835-v3d: frontier uniform stage=cs index=6 address=0xc82be0c8 value=0x00000000
bcm2835-v3d: frontier uniform stage=cs index=7 address=0xc82be0cc value=0x00000000
bcm2835-v3d: frontier uniform stage=cs index=8 address=0xc82be0d0 value=0x00000000
bcm2835-v3d: frontier uniform stage=cs index=9 address=0xc82be0d4 value=0x00000000
bcm2835-v3d: frontier uniform stage=cs index=10 address=0xc82be0d8 value=0x00000000
bcm2835-v3d: frontier uniform stage=cs index=11 address=0xc82be0dc value=0x00000000
bcm2835-v3d: frontier uniform stage=cs index=12 address=0xc82be0e0 value=0x00000000
bcm2835-v3d: frontier uniform stage=cs index=13 address=0xc82be0e4 value=0x00000000
bcm2835-v3d: frontier uniform stage=cs index=14 address=0xc82be0e8 value=0x00000000
bcm2835-v3d: frontier uniform stage=cs index=15 address=0xc82be0ec value=0x00000000
```

This gate runs a pinned Mesa VC4 Gallium driver inside the AArch64 guest. It requires a hardware VC4 renderer, compiles real GLES2 shaders, queues a full-surface triangle, waits for GPU completion, and verifies readback pixels. A non-clear classification is therefore the next concrete V3D/QPU contract rather than a synthetic packet guess.
