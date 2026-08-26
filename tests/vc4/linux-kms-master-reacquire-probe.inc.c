/*
 * Independent drm_file master-reacquisition, native-KMS modeset, and
 * event-driven page-flip witness for the pinned Linux VC4 fixture.
 *
 * The prerequisite inherited-file modeset and page-flip witnesses remain as
 * regression coverage.  This supervisor then drops master on PID 1's card
 * file.  Its bounded child closes that inherited descriptor before reopening
 * /dev/dri/card0, acquires master on the new drm_file, enumerates the physical
 * connector and mode, creates its own initial framebuffer, programs the CRTC,
 * and finally flips to a second exact-pixel framebuffer.
 *
 * Closing before reopening is intentional: the numeric descriptor may be
 * reused, but the kernel drm_file cannot be.  Requiring SETCRTC before the
 * page flip also prevents an already-active CRTC inherited from the original
 * owner from satisfying the independent-file witness.
 */

#include <fcntl.h>

#include "linux-kms-pageflip-probe.inc.c"

#define VC4_MASTER_REACQUIRE_CARD_PATH "/dev/dri/card0"
#define VC4_MASTER_REACQUIRE_TIMEOUT_SECONDS     35U
#define VC4_MASTER_REACQUIRE_VISUAL_HOLD_SECONDS 3U
#define VC4_MASTER_REACQUIRE_POLL_ATTEMPTS       20U
#define VC4_MASTER_REACQUIRE_POLL_TIMEOUT_MS     500
#define VC4_MASTER_REACQUIRE_USER_DATA \
    UINT64_C(0x5643344d41535452)

static int vc4_master_reacquire_fail(const char *stage)
{
    char message[224];
    int saved_errno = errno != 0 ? errno : EIO;

    snprintf(message, sizeof(message),
             "VC4_LINUX_KMS_MASTER_REACQUIRE_FAILED stage=%s errno=%d\n",
             stage, saved_errno);
    marker(message);
    errno = saved_errno;
    return -1;
}

static int vc4_master_reacquire_ioctl(int fd, unsigned long request,
                                      void *argument, const char *stage)
{
    int result;

    do {
        result = ioctl(fd, request, argument);
    } while (result < 0 && errno == EINTR);
    if (result < 0) {
        return vc4_master_reacquire_fail(stage);
    }
    return 0;
}

static int vc4_master_reacquire_ioctl0(int fd, unsigned long request,
                                       const char *stage)
{
    int result;

    do {
        result = ioctl(fd, request, 0);
    } while (result < 0 && errno == EINTR);
    if (result < 0) {
        return vc4_master_reacquire_fail(stage);
    }
    return 0;
}

static void vc4_master_reacquire_fill_pattern(void *mapping, uint32_t pitch,
                                               uint32_t width,
                                               uint32_t height)
{
    for (uint32_t y = 0; y < height; y++) {
        uint32_t *row =
            (uint32_t *)((uint8_t *)mapping + (size_t)y * pitch);

        for (uint32_t x = 0; x < width; x++) {
            uint32_t checker = ((x / 40) ^ (y / 24)) & 1;
            uint32_t red = width > 1 ?
                           255 - x * 255 / (width - 1) : 0xff;
            uint32_t green = checker ? 0xe0 : 0x24;
            uint32_t blue = height > 1 ?
                            y * 255 / (height - 1) : 0;

            row[x] = (red << 16) | (green << 8) | blue;
        }
    }
}

static int vc4_master_reacquire_wait_event(int fd, uint32_t crtc_id,
                                           uint64_t expected_user_data)
{
    uint64_t event_words[512];
    uint8_t *event_bytes = (uint8_t *)event_words;

    for (unsigned int attempt = 0;
         attempt < VC4_MASTER_REACQUIRE_POLL_ATTEMPTS;
         attempt++) {
        struct pollfd descriptor = {
            .fd = fd,
            .events = POLLIN,
        };
        int poll_result;
        ssize_t length;
        size_t offset = 0;

        do {
            poll_result = poll(&descriptor, 1,
                               VC4_MASTER_REACQUIRE_POLL_TIMEOUT_MS);
        } while (poll_result < 0 && errno == EINTR);
        if (poll_result < 0) {
            return vc4_master_reacquire_fail("poll-event");
        }
        if (poll_result == 0) {
            continue;
        }
        if (descriptor.revents & (POLLERR | POLLHUP | POLLNVAL)) {
            errno = EIO;
            return vc4_master_reacquire_fail("poll-event-state");
        }
        if (!(descriptor.revents & POLLIN)) {
            continue;
        }

        do {
            length = read(fd, event_words, sizeof(event_words));
        } while (length < 0 && errno == EINTR);
        if (length < 0 && errno == EAGAIN) {
            continue;
        }
        if (length <= 0) {
            if (length == 0) {
                errno = EIO;
            }
            return vc4_master_reacquire_fail("read-event");
        }

        while (offset < (size_t)length) {
            struct drm_event event;

            if ((size_t)length - offset < sizeof(event)) {
                errno = EPROTO;
                return vc4_master_reacquire_fail("event-header-short");
            }
            memcpy(&event, event_bytes + offset, sizeof(event));
            if (event.length < sizeof(event) ||
                event.length > (size_t)length - offset) {
                errno = EPROTO;
                return vc4_master_reacquire_fail("event-length");
            }

            if (event.type == DRM_EVENT_FLIP_COMPLETE &&
                event.length >= sizeof(struct drm_event_vblank)) {
                struct drm_event_vblank vblank;

                memcpy(&vblank, event_bytes + offset, sizeof(vblank));
                report("VC4_LINUX_KMS_MASTER_REACQUIRE_EVENT "
                       "type=%u length=%u user=0x%016llx "
                       "sequence=%u crtc=%u\n",
                       vblank.base.type, vblank.base.length,
                       (unsigned long long)vblank.user_data,
                       vblank.sequence, vblank.crtc_id);
                if (vblank.user_data == expected_user_data &&
                    (vblank.crtc_id == 0 || vblank.crtc_id == crtc_id)) {
                    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_EVENT_OK\n");
                    return 0;
                }
            }

            offset += event.length;
        }
    }

    errno = ETIMEDOUT;
    return vc4_master_reacquire_fail("wait-event-timeout");
}

static int vc4_master_reacquire_run(int inherited_master_fd)
{
    struct vc4_modeset_selection selection = { 0 };
    struct drm_mode_crtc baseline = { 0 };
    struct drm_mode_crtc current = { 0 };
    struct drm_mode_create_dumb initial_create = { 0 };
    struct drm_mode_map_dumb initial_map = { 0 };
    struct drm_mode_fb_cmd2 initial_framebuffer = { 0 };
    struct drm_mode_create_dumb create = { 0 };
    struct drm_mode_map_dumb map = { 0 };
    struct drm_mode_fb_cmd2 framebuffer = { 0 };
    struct drm_mode_crtc crtc = { 0 };
    struct drm_mode_crtc_page_flip page_flip = { 0 };
    void *initial_mapping = MAP_FAILED;
    void *mapping = MAP_FAILED;
    uint32_t connector_id;
    int fd;

    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_START\n");
    if (inherited_master_fd < 0) {
        errno = EBADF;
        return vc4_master_reacquire_fail("invalid-inherited-fd");
    }

    if (close(inherited_master_fd) < 0) {
        return vc4_master_reacquire_fail("close-inherited-fd");
    }
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_INHERITED_CLOSED\n");

    fd = open(VC4_MASTER_REACQUIRE_CARD_PATH,
              O_RDWR | O_CLOEXEC | O_NONBLOCK);
    if (fd < 0) {
        return vc4_master_reacquire_fail("reopen-card0");
    }
    report("VC4_LINUX_KMS_MASTER_REACQUIRE_OPEN_OK path=%s fd=%d\n",
           VC4_MASTER_REACQUIRE_CARD_PATH, fd);
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_OPEN_OK\n");

    if (vc4_master_reacquire_ioctl0(fd, DRM_IOCTL_SET_MASTER,
                                    "set-master-new-file") < 0) {
        return -1;
    }
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_SET_MASTER_OK\n");

    if (vc4_modeset_select(fd, &selection) < 0) {
        return vc4_master_reacquire_fail("select-mode-new-file");
    }
    report("VC4_LINUX_KMS_MASTER_REACQUIRE_SELECTION_OK "
           "connector=%u crtc=%u mode=%ux%u clock=%u\n",
           selection.connector_id, selection.crtc_id,
           selection.mode.hdisplay, selection.mode.vdisplay,
           selection.mode.clock);
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_SELECTION_OK\n");

    baseline.crtc_id = selection.crtc_id;
    if (vc4_master_reacquire_ioctl(fd, DRM_IOCTL_MODE_GETCRTC, &baseline,
                                   "getcrtc-baseline") < 0) {
        return -1;
    }
    report("VC4_LINUX_KMS_MASTER_REACQUIRE_BASELINE "
           "crtc=%u fb=%u mode_valid=%u mode=%ux%u\n",
           baseline.crtc_id, baseline.fb_id, baseline.mode_valid,
           baseline.mode.hdisplay, baseline.mode.vdisplay);
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_BASELINE_OK\n");

    initial_create.width = selection.mode.hdisplay;
    initial_create.height = selection.mode.vdisplay;
    initial_create.bpp = 32;
    if (vc4_master_reacquire_ioctl(
            fd, DRM_IOCTL_MODE_CREATE_DUMB, &initial_create,
            "create-modeset-dumb") < 0) {
        return -1;
    }
    if (initial_create.handle == 0 || initial_create.pitch == 0 ||
        initial_create.size == 0) {
        errno = EIO;
        return vc4_master_reacquire_fail("create-modeset-dumb-invalid");
    }
    report("VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_DUMB_OK "
           "handle=%u pitch=%u size=%llu\n",
           initial_create.handle, initial_create.pitch,
           (unsigned long long)initial_create.size);
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_DUMB_OK\n");

    initial_map.handle = initial_create.handle;
    if (vc4_master_reacquire_ioctl(
            fd, DRM_IOCTL_MODE_MAP_DUMB, &initial_map,
            "map-modeset-dumb") < 0) {
        return -1;
    }
    initial_mapping = mmap(NULL, initial_create.size,
                           PROT_READ | PROT_WRITE, MAP_SHARED,
                           fd, (off_t)initial_map.offset);
    if (initial_mapping == MAP_FAILED) {
        return vc4_master_reacquire_fail("mmap-modeset-dumb");
    }
    vc4_modeset_fill_pattern(initial_mapping, initial_create.pitch,
                             initial_create.width, initial_create.height);
    __sync_synchronize();
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_MAP_OK\n");

    initial_framebuffer.width = initial_create.width;
    initial_framebuffer.height = initial_create.height;
    initial_framebuffer.pixel_format = VC4_MODESET_DRM_FORMAT_XRGB8888;
    initial_framebuffer.handles[0] = initial_create.handle;
    initial_framebuffer.pitches[0] = initial_create.pitch;
    if (vc4_master_reacquire_ioctl(
            fd, DRM_IOCTL_MODE_ADDFB2, &initial_framebuffer,
            "addfb2-modeset") < 0) {
        return -1;
    }
    report("VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_FB_OK "
           "fb=%u baseline_fb=%u\n",
           initial_framebuffer.fb_id, baseline.fb_id);
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_FB_OK\n");

    connector_id = selection.connector_id;
    crtc.crtc_id = selection.crtc_id;
    crtc.fb_id = initial_framebuffer.fb_id;
    crtc.set_connectors_ptr = (uintptr_t)&connector_id;
    crtc.count_connectors = 1;
    crtc.mode = selection.mode;
    crtc.mode_valid = 1;
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_SETCRTC_START\n");
    if (vc4_master_reacquire_ioctl(fd, DRM_IOCTL_MODE_SETCRTC, &crtc,
                                   "setcrtc-new-file") < 0) {
        return -1;
    }
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_SETCRTC_OK\n");

    current.crtc_id = selection.crtc_id;
    if (vc4_master_reacquire_ioctl(
            fd, DRM_IOCTL_MODE_GETCRTC, &current,
            "getcrtc-after-setcrtc") < 0) {
        return -1;
    }
    if (!current.mode_valid ||
        current.fb_id != initial_framebuffer.fb_id ||
        current.mode.hdisplay != selection.mode.hdisplay ||
        current.mode.vdisplay != selection.mode.vdisplay) {
        report("VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_MISMATCH "
               "expected_fb=%u actual_fb=%u mode_valid=%u "
               "expected_mode=%ux%u actual_mode=%ux%u\n",
               initial_framebuffer.fb_id, current.fb_id,
               current.mode_valid,
               selection.mode.hdisplay, selection.mode.vdisplay,
               current.mode.hdisplay, current.mode.vdisplay);
        errno = EIO;
        return vc4_master_reacquire_fail(
            "modeset-current-fb-mismatch");
    }
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_CURRENT_FB_OK\n");
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_INDEPENDENT_MODESET_OK\n");
    report("VC4_LINUX_KMS_MASTER_REACQUIRE_ACTIVE_OK "
           "crtc=%u fb=%u mode=%ux%u\n",
           current.crtc_id, current.fb_id,
           current.mode.hdisplay, current.mode.vdisplay);
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_ACTIVE_OK\n");

    create.width = selection.mode.hdisplay;
    create.height = selection.mode.vdisplay;
    create.bpp = 32;
    if (vc4_master_reacquire_ioctl(fd, DRM_IOCTL_MODE_CREATE_DUMB, &create,
                                   "create-dumb") < 0) {
        return -1;
    }
    if (create.handle == 0 || create.pitch == 0 || create.size == 0) {
        errno = EIO;
        return vc4_master_reacquire_fail("create-dumb-invalid");
    }
    report("VC4_LINUX_KMS_MASTER_REACQUIRE_DUMB_OK "
           "handle=%u pitch=%u size=%llu\n",
           create.handle, create.pitch, (unsigned long long)create.size);
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_DUMB_OK\n");

    map.handle = create.handle;
    if (vc4_master_reacquire_ioctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &map,
                                   "map-dumb") < 0) {
        return -1;
    }
    mapping = mmap(NULL, create.size, PROT_READ | PROT_WRITE, MAP_SHARED,
                   fd, (off_t)map.offset);
    if (mapping == MAP_FAILED) {
        return vc4_master_reacquire_fail("mmap-dumb");
    }
    vc4_master_reacquire_fill_pattern(mapping, create.pitch,
                                      create.width, create.height);
    __sync_synchronize();
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_MAP_OK\n");

    framebuffer.width = create.width;
    framebuffer.height = create.height;
    framebuffer.pixel_format = VC4_MODESET_DRM_FORMAT_XRGB8888;
    framebuffer.handles[0] = create.handle;
    framebuffer.pitches[0] = create.pitch;
    if (vc4_master_reacquire_ioctl(fd, DRM_IOCTL_MODE_ADDFB2, &framebuffer,
                                   "addfb2") < 0) {
        return -1;
    }
    report("VC4_LINUX_KMS_MASTER_REACQUIRE_FB_OK fb=%u old_fb=%u\n",
           framebuffer.fb_id, initial_framebuffer.fb_id);
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_FB_OK\n");

    page_flip.crtc_id = selection.crtc_id;
    page_flip.fb_id = framebuffer.fb_id;
    page_flip.flags = DRM_MODE_PAGE_FLIP_EVENT;
    page_flip.user_data = VC4_MASTER_REACQUIRE_USER_DATA;
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_IOCTL_START\n");
    if (vc4_master_reacquire_ioctl(fd, DRM_IOCTL_MODE_PAGE_FLIP, &page_flip,
                                   "page-flip-ioctl") < 0) {
        return -1;
    }
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_QUEUED\n");

    if (vc4_master_reacquire_wait_event(
            fd, selection.crtc_id,
            VC4_MASTER_REACQUIRE_USER_DATA) < 0) {
        return -1;
    }

    memset(&current, 0, sizeof(current));
    current.crtc_id = selection.crtc_id;
    if (vc4_master_reacquire_ioctl(fd, DRM_IOCTL_MODE_GETCRTC, &current,
                                   "getcrtc-after") < 0) {
        return -1;
    }
    if (current.fb_id != framebuffer.fb_id) {
        report("VC4_LINUX_KMS_MASTER_REACQUIRE_FB_MISMATCH "
               "expected=%u actual=%u\n",
               framebuffer.fb_id, current.fb_id);
        errno = EIO;
        return vc4_master_reacquire_fail("current-fb-mismatch");
    }
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_CURRENT_FB_OK\n");
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_VISUAL_READY\n");
    sleep(VC4_MASTER_REACQUIRE_VISUAL_HOLD_SECONDS);

    if (vc4_master_reacquire_ioctl0(fd, DRM_IOCTL_DROP_MASTER,
                                    "drop-master-new-file") < 0) {
        return -1;
    }
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_CHILD_DROPPED\n");
    (void)munmap(mapping, create.size);
    (void)munmap(initial_mapping, initial_create.size);
    if (close(fd) < 0) {
        return vc4_master_reacquire_fail("close-reopened-fd");
    }
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_OK\n");
    return 0;
}

static int vc4_master_reacquire_restore_original(int master_fd)
{
    if (vc4_master_reacquire_ioctl0(master_fd, DRM_IOCTL_SET_MASTER,
                                    "restore-original-master") < 0) {
        return -1;
    }
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_ORIGINAL_RESTORED\n");
    return 0;
}

static int vc4_kms_master_reacquire_supervise(int master_fd)
{
    struct timespec delay = {
        .tv_sec = 0,
        .tv_nsec = VC4_MODESET_POLL_NS,
    };
    pid_t child;

    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_SUPERVISOR_START\n");
    if (master_fd < 0) {
        errno = EBADF;
        return vc4_master_reacquire_fail("invalid-original-fd");
    }
    if (vc4_master_reacquire_ioctl0(master_fd, DRM_IOCTL_DROP_MASTER,
                                    "drop-original-master") < 0) {
        return -1;
    }
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_ORIGINAL_DROPPED\n");

    child = fork();
    if (child < 0) {
        int saved_errno = errno;

        if (vc4_master_reacquire_restore_original(master_fd) < 0) {
            return -1;
        }
        errno = saved_errno;
        return vc4_master_reacquire_fail("fork");
    }
    if (child == 0) {
        alarm(VC4_MASTER_REACQUIRE_TIMEOUT_SECONDS - 2);
        _exit(vc4_master_reacquire_run(master_fd) == 0 ? 0 : 1);
    }

    for (unsigned int iteration = 0;
         iteration < VC4_MASTER_REACQUIRE_TIMEOUT_SECONDS * 10;
         iteration++) {
        int status;
        pid_t result;

        do {
            result = waitpid(child, &status, WNOHANG);
        } while (result < 0 && errno == EINTR);
        if (result == child) {
            bool child_ok = WIFEXITED(status) &&
                            WEXITSTATUS(status) == 0;

            if (WIFEXITED(status) && !child_ok) {
                report("VC4_LINUX_KMS_MASTER_REACQUIRE_CHILD_EXIT "
                       "status=%d\n", WEXITSTATUS(status));
            } else if (WIFSIGNALED(status)) {
                int signal = WTERMSIG(status);

                report("VC4_LINUX_KMS_MASTER_REACQUIRE_CHILD_SIGNAL "
                       "signal=%d\n", signal);
                if (signal == SIGALRM) {
                    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_TIMEOUT\n");
                }
            }

            if (vc4_master_reacquire_restore_original(master_fd) < 0) {
                marker("VC4_LINUX_KMS_MASTER_REACQUIRE_SUPERVISOR_FAILED\n");
                return -1;
            }
            if (child_ok) {
                marker("VC4_LINUX_KMS_MASTER_REACQUIRE_SUPERVISOR_OK\n");
                return 0;
            }
            marker("VC4_LINUX_KMS_MASTER_REACQUIRE_SUPERVISOR_FAILED\n");
            return -1;
        }
        if (result < 0) {
            int saved_errno = errno;

            (void)vc4_master_reacquire_restore_original(master_fd);
            errno = saved_errno;
            return vc4_master_reacquire_fail("waitpid");
        }
        nanosleep(&delay, NULL);
    }

    (void)kill(child, SIGKILL);
    (void)waitpid(child, NULL, 0);
    (void)vc4_master_reacquire_restore_original(master_fd);
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_TIMEOUT\n");
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_SUPERVISOR_FAILED\n");
    return -1;
}
