/*
 * Supervised native-KMS page-flip probe for the pinned Linux VC4 witness.
 *
 * Included after linux-kms-modeset-probe.inc.c so this slice can reuse the
 * connector/CRTC selection and bounded timeout diagnostics proven there.
 */

#include <poll.h>

#define VC4_PAGEFLIP_TIMEOUT_SECONDS 40
#define VC4_PAGEFLIP_USER_DATA UINT64_C(0x5643345041474546)

struct vc4_pageflip_buffer {
    struct drm_mode_create_dumb create;
    struct drm_mode_fb_cmd2 framebuffer;
    void *mapping;
};

static void vc4_pageflip_failed(const char *stage, int error)
{
    printf("VC4_LINUX_KMS_PAGEFLIP_FAILED stage=%s errno=%d\n",
           stage, error);
    fflush(stdout);
}

static uint32_t vc4_pageflip_pixel(uint32_t x, uint32_t y,
                                   uint32_t width, uint32_t height,
                                   bool second)
{
    uint32_t red = width > 1 ? x * 255 / (width - 1) : 0;
    uint32_t green = height > 1 ? y * 255 / (height - 1) : 0;
    bool checker = ((x / 32) ^ (y / 32)) & 1;
    uint32_t blue;

    if (second) {
        red = 255 - red;
        green = 255 - green;
        checker = !checker;
    }
    blue = checker ? 0xff : 0x20;
    return (red << 16) | (green << 8) | blue;
}

static void vc4_pageflip_fill(struct vc4_pageflip_buffer *buffer,
                              bool second)
{
    uint32_t y;

    for (y = 0; y < buffer->create.height; y++) {
        uint32_t *row = (uint32_t *)((uint8_t *)buffer->mapping +
                                     (size_t)y * buffer->create.pitch);
        uint32_t x;

        for (x = 0; x < buffer->create.width; x++) {
            row[x] = vc4_pageflip_pixel(x, y,
                                        buffer->create.width,
                                        buffer->create.height,
                                        second);
        }
    }
}

static int vc4_pageflip_create_buffer(int fd,
                                      const struct drm_mode_modeinfo *mode,
                                      bool second,
                                      struct vc4_pageflip_buffer *buffer)
{
    struct drm_mode_map_dumb map;

    memset(buffer, 0, sizeof(*buffer));
    buffer->mapping = MAP_FAILED;
    buffer->create.width = mode->hdisplay;
    buffer->create.height = mode->vdisplay;
    buffer->create.bpp = 32;
    if (vc4_modeset_ioctl(fd, DRM_IOCTL_MODE_CREATE_DUMB,
                          &buffer->create,
                          second ? "pageflip-create-b" :
                                   "pageflip-create-a") < 0) {
        return -1;
    }

    memset(&map, 0, sizeof(map));
    map.handle = buffer->create.handle;
    if (vc4_modeset_ioctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &map,
                          second ? "pageflip-map-b" :
                                   "pageflip-map-a") < 0) {
        return -1;
    }
    buffer->mapping = mmap(NULL, buffer->create.size,
                           PROT_READ | PROT_WRITE, MAP_SHARED,
                           fd, map.offset);
    if (buffer->mapping == MAP_FAILED) {
        vc4_pageflip_failed(second ? "pageflip-mmap-b" :
                                     "pageflip-mmap-a", errno);
        return -1;
    }
    vc4_pageflip_fill(buffer, second);
    if (msync(buffer->mapping, buffer->create.size, MS_SYNC) < 0) {
        vc4_pageflip_failed(second ? "pageflip-msync-b" :
                                     "pageflip-msync-a", errno);
        return -1;
    }

    buffer->framebuffer.width = buffer->create.width;
    buffer->framebuffer.height = buffer->create.height;
    buffer->framebuffer.pixel_format = VC4_DRM_FORMAT_XRGB8888;
    buffer->framebuffer.handles[0] = buffer->create.handle;
    buffer->framebuffer.pitches[0] = buffer->create.pitch;
    if (vc4_modeset_ioctl(fd, DRM_IOCTL_MODE_ADDFB2,
                          &buffer->framebuffer,
                          second ? "pageflip-addfb-b" :
                                   "pageflip-addfb-a") < 0) {
        return -1;
    }
    return 0;
}

static bool vc4_pageflip_wait_event(int fd)
{
    struct pollfd pollfd = {
        .fd = fd,
        .events = POLLIN,
    };
    uint8_t events[4096];
    int poll_result;
    ssize_t bytes;
    size_t offset;

    do {
        poll_result = poll(&pollfd, 1, 10000);
    } while (poll_result < 0 && errno == EINTR);
    if (poll_result == 0) {
        vc4_pageflip_failed("pageflip-wait-event", ETIMEDOUT);
        return false;
    }
    if (poll_result < 0) {
        vc4_pageflip_failed("pageflip-poll-event", errno);
        return false;
    }

    do {
        bytes = read(fd, events, sizeof(events));
    } while (bytes < 0 && errno == EINTR);
    if (bytes < 0) {
        vc4_pageflip_failed("pageflip-read-event", errno);
        return false;
    }

    offset = 0;
    while (offset + sizeof(struct drm_event) <= (size_t)bytes) {
        const struct drm_event *event =
            (const struct drm_event *)(events + offset);

        if (event->length < sizeof(*event) ||
            offset + event->length > (size_t)bytes) {
            vc4_pageflip_failed("pageflip-malformed-event", EPROTO);
            return false;
        }
        if (event->type == DRM_EVENT_FLIP_COMPLETE &&
            event->length >= sizeof(struct drm_event_vblank)) {
            const struct drm_event_vblank *vblank =
                (const struct drm_event_vblank *)event;

            if (vblank->user_data == VC4_PAGEFLIP_USER_DATA) {
                printf("VC4_LINUX_KMS_PAGEFLIP_EVENT_OK sequence=%u "
                       "tv_sec=%u tv_usec=%u\n",
                       vblank->sequence, vblank->tv_sec,
                       vblank->tv_usec);
                fflush(stdout);
                return true;
            }
        }
        offset += event->length;
    }

    vc4_pageflip_failed("pageflip-event-not-found", ENOMSG);
    return false;
}

static int vc4_pageflip_run(void)
{
    struct vc4_modeset_selection selection;
    struct vc4_pageflip_buffer first;
    struct vc4_pageflip_buffer second;
    struct drm_mode_crtc crtc;
    struct drm_mode_crtc_page_flip flip;
    uint32_t connector_id;
    int fd;

    printf("VC4_LINUX_KMS_PAGEFLIP_START\n");
    fflush(stdout);
    fd = open("/dev/dri/card0", O_RDWR | O_CLOEXEC);
    if (fd < 0) {
        vc4_pageflip_failed("pageflip-open-card0", errno);
        return 1;
    }

    memset(&selection, 0, sizeof(selection));
    if (vc4_modeset_select(fd, &selection) < 0) {
        close(fd);
        return 1;
    }
    printf("VC4_LINUX_KMS_PAGEFLIP_CONNECTOR_OK connector=%u crtc=%u "
           "mode=%ux%u\n",
           selection.connector_id, selection.crtc_id,
           selection.mode.hdisplay, selection.mode.vdisplay);
    fflush(stdout);

    if (vc4_pageflip_create_buffer(fd, &selection.mode, false, &first) < 0) {
        close(fd);
        return 1;
    }
    printf("VC4_LINUX_KMS_PAGEFLIP_BUFFER_A_OK fb=%u\n",
           first.framebuffer.fb_id);
    fflush(stdout);

    if (vc4_pageflip_create_buffer(fd, &selection.mode, true, &second) < 0) {
        close(fd);
        return 1;
    }
    printf("VC4_LINUX_KMS_PAGEFLIP_BUFFER_B_OK fb=%u\n",
           second.framebuffer.fb_id);
    fflush(stdout);

    connector_id = selection.connector_id;
    memset(&crtc, 0, sizeof(crtc));
    crtc.crtc_id = selection.crtc_id;
    crtc.fb_id = first.framebuffer.fb_id;
    crtc.set_connectors_ptr = (uintptr_t)&connector_id;
    crtc.count_connectors = 1;
    crtc.mode = selection.mode;
    crtc.mode_valid = 1;
    if (vc4_modeset_ioctl(fd, DRM_IOCTL_MODE_SETCRTC, &crtc,
                          "pageflip-setcrtc-a") < 0) {
        close(fd);
        return 1;
    }
    printf("VC4_LINUX_KMS_PAGEFLIP_PATTERN_A_OK\n");
    fflush(stdout);
    sleep(3);

    memset(&flip, 0, sizeof(flip));
    flip.crtc_id = selection.crtc_id;
    flip.fb_id = second.framebuffer.fb_id;
    flip.flags = DRM_MODE_PAGE_FLIP_EVENT;
    flip.user_data = VC4_PAGEFLIP_USER_DATA;
    if (vc4_modeset_ioctl(fd, DRM_IOCTL_MODE_PAGE_FLIP, &flip,
                          "pageflip-submit") < 0) {
        close(fd);
        return 1;
    }
    printf("VC4_LINUX_KMS_PAGEFLIP_SUBMIT_OK\n");
    fflush(stdout);

    if (!vc4_pageflip_wait_event(fd)) {
        close(fd);
        return 1;
    }
    printf("VC4_LINUX_KMS_PAGEFLIP_PATTERN_B_OK\n");
    fflush(stdout);
    sleep(3);
    printf("VC4_LINUX_KMS_PAGEFLIP_OK\n");
    fflush(stdout);
    close(fd);
    return 0;
}

static void vc4_kms_pageflip_supervise(void)
{
    struct timespec delay = {
        .tv_sec = 0,
        .tv_nsec = VC4_MODESET_POLL_NS,
    };
    pid_t child;
    unsigned int iteration;

    printf("VC4_LINUX_KMS_PAGEFLIP_SUPERVISOR_START\n");
    fflush(stdout);
    child = fork();
    if (child < 0) {
        vc4_pageflip_failed("pageflip-fork", errno);
        return;
    }
    if (child == 0) {
        alarm(VC4_PAGEFLIP_TIMEOUT_SECONDS - 2);
        _exit(vc4_pageflip_run());
    }

    for (iteration = 0;
         iteration < VC4_PAGEFLIP_TIMEOUT_SECONDS * 10;
         iteration++) {
        int status;
        pid_t result = waitpid(child, &status, WNOHANG);

        if (result == child) {
            if (WIFEXITED(status)) {
                printf("VC4_LINUX_KMS_PAGEFLIP_CHILD_EXIT status=%d\n",
                       WEXITSTATUS(status));
            } else if (WIFSIGNALED(status)) {
                printf("VC4_LINUX_KMS_PAGEFLIP_CHILD_SIGNAL signal=%d\n",
                       WTERMSIG(status));
            }
            printf("VC4_LINUX_KMS_PAGEFLIP_SUPERVISOR_DONE\n");
            fflush(stdout);
            return;
        }
        if (result < 0) {
            vc4_pageflip_failed("pageflip-waitpid", errno);
            return;
        }
        nanosleep(&delay, NULL);
    }

    vc4_modeset_dump_timeout_state(child);
    kill(child, SIGKILL);
    for (iteration = 0; iteration < 20; iteration++) {
        if (waitpid(child, NULL, WNOHANG) == child) {
            break;
        }
        nanosleep(&delay, NULL);
    }
    printf("VC4_LINUX_KMS_PAGEFLIP_TIMEOUT\n");
    printf("VC4_LINUX_KMS_PAGEFLIP_SUPERVISOR_DONE\n");
    fflush(stdout);
}
