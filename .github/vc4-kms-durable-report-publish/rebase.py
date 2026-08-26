#!/usr/bin/env python3
"""Make VC4 Linux witness detail records as durable as semantic markers."""

from __future__ import annotations

from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    content = path.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise RuntimeError(
            f"{path}: expected one semantic preimage, found {count}"
        )
    path.write_text(content.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    replace_once(
        Path("tests/vc4/linux-v3d-uapi-init.c"),
        """static int write_all(int fd, const char *text, size_t length)
{
    while (length != 0) {
        ssize_t written = write(fd, text, length);

        if (written < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -1;
        }
        text += written;
        length -= (size_t)written;
    }
    return 0;
}

static void marker(const char *text)
{
    int saved_errno = errno;
    size_t length = strlen(text);

    if (write_all(STDOUT_FILENO, text, length) < 0) {
        int fd = open("/dev/kmsg", O_WRONLY | O_CLOEXEC);

        if (fd >= 0) {
            (void)write_all(fd, text, length);
            close(fd);
        }
    }
    errno = saved_errno;
}

static void report(const char *format, ...)
{
    char buffer[768];
    va_list arguments;
    int length;

    va_start(arguments, format);
    length = vsnprintf(buffer, sizeof(buffer), format, arguments);
    va_end(arguments);
    if (length <= 0) {
        return;
    }
    if ((size_t)length >= sizeof(buffer)) {
        length = (int)sizeof(buffer) - 1;
    }
    (void)write_all(STDOUT_FILENO, buffer, (size_t)length);
}
""",
        """static int write_all(int fd, const char *text, size_t length)
{
    while (length != 0) {
        ssize_t written = write(fd, text, length);

        if (written < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -1;
        }
        if (written == 0) {
            errno = EIO;
            return -1;
        }
        text += written;
        length -= (size_t)written;
    }
    return 0;
}

static void emit_text(const char *text, size_t length)
{
    int fd;

    if (write_all(STDOUT_FILENO, text, length) == 0) {
        return;
    }

    fd = open("/dev/kmsg", O_WRONLY | O_CLOEXEC);
    if (fd >= 0) {
        (void)write_all(fd, text, length);
        close(fd);
    }
}

static void marker(const char *text)
{
    int saved_errno = errno;

    emit_text(text, strlen(text));
    errno = saved_errno;
}

static void report(const char *format, ...)
{
    int saved_errno = errno;
    char buffer[768];
    va_list arguments;
    int length;

    va_start(arguments, format);
    length = vsnprintf(buffer, sizeof(buffer), format, arguments);
    va_end(arguments);
    if (length > 0) {
        if ((size_t)length >= sizeof(buffer)) {
            length = (int)sizeof(buffer) - 1;
        }
        emit_text(buffer, (size_t)length);
    }
    errno = saved_errno;
}
""",
    )
    replace_once(
        Path("tests/vc4/linux-v3d-modular-init.c"),
        """/*
 * Detailed report() records are best-effort diagnostics: a busy serial tty
 * can reject a nonblocking write.  marker() has a /dev/kmsg fallback, so
 * repeat every semantically important successful stage as a plain marker.
 * These markers are emitted only after the corresponding aggregate helper
 * has returned success; they do not weaken the witness.
 */
""",
        """/*
 * Detailed report() records and semantic markers share a /dev/kmsg fallback,
 * so both survive nonblocking serial pressure.  Repeat every semantically
 * important successful stage as a plain marker as well: markers keep the
 * classifier independent of diagnostic formatting.  They are emitted only
 * after the corresponding aggregate helper has returned success and do not
 * weaken the witness.
 */
""",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
