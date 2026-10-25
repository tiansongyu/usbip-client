/* Guest-only controlled journal wait. Not installed in the product package.
 * Calls the real fsync after one controller byte; never invents a successful IO.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

static atomic_flag selected = ATOMIC_FLAG_INIT;
static const char *directory = "/run/rustusb-package-test/";

int fsync(int fd) {
    int (*actual)(int) = (int (*)(int))dlsym(RTLD_NEXT, "fsync");
    if (!actual) { errno = EIO; return -1; }
    const char *uid = getenv("RUSTUSB_PACKAGE_GATE_UID");
    if (!uid || getuid() != (uid_t)strtoul(uid, NULL, 10)) return actual(fd);
    char arm[128], proc[64], path[4096];
    snprintf(arm, sizeof arm, "%sarmed", directory);
    snprintf(proc, sizeof proc, "/proc/self/fd/%d", fd);
    ssize_t length = readlink(proc, path, sizeof path - 1);
    if (length < 0) return actual(fd);
    path[length] = '\0';
    if (strncmp(path, "/var/lib/rustusb-agent/", sizeof("/var/lib/rustusb-agent/") - 1) != 0
        && strcmp(path, "/var/lib/rustusb-agent") != 0) return actual(fd);
    if (access(arm, F_OK) != 0 || atomic_flag_test_and_set(&selected)) return actual(fd);
    char entry[128], pipe[128];
    snprintf(entry, sizeof entry, "%sentry.jsonl", directory);
    snprintf(pipe, sizeof pipe, "%srelease", directory);
    int log = open(entry, O_WRONLY | O_APPEND | O_CLOEXEC | O_NOFOLLOW);
    struct stat metadata;
    if (log < 0 || fstat(fd, &metadata) != 0) {
        if (log >= 0) close(log);
        errno = EIO; return -1;
    }
    dprintf(log, "{\"phase\":\"entered\",\"pid\":%ld,\"uid\":%lu,\"fd\":%d,\"device\":%" PRIuMAX ",\"inode\":%" PRIuMAX "}\n",
            (long)getpid(), (unsigned long)getuid(), fd,
            (uintmax_t)metadata.st_dev, (uintmax_t)metadata.st_ino);
    int gate = open(pipe, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (gate < 0) { close(log); return -1; }
    char byte = 0; ssize_t count;
    do { count = read(gate, &byte, 1); } while (count < 0 && errno == EINTR);
    close(gate);
    if (count != 1 || byte != 'G') { close(log); errno = EIO; return -1; }
    int result = actual(fd), saved = errno;
    dprintf(log, "{\"phase\":\"returned\",\"pid\":%ld,\"result\":%d}\n", (long)getpid(), result);
    close(log); errno = saved; return result;
}
