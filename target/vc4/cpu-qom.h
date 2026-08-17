/*
 * VideoCore IV VPU QOM definitions
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#ifndef VC4_CPU_QOM_H
#define VC4_CPU_QOM_H

#include "hw/core/cpu.h"

typedef struct BCM2835VC4IntcState BCM2835VC4IntcState;

#define TYPE_VC4_CPU "vc4-cpu"

#define VC4_CPU_TYPE_SUFFIX "-" TYPE_VC4_CPU
#define VC4_CPU_TYPE_NAME(model) model VC4_CPU_TYPE_SUFFIX
#define TYPE_VC4_VPU_CPU VC4_CPU_TYPE_NAME("vpu")

#ifdef VC4_SECONDARY_FRONTEND
typedef struct VC4CPU VC4CPU;
typedef struct VC4CPUClass VC4CPUClass;
OBJECT_DECLARE_TYPE(VC4CPU, VC4CPUClass, VC4_CPU)
#else
OBJECT_DECLARE_CPU_TYPE(VC4CPU, VC4CPUClass, VC4_CPU)
#endif

void vc4_cpu_set_intc(CPUState *cs, BCM2835VC4IntcState *intc);

#endif /* VC4_CPU_QOM_H */
