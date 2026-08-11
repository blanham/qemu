vc4_ss = ss.source_set()
vc4_ss.add(when: 'CONFIG_VC4_VPU', if_true: files('vc4_vpu.c'))

hw_arch += {'vc4': vc4_ss}
