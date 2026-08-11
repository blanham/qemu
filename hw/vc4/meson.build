vc4_ss = ss.source_set()
vc4_ss.add(when: 'CONFIG_VC4_VPU', if_true: files(
  'vc4_vpu.c',
  'bcm2835_vc4_intc.c',
))
vc4_ss.add(when: 'CONFIG_VC4_RASPI3', if_true: files(
  'raspi3_vpu.c',
  '../arm/bcm2835_peripherals.c',
))

hw_arch += {'vc4': vc4_ss}
