/*
 * VideoCore IV VPU QOM definitions
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#ifndef VC4_CPU_QOM_H
#define VC4_CPU_QOM_H

#include "hw/core/cpu.h"

#define TYPE_VC4_CPU "vc4-cpu"

#define VC4_CPU_TYPE_SUFFIX "-" TYPE_VC4_CPU
#define VC4_CPU_TYPE_NAME(model) model VC4_CPU_TYPE_SUFFIX
#define TYPE_VC4_VPU_CPU VC4_CPU_TYPE_NAME("vpu")

OBJECT_DECLARE_CPU_TYPE(VC4CPU, VC4CPUClass, VC4_CPU)

#endif /* VC4_CPU_QOM_H */
