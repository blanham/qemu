/*
 * Linux VC4 DRM submit witness.
 *
 * The first probe owns the low-level initramfs, console, framebuffer, and
 * baseline UAPI machinery.  This translation unit adds an actual clear-only
 * DRM_IOCTL_VC4_SUBMIT_CL job using the distribution's canonical libdrm UAPI
 * header, then waits on and verifies the GPU-written destination BO.
 */
#define main vc4_linux_v3d_uapi_base_main
#include "linux-v3d-uapi-init.c"
#undef main

#include <drm.h>
#include <vc4_drm.h>

#ifndef VC4_LINUX_V3D_SUBMIT_ENTRY
#define VC4_LINUX_V3D_SUBMIT_ENTRY main
#endif

#define VC4_SUBMIT_WIDTH             64U
#define VC4_SUBMIT_HEIGHT            64U
#define VC4_SUBMIT_BYTES_PER_PIXEL   4U
#define VC4_SUBMIT_BO_SIZE           \
    (VC4_SUBMIT_WIDTH * VC4_SUBMIT_HEIGHT * VC4_SUBMIT_BYTES_PER_PIXEL)
#define VC4_SUBMIT_BACKGROUND        UINT32_C(0x11223344)
#define VC4_SUBMIT_CLEAR             UINT32_C(0x00a55aff)
#define VC4_SUBMIT_RENDER_BITS       UINT16_C(4)
#define VC4_SUBMIT_WAIT_NS           UINT64_C(5000000000)

static void submit_memory_barrier(void)
{
    __sync_synchronize();
}

static void initialize_rcl_surfaces(struct drm_vc4_submit_cl *submit)
{
    /*
     * The VC4 UAPI embeds the six RCL surfaces directly in submit_cl.  A
     * hindex of ~0 means that a surface is absent.  Start from that safe
     * state and enable only color_write below.
     */
    submit->color_read.hindex = UINT32_MAX;
    submit->color_write.hindex = UINT32_MAX;
    submit->zs_read.hindex = UINT32_MAX;
    submit->zs_write.hindex = UINT32_MAX;
    submit->msaa_color_write.hindex = UINT32_MAX;
    submit->msaa_zs_write.hindex = UINT32_MAX;
}

static int submit_clear_job(VC4DRMNode *node)
{
    struct drm_vc4_create_bo create = {
        .size = VC4_SUBMIT_BO_SIZE,
    };
    struct drm_vc4_mmap_bo map = { 0 };
    struct drm_vc4_submit_cl submit = { 0 };
    struct drm_vc4_wait_bo wait = {
        .timeout_ns = VC4_SUBMIT_WAIT_NS,
    };
    struct drm_gem_close close_bo = { 0 };
    volatile uint32_t *pixels = MAP_FAILED;
    uint32_t handles[1];
    size_t pixel_count = VC4_SUBMIT_WIDTH * VC4_SUBMIT_HEIGHT;
    int result = -1;

    marker("VC4_LINUX_DRM_SUBMIT_START\n");
    if (ioctl(node->fd, DRM_IOCTL_VC4_CREATE_BO, &create) < 0) {
        report("VC4_LINUX_DRM_SUBMIT_FAILED stage=create-bo errno=%d (%s)\n",
               errno, strerror(errno));
        return -1;
    }
    if (create.handle == 0) {
        marker("VC4_LINUX_DRM_SUBMIT_FAILED stage=create-bo-zero-handle\n");
        return -1;
    }
    handles[0] = create.handle;
    close_bo.handle = create.handle;
    map.handle = create.handle;
    if (ioctl(node->fd, DRM_IOCTL_VC4_MMAP_BO, &map) < 0) {
        report("VC4_LINUX_DRM_SUBMIT_FAILED stage=mmap-bo errno=%d (%s)\n",
               errno, strerror(errno));
        goto out;
    }
    pixels = mmap(NULL, VC4_SUBMIT_BO_SIZE, PROT_READ | PROT_WRITE,
                  MAP_SHARED, node->fd, (off_t)map.offset);
    if (pixels == MAP_FAILED) {
        report("VC4_LINUX_DRM_SUBMIT_FAILED stage=mmap errno=%d (%s)\n",
               errno, strerror(errno));
        goto out;
    }
    for (size_t index = 0; index < pixel_count; index++) {
        pixels[index] = VC4_SUBMIT_BACKGROUND;
    }
    submit_memory_barrier();
    (void)msync((void *)pixels, VC4_SUBMIT_BO_SIZE, MS_SYNC);

    initialize_rcl_surfaces(&submit);
    submit.bo_handles = (uintptr_t)handles;
    submit.bo_handle_count = 1;
    submit.width = VC4_SUBMIT_WIDTH;
    submit.height = VC4_SUBMIT_HEIGHT;
    submit.min_x_tile = 0;
    submit.min_y_tile = 0;
    submit.max_x_tile = 0;
    submit.max_y_tile = 0;
    submit.clear_color[0] = VC4_SUBMIT_CLEAR;
    submit.clear_color[1] = VC4_SUBMIT_CLEAR;
    submit.clear_z = UINT32_C(0x00ffffff);
    submit.clear_s = 0;
    submit.color_write.hindex = 0;
    submit.color_write.offset = 0;
    submit.color_write.bits = VC4_SUBMIT_RENDER_BITS;
    submit.color_write.flags = 0;
    submit.flags = VC4_SUBMIT_CL_USE_CLEAR_COLOR;

    if (ioctl(node->fd, DRM_IOCTL_VC4_SUBMIT_CL, &submit) < 0) {
        report("VC4_LINUX_DRM_SUBMIT_FAILED stage=submit-cl errno=%d (%s)\n",
               errno, strerror(errno));
        goto out;
    }
    report("VC4_LINUX_DRM_SUBMIT_CL_OK seqno=%llu handle=%u size=%u\n",
           (unsigned long long)submit.seqno, create.handle, create.size);

    wait.handle = create.handle;
    if (ioctl(node->fd, DRM_IOCTL_VC4_WAIT_BO, &wait) < 0) {
        report("VC4_LINUX_DRM_SUBMIT_FAILED stage=wait-bo errno=%d (%s)\n",
               errno, strerror(errno));
        goto out;
    }
    report("VC4_LINUX_DRM_SUBMIT_WAIT_OK handle=%u timeout_ns=%llu\n",
           wait.handle, (unsigned long long)wait.timeout_ns);
    submit_memory_barrier();

    report("VC4_LINUX_DRM_SUBMIT_SAMPLES first=0x%08x center=0x%08x last=0x%08x expected=0x%08x\n",
           pixels[0], pixels[pixel_count / 2], pixels[pixel_count - 1],
           VC4_SUBMIT_CLEAR);
    for (size_t index = 0; index < pixel_count; index++) {
        if (pixels[index] != VC4_SUBMIT_CLEAR) {
            report("VC4_LINUX_DRM_SUBMIT_FAILED stage=pixel-verify index=%zu actual=0x%08x expected=0x%08x\n",
                   index, pixels[index], VC4_SUBMIT_CLEAR);
            goto out;
        }
    }
    marker("VC4_LINUX_DRM_SUBMIT_PIXELS_OK\n");
    marker("VC4_LINUX_DRM_SUBMIT_OK\n");
    result = 0;

out:
    if (pixels != MAP_FAILED) {
        (void)munmap((void *)pixels, VC4_SUBMIT_BO_SIZE);
    }
    if (close_bo.handle != 0) {
        (void)ioctl(node->fd, DRM_IOCTL_GEM_CLOSE, &close_bo);
    }
    return result;
}

int VC4_LINUX_V3D_SUBMIT_ENTRY(void)
{
    VC4DRMNode card;
    VC4DRMNode render;
    VC4DRMNode *selected = NULL;
    int uapi_result = -1;
    int submit_result = -1;
    int framebuffer_result;

    prepare_filesystems();
    marker("VC4_LINUX_INIT_OK\n");
    marker("VC4_LINUX_DRM_SUBMIT_PROBE_START\n");
    report_topology();

    card = open_drm_node("CARD0", "/dev/dri/card0");
    render = open_drm_node("RENDER128", "/dev/dri/renderD128");
    if (render.fd >= 0 && render.vc4) {
        selected = &render;
    } else if (card.fd >= 0 && card.vc4) {
        selected = &card;
    }
    if (selected != NULL) {
        uapi_result = probe_vc4_uapi(selected);
        if (uapi_result == 0) {
            submit_result = submit_clear_job(selected);
        }
    } else {
        marker("VC4_LINUX_DRM_SUBMIT_SKIPPED no-vc4-node\n");
    }

    framebuffer_result = paint_framebuffer();
    report("VC4_LINUX_DRM_SUBMIT_PROBE_DONE card0=%d render128=%d uapi=%d submit=%d framebuffer=%d\n",
           card.fd >= 0 && card.vc4 ? 0 : -1,
           render.fd >= 0 && render.vc4 ? 0 : -1,
           uapi_result, submit_result, framebuffer_result);
    if (submit_result == 0) {
        marker("VC4_LINUX_V3D_SUBMIT_DRIVER_OK\n");
    } else if (selected != NULL) {
        marker("VC4_LINUX_V3D_SUBMIT_DRIVER_PARTIAL\n");
    } else {
        marker("VC4_LINUX_V3D_SUBMIT_DRIVER_MISSING\n");
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
