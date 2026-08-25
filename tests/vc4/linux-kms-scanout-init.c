/*
 * Native VC4 KMS scanout witness.
 *
 * Reuse the pinned module, topology, UAPI, renderer, and framebuffer probes,
 * then keep a DRM dumb framebuffer active after a completed page flip so the
 * host-side QEMU capture can verify the display pipeline end to end.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#define main vc4_linux_v3d_modular_base_main
#include "linux-v3d-modular-init.c"
#undef main

#include "linux-kms-scanout-probe.inc.c"

int main(void)
{
    struct timespec settle = {
        .tv_sec = 2,
    };
    VC4DRMNode card;
    VC4DRMNode render;
    VC4DRMNode *selected = NULL;
    int module_result;
    int uapi_result = -1;
    int submit_result = -1;
    int kms_result = -1;
    int scanout_result = -1;
    int framebuffer_result;

    prepare_filesystems();
    marker("VC4_LINUX_INIT_OK\n");
    marker("VC4_LINUX_V3D_MODULAR_START\n");
    module_result = load_vc4_module_manifest();
    if (module_result == 0) {
        mark_module_success();
    }
    nanosleep(&settle, NULL);
    report_topology();

    card = open_drm_node("CARD0", "/dev/dri/card0");
    render = open_drm_node("RENDER128", "/dev/dri/renderD128");
    if (card.fd >= 0 && card.vc4) {
        kms_result = probe_kms_topology(&card);
    } else {
        marker("VC4_LINUX_KMS_FAILED stage=no-vc4-card\n");
    }
    if (kms_result == 0) {
        scanout_result = vc4_kms_scanout_supervise();
        if (scanout_result == 0) {
            marker("VC4_LINUX_KMS_SCANOUT_OK\n");
        } else {
            marker("VC4_LINUX_KMS_SCANOUT_PARTIAL\n");
        }
    } else {
        marker("VC4_LINUX_KMS_SCANOUT_SKIPPED topology-incomplete\n");
    }

    if (render.fd >= 0 && render.vc4) {
        selected = &render;
    } else if (card.fd >= 0 && card.vc4) {
        selected = &card;
    }
    if (selected != NULL) {
        mark_node_success(selected, &card, &render);
        uapi_result = probe_vc4_uapi(selected);
        if (uapi_result == 0) {
            mark_uapi_success();
            submit_result = submit_clear_job(selected);
            if (submit_result == 0) {
                mark_submit_success();
            }
        }
    } else {
        marker("VC4_LINUX_DRM_SUBMIT_SKIPPED no-vc4-node\n");
    }

    framebuffer_result = paint_framebuffer();
    report("VC4_LINUX_V3D_MODULAR_DONE modules=%d card0=%d render128=%d "
           "uapi=%d submit=%d kms=%d scanout=%d framebuffer=%d\n",
           module_result,
           card.fd >= 0 && card.vc4 ? 0 : -1,
           render.fd >= 0 && render.vc4 ? 0 : -1,
           uapi_result, submit_result, kms_result, scanout_result,
           framebuffer_result);
    marker("VC4_LINUX_V3D_MODULAR_DONE\n");
    if (module_result == 0 && submit_result == 0) {
        marker("VC4_LINUX_V3D_MODULAR_OK\n");
    } else {
        marker("VC4_LINUX_V3D_MODULAR_PARTIAL\n");
    }
    if (framebuffer_result == 0) {
        marker("VC4_LINUX_FB_OK\n");
    }

    if (render.fd >= 0) {
        close(render.fd);
    }
    if (card.fd >= 0) {
        close(card.fd);
    }
    marker("VC4_LINUX_INIT_IDLE\n");
    for (;;) {
        pause();
    }
}
