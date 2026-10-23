/*
 * SPDX-License-Identifier: MIT
 * Guest-only USBFS syscall-entry gate for the RustUSB QEMU test harness.
 *
 * This tool deliberately does not inspect ioctl argument pointers, USB data,
 * registers, or syscall return values other than GET_SYSCALL_INFO's scalar
 * result. It neither changes a syscall nor emulates one. A held entry is
 * BEFORE the kernel executes that ioctl, not an in-kernel active USB request.
 * See docs/research/usbfs-host-state-race-design.md for the test contract.
 */
#define _GNU_SOURCE
#include <sys/ioctl.h>
#include <sys/ptrace.h>
#include <linux/ptrace.h>
#include <linux/usbdevice_fs.h>
#include <linux/audit.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <signal.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#if !defined(__linux__) || !defined(__x86_64__)
#error "This isolated QEMU test tool supports Linux x86_64 only"
#endif

#define DIRECTORY "/run/rustusb-gate"
#define MARKER "rustusb.host_state_test=1"
#define MAX_THREADS 128
#define MAX_EVENTS 16384
#define MAX_LOG_BYTES (4U * 1024U * 1024U)
#define GATE_NS UINT64_C(8000000000)
#define PROCESS_NS UINT64_C(120000000000)
#define REAP_NS UINT64_C(3000000000)

enum mutation { CLAIM, RELEASE, CONFIGURATION, ALTERNATE, CLEAR, DRIVER, MUTATIONS };
static const char *const names[MUTATIONS] = {
    "claim", "release", "configuration", "alternate", "clear", "driver"
};

struct thread {
    pid_t tid;
    bool live;
    bool ioctl;
    int fd;
    unsigned long request;
    enum mutation mutation;
};

struct gate {
    int directory;
    int events;
    pid_t child;
    uint64_t starttime;
    uint64_t started;
    struct stat node;
    struct thread threads[MAX_THREADS];
    uint64_t counts[MUTATIONS];
    uint64_t stops;
    size_t log_bytes;
    size_t log_events;
    bool ready;
    bool armed;
    bool entered;
    bool held;
    bool released;
    bool completed;
    bool child_exited;
    int child_status;
    char nonce[65];
    char operation[20];
    char arm[128];
    unsigned long request;
    pid_t tid;
    int fd;
    uint64_t entered_at;
};

static volatile sig_atomic_t interrupted;

static void signal_handler(int number)
{
    interrupted = number;
}

static uint64_t now_ns(void)
{
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC, &value) != 0)
        return 0;
    return (uint64_t)value.tv_sec * UINT64_C(1000000000) + (uint64_t)value.tv_nsec;
}

static int write_all(int fd, const char *data, size_t length)
{
    while (length != 0) {
        ssize_t result = write(fd, data, length);
        if (result < 0 && errno == EINTR)
            continue;
        if (result <= 0)
            return -1;
        data += result;
        length -= (size_t)result;
    }
    return 0;
}

/* These files live in a root-owned, non-writable-by-others directory. */
static int publish(struct gate *gate, const char *name, const char *value)
{
    char temporary[96];
    struct stat existing;
    if (fstatat(gate->directory, name, &existing, AT_SYMLINK_NOFOLLOW) == 0 ||
        errno != ENOENT)
        return -1;
    int length = snprintf(temporary, sizeof(temporary), ".%s.%ld.tmp", name, (long)getpid());
    if (length < 0 || (size_t)length >= sizeof(temporary))
        return -1;
    int fd = openat(gate->directory, temporary,
                    O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd < 0)
        return -1;
    int result = write_all(fd, value, strlen(value));
    if (result == 0 && fsync(fd) != 0)
        result = -1;
    if (close(fd) != 0)
        result = -1;
    if (result == 0 && renameat(gate->directory, temporary, gate->directory, name) != 0)
        result = -1;
    if (result != 0)
        (void)unlinkat(gate->directory, temporary, 0);
    return result;
}

static int counts_json(const struct gate *gate, char *buffer, size_t capacity)
{
    int length = snprintf(buffer, capacity,
        "{\"claim\":%" PRIu64 ",\"release\":%" PRIu64
        ",\"configuration\":%" PRIu64 ",\"alternate\":%" PRIu64
        ",\"clear\":%" PRIu64 ",\"driver\":%" PRIu64 "}",
        gate->counts[CLAIM], gate->counts[RELEASE], gate->counts[CONFIGURATION],
        gate->counts[ALTERNATE], gate->counts[CLEAR], gate->counts[DRIVER]);
    return length >= 0 && (size_t)length < capacity ? 0 : -1;
}

/* Event strings and the nonce are validated ASCII, never tracee output. */
static int event(struct gate *gate, const char *kind, pid_t tid, int fd,
                 unsigned long request, const char *extra)
{
    char counts[384];
    char line[2048];
    if (counts_json(gate, counts, sizeof(counts)) != 0)
        return -1;
    int length = snprintf(line, sizeof(line),
        "{\"schema_version\":1,\"event\":\"%s\",\"nonce\":\"%s\","
        "\"operation\":\"%s\",\"tracer_pid\":%ld,\"tracee_pid\":%ld,"
        "\"tracee_starttime\":%" PRIu64 ",\"tid\":%ld,\"fd\":%d,"
        "\"request\":%lu,\"held\":%s,\"node_device\":%ju,"
        "\"node_inode\":%ju,\"node_rdev\":%ju,\"monotonic_ns\":%" PRIu64 ","
        "\"mutation_counts\":%s%s}\n",
        kind, gate->nonce, gate->operation, (long)getpid(), (long)gate->child,
        gate->starttime, (long)tid, fd, request, gate->held ? "true" : "false",
        (uintmax_t)gate->node.st_dev, (uintmax_t)gate->node.st_ino,
        (uintmax_t)gate->node.st_rdev, now_ns(), counts, extra == NULL ? "" : extra);
    if (length < 0 || (size_t)length >= sizeof(line) ||
        gate->log_events >= MAX_EVENTS ||
        gate->log_bytes + (size_t)length > MAX_LOG_BYTES)
        return -1;
    if (write_all(gate->events, line, (size_t)length) != 0)
        return -1;
    gate->log_events++;
    gate->log_bytes += (size_t)length;
    return 0;
}

static int read_small(int directory, const char *name, char *buffer, size_t capacity)
{
    int fd = openat(directory, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    if (fd < 0)
        return errno == ENOENT ? 0 : -1;
    struct stat metadata;
    if (fstat(fd, &metadata) != 0 || !S_ISREG(metadata.st_mode) ||
        metadata.st_uid != 0 || metadata.st_nlink != 1 ||
        (metadata.st_mode & 022) != 0 || metadata.st_size < 0 ||
        (uintmax_t)metadata.st_size >= capacity) {
        (void)close(fd);
        return -1;
    }
    size_t used = 0;
    for (;;) {
        ssize_t count = read(fd, buffer + used, capacity - used - 1);
        if (count < 0 && errno == EINTR)
            continue;
        if (count < 0) {
            (void)close(fd);
            return -1;
        }
        if (count == 0)
            break;
        used += (size_t)count;
        if (used == capacity - 1) {
            (void)close(fd);
            return -1;
        }
    }
    if (close(fd) != 0)
        return -1;
    if (memchr(buffer, '\0', used) != NULL)
        return -1;
    buffer[used] = '\0';
    /* A shell may create/truncate just before its single short write. */
    if (used == 0 || buffer[used - 1] != '\n')
        return 0;
    if (memchr(buffer, '\n', used - 1) != NULL)
        return -1;
    return 1;
}

static int read_file(const char *path, char *buffer, size_t capacity)
{
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0)
        return -1;
    size_t used = 0;
    while (used + 1 < capacity) {
        ssize_t count = read(fd, buffer + used, capacity - used - 1);
        if (count < 0 && errno == EINTR)
            continue;
        if (count < 0) {
            (void)close(fd);
            return -1;
        }
        if (count == 0) {
            buffer[used] = '\0';
            return close(fd);
        }
        used += (size_t)count;
    }
    (void)close(fd);
    return -1;
}

static bool has_marker(void)
{
    char buffer[8192];
    if (read_file("/proc/cmdline", buffer, sizeof(buffer)) != 0)
        return false;
    char *state = NULL;
    for (char *token = strtok_r(buffer, " \t\r\n", &state); token != NULL;
         token = strtok_r(NULL, " \t\r\n", &state))
        if (strcmp(token, MARKER) == 0)
            return true;
    return false;
}

static int starttime(pid_t pid, uint64_t *result)
{
    char path[64];
    char buffer[4096];
    int size = snprintf(path, sizeof(path), "/proc/%ld/stat", (long)pid);
    if (size < 0 || (size_t)size >= sizeof(path) ||
        read_file(path, buffer, sizeof(buffer)) != 0)
        return -1;
    char *tail = strrchr(buffer, ')');
    if (tail == NULL || tail[1] != ' ')
        return -1;
    char *state = NULL;
    unsigned int field = 3;
    for (char *token = strtok_r(tail + 2, " ", &state); token != NULL;
         token = strtok_r(NULL, " ", &state), field++) {
        if (field == 22) {
            char *end = NULL;
            errno = 0;
            unsigned long long value = strtoull(token, &end, 10);
            if (errno != 0 || end == token || *end != '\0' || value == 0)
                return -1;
            *result = (uint64_t)value;
            return 0;
        }
    }
    return -1;
}

static int tracee_identity(struct gate *gate)
{
    char path[64];
    char buffer[8192];
    int length = snprintf(path, sizeof(path), "/proc/%ld/status", (long)gate->child);
    if (length < 0 || (size_t)length >= sizeof(path) ||
        read_file(path, buffer, sizeof(buffer)) != 0)
        return -1;
    unsigned int found = 0;
    char *state = NULL;
    for (char *line = strtok_r(buffer, "\n", &state); line != NULL;
         line = strtok_r(NULL, "\n", &state)) {
        unsigned long a, b, c, d;
        unsigned long long caps;
        if (sscanf(line, "Uid:\t%lu\t%lu\t%lu\t%lu", &a, &b, &c, &d) == 4) {
            if (a != 1000 || b != 1000 || c != 1000 || d != 1000)
                return -1;
            found |= 1U;
        } else if (sscanf(line, "Gid:\t%lu\t%lu\t%lu\t%lu", &a, &b, &c, &d) == 4) {
            if (a != 1000 || b != 1000 || c != 1000 || d != 1000)
                return -1;
            found |= 2U;
        } else if (sscanf(line, "CapEff:\t%llx", &caps) == 1) {
            if (caps != 0)
                return -1;
            found |= 4U;
        } else if (sscanf(line, "CapPrm:\t%llx", &caps) == 1) {
            if (caps != 0)
                return -1;
            found |= 8U;
        } else if (sscanf(line, "CapAmb:\t%llx", &caps) == 1) {
            if (caps != 0)
                return -1;
            found |= 16U;
        } else if (sscanf(line, "CapInh:\t%llx", &caps) == 1) {
            if (caps != 0)
                return -1;
            found |= 32U;
        }
    }
    uint64_t current;
    return found == 63U && starttime(gate->child, &current) == 0 &&
        current == gate->starttime ? 0 : -1;
}

static bool same_node(const struct gate *gate, pid_t tid, int fd)
{
    char path[80];
    struct stat metadata;
    int length = snprintf(path, sizeof(path), "/proc/%ld/fd/%d", (long)tid, fd);
    return length > 0 && (size_t)length < sizeof(path) && stat(path, &metadata) == 0 &&
        S_ISCHR(metadata.st_mode) && metadata.st_dev == gate->node.st_dev &&
        metadata.st_ino == gate->node.st_ino && metadata.st_rdev == gate->node.st_rdev;
}

static bool same_group(const struct gate *gate, pid_t tid)
{
    char path[64];
    char buffer[8192];
    int length = snprintf(path, sizeof(path), "/proc/%ld/status", (long)tid);
    if (length < 0 || (size_t)length >= sizeof(path) ||
        read_file(path, buffer, sizeof(buffer)) != 0)
        return false;
    char *state = NULL;
    for (char *line = strtok_r(buffer, "\n", &state); line != NULL;
         line = strtok_r(NULL, "\n", &state)) {
        long group;
        if (sscanf(line, "Tgid:\t%ld", &group) == 1)
            return group == (long)gate->child;
    }
    return false;
}

static struct thread *find_thread(struct gate *gate, pid_t tid, bool create)
{
    struct thread *empty = NULL;
    for (size_t i = 0; i < MAX_THREADS; i++) {
        if (gate->threads[i].live && gate->threads[i].tid == tid)
            return &gate->threads[i];
        if (!gate->threads[i].live && empty == NULL)
            empty = &gate->threads[i];
    }
    if (!create || empty == NULL)
        return NULL;
    memset(empty, 0, sizeof(*empty));
    empty->tid = tid;
    empty->live = true;
    return empty;
}

static int mutation(unsigned long request)
{
    if (request == USBDEVFS_CLAIMINTERFACE || request == USBDEVFS_DISCONNECT_CLAIM)
        return CLAIM;
    if (request == USBDEVFS_RELEASEINTERFACE)
        return RELEASE;
    if (request == USBDEVFS_SETCONFIGURATION)
        return CONFIGURATION;
    if (request == USBDEVFS_SETINTERFACE)
        return ALTERNATE;
    if (request == USBDEVFS_CLEAR_HALT)
        return CLEAR;
    if (request == USBDEVFS_IOCTL || request == USBDEVFS_CONNECT ||
        request == USBDEVFS_DISCONNECT || request == USBDEVFS_RESET ||
        request == USBDEVFS_RESETEP)
        return DRIVER;
    return -1;
}

static int gate_json(struct gate *gate, bool exit, int64_t returned, bool is_error)
{
    char counts[384];
    char value[2048];
    char result[160] = "";
    if (exit) {
        int length = snprintf(result, sizeof(result),
            ",\"return_value\":%" PRId64 ",\"is_error\":%s", returned,
            is_error ? "true" : "false");
        if (length < 0 || (size_t)length >= sizeof(result))
            return -1;
    }
    if (counts_json(gate, counts, sizeof(counts)) != 0)
        return -1;
    int length = snprintf(value, sizeof(value),
        "{\"schema_version\":1,\"event\":\"%s\",\"nonce\":\"%s\","
        "\"operation\":\"%s\",\"tracer_pid\":%ld,\"tracee_pid\":%ld,"
        "\"tracee_starttime\":%" PRIu64 ",\"tid\":%ld,\"fd\":%d,"
        "\"request\":%lu,\"node_device\":%ju,\"node_inode\":%ju,"
        "\"node_rdev\":%ju,\"monotonic_ns\":%" PRIu64 ",\"mutation_counts\":%s%s}\n",
        exit ? "exit" : "held", gate->nonce, gate->operation, (long)getpid(),
        (long)gate->child, gate->starttime, (long)gate->tid, gate->fd, gate->request,
        (uintmax_t)gate->node.st_dev, (uintmax_t)gate->node.st_ino,
        (uintmax_t)gate->node.st_rdev, now_ns(), counts, result);
    if (length < 0 || (size_t)length >= sizeof(value))
        return -1;
    return publish(gate, exit ? "exit.json" : "held.json", value);
}

static int controls(struct gate *gate)
{
    char line[128];
    int result = read_small(gate->directory, "arm", line, sizeof(line));
    if (result < 0)
        return -1;
    if (result == 1) {
        if (gate->armed) {
            if (strcmp(line, gate->arm) != 0)
                return -1;
        } else {
            if (!gate->ready)
                return -1;
            char nonce[65], operation[20], extra;
            if (sscanf(line, "%64s %19s %c", nonce, operation, &extra) != 2)
                return -1;
            char canonical[128];
            int length = snprintf(canonical, sizeof(canonical), "%s %s\n", nonce, operation);
            if (length < 0 || (size_t)length >= sizeof(canonical) || strcmp(canonical, line) != 0)
                return -1;
            for (size_t i = 0; nonce[i] != '\0'; i++) {
                char c = nonce[i];
                if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                      (c >= '0' && c <= '9') || c == '_' || c == '-'))
                    return -1;
            }
            if (strcmp(operation, "configuration") == 0)
                gate->request = USBDEVFS_SETCONFIGURATION;
            else if (strcmp(operation, "alternate") == 0)
                gate->request = USBDEVFS_SETINTERFACE;
            else if (strcmp(operation, "clear") == 0)
                gate->request = USBDEVFS_CLEAR_HALT;
            else if (strcmp(operation, "release") == 0)
                gate->request = USBDEVFS_RELEASEINTERFACE;
            else
                return -1;
            memcpy(gate->nonce, nonce, strlen(nonce) + 1);
            memcpy(gate->operation, operation, strlen(operation) + 1);
            memcpy(gate->arm, line, strlen(line) + 1);
            gate->armed = true;
            if (event(gate, "armed", 0, -1, gate->request, NULL) != 0)
                return -1;
        }
    } else if (gate->armed) {
        /* Removing, truncating, or replacing a consumed arm is not a new case. */
        return -1;
    }
    result = read_small(gate->directory, "release", line, sizeof(line));
    if (result < 0)
        return -1;
    if (result == 1) {
        char expected[80];
        int length = snprintf(expected, sizeof(expected), "%s\n", gate->nonce);
        if (!gate->entered || length < 0 || (size_t)length >= sizeof(expected) ||
            strcmp(line, expected) != 0)
            return -1;
        if (!gate->released) {
            if (!gate->held || !same_node(gate, gate->tid, gate->fd) ||
                tracee_identity(gate) != 0)
                return -1;
            /* Keep held=true in the release event: it describes the last stop. */
            if (event(gate, "released", gate->tid, gate->fd, gate->request, NULL) != 0)
                return -1;
            gate->held = false;
            gate->released = true;
            if (ptrace(PTRACE_SYSCALL, gate->tid, NULL, NULL) != 0)
                return -1;
        }
    }
    return 0;
}

static int exec_event(struct gate *gate, pid_t tid)
{
    char path[64];
    char executable[PATH_MAX];
    int length = snprintf(path, sizeof(path), "/proc/%ld/exe", (long)tid);
    if (length < 0 || (size_t)length >= sizeof(path))
        return -1;
    ssize_t count = readlink(path, executable, sizeof(executable) - 1);
    if (count < 0 || (size_t)count >= sizeof(executable) - 1)
        return -1;
    executable[count] = '\0';
    if (tid != gate->child || gate->ready)
        return -1;
    if (strcmp(executable, "/usr/bin/setpriv") == 0)
        return event(gate, "launcher_exec", tid, -1, 0, NULL);
    if (strcmp(executable, "/usr/bin/rustusb") != 0 || tracee_identity(gate) != 0)
        return -1;
    char value[768];
    length = snprintf(value, sizeof(value),
        "{\"schema_version\":1,\"event\":\"ready\",\"tracer_pid\":%ld,"
        "\"tracee_pid\":%ld,\"tracee_starttime\":%" PRIu64 ","
        "\"uid\":1000,\"gid\":1000,\"cap_effective\":0,\"cap_permitted\":0,"
        "\"cap_ambient\":0,\"cap_inheritable\":0,\"monotonic_ns\":%" PRIu64 "}\n",
        (long)getpid(), (long)gate->child, gate->starttime, now_ns());
    if (length < 0 || (size_t)length >= sizeof(value) ||
        publish(gate, "ready.json", value) != 0)
        return -1;
    gate->ready = true;
    return event(gate, "ready", tid, -1, 0, NULL);
}

/* Return 1 only for the one thread intentionally left at syscall-entry. */
static int syscall_stop(struct gate *gate, struct thread *thread)
{
    struct ptrace_syscall_info info;
    memset(&info, 0, sizeof(info));
    long size = ptrace(PTRACE_GET_SYSCALL_INFO, thread->tid, sizeof(info), &info);
    if (size < 0 || info.arch != AUDIT_ARCH_X86_64)
        return -1;
    if (info.op == PTRACE_SYSCALL_INFO_ENTRY) {
        if ((size_t)size < offsetof(struct ptrace_syscall_info, entry.args) + sizeof(info.entry.args))
            return -1;
        thread->ioctl = false;
        if (info.entry.nr != SYS_ioctl || info.entry.args[0] > INT_MAX)
            return 0;
        unsigned long request = (unsigned long)info.entry.args[1];
        int category = mutation(request);
        int fd = (int)info.entry.args[0];
        if (category < 0 || !same_node(gate, thread->tid, fd))
            return 0;
        if (!gate->ready)
            return -1;
        thread->ioctl = true;
        thread->fd = fd;
        thread->request = request;
        thread->mutation = (enum mutation)category;
        gate->counts[category]++;
        char extra[96];
        int length = snprintf(extra, sizeof(extra), ",\"mutation\":\"%s\"", names[category]);
        if (length < 0 || (size_t)length >= sizeof(extra) ||
            event(gate, "ioctl_entry", thread->tid, fd, request, extra) != 0)
            return -1;
        /* A second mutation cannot be a legal cleanup while this gate is held. */
        if (gate->held)
            return -1;
        if (gate->armed && !gate->entered && request == gate->request) {
            if (tracee_identity(gate) != 0)
                return -1;
            gate->tid = thread->tid;
            gate->fd = fd;
            gate->entered = true;
            gate->held = true;
            gate->entered_at = now_ns();
            if (gate_json(gate, false, 0, false) != 0 ||
                event(gate, "held", thread->tid, fd, request, NULL) != 0)
                return -1;
            return 1;
        }
        return 0;
    }
    if (info.op == PTRACE_SYSCALL_INFO_EXIT) {
        if ((size_t)size < offsetof(struct ptrace_syscall_info, exit.is_error) + sizeof(info.exit.is_error))
            return -1;
        if (!thread->ioctl)
            return 0;
        thread->ioctl = false;
        char extra[192];
        int length = snprintf(extra, sizeof(extra),
            ",\"mutation\":\"%s\",\"return_value\":%" PRId64 ",\"is_error\":%s",
            names[thread->mutation], (int64_t)info.exit.rval, info.exit.is_error ? "true" : "false");
        if (length < 0 || (size_t)length >= sizeof(extra) ||
            event(gate, "ioctl_exit", thread->tid, thread->fd, thread->request, extra) != 0)
            return -1;
        if (gate->entered && !gate->completed && thread->tid == gate->tid &&
            thread->fd == gate->fd && thread->request == gate->request) {
            if (!gate->released || gate->held ||
                !same_node(gate, thread->tid, thread->fd) || tracee_identity(gate) != 0 ||
                /* These are internal restart results, not the real USB errno. */
                (info.exit.rval <= -512 && info.exit.rval >= -516))
                return -1;
            if (gate_json(gate, true, (int64_t)info.exit.rval, info.exit.is_error != 0) != 0 ||
                event(gate, "exit", thread->tid, thread->fd, thread->request, extra) != 0)
                return -1;
            gate->completed = true;
        }
        return 0;
    }
    return -1;
}

static int wait_event(struct gate *gate, pid_t tid, int status)
{
    unsigned int code = (unsigned int)status >> 16;
    struct thread *thread = find_thread(gate, tid, false);
    /* wait(-1) may report the new thread's stop before its parent's clone stop. */
    if (thread == NULL && WIFSTOPPED(status) && code == PTRACE_EVENT_STOP &&
        WSTOPSIG(status) == SIGTRAP && same_group(gate, tid))
        thread = find_thread(gate, tid, true);
    if (thread == NULL)
        return -1;
    if (WIFEXITED(status) || WIFSIGNALED(status)) {
        thread->live = false;
        if (tid == gate->child) {
            gate->child_exited = true;
            gate->child_status = status;
        }
        char extra[96];
        int length = snprintf(extra, sizeof(extra), ",\"exit_code\":%d,\"signal\":%d",
            WIFEXITED(status) ? WEXITSTATUS(status) : -1,
            WIFSIGNALED(status) ? WTERMSIG(status) : 0);
        return length >= 0 && (size_t)length < sizeof(extra) ?
            event(gate, "tracee_exit", tid, -1, 0, extra) : -1;
    }
    if (!WIFSTOPPED(status))
        return -1;
    gate->stops++;
    int signal = WSTOPSIG(status);
    int deliver = 0;
    if (code == PTRACE_EVENT_CLONE) {
        unsigned long child = 0;
        if (ptrace(PTRACE_GETEVENTMSG, tid, NULL, &child) != 0 || child == 0 ||
            child > INT_MAX || !same_group(gate, (pid_t)child) ||
            find_thread(gate, (pid_t)child, true) == NULL)
            return -1;
        char extra[80];
        int length = snprintf(extra, sizeof(extra), ",\"new_tid\":%lu", child);
        if (length < 0 || (size_t)length >= sizeof(extra) ||
            event(gate, "thread_clone", tid, -1, 0, extra) != 0)
            return -1;
    } else if (code == PTRACE_EVENT_EXEC) {
        if (exec_event(gate, tid) != 0)
            return -1;
    } else if (code == PTRACE_EVENT_EXIT || code == PTRACE_EVENT_STOP) {
        /* SEIZE's initial child-thread stop is synthetic, never reinject it. */
        if (code == PTRACE_EVENT_STOP && signal != SIGTRAP)
            return -1; /* Unexpected group-stop is outside this test protocol. */
    } else if (code != 0) {
        return -1;
    } else if (signal == (SIGTRAP | 0x80)) {
        int result = syscall_stop(gate, thread);
        if (result != 0)
            return result > 0 ? 0 : -1;
    } else {
        /* Verify this is signal-delivery-stop and forward the actual signal. */
        siginfo_t info;
        if (ptrace(PTRACE_GETSIGINFO, tid, NULL, &info) != 0)
            return -1;
        deliver = signal;
        char extra[80];
        int length = snprintf(extra, sizeof(extra), ",\"signal\":%d", signal);
        if (length < 0 || (size_t)length >= sizeof(extra) ||
            event(gate, "signal_delivery", tid, -1, 0, extra) != 0)
            return -1;
    }
    return ptrace(PTRACE_SYSCALL, tid, NULL, (void *)(intptr_t)deliver) == 0 ? 0 : -1;
}

static void terminate_tracees(struct gate *gate)
{
    if (gate->child > 0)
        (void)kill(gate->child, SIGKILL);
    uint64_t deadline = now_ns() + REAP_NS;
    while (now_ns() < deadline) {
        int status;
        pid_t tid = waitpid(-1, &status, __WALL | WNOHANG);
        if (tid > 0) {
            if (WIFSTOPPED(status)) {
                (void)kill(tid, SIGKILL);
                (void)ptrace(PTRACE_CONT, tid, NULL, NULL);
            } else {
                struct thread *thread = find_thread(gate, tid, false);
                if (thread != NULL)
                    thread->live = false;
            }
        } else if (tid < 0 && errno == ECHILD) {
            break;
        } else {
            struct timespec delay = { .tv_sec = 0, .tv_nsec = 1000000 };
            (void)nanosleep(&delay, NULL);
        }
    }
    /* EXITKILL also covers a tracee stuck beyond this bounded reap interval. */
}

static int fail(struct gate *gate, const char *reason)
{
    int saved = errno;
    char extra[160];
    int length = snprintf(extra, sizeof(extra), ",\"reason\":\"%s\",\"errno\":%d", reason, saved);
    if (length > 0 && (size_t)length < sizeof(extra))
        (void)event(gate, "failure", gate->tid, gate->fd, gate->request, extra);
    terminate_tracees(gate);
    return 1;
}

static bool node_path(const char *path)
{
    const char *prefix = "/dev/bus/usb/";
    size_t length = strlen(prefix);
    if (strlen(path) != length + 7 || strncmp(path, prefix, length) != 0 || path[length + 3] != '/')
        return false;
    for (size_t i = 0; i < 7; i++)
        if (i != 3 && (path[length + i] < '0' || path[length + i] > '9'))
            return false;
    return true;
}

int main(int argc, char **argv)
{
    /* Reject before opening a device, directory, forking, or calling ptrace. */
    if (getuid() != 0 || geteuid() != 0) {
        const char *message = "usbfs-syscall-gate: guest root required\n";
        (void)write_all(STDERR_FILENO, message, strlen(message));
        return 1;
    }
    if (!has_marker()) {
        const char *message = "usbfs-syscall-gate: isolated QEMU marker required\n";
        (void)write_all(STDERR_FILENO, message, strlen(message));
        return 1;
    }
    if (argc < 12 || strcmp(argv[1], "--directory") != 0 ||
        strcmp(argv[2], DIRECTORY) != 0 || strcmp(argv[3], "--usb-node") != 0 ||
        !node_path(argv[4]) || strcmp(argv[5], "--") != 0 ||
        strcmp(argv[6], "/usr/bin/setpriv") != 0 || strcmp(argv[7], "--reuid=1000") != 0 ||
        strcmp(argv[8], "--regid=1000") != 0 || strcmp(argv[9], "--clear-groups") != 0 ||
        strcmp(argv[10], "/usr/bin/rustusb") != 0) {
        const char *message = "usbfs-syscall-gate: invalid fixed test invocation\n";
        (void)write_all(STDERR_FILENO, message, strlen(message));
        return 1;
    }
    struct gate gate;
    memset(&gate, 0, sizeof(gate));
    gate.directory = -1;
    gate.events = -1;
    gate.fd = -1;
    gate.started = now_ns();
    if (gate.started == 0)
        return 1;
    umask(077);
    if (mkdir(DIRECTORY, 0700) != 0 && errno != EEXIST)
        return 1;
    gate.directory = open(DIRECTORY, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    struct stat directory;
    if (gate.directory < 0 || fstat(gate.directory, &directory) != 0 ||
        directory.st_uid != 0 || (directory.st_mode & 0777) != 0700)
        return 1;
    const char *const files[] = {
        "arm", "release", "tracee.pid", "ready.json", "held.json", "exit.json", "events.jsonl"
    };
    for (size_t i = 0; i < sizeof(files) / sizeof(files[0]); i++) {
        struct stat existing;
        if (fstatat(gate.directory, files[i], &existing, AT_SYMLINK_NOFOLLOW) == 0 || errno != ENOENT)
            return 1;
    }
    if (lstat(argv[4], &gate.node) != 0 || !S_ISCHR(gate.node.st_mode) ||
        gate.node.st_uid != 0 || (gate.node.st_mode & 0777) != 0600)
        return 1;
    gate.events = openat(gate.directory, "events.jsonl",
        O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (gate.events < 0)
        return 1;
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_handler = signal_handler;
    (void)sigemptyset(&action.sa_mask);
    if (sigaction(SIGTERM, &action, NULL) != 0 || sigaction(SIGINT, &action, NULL) != 0)
        return fail(&gate, "signal_setup");
    int handshake[2];
    if (pipe2(handshake, O_CLOEXEC) != 0)
        return fail(&gate, "handshake_pipe");
    gate.child = fork();
    if (gate.child < 0)
        return fail(&gate, "fork");
    if (gate.child == 0) {
        (void)close(handshake[1]);
        (void)close(gate.directory);
        (void)close(gate.events);
        char byte;
        ssize_t count;
        do {
            count = read(handshake[0], &byte, 1);
        } while (count < 0 && errno == EINTR);
        (void)close(handshake[0]);
        if (count != 1 || byte != 'G')
            _exit(125);
        /* Do not leak any future caller's root capabilities through open FDs. */
        if (syscall(SYS_close_range, 3U, UINT_MAX, 0U) != 0)
            _exit(125);
        /* The caller owns these three streams; the tracer never logs to them. */
        execv(argv[6], argv + 6);
        _exit(126);
    }
    (void)close(handshake[0]);
    if (find_thread(&gate, gate.child, true) == NULL || starttime(gate.child, &gate.starttime) != 0)
        return fail(&gate, "initial_identity");
    char pidfile[64];
    int length = snprintf(pidfile, sizeof(pidfile), "%ld\n", (long)gate.child);
    if (length < 0 || (size_t)length >= sizeof(pidfile) || publish(&gate, "tracee.pid", pidfile) != 0)
        return fail(&gate, "pid_publish");
    unsigned long options = PTRACE_O_TRACESYSGOOD | PTRACE_O_TRACECLONE |
        PTRACE_O_TRACEEXEC | PTRACE_O_TRACEEXIT | PTRACE_O_EXITKILL |
        PTRACE_O_TRACEFORK | PTRACE_O_TRACEVFORK;
    if (ptrace(PTRACE_SEIZE, gate.child, NULL, (void *)options) != 0 ||
        ptrace(PTRACE_INTERRUPT, gate.child, NULL, NULL) != 0)
        return fail(&gate, "initial_seize");
    if (event(&gate, "start", gate.child, -1, 0, NULL) != 0)
        return fail(&gate, "start_log");
    /* The handshake is released only after the first ptrace stop is handled. */
    bool launched = false;
    for (;;) {
        uint64_t now = now_ns();
        if (interrupted != 0)
            return fail(&gate, "tracer_signal");
        if (now == 0 || now - gate.started >= PROCESS_NS)
            return fail(&gate, "process_deadline");
        if (gate.entered && !gate.completed && now - gate.entered_at >= GATE_NS)
            return fail(&gate, "gate_deadline");
        if (controls(&gate) != 0)
            return fail(&gate, "control_protocol");
        bool activity = false;
        bool no_children = false;
        for (unsigned int batch = 0; batch < 256; batch++) {
            int status;
            pid_t tid = waitpid(-1, &status, __WALL | WNOHANG);
            if (tid == 0)
                break;
            if (tid < 0) {
                if (errno == EINTR)
                    continue;
                if (errno == ECHILD) {
                    no_children = true;
                    break;
                }
                return fail(&gate, "waitpid");
            }
            activity = true;
            if (wait_event(&gate, tid, status) != 0) {
                int trace_errno = errno;
                char extra[160];
                length = snprintf(extra, sizeof(extra),
                    ",\"raw_wait_status\":%d,\"stop_signal\":%d,\"ptrace_event\":%u",
                    status, WIFSTOPPED(status) ? WSTOPSIG(status) : 0,
                    (unsigned int)status >> 16);
                if (length > 0 && (size_t)length < sizeof(extra))
                    (void)event(&gate, "trace_event_error", tid, -1, 0, extra);
                errno = trace_errno;
                return fail(&gate, "trace_event");
            }
            if (!launched) {
                if (tid != gate.child || !WIFSTOPPED(status))
                    return fail(&gate, "initial_stop");
                if (write_all(handshake[1], "G", 1) != 0 || close(handshake[1]) != 0)
                    return fail(&gate, "launch_release");
                launched = true;
            }
        }
        if (no_children) {
            if (!gate.ready || !gate.entered || !gate.released || !gate.completed ||
                !gate.child_exited || !WIFEXITED(gate.child_status) ||
                WEXITSTATUS(gate.child_status) != 0)
                return fail(&gate, "incomplete_or_failed_tracee");
            char extra[128];
            length = snprintf(extra, sizeof(extra), ",\"tracee_exit_code\":0,\"ptrace_stops\":%" PRIu64,
                gate.stops);
            if (length < 0 || (size_t)length >= sizeof(extra) ||
                event(&gate, "finished", gate.child, -1, 0, extra) != 0)
                return fail(&gate, "finished_log");
            if (close(gate.events) != 0 || close(gate.directory) != 0)
                return 1;
            return 0;
        }
        if (!activity) {
            struct timespec delay = { .tv_sec = 0, .tv_nsec = 1000000 };
            (void)nanosleep(&delay, NULL);
        }
    }
}
