/*
 * Independent drm_file master-reacquisition and native-KMS page-flip witness
 * for the pinned Linux VC4 fixture.
 *
 * The prerequisite modeset and page-flip witnesses run first on the card
 * drm_file opened by PID 1.  This supervisor then explicitly drops master on
 * that original file.  Its bounded child closes the inherited descriptor
 * before reopening /dev/dri/card0, acquires master on the new drm_file, and
 * performs a distinct event-driven page flip.  The parent reacquires master
 * on the original file only after the child has closed the independent one.
 *
 * Closing before reopening is intentional: the numeric descriptor may be
 * reused, but the kernel drm_file cannot be.  A successful witness therefore
 * cannot be satisfied by the inherited-file shortcut covered by
 * linux-kms-pageflip-probe.inc.c.
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
    struct drm_mode_crtc current = { 0 };
    struct drm_mode_create_dumb create = { 0 };
    struct drm_mode_map_dumb map = { 0 };
    struct drm_mode_fb_cmd2 framebuffer = { 0 };
    struct drm_mode_crtc_page_flip page_flip = { 0 };
    void *mapping = MAP_FAILED;
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
        return vc4_master_reacquire_fail("select-active-crtc");
    }

    current.crtc_id = selection.crtc_id;
    if (vc4_master_reacquire_ioctl(fd, DRM_IOCTL_MODE_GETCRTC, &current,
                                   "getcrtc-before") < 0) {
        return -1;
    }
    if (!current.mode_valid || current.fb_id == 0 ||
        current.mode.hdisplay == 0 || current.mode.vdisplay == 0) {
        errno = ENODEV;
        return vc4_master_reacquire_fail("crtc-not-active");
    }
    report("VC4_LINUX_KMS_MASTER_REACQUIRE_ACTIVE_OK "
           "crtc=%u fb=%u mode=%ux%u\n",
           current.crtc_id, current.fb_id,
           current.mode.hdisplay, current.mode.vdisplay);
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_ACTIVE_OK\n");

    create.width = current.mode.hdisplay;
    create.height = current.mode.vdisplay;
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
           framebuffer.fb_id, current.fb_id);
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
