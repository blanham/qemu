/*
 * Pinned Mesa/EGL/GLES2 frontier for the Linux VC4 render node.
 *
 * This is intentionally a real userspace driver test rather than another
 * handcrafted DRM submit.  It creates a surfaceless EGL context, requires the
 * Mesa VC4 renderer, compiles shaders, draws a full-surface triangle, waits
 * for completion, and verifies pixels read back through GLES2.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#define _GNU_SOURCE

#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>

#include <errno.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#ifndef EGL_PLATFORM_SURFACELESS_MESA
#define EGL_PLATFORM_SURFACELESS_MESA 0x31DD
#endif

#define VC4_MESA_SURFACE_WIDTH  64
#define VC4_MESA_SURFACE_HEIGHT 64
#define VC4_MESA_TIMEOUT_SECONDS 40U

static void vc4_mesa_write_all(const char *buffer, size_t length)
{
    int saved_errno = errno;

    while (length > 0) {
        ssize_t written = write(STDERR_FILENO, buffer, length);

        if (written > 0) {
            buffer += written;
            length -= written;
            continue;
        }
        if (written < 0 && errno == EINTR) {
            continue;
        }
        break;
    }
    errno = saved_errno;
}

static void vc4_mesa_report(const char *format, ...)
{
    char buffer[1024];
    va_list arguments;
    int length;

    va_start(arguments, format);
    length = vsnprintf(buffer, sizeof(buffer), format, arguments);
    va_end(arguments);
    if (length < 0) {
        return;
    }
    if ((size_t)length >= sizeof(buffer)) {
        length = sizeof(buffer) - 1;
    }
    vc4_mesa_write_all(buffer, (size_t)length);
}

static void vc4_mesa_timeout(int signal_number)
{
    static const char message[] =
        "VC4_LINUX_MESA_GLES2_TIMEOUT stage=process-alarm\n";

    (void)signal_number;
    vc4_mesa_write_all(message, sizeof(message) - 1);
    _exit(124);
}

static void vc4_mesa_fail(const char *stage)
{
    EGLint egl_error = eglGetError();
    GLenum gl_error = glGetError();

    vc4_mesa_report(
        "VC4_LINUX_MESA_GLES2_FAILED stage=%s egl=0x%04x "
        "gl=0x%04x errno=%d\n",
        stage, (unsigned)egl_error, (unsigned)gl_error, errno);
    exit(EXIT_FAILURE);
}

static void vc4_mesa_require_gl(const char *stage)
{
    GLenum error = glGetError();

    if (error != GL_NO_ERROR) {
        vc4_mesa_report(
            "VC4_LINUX_MESA_GLES2_GL_ERROR stage=%s error=0x%04x\n",
            stage, (unsigned)error);
        vc4_mesa_fail(stage);
    }
}

static GLuint vc4_mesa_compile_shader(GLenum type, const char *source,
                                      const char *label)
{
    GLuint shader = glCreateShader(type);
    GLint compiled = GL_FALSE;

    if (shader == 0) {
        vc4_mesa_fail(label);
    }
    glShaderSource(shader, 1, &source, NULL);
    glCompileShader(shader);
    glGetShaderiv(shader, GL_COMPILE_STATUS, &compiled);
    if (compiled != GL_TRUE) {
        char log[2048] = { 0 };
        GLsizei length = 0;

        glGetShaderInfoLog(shader, sizeof(log) - 1, &length, log);
        vc4_mesa_report(
            "VC4_LINUX_MESA_GLES2_SHADER_LOG stage=%s length=%d "
            "text=%s\n",
            label, (int)length, log);
        glDeleteShader(shader);
        vc4_mesa_fail(label);
    }

    vc4_mesa_report(
        "VC4_LINUX_MESA_GLES2_SHADER_COMPILE_OK stage=%s\n", label);
    return shader;
}

static bool vc4_mesa_near(uint8_t actual, uint8_t expected)
{
    unsigned difference = actual > expected ?
                          actual - expected : expected - actual;

    return difference <= 4;
}

static void vc4_mesa_verify_pixel(const uint8_t pixel[4],
                                  unsigned x, unsigned y)
{
    static const uint8_t expected[4] = { 32, 128, 223, 255 };

    if (!vc4_mesa_near(pixel[0], expected[0]) ||
        !vc4_mesa_near(pixel[1], expected[1]) ||
        !vc4_mesa_near(pixel[2], expected[2]) ||
        !vc4_mesa_near(pixel[3], expected[3])) {
        vc4_mesa_report(
            "VC4_LINUX_MESA_GLES2_PIXEL_MISMATCH x=%u y=%u "
            "actual=%u,%u,%u,%u expected=%u,%u,%u,%u\n",
            x, y, pixel[0], pixel[1], pixel[2], pixel[3],
            expected[0], expected[1], expected[2], expected[3]);
        vc4_mesa_fail("pixel-mismatch");
    }

    vc4_mesa_report(
        "VC4_LINUX_MESA_GLES2_PIXEL_OK x=%u y=%u rgba=%u,%u,%u,%u\n",
        x, y, pixel[0], pixel[1], pixel[2], pixel[3]);
}

int main(void)
{
    static const EGLint config_attributes[] = {
        EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
        EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
        EGL_RED_SIZE, 8,
        EGL_GREEN_SIZE, 8,
        EGL_BLUE_SIZE, 8,
        EGL_ALPHA_SIZE, 8,
        EGL_NONE,
    };
    static const EGLint pbuffer_attributes[] = {
        EGL_WIDTH, VC4_MESA_SURFACE_WIDTH,
        EGL_HEIGHT, VC4_MESA_SURFACE_HEIGHT,
        EGL_NONE,
    };
    static const EGLint context_attributes[] = {
        EGL_CONTEXT_CLIENT_VERSION, 2,
        EGL_NONE,
    };
    static const char vertex_source[] =
        "attribute vec2 a_position;\n"
        "void main(void) {\n"
        "    gl_Position = vec4(a_position, 0.0, 1.0);\n"
        "}\n";
    static const char fragment_source[] =
        "precision mediump float;\n"
        "void main(void) {\n"
        "    gl_FragColor = vec4(0.125, 0.5, 0.875, 1.0);\n"
        "}\n";
    static const GLfloat vertices[] = {
        -1.0f, -1.0f,
         3.0f, -1.0f,
        -1.0f,  3.0f,
    };
    static const unsigned sample_x[] = { 1, 32, 62 };
    static const unsigned sample_y[] = { 1, 32, 62 };
    PFNEGLGETPLATFORMDISPLAYEXTPROC get_platform_display;
    EGLDisplay display = EGL_NO_DISPLAY;
    EGLConfig config = NULL;
    EGLSurface surface = EGL_NO_SURFACE;
    EGLContext context = EGL_NO_CONTEXT;
    EGLint config_count = 0;
    EGLint major = 0;
    EGLint minor = 0;
    const char *renderer;
    const char *vendor;
    const char *version;
    GLuint vertex_shader = 0;
    GLuint fragment_shader = 0;
    GLuint program = 0;
    GLint linked = GL_FALSE;
    GLint position = -1;

    signal(SIGALRM, vc4_mesa_timeout);
    alarm(VC4_MESA_TIMEOUT_SECONDS);
    vc4_mesa_report("VC4_LINUX_MESA_GLES2_START\n");

    get_platform_display = (PFNEGLGETPLATFORMDISPLAYEXTPROC)
        eglGetProcAddress("eglGetPlatformDisplayEXT");
    if (get_platform_display != NULL) {
        display = get_platform_display(
            EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL);
    } else {
        display = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    }
    if (display == EGL_NO_DISPLAY) {
        vc4_mesa_fail("egl-get-display");
    }
    vc4_mesa_report("VC4_LINUX_MESA_GLES2_EGL_DISPLAY_OK\n");

    if (eglInitialize(display, &major, &minor) != EGL_TRUE) {
        vc4_mesa_fail("egl-initialize");
    }
    vc4_mesa_report(
        "VC4_LINUX_MESA_GLES2_EGL_INITIALIZE_OK version=%d.%d\n",
        major, minor);
    vc4_mesa_report(
        "VC4_LINUX_MESA_GLES2_EGL_INFO vendor=%s version=%s apis=%s\n",
        eglQueryString(display, EGL_VENDOR),
        eglQueryString(display, EGL_VERSION),
        eglQueryString(display, EGL_CLIENT_APIS));

    if (eglBindAPI(EGL_OPENGL_ES_API) != EGL_TRUE) {
        vc4_mesa_fail("egl-bind-api");
    }
    if (eglChooseConfig(display, config_attributes, &config, 1,
                        &config_count) != EGL_TRUE ||
        config_count != 1 || config == NULL) {
        vc4_mesa_fail("egl-choose-config");
    }
    vc4_mesa_report("VC4_LINUX_MESA_GLES2_EGL_CONFIG_OK\n");

    surface = eglCreatePbufferSurface(
        display, config, pbuffer_attributes);
    if (surface == EGL_NO_SURFACE) {
        vc4_mesa_fail("egl-create-pbuffer");
    }
    vc4_mesa_report("VC4_LINUX_MESA_GLES2_EGL_SURFACE_OK\n");

    context = eglCreateContext(
        display, config, EGL_NO_CONTEXT, context_attributes);
    if (context == EGL_NO_CONTEXT) {
        vc4_mesa_fail("egl-create-context");
    }
    vc4_mesa_report("VC4_LINUX_MESA_GLES2_EGL_CONTEXT_OK\n");

    if (eglMakeCurrent(display, surface, surface, context) != EGL_TRUE) {
        vc4_mesa_fail("egl-make-current");
    }
    vc4_mesa_report("VC4_LINUX_MESA_GLES2_EGL_MAKE_CURRENT_OK\n");

    renderer = (const char *)glGetString(GL_RENDERER);
    vendor = (const char *)glGetString(GL_VENDOR);
    version = (const char *)glGetString(GL_VERSION);
    if (renderer == NULL || vendor == NULL || version == NULL) {
        vc4_mesa_fail("gl-strings");
    }
    vc4_mesa_report(
        "VC4_LINUX_MESA_GLES2_GL_INFO renderer=%s vendor=%s version=%s\n",
        renderer, vendor, version);
    if (strcasestr(renderer, "vc4") == NULL) {
        vc4_mesa_fail("renderer-not-vc4");
    }
    vc4_mesa_report("VC4_LINUX_MESA_GLES2_RENDERER_VC4_OK\n");

    vertex_shader = vc4_mesa_compile_shader(
        GL_VERTEX_SHADER, vertex_source, "vertex");
    fragment_shader = vc4_mesa_compile_shader(
        GL_FRAGMENT_SHADER, fragment_source, "fragment");

    program = glCreateProgram();
    if (program == 0) {
        vc4_mesa_fail("create-program");
    }
    glAttachShader(program, vertex_shader);
    glAttachShader(program, fragment_shader);
    glBindAttribLocation(program, 0, "a_position");
    glLinkProgram(program);
    glGetProgramiv(program, GL_LINK_STATUS, &linked);
    if (linked != GL_TRUE) {
        char log[2048] = { 0 };
        GLsizei length = 0;

        glGetProgramInfoLog(program, sizeof(log) - 1, &length, log);
        vc4_mesa_report(
            "VC4_LINUX_MESA_GLES2_PROGRAM_LOG length=%d text=%s\n",
            (int)length, log);
        vc4_mesa_fail("link-program");
    }
    vc4_mesa_report("VC4_LINUX_MESA_GLES2_PROGRAM_LINK_OK\n");

    position = glGetAttribLocation(program, "a_position");
    if (position < 0) {
        vc4_mesa_fail("attribute-location");
    }

    glViewport(0, 0, VC4_MESA_SURFACE_WIDTH,
               VC4_MESA_SURFACE_HEIGHT);
    glDisable(GL_DITHER);
    glClearColor(0.0f, 0.0f, 0.0f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    glUseProgram(program);
    glVertexAttribPointer(
        (GLuint)position, 2, GL_FLOAT, GL_FALSE, 0, vertices);
    glEnableVertexAttribArray((GLuint)position);
    vc4_mesa_require_gl("draw-setup");

    vc4_mesa_report("VC4_LINUX_MESA_GLES2_DRAW_START\n");
    glDrawArrays(GL_TRIANGLES, 0, 3);
    vc4_mesa_require_gl("draw-arrays");
    vc4_mesa_report("VC4_LINUX_MESA_GLES2_DRAW_OK\n");

    vc4_mesa_report("VC4_LINUX_MESA_GLES2_FINISH_START\n");
    glFinish();
    vc4_mesa_require_gl("finish");
    vc4_mesa_report("VC4_LINUX_MESA_GLES2_FINISH_OK\n");

    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    vc4_mesa_report("VC4_LINUX_MESA_GLES2_READPIXELS_START\n");
    for (size_t index = 0;
         index < sizeof(sample_x) / sizeof(sample_x[0]);
         index++) {
        uint8_t pixel[4] = { 0 };

        glReadPixels((GLint)sample_x[index], (GLint)sample_y[index],
                     1, 1, GL_RGBA, GL_UNSIGNED_BYTE, pixel);
        vc4_mesa_require_gl("read-pixels");
        vc4_mesa_verify_pixel(
            pixel, sample_x[index], sample_y[index]);
    }
    vc4_mesa_report("VC4_LINUX_MESA_GLES2_READPIXELS_OK\n");
    vc4_mesa_report("VC4_LINUX_MESA_GLES2_PIXELS_OK\n");

    glDisableVertexAttribArray((GLuint)position);
    glUseProgram(0);
    glDeleteProgram(program);
    glDeleteShader(fragment_shader);
    glDeleteShader(vertex_shader);
    (void)eglMakeCurrent(
        display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    (void)eglDestroyContext(display, context);
    (void)eglDestroySurface(display, surface);
    (void)eglTerminate(display);

    alarm(0);
    vc4_mesa_report("VC4_LINUX_MESA_GLES2_OK\n");
    return EXIT_SUCCESS;
}
