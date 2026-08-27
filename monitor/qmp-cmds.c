/*
 * QEMU Management Protocol commands
 *
 * Copyright IBM, Corp. 2011
 *
 * Authors:
 *  Anthony Liguori   <aliguori@us.ibm.com>
 *
 * This work is licensed under the terms of the GNU GPL, version 2.  See
 * the COPYING file in the top-level directory.
 *
 * Contributions after 2012-01-13 are licensed under the terms of the
 * GNU GPL, version 2 or (at your option) any later version.
 */

#include "qemu/osdep.h"
#include "qemu/ctype.h"
#include "qemu/sockets.h"
#include "qemu/log.h"
#include "exec/gdbstub.h"
#include "hw/core/cpu.h"
#include "qemu/target-info.h"
#include "system/hw_accel.h"
#include "monitor-internal.h"
#include "monitor/qdev.h"
#include "monitor/qmp-helpers.h"
#include "system/system.h"
#include "system/kvm.h"
#include "system/runstate.h"
#include "system/runstate-action.h"
#include "system/block-backend.h"
#include "qapi/error.h"
#include "qapi/qapi-init-commands.h"
#include "qapi/qapi-commands-control.h"
#include "qapi/qapi-commands-misc.h"
#include "qapi/qmp/qerror.h"
#include "qapi/type-helpers.h"
#include "hw/mem/memory-device.h"
#include "hw/intc/intc.h"
#include "migration/misc.h"

typedef struct WD40RegisterDescriptor {
    int number;
    const char *name;
    const char *feature;
} WD40RegisterDescriptor;

static bool wd40_register_descriptor_present(const GArray *descriptors,
                                               int number)
{
    guint i;

    for (i = 0; i < descriptors->len; i++) {
        const WD40RegisterDescriptor *descriptor =
            &g_array_index(descriptors, WD40RegisterDescriptor, i);

        if (descriptor->number == number) {
            return true;
        }
    }
    return false;
}

static int wd40_register_descriptor_compare(const void *left,
                                             const void *right)
{
    const WD40RegisterDescriptor *left_descriptor = left;
    const WD40RegisterDescriptor *right_descriptor = right;

    return (left_descriptor->number > right_descriptor->number) -
           (left_descriptor->number < right_descriptor->number);
}

static char *wd40_register_value_to_hex(const GByteArray *value)
{
    static const char digits[] = "0123456789abcdef";
    char *hex = g_malloc_n((gsize)value->len + 1, 2);
    guint i;

    for (i = 0; i < value->len; i++) {
        hex[i * 2] = digits[value->data[i] >> 4];
        hex[i * 2 + 1] = digits[value->data[i] & 0x0f];
    }
    hex[value->len * 2] = '\0';
    return hex;
}

static CPUState *wd40_cpu_by_index(bool has_cpu_index, int64_t cpu_index)
{
    CPUState *cpu;

    if (!has_cpu_index) {
        return first_cpu;
    }

    CPU_FOREACH(cpu) {
        if (cpu->cpu_index == cpu_index) {
            return cpu;
        }
    }
    return NULL;
}

WD40CPURegisterSnapshot *
qmp_x_wd40_query_cpu_registers(bool has_cpu_index, int64_t cpu_index,
                                Error **errp)
{
    CPUState *cpu = wd40_cpu_by_index(has_cpu_index, cpu_index);
    GArray *gdb_descriptors = NULL;
    GArray *descriptors = NULL;
    GByteArray *value = NULL;
    WD40CPURegisterSnapshot *snapshot = NULL;
    WD40CPURegisterList **tail;
    guint i;

    if (!cpu) {
        if (has_cpu_index) {
            error_setg(errp, "CPU index %" PRId64 " does not exist",
                       cpu_index);
        } else {
            error_setg(errp, "No realized CPU is available");
        }
        return NULL;
    }

    cpu_synchronize_state(cpu);
    gdb_descriptors = gdb_get_register_list(cpu);
    descriptors = g_array_new(false, false,
                              sizeof(WD40RegisterDescriptor));

    /* gdb_read_register() checks the legacy core range first. */
    for (i = 0; i < cpu->cc->gdb_num_core_regs; i++) {
        WD40RegisterDescriptor descriptor = {
            .number = i,
        };

        g_array_append_val(descriptors, descriptor);
    }

    /* Supplemental feature ranges are checked in registration order. */
    for (i = 0; i < gdb_descriptors->len; i++) {
        const GDBRegDesc *gdb_descriptor =
            &g_array_index(gdb_descriptors, GDBRegDesc, i);
        WD40RegisterDescriptor descriptor = {
            .number = gdb_descriptor->gdb_reg,
            .name = gdb_descriptor->name,
            .feature = gdb_descriptor->feature_name,
        };

        if (wd40_register_descriptor_present(descriptors,
                                             descriptor.number)) {
            error_setg(errp,
                       "CPU type '%s' exposes GDB register %d more than once",
                       object_get_typename(OBJECT(cpu)), descriptor.number);
            goto fail;
        }
        g_array_append_val(descriptors, descriptor);
    }

    if (descriptors->len == 0) {
        error_setg(errp, "CPU type '%s' exposes no GDB registers",
                   object_get_typename(OBJECT(cpu)));
        goto fail;
    }
    g_array_sort(descriptors, wd40_register_descriptor_compare);

    snapshot = g_new0(WD40CPURegisterSnapshot, 1);
    snapshot->cpu_index = cpu->cpu_index;
    snapshot->target = g_strdup(target_name());
    snapshot->target_bits = target_long_bits();
    snapshot->target_big_endian = target_big_endian();
    snapshot->qom_type = g_strdup(object_get_typename(OBJECT(cpu)));
    tail = &snapshot->registers;
    value = g_byte_array_new();

    for (i = 0; i < descriptors->len; i++) {
        const WD40RegisterDescriptor *descriptor =
            &g_array_index(descriptors, WD40RegisterDescriptor, i);
        WD40CPURegister *info;
        WD40CPURegisterList *entry;
        bool name_valid;
        int bytes;

        g_byte_array_set_size(value, 0);
        bytes = gdb_read_register(cpu, value, descriptor->number);
        if (bytes < 0 || (guint)bytes != value->len) {
            error_setg(errp,
                       "GDB register %d returned inconsistent size %d/%u",
                       descriptor->number, bytes, value->len);
            goto fail;
        }

        info = g_new0(WD40CPURegister, 1);
        info->number = descriptor->number;
        name_valid = descriptor->name &&
                     g_utf8_validate(descriptor->name, -1, NULL);
        info->described = name_valid;
        info->name = name_valid
            ? g_strdup(descriptor->name)
            : g_strdup_printf("gdb-reg-%d", descriptor->number);
        if (descriptor->feature &&
            g_utf8_validate(descriptor->feature, -1, NULL)) {
            info->feature = g_strdup(descriptor->feature);
        }
        info->available = bytes > 0;
        info->bytes = value->len;
        if (info->available) {
            info->value = wd40_register_value_to_hex(value);
        }

        entry = g_new0(WD40CPURegisterList, 1);
        entry->value = info;
        *tail = entry;
        tail = &entry->next;
    }

    g_byte_array_unref(value);
    g_array_free(descriptors, true);
    g_array_free(gdb_descriptors, true);
    return snapshot;

fail:
    if (value) {
        g_byte_array_unref(value);
    }
    if (descriptors) {
        g_array_free(descriptors, true);
    }
    if (gdb_descriptors) {
        g_array_free(gdb_descriptors, true);
    }
    qapi_free_WD40CPURegisterSnapshot(snapshot);
    return NULL;
}

static LogCategoryInfoList *qmp_log_category_info_list(void)
{
    const QEMULogItem *item;
    LogCategoryInfoList *list = NULL;
    LogCategoryInfoList **tail = &list;
    unsigned mask = qemu_get_log_mask();

    for (item = qemu_log_items; item->mask != 0; item++) {
        LogCategoryInfo *info = g_new0(LogCategoryInfo, 1);
        LogCategoryInfoList *entry = g_new0(LogCategoryInfoList, 1);

        info->name = g_strdup(item->name);
        info->help = g_strdup(item->help);
        info->enabled = (mask & item->mask) == item->mask;
        info->sticky = item->mask == LOG_PER_THREAD;
        entry->value = info;
        *tail = entry;
        tail = &entry->next;
    }

    return list;
}

static bool qmp_log_category_mask(strList *categories, unsigned *mask,
                                  Error **errp)
{
    strList *category;
    unsigned result = 0;

    for (category = categories; category; category = category->next) {
        const QEMULogItem *item;

        for (item = qemu_log_items; item->mask != 0; item++) {
            if (g_str_equal(category->value, item->name)) {
                result |= item->mask;
                break;
            }
        }
        if (item->mask == 0) {
            error_setg(errp, "Unknown log category '%s'", category->value);
            return false;
        }
    }

    *mask = result;
    return true;
}

LogCategoryInfoList *qmp_query_log_categories(Error **errp)
{
    return qmp_log_category_info_list();
}

LogCategoryInfoList *qmp_set_log_categories(LogCategoryAction action,
                                             strList *categories,
                                             Error **errp)
{
    unsigned current = qemu_get_log_mask();
    unsigned selected;
    unsigned target;

    if (!qmp_log_category_mask(categories, &selected, errp)) {
        return NULL;
    }

    switch (action) {
    case LOG_CATEGORY_ACTION_REPLACE:
        target = selected;
        break;
    case LOG_CATEGORY_ACTION_ENABLE:
        target = current | selected;
        break;
    case LOG_CATEGORY_ACTION_DISABLE:
        target = current & ~selected;
        break;
    default:
        g_assert_not_reached();
    }

    if ((current ^ target) & LOG_PER_THREAD) {
        if (current & LOG_PER_THREAD) {
            error_setg(errp,
                       "The 'tid' log category cannot be disabled once set");
        } else {
            error_setg(errp,
                       "The 'tid' log category can only be selected at "
                       "process startup with a '%%d' logfile template");
        }
        return NULL;
    }
    if (!qemu_set_log(target, errp)) {
        return NULL;
    }

    return qmp_log_category_info_list();
}

NameInfo *qmp_query_name(Error **errp)
{
    NameInfo *info = g_malloc0(sizeof(*info));

    info->name = g_strdup(qemu_name);
    return info;
}

void qmp_quit(Error **errp)
{
    shutdown_action = SHUTDOWN_ACTION_POWEROFF;
    qemu_system_shutdown_request(SHUTDOWN_CAUSE_HOST_QMP_QUIT);
}

void qmp_stop(Error **errp)
{
    /* if there is a dump in background, we should wait until the dump
     * finished */
    if (qemu_system_dump_in_progress()) {
        error_setg(errp, "There is a dump in process, please wait.");
        return;
    }

    if (runstate_check(RUN_STATE_INMIGRATE)) {
        autostart = 0;
    } else {
        vm_stop(RUN_STATE_PAUSED);
    }
}

void qmp_cont(Error **errp)
{
    BlockBackend *blk;
    BlockJob *job;
    Error *local_err = NULL;

    /* if there is a dump in background, we should wait until the dump
     * finished */
    if (qemu_system_dump_in_progress()) {
        error_setg(errp, "There is a dump in process, please wait.");
        return;
    }

    if (runstate_needs_reset()) {
        error_setg(errp, "Resetting the Virtual Machine is required");
        return;
    } else if (runstate_check(RUN_STATE_SUSPENDED)) {
        return;
    } else if (runstate_check(RUN_STATE_FINISH_MIGRATE)) {
        error_setg(errp, "Migration is not finalized yet");
        return;
    } else if (runstate_check(RUN_STATE_COLO)) {
        error_setg(errp, "COLO checkpoint in progress");
        return;
    }

    for (blk = blk_next(NULL); blk; blk = blk_next(blk)) {
        blk_iostatus_reset(blk);
    }

    WITH_JOB_LOCK_GUARD() {
        for (job = block_job_next_locked(NULL); job;
             job = block_job_next_locked(job)) {
            block_job_iostatus_reset_locked(job);
        }
    }

    if (runstate_check(RUN_STATE_INMIGRATE)) {
        autostart = 1;
    } else {
        /*
         * Continuing after completed migration. Images have been
         * inactivated to allow the destination to take control. Need to
         * get control back now.
         */
        if (!migration_block_activate(&local_err)) {
            error_propagate(errp, local_err);
            return;
        }
        vm_start();
    }
}

void qmp_add_client(const char *protocol, const char *fdname,
                    bool has_skipauth, bool skipauth, bool has_tls, bool tls,
                    Error **errp)
{
    static const struct {
        const char *name;
        bool (*add_client)(int fd, bool has_skipauth, bool skipauth,
                           bool has_tls, bool tls, Error **errp);
    } protocol_table[] = {
        { "spice", qmp_add_client_spice },
#ifdef CONFIG_VNC
        { "vnc", qmp_add_client_vnc },
#endif
#ifdef CONFIG_DBUS_DISPLAY
        { "@dbus-display", qmp_add_client_dbus_display },
#endif
    };
    int fd, i;

    fd = monitor_get_fd(monitor_cur(), fdname, errp);
    if (fd < 0) {
        return;
    }

    if (!fd_is_socket(fd)) {
        error_setg(errp, "parameter @fdname must name a socket");
        close(fd);
        return;
    }

    for (i = 0; i < ARRAY_SIZE(protocol_table); i++) {
        if (!strcmp(protocol, protocol_table[i].name)) {
            if (!protocol_table[i].add_client(fd, has_skipauth, skipauth,
                                              has_tls, tls, errp)) {
                close(fd);
            }
            return;
        }
    }

    if (!qmp_add_client_char(fd, has_skipauth, skipauth, has_tls, tls,
                             protocol, errp)) {
        close(fd);
    }
}

char *qmp_human_monitor_command(const char *command_line, bool has_cpu_index,
                                int64_t cpu_index, Error **errp)
{
    char *output = NULL;
    MonitorHMP *hmp = MONITOR_HMP(object_new(TYPE_MONITOR_HMP));

    if (has_cpu_index) {
        int ret = monitor_set_cpu(&hmp->parent_obj, cpu_index);
        if (ret < 0) {
            error_setg(errp, QERR_INVALID_PARAMETER_VALUE, "cpu-index",
                       "a CPU number");
            goto out;
        }
    }

    handle_hmp_command(hmp, command_line);

    WITH_QEMU_LOCK_GUARD(&hmp->parent_obj.mon_lock) {
        output = g_strdup(hmp->parent_obj.outbuf->str);
    }

out:
    object_unref(hmp);
    return output;
}

static bool wd40_capture_in_progress;

static bool wd40_capture_command_is_recursive(const char *command_line)
{
    const char *start = command_line;
    const char *end;
    g_autofree char *name = NULL;

    while (qemu_isspace(*start)) {
        start++;
    }
    end = start;
    while (*end && !qemu_isspace(*end) && *end != '/') {
        end++;
    }
    if (end == start) {
        return false;
    }

    name = g_strndup(start, end - start);
    return hmp_compare_cmd(name, "capture-output|save-output");
}

static bool wd40_write_capture_file(const char *path, bool append,
                                      const char *text, size_t length,
                                      Error **errp)
{
    GError *gerr = NULL;
    int fd;
    ssize_t written;
    int saved_errno;

    if (length > G_MAXSSIZE) {
        error_setg(errp, "Captured output is too large to write");
        return false;
    }

    if (!append) {
        /* g_file_set_contents() uses a consistent whole-file replacement. */
        if (!g_file_set_contents(path, text, (gssize)length, &gerr)) {
            error_setg(errp, "Could not write '%s': %s",
                       path, gerr->message);
            g_error_free(gerr);
            return false;
        }
        return true;
    }

    fd = qemu_create(path, O_WRONLY | O_BINARY | O_APPEND, 0666, errp);
    if (fd < 0) {
        return false;
    }

    written = qemu_write_full(fd, text, length);
    if (written < 0 || (size_t)written != length) {
        saved_errno = written < 0 ? errno : EIO;
        qemu_close(fd);
        error_setg_errno(errp, saved_errno, "Could not append to '%s'", path);
        return false;
    }

    if (qemu_close(fd) < 0) {
        error_setg_errno(errp, errno, "Could not close '%s'", path);
        return false;
    }
    return true;
}

WD40TextCapture *qmp_x_wd40_capture_hmp(const char *command_line,
                                         bool has_cpu_index,
                                         int64_t cpu_index,
                                         const char *path,
                                         bool has_append, bool append,
                                         bool has_return_text,
                                         bool return_text,
                                         Error **errp)
{
    g_autofree char *output = NULL;
    WD40TextCapture *result;
    bool keep_text = !has_return_text || return_text;
    size_t length;

    append = has_append && append;
    if (!command_line[0]) {
        error_setg(errp, "command-line must not be empty");
        return NULL;
    }
    if (append && !path) {
        error_setg(errp, "append requires path");
        return NULL;
    }
    if (!path && !keep_text) {
        error_setg(errp,
                   "at least one output destination must be selected");
        return NULL;
    }
    if (wd40_capture_command_is_recursive(command_line) ||
        wd40_capture_in_progress) {
        error_setg(errp, "nested WD40 output capture is not supported");
        return NULL;
    }

    wd40_capture_in_progress = true;
    output = qmp_human_monitor_command(command_line, has_cpu_index,
                                       cpu_index, errp);
    wd40_capture_in_progress = false;
    if (!output) {
        return NULL;
    }
    length = strlen(output);

    if (path && !wd40_write_capture_file(path, append, output, length, errp)) {
        return NULL;
    }

    result = g_new0(WD40TextCapture, 1);
    result->bytes = length;
    result->append = path && append;
    result->path = g_strdup(path);
    if (keep_text) {
        result->text = g_steal_pointer(&output);
    }
    return result;
}

static void __attribute__((__constructor__)) monitor_init_qmp_commands(void)
{
    /*
     * Two command lists:
     * - qmp_commands contains all QMP commands
     * - qmp_cap_negotiation_commands contains just
     *   "qmp_capabilities", to enforce capability negotiation
     */

    qmp_init_marshal(&qmp_commands);

    qmp_register_command(&qmp_commands, "device_add",
                         qmp_device_add, 0, 0);

    QTAILQ_INIT(&qmp_cap_negotiation_commands);
    qmp_register_command(&qmp_cap_negotiation_commands, "qmp_capabilities",
                         qmp_marshal_qmp_capabilities,
                         QCO_ALLOW_PRECONFIG, 0);
}
