#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#define DIRECTORY "/run/prepare-gate"
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

int main(int argc, char **argv) {
    if (argc != 15 || strcmp(argv[1], "--directory") != 0 ||
        strcmp(argv[2], DIRECTORY) != 0 || strcmp(argv[3], "--usb-node") != 0 ||
        !node_path(argv[4]) || strcmp(argv[5], "--") != 0 ||
        strcmp(argv[6], "/usr/bin/setpriv") != 0 || strcmp(argv[7], "--reuid=2000") != 0 ||
        strcmp(argv[8], "--regid=2000") != 0 || strcmp(argv[9], "--clear-groups") != 0 ||
        strcmp(argv[10], "/usr/bin/rustusb") != 0 ||
        strcmp(argv[11], "agent") != 0 || strcmp(argv[12], "--config") != 0 ||
        strcmp(argv[13], "/etc/rustusb/agent.json") != 0 || strcmp(argv[14], "run") != 0) return 1;
return 0;
}
