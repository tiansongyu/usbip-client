#define main guardian_guest_main
#include "/home/ubuntu/github/usbip-client/scripts/qemu/usbfs-guardian-probe.c"
#undef main
int main(int argc, char **argv) { if(argc != 3) return 2; printf("%llu\n", number(argv[1], atoi(argv[2]))); return 0; }
