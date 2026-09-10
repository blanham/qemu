# VC4 Linux Mesa GLES2 frontier

Validation passed: **false**

Harness valid: **true**

Frontier: **`vc4-mesa-gles2-readpixels-start-timeout`**

- Module closure preserved: `True`
- Handwritten DRM submit preserved: `True`
- Mesa process started: `True`
- VC4 hardware frontier reached: `True`
- Last stage: `VC4_LINUX_MESA_GLES2_READPIXELS_START`
- Next missing stage: `VC4_LINUX_MESA_GLES2_READPIXELS_OK`
- Renderer: `VC4 V3D 2.1`
- GL version: `OpenGL ES 2.0 Mesa 24.0.2`
- Timed out: `True`
- Child exit: `124`
- Child signal: `None`
- Probe return code: `0`
- Shader/QPU witness lines: `119`

## Bounded shader/QPU witness

```text
bcm2835-v3d: frontier primitive thread=0 packet=0x21 mode=6:triangle-fan length=4 first=0 bin=1x1 flags=0x44 valid=1 alloc=0xf8201000+0x0007f000 state=0xf8200000
bcm2835-v3d: frontier shader record=0xc5670060 raw=0xc5670062 attrs=2 extended=0 flags=0x04 varyings=2 fs=0xc8277000 fs-uniforms=0xc56700a8 vs=0xc22a1000 vs-uniforms=0xc56700b0 cs=0xc55ae000 cs-uniforms=0xc56700c0 vs-select=0x03 vs-size=24 cs-select=0x01 cs-size=16
bcm2835-v3d: frontier attribute index=0 address=0xf9200018 bytes=16 stride=32 vs-vpm=0 cs-vpm=0
bcm2835-v3d: frontier attribute-data index=0 vertex=0 address=0xf9200018 size=16 words=bf800000,bf800000,00000000,3f800000
bcm2835-v3d: frontier attribute-data index=0 vertex=1 address=0xf9200038 size=16 words=3f800000,bf800000,00000000,3f800000
bcm2835-v3d: frontier attribute-data index=0 vertex=2 address=0xf9200058 size=16 words=3f800000,3f800000,00000000,3f800000
bcm2835-v3d: frontier attribute index=1 address=0xf9200028 bytes=16 stride=32 vs-vpm=16 cs-vpm=16
bcm2835-v3d: frontier attribute-data index=1 vertex=0 address=0xf9200028 size=16 words=3c800000,3f7c0000,00000000,00000000
bcm2835-v3d: frontier attribute-data index=1 vertex=1 address=0xf9200048 size=16 words=3d000000,3f7c0000,00000000,00000000
bcm2835-v3d: frontier attribute-data index=1 vertex=2 address=0xf9200068 size=16 words=3d000000,3f780000,00000000,00000000
bcm2835-v3d: qpu frontier stage=fs index=0 address=0xc8277000 word=0x100049e0203e303e sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=1 sf=0 ws=0 wa=39 wm=32 add=0 mul=1 ra=15 rb=35 aa=0 ab=0 ma=7 mb=6
bcm2835-v3d: qpu frontier stage=fs index=1 address=0xc8277008 word=0x100248e1213e317e sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=1 sf=0 ws=0 wa=35 wm=33 add=1 mul=1 ra=15 rb=35 aa=0 ab=5 ma=7 mb=6
bcm2835-v3d: qpu frontier stage=fs index=2 address=0xc8277010 word=0x600208a7019e7340 sig=6:last-thread-switch unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=0 wa=34 wm=39 add=1 mul=0 ra=39 rb=39 aa=1 ab=5 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=fs index=3 address=0xc8277018 word=0x10021e67159e7480 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=1 wa=57 wm=39 add=21 mul=0 ra=39 rb=39 aa=2 ab=2 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=fs index=4 address=0xc8277020 word=0x10021e27159e76c0 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=1 wa=56 wm=39 add=21 mul=0 ra=39 rb=39 aa=3 ab=3 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=fs index=5 address=0xc8277028 word=0xa00009e7009e7000 sig=10:tmu0-load unpack=0 pm=0 pack=0 ca=0 cm=0 sf=0 ws=0 wa=39 wm=39 add=0 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=fs index=6 address=0xc8277030 word=0x1d020867049e7900 sig=1:none unpack=6 pm=1 pack=0 ca=1 cm=0 sf=0 ws=0 wa=33 wm=39 add=4 mul=0 ra=39 rb=39 aa=4 ab=4 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=fs index=7 address=0xc8277038 word=0x1b424821849e7909 sig=1:none unpack=5 pm=1 pack=4 ca=1 cm=1 sf=0 ws=0 wa=32 wm=33 add=4 mul=4 ra=39 rb=39 aa=4 ab=4 ma=1 mb=1
bcm2835-v3d: qpu frontier stage=fs index=8 address=0xc8277040 word=0x195248e1849e7900 sig=1:none unpack=4 pm=1 pack=5 ca=1 cm=1 sf=0 ws=0 wa=35 wm=33 add=4 mul=4 ra=39 rb=39 aa=4 ab=4 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=fs index=9 address=0xc8277048 word=0x1f6248a1849e791b sig=1:none unpack=7 pm=1 pack=6 ca=1 cm=1 sf=0 ws=0 wa=34 wm=33 add=4 mul=4 ra=39 rb=39 aa=4 ab=4 ma=3 mb=3
bcm2835-v3d: qpu frontier stage=fs index=10 address=0xc8277050 word=0x117049e1809e7012 sig=1:none unpack=0 pm=1 pack=7 ca=0 cm=1 sf=0 ws=0 wa=39 wm=33 add=0 mul=4 ra=39 rb=39 aa=0 ab=0 ma=2 mb=2
bcm2835-v3d: qpu frontier stage=fs index=11 address=0xc8277058 word=0x10020ba7159e7240 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=0 wa=46 wm=39 add=21 mul=0 ra=39 rb=39 aa=1 ab=1 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=fs index=12 address=0xc8277060 word=0x300009e7009e7000 sig=3:program-end unpack=0 pm=0 pack=0 ca=0 cm=0 sf=0 ws=0 wa=39 wm=39 add=0 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=fs index=13 address=0xc8277068 word=0x100009e7009e7000 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=0 sf=0 ws=0 wa=39 wm=39 add=0 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=fs index=14 address=0xc8277070 word=0x500009e7009e7000 sig=5:scoreboard-unlock unpack=0 pm=0 pack=0 ca=0 cm=0 sf=0 ws=0 wa=39 wm=39 add=0 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: frontier uniform stage=fs index=0 address=0xc56700a8 value=0xf8094000
bcm2835-v3d: frontier uniform stage=fs index=1 address=0xc56700ac value=0x040040a5
bcm2835-v3d: frontier uniform stage=fs index=2 address=0xc56700b0 value=0x41000000
bcm2835-v3d: frontier uniform stage=fs index=3 address=0xc56700b4 value=0x41000000
bcm2835-v3d: frontier uniform stage=fs index=4 address=0xc56700b8 value=0x3f800000
bcm2835-v3d: frontier uniform stage=fs index=5 address=0xc56700bc value=0x00000000
bcm2835-v3d: frontier uniform stage=fs index=6 address=0xc56700c0 value=0x41000000
bcm2835-v3d: frontier uniform stage=fs index=7 address=0xc56700c4 value=0x41000000
bcm2835-v3d: frontier uniform stage=fs index=8 address=0xc56700c8 value=0x3f800000
bcm2835-v3d: frontier uniform stage=fs index=9 address=0xc56700cc value=0x00000000
bcm2835-v3d: frontier uniform stage=fs index=10 address=0xc56700d0 value=0x00000000
bcm2835-v3d: frontier uniform stage=fs index=11 address=0xc56700d4 value=0x00000000
bcm2835-v3d: frontier uniform stage=fs index=12 address=0xc56700d8 value=0x00000000
bcm2835-v3d: frontier uniform stage=fs index=13 address=0xc56700dc value=0x00000000
bcm2835-v3d: frontier uniform stage=fs index=14 address=0xc56700e0 value=0x00000000
bcm2835-v3d: frontier uniform stage=fs index=15 address=0xc56700e4 value=0x00000000
bcm2835-v3d: qpu frontier stage=vs index=0 address=0xc22a1000 word=0xe0024c6700601a00 sig=14:load-immediate unpack=0 pm=0 pack=0 ca=1 cm=1 sf=0 ws=0 wa=49 wm=39 add=0 mul=0 ra=24 rb=1 aa=5 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=vs index=1 address=0xc22a1008 word=0x100049e020c20037 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=1 sf=0 ws=0 wa=39 wm=32 add=0 mul=1 ra=48 rb=32 aa=0 ab=0 ma=6 mb=7
bcm2835-v3d: qpu frontier stage=vs index=2 address=0xc22a1010 word=0x100049c020c20037 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=1 sf=0 ws=0 wa=39 wm=0 add=0 mul=1 ra=48 rb=32 aa=0 ab=0 ma=6 mb=7
bcm2835-v3d: qpu frontier stage=vs index=3 address=0xc22a1018 word=0x100059c120c20037 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=1 sf=0 ws=1 wa=39 wm=1 add=0 mul=1 ra=48 rb=32 aa=0 ab=0 ma=6 mb=7
bcm2835-v3d: qpu frontier stage=vs index=4 address=0xc22a1020 word=0x1002086715c27d80 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=0 wa=33 wm=39 add=21 mul=0 ra=48 rb=39 aa=6 ab=6 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=vs index=5 address=0xc22a1028 word=0x10021d27159e7240 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=1 wa=52 wm=39 add=21 mul=0 ra=39 rb=39 aa=1 ab=1 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=vs index=6 address=0xc22a1030 word=0xe0025c6700001a00 sig=14:load-immediate unpack=0 pm=0 pack=0 ca=1 cm=1 sf=0 ws=1 wa=49 wm=39 add=0 mul=0 ra=0 rb=1 aa=5 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=vs index=7 address=0xc22a1038 word=0x100009e7009e7000 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=0 sf=0 ws=0 wa=39 wm=39 add=0 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=vs index=8 address=0xc22a1040 word=0x100049e2209e700c sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=1 sf=0 ws=0 wa=39 wm=34 add=0 mul=1 ra=39 rb=39 aa=0 ab=0 ma=1 mb=4
bcm2835-v3d: qpu frontier stage=vs index=9 address=0xc22a1048 word=0xd00208e7029e1e80 sig=13:small-immediate unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=0 wa=35 wm=39 add=2 mul=0 ra=39 rb=33 aa=7 ab=2 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=vs index=10 address=0xc22a1050 word=0x100049e1209e7023 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=1 sf=0 ws=0 wa=39 wm=33 add=0 mul=1 ra=39 rb=39 aa=0 ab=0 ma=4 mb=3
bcm2835-v3d: qpu frontier stage=vs index=11 address=0xc22a1058 word=0x100049e2209e7001 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=1 sf=0 ws=0 wa=39 wm=34 add=0 mul=1 ra=39 rb=39 aa=0 ab=0 ma=0 mb=1
bcm2835-v3d: qpu frontier stage=vs index=12 address=0xc22a1060 word=0x10124023279c04b9 sig=1:none unpack=0 pm=0 pack=1 ca=1 cm=1 sf=0 ws=0 wa=0 wm=35 add=7 mul=1 ra=39 rb=0 aa=2 ab=2 ma=7 mb=1
bcm2835-v3d: qpu frontier stage=vs index=13 address=0xc22a1068 word=0x10224020270676f1 sig=1:none unpack=0 pm=0 pack=2 ca=1 cm=1 sf=0 ws=0 wa=0 wm=32 add=7 mul=1 ra=1 rb=39 aa=3 ab=3 ma=6 mb=1
bcm2835-v3d: qpu frontier stage=vs index=14 address=0xc22a1070 word=0x100009e7009e7000 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=0 sf=0 ws=0 wa=39 wm=39 add=0 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=vs index=15 address=0xc22a1078 word=0x10020c2715027d80 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=0 wa=48 wm=39 add=21 mul=0 ra=0 rb=39 aa=6 ab=6 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=vs index=16 address=0xc22a1080 word=0x10024c2081c201f6 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=1 sf=0 ws=0 wa=48 wm=32 add=1 mul=4 ra=48 rb=32 aa=0 ab=7 ma=6 mb=6
bcm2835-v3d: qpu frontier stage=vs index=17 address=0xc22a1088 word=0x1002487095c27d89 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=1 sf=0 ws=0 wa=33 wm=48 add=21 mul=4 ra=48 rb=39 aa=6 ab=6 ma=1 mb=1
bcm2835-v3d: qpu frontier stage=vs index=18 address=0xc22a1090 word=0x10020c27159e7000 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=0 wa=48 wm=39 add=21 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=vs index=19 address=0xc22a1098 word=0x10020c27159e7240 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=0 wa=48 wm=39 add=21 mul=0 ra=39 rb=39 aa=1 ab=1 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=vs index=20 address=0xc22a10a0 word=0x300009e7009e7000 sig=3:program-end unpack=0 pm=0 pack=0 ca=0 cm=0 sf=0 ws=0 wa=39 wm=39 add=0 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=vs index=21 address=0xc22a10a8 word=0x100009e7009e7000 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=0 sf=0 ws=0 wa=39 wm=39 add=0 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=vs index=22 address=0xc22a10b0 word=0x100009e7009e7000 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=0 sf=0 ws=0 wa=39 wm=39 add=0 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: frontier uniform stage=vs index=0 address=0xc56700b0 value=0x41000000
bcm2835-v3d: frontier uniform stage=vs index=1 address=0xc56700b4 value=0x41000000
bcm2835-v3d: frontier uniform stage=vs index=2 address=0xc56700b8 value=0x3f800000
bcm2835-v3d: frontier uniform stage=vs index=3 address=0xc56700bc value=0x00000000
bcm2835-v3d: frontier uniform stage=vs index=4 address=0xc56700c0 value=0x41000000
bcm2835-v3d: frontier uniform stage=vs index=5 address=0xc56700c4 value=0x41000000
bcm2835-v3d: frontier uniform stage=vs index=6 address=0xc56700c8 value=0x3f800000
bcm2835-v3d: frontier uniform stage=vs index=7 address=0xc56700cc value=0x00000000
bcm2835-v3d: frontier uniform stage=vs index=8 address=0xc56700d0 value=0x00000000
bcm2835-v3d: frontier uniform stage=vs index=9 address=0xc56700d4 value=0x00000000
bcm2835-v3d: frontier uniform stage=vs index=10 address=0xc56700d8 value=0x00000000
bcm2835-v3d: frontier uniform stage=vs index=11 address=0xc56700dc value=0x00000000
bcm2835-v3d: frontier uniform stage=vs index=12 address=0xc56700e0 value=0x00000000
bcm2835-v3d: frontier uniform stage=vs index=13 address=0xc56700e4 value=0x00000000
bcm2835-v3d: frontier uniform stage=vs index=14 address=0xc56700e8 value=0x00000000
bcm2835-v3d: frontier uniform stage=vs index=15 address=0xc56700ec value=0x00000000
bcm2835-v3d: qpu frontier stage=cs index=0 address=0xc55ae000 word=0xe0024c6700401a00 sig=14:load-immediate unpack=0 pm=0 pack=0 ca=1 cm=1 sf=0 ws=0 wa=49 wm=39 add=0 mul=0 ra=16 rb=1 aa=5 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=cs index=1 address=0xc55ae008 word=0x100208e715c27d80 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=0 wa=35 wm=39 add=21 mul=0 ra=48 rb=39 aa=6 ab=6 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=cs index=2 address=0xc55ae010 word=0x100208a715c27d80 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=0 wa=34 wm=39 add=21 mul=0 ra=48 rb=39 aa=6 ab=6 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=cs index=3 address=0xc55ae018 word=0x1002006715c27d80 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=0 wa=1 wm=39 add=21 mul=0 ra=48 rb=39 aa=6 ab=6 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=cs index=4 address=0xc55ae020 word=0xe0025c6700001a00 sig=14:load-immediate unpack=0 pm=0 pack=0 ca=1 cm=1 sf=0 ws=1 wa=49 wm=39 add=0 mul=0 ra=0 rb=1 aa=5 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=cs index=5 address=0xc55ae028 word=0x10024c2095c276f6 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=1 sf=0 ws=0 wa=48 wm=32 add=21 mul=4 ra=48 rb=39 aa=3 ab=3 ma=6 mb=6
bcm2835-v3d: qpu frontier stage=cs index=6 address=0xc55ae030 word=0x10024c34959e7480 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=1 sf=0 ws=0 wa=48 wm=52 add=21 mul=4 ra=39 rb=39 aa=2 ab=2 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=cs index=7 address=0xc55ae038 word=0x10024c2235060d97 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=1 sf=0 ws=0 wa=48 wm=34 add=21 mul=1 ra=1 rb=32 aa=6 ab=6 ma=2 mb=7
bcm2835-v3d: qpu frontier stage=cs index=8 address=0xc55ae040 word=0x10020c27159e7000 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=0 wa=48 wm=39 add=21 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=cs index=9 address=0xc55ae048 word=0x100049e0209e7004 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=1 sf=0 ws=0 wa=39 wm=32 add=0 mul=1 ra=39 rb=39 aa=0 ab=0 ma=0 mb=4
bcm2835-v3d: qpu frontier stage=cs index=10 address=0xc55ae050 word=0xd0020867029e1e00 sig=13:small-immediate unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=0 wa=33 wm=39 add=2 mul=0 ra=39 rb=33 aa=7 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=cs index=11 address=0xc55ae058 word=0x100049c0209e7021 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=1 sf=0 ws=0 wa=39 wm=0 add=0 mul=1 ra=39 rb=39 aa=0 ab=0 ma=4 mb=1
bcm2835-v3d: qpu frontier stage=cs index=12 address=0xc55ae060 word=0x100049e02082701e sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=1 sf=0 ws=0 wa=39 wm=32 add=0 mul=1 ra=32 rb=39 aa=0 ab=0 ma=3 mb=6
bcm2835-v3d: qpu frontier stage=cs index=13 address=0xc55ae068 word=0x100049e1209c0007 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=1 sf=0 ws=0 wa=39 wm=33 add=0 mul=1 ra=39 rb=0 aa=0 ab=0 ma=0 mb=7
bcm2835-v3d: qpu frontier stage=cs index=14 address=0xc55ae070 word=0x10124023279c0257 sig=1:none unpack=0 pm=0 pack=1 ca=1 cm=1 sf=0 ws=0 wa=0 wm=35 add=7 mul=1 ra=39 rb=0 aa=1 ab=1 ma=2 mb=7
bcm2835-v3d: qpu frontier stage=cs index=15 address=0xc55ae078 word=0x10224022270606f7 sig=1:none unpack=0 pm=0 pack=2 ca=1 cm=1 sf=0 ws=0 wa=0 wm=34 add=7 mul=1 ra=1 rb=32 aa=3 ab=3 ma=6 mb=7
bcm2835-v3d: qpu frontier stage=cs index=16 address=0xc55ae080 word=0x100049e3209c0017 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=1 sf=0 ws=0 wa=39 wm=35 add=0 mul=1 ra=39 rb=0 aa=0 ab=0 ma=2 mb=7
bcm2835-v3d: qpu frontier stage=cs index=17 address=0xc55ae088 word=0x10020c2715027d80 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=0 wa=48 wm=39 add=21 mul=0 ra=0 rb=39 aa=6 ab=6 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=cs index=18 address=0xc55ae090 word=0x10020c2701827780 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=0 wa=48 wm=39 add=1 mul=0 ra=32 rb=39 aa=3 ab=6 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=cs index=19 address=0xc55ae098 word=0x10020c27159c0fc0 sig=1:none unpack=0 pm=0 pack=0 ca=1 cm=0 sf=0 ws=0 wa=48 wm=39 add=21 mul=0 ra=39 rb=0 aa=7 ab=7 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=cs index=20 address=0xc55ae0a0 word=0x300009e7009e7000 sig=3:program-end unpack=0 pm=0 pack=0 ca=0 cm=0 sf=0 ws=0 wa=39 wm=39 add=0 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=cs index=21 address=0xc55ae0a8 word=0x100009e7009e7000 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=0 sf=0 ws=0 wa=39 wm=39 add=0 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: qpu frontier stage=cs index=22 address=0xc55ae0b0 word=0x100009e7009e7000 sig=1:none unpack=0 pm=0 pack=0 ca=0 cm=0 sf=0 ws=0 wa=39 wm=39 add=0 mul=0 ra=39 rb=39 aa=0 ab=0 ma=0 mb=0
bcm2835-v3d: frontier uniform stage=cs index=0 address=0xc56700c0 value=0x41000000
bcm2835-v3d: frontier uniform stage=cs index=1 address=0xc56700c4 value=0x41000000
bcm2835-v3d: frontier uniform stage=cs index=2 address=0xc56700c8 value=0x3f800000
bcm2835-v3d: frontier uniform stage=cs index=3 address=0xc56700cc value=0x00000000
bcm2835-v3d: frontier uniform stage=cs index=4 address=0xc56700d0 value=0x00000000
bcm2835-v3d: frontier uniform stage=cs index=5 address=0xc56700d4 value=0x00000000
bcm2835-v3d: frontier uniform stage=cs index=6 address=0xc56700d8 value=0x00000000
bcm2835-v3d: frontier uniform stage=cs index=7 address=0xc56700dc value=0x00000000
bcm2835-v3d: frontier uniform stage=cs index=8 address=0xc56700e0 value=0x00000000
bcm2835-v3d: frontier uniform stage=cs index=9 address=0xc56700e4 value=0x00000000
bcm2835-v3d: frontier uniform stage=cs index=10 address=0xc56700e8 value=0x00000000
bcm2835-v3d: frontier uniform stage=cs index=11 address=0xc56700ec value=0x00000000
bcm2835-v3d: frontier uniform stage=cs index=12 address=0xc56700f0 value=0x00000000
bcm2835-v3d: frontier uniform stage=cs index=13 address=0xc56700f4 value=0x00000000
bcm2835-v3d: frontier uniform stage=cs index=14 address=0xc56700f8 value=0x00000000
bcm2835-v3d: frontier uniform stage=cs index=15 address=0xc56700fc value=0x00000000
```

This gate runs a pinned Mesa VC4 Gallium driver inside the AArch64 guest. It requires a hardware VC4 renderer, compiles real GLES2 shaders, queues a full-surface triangle, waits for GPU completion, and verifies readback pixels. A non-clear classification is therefore the next concrete V3D/QPU contract rather than a synthetic packet guess.
