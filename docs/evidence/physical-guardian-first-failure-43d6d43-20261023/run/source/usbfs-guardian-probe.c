/* Original, disposable-QEMU-only mechanism probe. Not a product recovery tool. */
#define _GNU_SOURCE
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <inttypes.h>
#include <limits.h>
#include <linux/kcmp.h>
#include <linux/usbdevice_fs.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/sysmacros.h>
#include <time.h>
#include <unistd.h>

struct device {
    const char *node, *sysfs, *original_driver;
    unsigned vendor, product, address;
    int b, d, c, b_claimed, c_claimed;
    struct stat node_identity, sysfs_identity;
};
static struct device devices[] = {
    {"/dev/bus/usb/001/002", "/sys/bus/usb/devices/1-1", "usbhid", 0x0627, 1, 2, -1, -1, -1, 0, 0, {0}, {0}},
    {"/dev/bus/usb/001/003", "/sys/bus/usb/devices/1-2", "ftdi_sio", 0x0403, 0x6001, 3, -1, -1, -1, 0, 0, {0}, {0}}
};
static unsigned long sequence;

static void fail(const char *reason) {
    int saved = errno;
    printf("RUSTUSB_GUARDIAN_PROBE {\"event\":\"fatal\",\"seq\":%lu,\"reason\":\"%s\",\"errno\":%d}\n", sequence, reason, saved);
    exit(1);
}
static void require(int condition, const char *reason) {
    if (!condition) { errno = EINVAL; fail(reason); }
}
static void read_text(const char *path, char *out, size_t capacity) {
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) fail("read-open");
    ssize_t n = read(fd, out, capacity - 1);
    if (n < 0) fail("read");
    require((size_t)n < capacity - 1, "read-too-large");
    out[n] = 0;
    if (close(fd)) fail("read-close");
}
static unsigned long long number(const char *text, int base) {
    char *end;
    errno = 0;
    while (*text == ' ' || *text == '\t' || *text == '\n') ++text;
    require(isdigit((unsigned char)*text) || (base == 16 && isxdigit((unsigned char)*text)), "unsigned-number");
    unsigned long long value = strtoull(text, &end, base);
    if (errno) fail("number-overflow");
    require(end != text, "number-without-digits");
    while (*end == ' ' || *end == '\t' || *end == '\n') ++end;
    require(!*end, "number-trailing-data");
    return value;
}
static void path_join(char *out, size_t size, const char *base, const char *leaf) {
    int n = snprintf(out, size, "%s/%s", base, leaf);
    require(n > 0 && (size_t)n < size, "path-overflow");
}
static unsigned long long attribute(const char *base, const char *name, int radix) {
    char path[256], data[128];
    path_join(path, sizeof(path), base, name);
    read_text(path, data, sizeof(data));
    return number(data, radix);
}
static unsigned long long starttime(pid_t pid) {
    char path[64], data[4096], *saved, *word;
    snprintf(path, sizeof(path), "/proc/%ld/stat", (long)pid);
    read_text(path, data, sizeof(data));
    char *end = strrchr(data, ')');
    require(end != NULL, "proc-stat");
    word = strtok_r(end + 1, " ", &saved);
    for (int field = 3; word; ++field, word = strtok_r(NULL, " ", &saved))
        if (field == 22) return number(word, 10);
    fail("proc-starttime-missing");
    return 0;
}
static void owner(pid_t pid, unsigned long long expected_start) {
    char path[64], data[4096], exe[256];
    require(starttime(pid) == expected_start, "owner-starttime");
    snprintf(path, sizeof(path), "/proc/%ld/exe", (long)pid);
    ssize_t n = readlink(path, exe, sizeof(exe) - 1);
    if (n < 0) fail("owner-exe-read");
    exe[n] = 0;
    require(!strcmp(exe, "/usr/bin/rustusb"), "owner-exe");
    snprintf(path, sizeof(path), "/proc/%ld/status", (long)pid);
    read_text(path, data, sizeof(data));
    char *uid = strstr(data, "\nUid:");
    unsigned a, b, c, d;
    require(uid && sscanf(uid, "\nUid: %u %u %u %u", &a, &b, &c, &d) == 4 && a == 1000 && b == 1000 && c == 1000 && d == 1000, "owner-uids");
    const char *names[] = {"\nCapEff:", "\nCapPrm:", "\nCapAmb:"};
    for (unsigned i = 0; i < 3; ++i) {
        char *line = strstr(data, names[i]);
        unsigned long long capability;
        require(line && sscanf(line + strlen(names[i]), "%llx", &capability) == 1 && capability == 0, "owner-capability");
    }
}
static int same_node(const struct stat *a, const struct stat *b) {
    return a->st_dev == b->st_dev && a->st_ino == b->st_ino && a->st_rdev == b->st_rdev && S_ISCHR(b->st_mode) && b->st_uid == 0 && (b->st_mode & 07777) == 0600;
}
static void state(unsigned i, char *driver, size_t size) {
    struct device *d = &devices[i];
    struct stat node, sysfs, held;
    if (lstat(d->node, &node) || stat(d->sysfs, &sysfs) || fstat(d->b, &held)) fail("receipt-stat");
    require(same_node(&d->node_identity, &node) && same_node(&node, &held) && sysfs.st_dev == d->sysfs_identity.st_dev && sysfs.st_ino == d->sysfs_identity.st_ino, "receipt-changed");
    require(attribute(d->sysfs, "idVendor", 16) == d->vendor && attribute(d->sysfs, "idProduct", 16) == d->product && attribute(d->sysfs, "busnum", 10) == 1 && attribute(d->sysfs, "devnum", 10) == d->address && attribute(d->sysfs, "bConfigurationValue", 10) == 1 && attribute(d->sysfs, "bNumInterfaces", 10) == 1, "fixture-configuration");
    char pattern[256];
    int length = snprintf(pattern, sizeof(pattern), "%s:*", d->sysfs);
    require(length > 0 && (size_t)length < sizeof(pattern), "interface-pattern");
    glob_t interfaces = {0};
    require(glob(pattern, 0, NULL, &interfaces) == 0 && interfaces.gl_pathc == 1, "full-interface-set");
    require(attribute(interfaces.gl_pathv[0], "bInterfaceNumber", 16) == 0 && attribute(interfaces.gl_pathv[0], "bAlternateSetting", 10) == 0, "fixture-interface-alternate");
    char path[256], target[256];
    path_join(path, sizeof(path), interfaces.gl_pathv[0], "driver");
    ssize_t n = readlink(path, target, sizeof(target) - 1);
    if (n < 0) {
        if (errno != ENOENT) fail("driver-read");
        driver[0] = 0;
    } else {
        target[n] = 0;
        const char *name = strrchr(target, '/');
        require(name != NULL && strlen(name + 1) < size, "driver-name");
        strcpy(driver, name + 1);
        require(!strcmp(driver, "usbfs") || !strcmp(driver, d->original_driver), "unexpected-driver");
    }
    globfree(&interfaces);
}
static void snapshot(void) {
    for (unsigned i = 0; i < 2; ++i) {
        struct device *d = &devices[i];
        char driver[64];
        state(i, driver, sizeof(driver));
        printf("RUSTUSB_GUARDIAN_PROBE {\"event\":\"state\",\"seq\":%lu,\"device\":%u,\"pid\":%ld,\"starttime\":%llu,\"b_fd\":%d,\"d_fd\":%d,\"c_fd\":%d,\"b_claimed\":%d,\"c_claimed\":%d,\"node_dev\":%ju,\"node_inode\":%ju,\"node_rdev\":%ju,\"sysfs_dev\":%ju,\"sysfs_inode\":%ju,\"configuration\":1,\"interfaces\":[{\"number\":0,\"alternate\":0,\"driver\":", sequence, i, (long)getpid(), starttime(getpid()), d->b, d->d, d->c, d->b_claimed, d->c_claimed, (uintmax_t)d->node_identity.st_dev, (uintmax_t)d->node_identity.st_ino, (uintmax_t)d->node_identity.st_rdev, (uintmax_t)d->sysfs_identity.st_dev, (uintmax_t)d->sysfs_identity.st_ino);
        if (*driver) printf("\"%s\"", driver); else printf("null");
        puts("}]}");
    }
}
static int new_open(unsigned i) {
    struct device *d = &devices[i];
    int fd = open(d->node, O_RDWR | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
    if (fd < 0) fail("independent-open");
    struct stat current;
    if (fstat(fd, &current)) fail("open-fstat");
    require(same_node(&d->node_identity, &current), "open-receipt");
    return fd;
}
static long compare(pid_t first, pid_t second, int fd1, int fd2) {
    errno = 0;
    long result = syscall(SYS_kcmp, first, second, KCMP_FILE, (unsigned long)fd1, (unsigned long)fd2);
    if (result < 0) fail("kcmp");
    require(result <= 3, "kcmp-range");
    return result;
}
static void close_fd(unsigned i, const char *handle, int *fd) {
    require(*fd >= 0, "close-missing-fd");
    int old = *fd;
    if (close(old)) fail("close-fd");
    *fd = -1;
    errno = 0;
    int result = fcntl(old, F_GETFD);
    int error = errno;
    printf("RUSTUSB_GUARDIAN_PROBE {\"event\":\"close\",\"seq\":%lu,\"device\":%u,\"handle\":\"%s\",\"fd\":%d,\"close_rc\":0,\"getfd_rc\":%d,\"getfd_errno\":%d}\n", sequence, i, handle, old, result, error);
    require(result == -1 && error == EBADF, "fd-not-closed");
}
int main(void) {
    setvbuf(stdout, NULL, _IOLBF, 0);
    require(getuid() == 0 && geteuid() == 0, "guest-root-only");
    char cmdline[4096];
    read_text("/proc/cmdline", cmdline, sizeof(cmdline));
    int guest = 0, journal = 0;
    char *saved, *token = strtok_r(cmdline, " \t\n", &saved);
    while (token) { guest |= !strcmp(token, "RUSTUSB_QEMU_GUEST=1"); journal |= !strcmp(token, "rustusb.journal_test=1"); token = strtok_r(NULL, " \t\n", &saved); }
    require(guest && journal, "disposable-guest-markers-required");
    for (unsigned i = 0; i < 2; ++i) {
        struct device *d = &devices[i];
        if (lstat(d->node, &d->node_identity) || stat(d->sysfs, &d->sysfs_identity)) fail("initial-receipt");
        require(same_node(&d->node_identity, &d->node_identity) && major(d->node_identity.st_rdev) == 189 && minor(d->node_identity.st_rdev) == d->address - 1, "fixed-root0600-node");
        d->b = new_open(i);
        char driver[64]; state(i, driver, sizeof(driver));
        require(!strcmp(driver, d->original_driver), "original-driver-required");
    }
    snapshot();
    printf("RUSTUSB_GUARDIAN_PROBE {\"event\":\"ready\",\"seq\":0,\"pid\":%ld,\"starttime\":%llu,\"uid\":0}\n", (long)getpid(), starttime(getpid()));
    char line[256];
    while (fgets(line, sizeof(line), stdin)) {
        require(strchr(line, '\n') != NULL, "bounded-command-line");
        char *args[8], *save; unsigned count = 0;
        for (char *item = strtok_r(line, " \t\r\n", &save); item; item = strtok_r(NULL, " \t\r\n", &save)) { require(count < 8, "command-arguments"); args[count++] = item; }
        require(count >= 2 && number(args[0], 10) == sequence + 1, "command-sequence");
        ++sequence;
        const char *op = args[1];
        if (!strcmp(op, "arm")) {
            require(count == 6, "arm-arguments");
            unsigned long long p = number(args[2], 10), start = number(args[3], 10);
            require(p > 1 && p <= INT_MAX, "owner-pid-range");
            owner((pid_t)p, start);
            int pidfd = (int)syscall(SYS_pidfd_open, (pid_t)p, 0);
            if (pidfd < 0) fail("pidfd-open");
            for (unsigned i = 0; i < 2; ++i) {
                struct device *d = &devices[i];
                unsigned long long a = number(args[4 + i], 10);
                require(a <= INT_MAX && d->d == -1, "source-fd-range");
                d->d = (int)syscall(SYS_pidfd_getfd, pidfd, (int)a, 0);
                if (d->d < 0) fail("pidfd-getfd");
                struct stat identity;
                if (fstat(d->d, &identity)) fail("duplicate-stat");
                require(same_node(&d->node_identity, &identity), "duplicate-receipt");
                long ad = compare(getpid(), (pid_t)p, d->d, (int)a);
                long ab = compare(getpid(), (pid_t)p, d->b, (int)a);
                long bd = compare(getpid(), getpid(), d->b, d->d);
                printf("RUSTUSB_GUARDIAN_PROBE {\"event\":\"ofd_compare\",\"seq\":%lu,\"device\":%u,\"owner_pid\":%llu,\"owner_starttime\":%llu,\"a_fd\":%llu,\"b_fd\":%d,\"d_fd\":%d,\"a_d\":%ld,\"a_b\":%ld,\"b_d\":%ld}\n", sequence, i, p, start, a, d->b, d->d, ad, ab, bd);
                require(ad == 0 && ab > 0 && bd > 0, "ofd-identity");
            }
            owner((pid_t)p, start);
            if (close(pidfd)) fail("pidfd-close");
        } else if (!strcmp(op, "drop")) {
            require(count == 2, "drop-arguments");
            for (unsigned i = 0; i < 2; ++i) close_fd(i, "D", &devices[i].d);
        } else if (!strcmp(op, "copen")) {
            require(count == 2, "copen-arguments");
            for (unsigned i = 0; i < 2; ++i) {
                struct device *d = &devices[i]; require(d->c == -1 && d->d == -1, "copen-phase");
                d->c = new_open(i);
                long bc = compare(getpid(), getpid(), d->b, d->c);
                printf("RUSTUSB_GUARDIAN_PROBE {\"event\":\"external_ofd\",\"seq\":%lu,\"device\":%u,\"pid\":%ld,\"b_fd\":%d,\"c_fd\":%d,\"b_c\":%ld}\n", sequence, i, (long)getpid(), d->b, d->c, bc);
                require(bc > 0, "external-ofd-distinct");
            }
        } else if (!strcmp(op, "cclose")) {
            require(count == 2, "cclose-arguments");
            for (unsigned i = 0; i < 2; ++i) { require(!devices[i].c_claimed, "external-still-claimed"); close_fd(i, "C", &devices[i].c); }
        } else if (!strcmp(op, "claim") || !strcmp(op, "release") || !strcmp(op, "connect")) {
            int connect = !strcmp(op, "connect");
            require(count == (connect ? 3u : 4u), "ioctl-arguments");
            unsigned long long index = number(args[connect ? 2 : 3], 10);
            require(index < 2, "device-index");
            unsigned i = (unsigned)index; struct device *d = &devices[i];
            char driver[64]; state(i, driver, sizeof(driver));
            const char *handle = connect ? "B" : args[2];
            require(!strcmp(handle, "B") || !strcmp(handle, "C"), "handle-selector");
            int fd = !strcmp(handle, "B") ? d->b : d->c;
            int *claimed = !strcmp(handle, "B") ? &d->b_claimed : &d->c_claimed;
            require(fd >= 0, "handle-not-open");
            int result, error; unsigned interface = 0;
            if (connect) {
                require(!d->b_claimed, "connect-own-claim");
                struct usbdevfs_ioctl ctl = {.ifno = 0, .ioctl_code = USBDEVFS_CONNECT, .data = NULL};
                errno = 0; result = ioctl(fd, USBDEVFS_IOCTL, &ctl); error = errno;
            } else if (!strcmp(op, "claim")) {
                require(!*claimed, "duplicate-own-claim");
                errno = 0; result = ioctl(fd, USBDEVFS_CLAIMINTERFACE, &interface); error = errno;
                if (result == 0) *claimed = 1;
            } else {
                require(*claimed, "release-not-owned");
                errno = 0; result = ioctl(fd, USBDEVFS_RELEASEINTERFACE, &interface); error = errno;
                if (result == 0) *claimed = 0;
            }
            printf("RUSTUSB_GUARDIAN_PROBE {\"event\":\"ioctl\",\"seq\":%lu,\"op\":\"%s\",\"device\":%u,\"handle\":\"%s\",\"fd\":%d,\"interface\":0,\"rc\":%d,\"errno\":%d}\n", sequence, op, i, handle, fd, result, error);
        } else if (!strcmp(op, "quit")) {
            require(count == 2, "quit-arguments");
            snapshot();
            for (unsigned i = 0; i < 2; ++i) { struct device *d = &devices[i]; require(d->d == -1 && d->c == -1 && !d->b_claimed && !d->c_claimed, "quit-owned-resources"); close_fd(i, "B", &d->b); }
            printf("RUSTUSB_GUARDIAN_PROBE {\"event\":\"done\",\"seq\":%lu,\"op\":\"quit\"}\n", sequence);
            return 0;
        } else { require(count == 2 && !strcmp(op, "snapshot"), "unknown-command"); }
        snapshot();
        printf("RUSTUSB_GUARDIAN_PROBE {\"event\":\"done\",\"seq\":%lu,\"op\":\"%s\"}\n", sequence, op);
    }
    fail("unexpected-control-eof");
    return 1;
}
