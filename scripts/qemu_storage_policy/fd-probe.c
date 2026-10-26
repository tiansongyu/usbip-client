/* Guest-only test: retain two USBFS file descriptions across explicit barriers. */
#define _GNU_SOURCE
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <inttypes.h>
#include <linux/usbdevice_fs.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>

static int safe_node(const char *s) {
    if (strlen(s) != 20 || strncmp(s, "/dev/bus/usb/", 13) || s[16] != '/') return 0;
    for (int i=13; i<20; i++) if (i != 16 && !isdigit((unsigned char)s[i])) return 0;
    return 1;
}
static unsigned long long capabilities(void) {
    FILE *f=fopen("/proc/self/status", "r");
    if (!f) return ~0ULL;
    char line[256]; unsigned long long value=~0ULL;
    while (fgets(line,sizeof(line),f)) if (sscanf(line,"CapEff:\t%llx",&value)==1) break;
    fclose(f); return value;
}
static void control(int fd, const char *name) {
    unsigned char bytes[18]={0};
    struct usbdevfs_ctrltransfer c={.bRequestType=0x80,.bRequest=6,.wValue=0x0100,
        .wIndex=0,.wLength=sizeof(bytes),.timeout=1000,.data=bytes};
    errno=0; int rc=ioctl(fd,USBDEVFS_CONTROL,&c); int error=errno;
    printf("\"%s\":{\"rc\":%d,\"errno\":%d,\"length\":%u,\"type\":%u,\"vid\":%u,\"pid\":%u}",
        name,rc,error,bytes[0],bytes[1],bytes[8]|bytes[9]<<8,bytes[10]|bytes[11]<<8);
}
int main(int argc,char **argv) {
    if (argc!=2 || geteuid()!=0 || !safe_node(argv[1])) return 2;
    int rw=open(argv[1],O_RDWR|O_CLOEXEC|O_NOFOLLOW);
    int ro=open(argv[1],O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
    if (rw<0 || ro<0) {perror("open fixture device");return 3;}
    struct stat a,b;
    if (fstat(rw,&a)||fstat(ro,&b)||!S_ISCHR(a.st_mode)||major(a.st_rdev)!=189||
        a.st_dev!=b.st_dev||a.st_ino!=b.st_ino||a.st_rdev!=b.st_rdev) return 4;
    if (setgroups(0,NULL)||setgid(65534)||setuid(65534)) {perror("drop identity");return 5;}
    if (getuid()!=65534||geteuid()!=65534||getgid()!=65534||getegid()!=65534||
        getgroups(0,NULL)!=0||capabilities()!=0) return 6;
    printf("{\"phase\":\"ready\",\"pid\":%ld,\"uid\":%ld,\"gid\":%ld,\"groups\":0,\"cap_eff\":0,"
        "\"rw_fd\":%d,\"ro_fd\":%d,\"node_dev\":%ju,\"node_ino\":%ju,\"node_rdev\":%ju,\"rw_flags\":%d,\"ro_flags\":%d}\n",
        (long)getpid(),(long)getuid(),(long)getgid(),rw,ro,(uintmax_t)a.st_dev,(uintmax_t)a.st_ino,
        (uintmax_t)a.st_rdev,fcntl(rw,F_GETFL),fcntl(ro,F_GETFL));
    fflush(stdout);
    char line[24]; unsigned int index=0;
    while (fgets(line,sizeof(line),stdin)) {
        if (!strcmp(line,"quit\n")) break;
        if (strcmp(line,"probe\n")) return 7;
        errno=0;int fresh=open(argv[1],O_RDWR|O_CLOEXEC|O_NOFOLLOW);int error=errno;
        if (fresh>=0 && close(fresh)) return 8;
        if (fstat(rw,&b)||a.st_dev!=b.st_dev||a.st_ino!=b.st_ino||a.st_rdev!=b.st_rdev) return 9;
        printf("{\"phase\":\"probe\",\"index\":%u,\"pid\":%ld,\"fresh_open_rc\":%d,\"fresh_open_errno\":%d,\"node_mode\":%u,",
            ++index,(long)getpid(),fresh<0?-1:0,error,(unsigned int)(b.st_mode&0777));
        control(rw,"held_rw");printf(",");control(ro,"held_ro");printf("}\n");fflush(stdout);
    }
    if (close(ro)||close(rw)) return 10;
    printf("{\"phase\":\"closed\",\"pid\":%ld}\n",(long)getpid());fflush(stdout);
    return 0;
}
