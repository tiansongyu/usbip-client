/* Test-only, compiled/executed inside a disposable guest. */
#include <errno.h>
#include <fcntl.h>
#include <linux/usbdevice_fs.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/ioctl.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc != 3 || geteuid() != 0) return 2;
    unsigned int interface = (unsigned int)strtoul(argv[2], NULL, 10);
    int fd = open(argv[1], O_RDWR | O_CLOEXEC);
    if (fd < 0) { perror("open synthetic USB"); return 3; }
    errno = 0;
    int rc = ioctl(fd, USBDEVFS_CLAIMINTERFACE, &interface);
    int error = errno;
    if (rc == 0) ioctl(fd, USBDEVFS_RELEASEINTERFACE, &interface);
    close(fd);
    printf("{\"claim_rc\":%d,\"errno\":%d,\"interface\":%u}\n", rc, error, interface);
    return rc == -1 && error == ENODEV ? 0 : 1;
}
