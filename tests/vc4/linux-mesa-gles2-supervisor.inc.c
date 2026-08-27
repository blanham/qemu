/*
 * Bounded supervisor for the dynamically linked Mesa VC4 GLES2 probe.
 *
 * Included by a generated copy of linux-v3d-modular-init.c after its common
 * marker/report helpers are available.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#include <fcntl.h>
#include <signal.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#define VC4_MESA_GLES2_PROGRAM \
    "/usr/bin/vc4-mesa-gles2-probe"
#define VC4_MESA_GLES2_SUPERVISOR_SECONDS 50U
#define VC4_MESA_GLES2_POLL_NS 100000000L

static void vc4_linux_mesa_gles2_reopen_log(void)
{
    int fd = open("/dev/kmsg", O_WRONLY | O_CLOEXEC);

    if (fd < 0) {
        return;
    }
    if (fd != STDOUT_FILENO && dup2(fd, STDOUT_FILENO) < 0) {
        close(fd);
        return;
    }
    if (fd != STDERR_FILENO && dup2(fd, STDERR_FILENO) < 0) {
        close(fd);
        return;
    }
    if (fd > STDERR_FILENO) {
        close(fd);
    }
}

static int vc4_linux_mesa_gles2_supervise(void)
{
    struct timespec delay = {
        .tv_sec = 0,
        .tv_nsec = VC4_MESA_GLES2_POLL_NS,
    };
    pid_t child;

    marker("VC4_LINUX_MESA_GLES2_SUPERVISOR_START\n");
    if (mkdir("/tmp", 01777) < 0 && errno != EEXIST) {
        report("VC4_LINUX_MESA_GLES2_SUPERVISOR_FAILED "
               "stage=mkdir-tmp errno=%d (%s)\n",
               errno, strerror(errno));
        return -1;
    }

    child = fork();
    if (child < 0) {
        report("VC4_LINUX_MESA_GLES2_SUPERVISOR_FAILED "
               "stage=fork errno=%d (%s)\n",
               errno, strerror(errno));
        return -1;
    }
    if (child == 0) {
        /*
         * The init console is deliberately nonblocking so the static probe
         * cannot deadlock under serial backpressure.  A dynamically linked
         * Mesa process can emit enough diagnostics for every stderr marker,
         * including its alarm marker, to be lost with EAGAIN.  Send the
         * child directly through kmsg instead; the kernel console drains it
         * asynchronously and the workflow retains an exact execution stage.
         */
        vc4_linux_mesa_gles2_reopen_log();
        (void)setenv("EGL_PLATFORM", "surfaceless", 1);
        (void)setenv("MESA_LOADER_DRIVER_OVERRIDE", "vc4", 1);
        (void)setenv("GALLIUM_DRIVER", "vc4", 1);
        (void)setenv("LIBGL_ALWAYS_SOFTWARE", "0", 1);
        (void)setenv("MESA_SHADER_CACHE_DISABLE", "true", 1);
        (void)setenv("LIBGL_DRIVERS_PATH",
                     "/usr/lib/aarch64-linux-gnu/dri", 1);
        (void)setenv("LD_LIBRARY_PATH",
                     "/usr/lib/aarch64-linux-gnu:"
                     "/lib/aarch64-linux-gnu:/lib", 1);
        execl(VC4_MESA_GLES2_PROGRAM,
              VC4_MESA_GLES2_PROGRAM, (char *)NULL);
        report("VC4_LINUX_MESA_GLES2_EXEC_FAILED "
               "path=%s errno=%d (%s)\n",
               VC4_MESA_GLES2_PROGRAM, errno, strerror(errno));
        _exit(127);
    }

    for (unsigned int iteration = 0;
         iteration < VC4_MESA_GLES2_SUPERVISOR_SECONDS * 10;
         iteration++) {
        int status = 0;
        pid_t result;

        do {
            result = waitpid(child, &status, WNOHANG);
        } while (result < 0 && errno == EINTR);
        if (result == child) {
            if (WIFEXITED(status) && WEXITSTATUS(status) == 0) {
                marker("VC4_LINUX_MESA_GLES2_SUPERVISOR_OK\n");
                return 0;
            }
            if (WIFEXITED(status)) {
                report("VC4_LINUX_MESA_GLES2_CHILD_EXIT status=%d\n",
                       WEXITSTATUS(status));
            } else if (WIFSIGNALED(status)) {
                report("VC4_LINUX_MESA_GLES2_CHILD_SIGNAL signal=%d\n",
                       WTERMSIG(status));
            }
            marker("VC4_LINUX_MESA_GLES2_SUPERVISOR_FAILED\n");
            return -1;
        }
        if (result < 0) {
            report("VC4_LINUX_MESA_GLES2_SUPERVISOR_FAILED "
                   "stage=waitpid errno=%d (%s)\n",
                   errno, strerror(errno));
            return -1;
        }
        nanosleep(&delay, NULL);
    }

    (void)kill(child, SIGKILL);
    (void)waitpid(child, NULL, 0);
    marker("VC4_LINUX_MESA_GLES2_SUPERVISOR_TIMEOUT\n");
    return -1;
}
