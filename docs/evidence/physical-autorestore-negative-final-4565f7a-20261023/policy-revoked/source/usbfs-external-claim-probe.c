/* SPDX-License-Identifier: MIT
 * Disposable QEMU negative-test actor. Only independent open, plain claim,
 * release and close. No CONNECT, detach, reset, configuration or alternate API.
 */
#define _GNU_SOURCE
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <inttypes.h>
#include <linux/usbdevice_fs.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>

struct fixture {
    const char *node, *sysfs;
    unsigned vendor, product, address;
    int fd;
    struct stat node_id, sysfs_id;
};
static struct fixture fixtures[] = {
    {"/dev/bus/usb/001/002", "/sys/bus/usb/devices/1-1", 0x0627, 1, 2, -1, {0}, {0}},
    {"/dev/bus/usb/001/003", "/sys/bus/usb/devices/1-2", 0x0403, 0x6001, 3, -1, {0}, {0}}
};
static unsigned long long own_start;
static void die(const char *reason) {
    int error = errno;
    printf("RUSTUSB_EXTERNAL_C {\"event\":\"fatal\",\"reason\":\"%s\",\"errno\":%d}\n", reason, error);
    exit(1);
}
static void require(int condition, const char *reason) {
    if (!condition) { errno = EINVAL; die(reason); }
}
static void text_file(const char *path, char *data, size_t capacity) {
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) die("read-open");
    ssize_t length = read(fd, data, capacity - 1);
    if (length < 0) die("read");
    require((size_t)length < capacity - 1, "read-bound");
    data[length] = 0;
    if (close(fd)) die("read-close");
}
static unsigned long long number(const char *data, int radix) {
    while (isspace((unsigned char)*data)) ++data;
    require(isdigit((unsigned char)*data) || (radix == 16 && isxdigit((unsigned char)*data)), "number-first-digit");
    char *end;
    errno = 0;
    unsigned long long value = strtoull(data, &end, radix);
    if (errno) die("number-range");
    require(end != data, "number-empty");
    while (isspace((unsigned char)*end)) ++end;
    require(*end == 0, "number-tail");
    return value;
}
static unsigned long long attribute(const char *base, const char *leaf, int radix) {
    char path[256], data[128];
    int n = snprintf(path, sizeof(path), "%s/%s", base, leaf);
    require(n > 0 && (size_t)n < sizeof(path), "path-bound");
    text_file(path, data, sizeof(data));
    return number(data, radix);
}
static int same_node(const struct stat *a, const struct stat *b) {
    return a->st_dev == b->st_dev && a->st_ino == b->st_ino && a->st_rdev == b->st_rdev
        && S_ISCHR(b->st_mode) && b->st_uid == 0 && (b->st_mode & 07777) == 0600;
}
static void observe(unsigned index, const char *phase, int claimed) {
    struct fixture *f = &fixtures[index];
    struct stat node, sysfs, held;
    if (lstat(f->node, &node) || stat(f->sysfs, &sysfs)) die("receipt-stat");
    require(same_node(&f->node_id, &node) && sysfs.st_dev == f->sysfs_id.st_dev && sysfs.st_ino == f->sysfs_id.st_ino, "receipt-changed");
    if (f->fd >= 0) {
        if (fstat(f->fd, &held)) die("descriptor-stat");
        require(same_node(&node, &held), "descriptor-receipt");
    }
    require(attribute(f->sysfs, "idVendor", 16) == f->vendor && attribute(f->sysfs, "idProduct", 16) == f->product
            && attribute(f->sysfs, "busnum", 10) == 1 && attribute(f->sysfs, "devnum", 10) == f->address
            && attribute(f->sysfs, "bConfigurationValue", 10) == 1 && attribute(f->sysfs, "bNumInterfaces", 10) == 1, "fixture-scope");
    char pattern[256], path[256], target[256];
    int n = snprintf(pattern, sizeof(pattern), "%s:*", f->sysfs);
    require(n > 0 && (size_t)n < sizeof(pattern), "interface-pattern");
    glob_t interfaces = {0};
    require(glob(pattern, 0, NULL, &interfaces) == 0 && interfaces.gl_pathc == 1, "full-interface-set");
    require(attribute(interfaces.gl_pathv[0], "bInterfaceNumber", 16) == 0 && attribute(interfaces.gl_pathv[0], "bAlternateSetting", 10) == 0, "interface-alternate");
    n = snprintf(path, sizeof(path), "%s/driver", interfaces.gl_pathv[0]);
    require(n > 0 && (size_t)n < sizeof(path), "driver-path");
    errno = 0;
    ssize_t length = readlink(path, target, sizeof(target) - 1);
    if (claimed) {
        require(length > 0 && (size_t)length < sizeof(target) - 1, "claimed-driver-read");
        target[length] = 0;
        const char *leaf = strrchr(target, '/');
        require(leaf && !strcmp(leaf + 1, "usbfs"), "claimed-driver");
    } else {
        require(length == -1 && errno == ENOENT, "unbound-driver-required");
    }
    globfree(&interfaces);
    printf("RUSTUSB_EXTERNAL_C {\"event\":\"state\",\"phase\":\"%s\",\"device\":%u,\"pid\":%ld,\"starttime\":%llu,\"fd\":%d,\"node_dev\":%ju,\"node_inode\":%ju,\"node_rdev\":%ju,\"sysfs_dev\":%ju,\"sysfs_inode\":%ju,\"bus_number\":1,\"device_number\":%u,\"configuration\":1,\"interfaces\":[{\"number\":0,\"alternate\":0,\"driver\":%s}]}\n",
           phase, index, (long)getpid(), own_start, f->fd, (uintmax_t)node.st_dev, (uintmax_t)node.st_ino, (uintmax_t)node.st_rdev,
           (uintmax_t)sysfs.st_dev, (uintmax_t)sysfs.st_ino, f->address, claimed ? "\"usbfs\"" : "null");
}
int main(void) {
    setvbuf(stdout, NULL, _IOLBF, 0);
    require(getuid() == 0 && geteuid() == 0, "guest-root-only");
    char data[4096], *save;
    text_file("/proc/cmdline", data, sizeof(data));
    int guest = 0, journal = 0;
    for (char *word = strtok_r(data, " \t\r\n", &save); word; word = strtok_r(NULL, " \t\r\n", &save)) {
        guest |= !strcmp(word, "RUSTUSB_QEMU_GUEST=1");
        journal |= !strcmp(word, "rustusb.journal_test=1");
    }
    require(guest && journal, "disposable-guest-markers");
    text_file("/proc/self/stat", data, sizeof(data));
    char *last = strrchr(data, ')');
    require(last != NULL, "proc-stat");
    int field = 3;
    for (char *word = strtok_r(last + 1, " ", &save); word; word = strtok_r(NULL, " ", &save), ++field)
        if (field == 22) { own_start = number(word, 10); break; }
    require(own_start > 0, "proc-starttime");
    for (unsigned i = 0; i < 2; ++i) {
        struct fixture *f = &fixtures[i];
        if (lstat(f->node, &f->node_id) || stat(f->sysfs, &f->sysfs_id)) die("initial-receipt");
        require(same_node(&f->node_id, &f->node_id) && major(f->node_id.st_rdev) == 189 && minor(f->node_id.st_rdev) == f->address - 1, "fixed-root0600-node");
        observe(i, "before-open", 0);
        f->fd = open(f->node, O_RDWR | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
        if (f->fd < 0) die("independent-open");
        observe(i, "independently-opened", 0);
        unsigned interface = 0;
        errno = 0;
        int result = ioctl(f->fd, USBDEVFS_CLAIMINTERFACE, &interface), error = errno;
        printf("RUSTUSB_EXTERNAL_C {\"event\":\"ioctl\",\"op\":\"claim\",\"device\":%u,\"fd\":%d,\"interface\":0,\"rc\":%d,\"errno\":%d}\n", i, f->fd, result, error);
        require(result == 0 && error == 0, "claim-failed");
        observe(i, "claimed", 1);
    }
    printf("RUSTUSB_EXTERNAL_C {\"event\":\"ready\",\"pid\":%ld,\"starttime\":%llu,\"uid\":0}\n", (long)getpid(), own_start);
    char command[32];
    require(fgets(command, sizeof(command), stdin) != NULL && !strcmp(command, "release\n"), "one-release-command");
    for (unsigned i = 0; i < 2; ++i) {
        struct fixture *f = &fixtures[i];
        observe(i, "before-release", 1);
        unsigned interface = 0;
        errno = 0;
        int result = ioctl(f->fd, USBDEVFS_RELEASEINTERFACE, &interface), error = errno;
        printf("RUSTUSB_EXTERNAL_C {\"event\":\"ioctl\",\"op\":\"release\",\"device\":%u,\"fd\":%d,\"interface\":0,\"rc\":%d,\"errno\":%d}\n", i, f->fd, result, error);
        require(result == 0 && error == 0, "release-failed");
        observe(i, "released", 0);
        int old = f->fd;
        if (close(old)) die("close");
        f->fd = -1;
        errno = 0;
        result = fcntl(old, F_GETFD); error = errno;
        printf("RUSTUSB_EXTERNAL_C {\"event\":\"close\",\"device\":%u,\"fd\":%d,\"close_rc\":0,\"getfd_rc\":%d,\"getfd_errno\":%d}\n", i, old, result, error);
        require(result == -1 && error == EBADF, "descriptor-still-open");
        observe(i, "closed", 0);
    }
    puts("RUSTUSB_EXTERNAL_C {\"event\":\"done\"}");
    return 0;
}
