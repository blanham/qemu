/*
 * Bounded supervisor for the dynamically linked Mesa VC4 GLES2 probe.
 *
 * Included by a generated copy of linux-v3d-modular-init.c after its common
 * marker/report helpers are available.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#define VC4_MESA_GLES2_PROGRAM \
    "/usr/bin/vc4-mesa-gles2-probe"
#define VC4_MESA_GLES2_SUPERVISOR_SECONDS 50U
#define VC4_MESA_GLES2_POLL_NS 100000000L
#define VC4_MESA_GLES2_LOG_BYTES (256U * 1024U)

typedef struct VC4MesaGLES2Log {
    int fd;
    size_t length;
    size_t dropped;
} VC4MesaGLES2Log;

static char vc4_linux_mesa_gles2_log_buffer[VC4_MESA_GLES2_LOG_BYTES];

static int vc4_linux_mesa_gles2_set_nonblocking(int fd)
{
    int flags = fcntl(fd, F_GETFL);

    if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0) {
        return -1;
    }
    return 0;
}

static int vc4_linux_mesa_gles2_redirect_log(int fd)
{
    if (fd != STDOUT_FILENO && dup2(fd, STDOUT_FILENO) < 0) {
        return -1;
    }
    if (fd != STDERR_FILENO && dup2(fd, STDERR_FILENO) < 0) {
        return -1;
    }
    if (fd > STDERR_FILENO) {
        close(fd);
    }
    return 0;
}

/*
 * Drain even after the retained buffer is full.  The fixed-size transcript
 * bounds PID 1's memory use while making sure a verbose Mesa child can never
 * block forever on a full pipe and hide the actual V3D frontier.
 */
static int vc4_linux_mesa_gles2_drain_log(VC4MesaGLES2Log *log)
{
    char scratch[4096];

    for (;;) {
        ssize_t received = read(log->fd, scratch, sizeof(scratch));

        if (received > 0) {
            size_t bytes = (size_t)received;
            size_t available = VC4_MESA_GLES2_LOG_BYTES - log->length;
            size_t retained = bytes < available ? bytes : available;

            if (retained != 0) {
                memcpy(vc4_linux_mesa_gles2_log_buffer + log->length,
                       scratch, retained);
                log->length += retained;
            }
            log->dropped += bytes - retained;
            continue;
        }
        if (received == 0) {
            return 1;
        }
        if (errno == EINTR) {
            continue;
        }
        if (errno == EAGAIN || errno == EWOULDBLOCK) {
            return 0;
        }
        return -1;
    }
}

static void vc4_linux_mesa_gles2_replay_log(VC4MesaGLES2Log *log)
{
    if (log->fd >= 0) {
        close(log->fd);
        log->fd = -1;
    }
    if (log->length != 0) {
        emit_text(vc4_linux_mesa_gles2_log_buffer, log->length);
    }
    if (log->dropped != 0) {
        report("VC4_LINUX_MESA_GLES2_LOG_TRUNCATED "
               "kept=%zu dropped=%zu\n",
               log->length, log->dropped);
    }
}

static int vc4_linux_mesa_gles2_finish_log(VC4MesaGLES2Log *log)
{
    int result = vc4_linux_mesa_gles2_drain_log(log);
    int saved_errno = errno;

    vc4_linux_mesa_gles2_replay_log(log);
    errno = saved_errno;
    return result < 0 ? -1 : 0;
}

static void vc4_linux_mesa_gles2_stop_child(pid_t child)
{
    int status;

    (void)kill(child, SIGKILL);
    while (waitpid(child, &status, 0) < 0 && errno == EINTR) {
    }
}

static int vc4_linux_mesa_gles2_supervise(void)
{
    struct timespec delay = {
        .tv_sec = 0,
        .tv_nsec = VC4_MESA_GLES2_POLL_NS,
    };
    VC4MesaGLES2Log log = {
        .fd = -1,
        .length = 0,
        .dropped = 0,
    };
    int log_pipe[2];
    pid_t child;

    marker("VC4_LINUX_MESA_GLES2_SUPERVISOR_START\n");
    if (mkdir("/tmp", 01777) < 0 && errno != EEXIST) {
        report("VC4_LINUX_MESA_GLES2_SUPERVISOR_FAILED "
               "stage=mkdir-tmp errno=%d (%s)\n",
               errno, strerror(errno));
        return -1;
    }
    if (pipe2(log_pipe, O_CLOEXEC) < 0) {
        report("VC4_LINUX_MESA_GLES2_SUPERVISOR_FAILED "
               "stage=pipe errno=%d (%s)\n",
               errno, strerror(errno));
        return -1;
    }
    if (vc4_linux_mesa_gles2_set_nonblocking(log_pipe[0]) < 0) {
        int saved_errno = errno;

        close(log_pipe[0]);
        close(log_pipe[1]);
        report("VC4_LINUX_MESA_GLES2_SUPERVISOR_FAILED "
               "stage=nonblocking-log errno=%d (%s)\n",
               saved_errno, strerror(saved_errno));
        return -1;
    }

    child = fork();
    if (child < 0) {
        int saved_errno = errno;

        close(log_pipe[0]);
        close(log_pipe[1]);
        report("VC4_LINUX_MESA_GLES2_SUPERVISOR_FAILED "
               "stage=fork errno=%d (%s)\n",
               saved_errno, strerror(saved_errno));
        return -1;
    }
    if (child == 0) {
        int saved_errno;

        close(log_pipe[0]);
        if (vc4_linux_mesa_gles2_redirect_log(log_pipe[1]) < 0) {
            saved_errno = errno;
            report("VC4_LINUX_MESA_GLES2_EXEC_FAILED "
                   "stage=redirect-log errno=%d (%s)\n",
                   saved_errno, strerror(saved_errno));
            _exit(126);
        }
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
        saved_errno = errno;
        report("VC4_LINUX_MESA_GLES2_EXEC_FAILED "
               "path=%s errno=%d (%s)\n",
               VC4_MESA_GLES2_PROGRAM,
               saved_errno, strerror(saved_errno));
        _exit(127);
    }

    close(log_pipe[1]);
    log.fd = log_pipe[0];

    for (unsigned int iteration = 0;
         iteration < VC4_MESA_GLES2_SUPERVISOR_SECONDS * 10;
         iteration++) {
        int status = 0;
        pid_t result;

        if (vc4_linux_mesa_gles2_drain_log(&log) < 0) {
            int saved_errno = errno;

            vc4_linux_mesa_gles2_stop_child(child);
            vc4_linux_mesa_gles2_replay_log(&log);
            report("VC4_LINUX_MESA_GLES2_SUPERVISOR_FAILED "
                   "stage=read-log errno=%d (%s)\n",
                   saved_errno, strerror(saved_errno));
            return -1;
        }

        do {
            result = waitpid(child, &status, WNOHANG);
        } while (result < 0 && errno == EINTR);
        if (result == child) {
            if (vc4_linux_mesa_gles2_finish_log(&log) < 0) {
                int saved_errno = errno;

                report("VC4_LINUX_MESA_GLES2_SUPERVISOR_FAILED "
                       "stage=finish-log errno=%d (%s)\n",
                       saved_errno, strerror(saved_errno));
                return -1;
            }
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
            int saved_errno = errno;

            vc4_linux_mesa_gles2_stop_child(child);
            (void)vc4_linux_mesa_gles2_finish_log(&log);
            report("VC4_LINUX_MESA_GLES2_SUPERVISOR_FAILED "
                   "stage=waitpid errno=%d (%s)\n",
                   saved_errno, strerror(saved_errno));
            return -1;
        }
        nanosleep(&delay, NULL);
    }

    vc4_linux_mesa_gles2_stop_child(child);
    if (vc4_linux_mesa_gles2_finish_log(&log) < 0) {
        int saved_errno = errno;

        report("VC4_LINUX_MESA_GLES2_SUPERVISOR_FAILED "
               "stage=finish-timeout-log errno=%d (%s)\n",
               saved_errno, strerror(saved_errno));
        return -1;
    }
    marker("VC4_LINUX_MESA_GLES2_SUPERVISOR_TIMEOUT\n");
    return -1;
}
