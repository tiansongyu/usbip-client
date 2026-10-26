/* Disposable guest fixture only: hold a reference until stdin EOF. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <sched.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>

static const char *path;
static int worker_result;
static void *worker(void *unused) {
    (void)unused;
    if (unshare(CLONE_FILES) != 0) { perror("unshare"); worker_result=1; return NULL; }
    int fd=open(path,O_RDONLY|O_CLOEXEC);
    if(fd<0) { perror("open");worker_result=1;return NULL; }
    printf("%ld\n",syscall(SYS_gettid));fflush(stdout);
    char c;while(read(STDIN_FILENO,&c,1)>0) {}
    if(close(fd)!=0)worker_result=1;
    return NULL;
}
int main(int argc,char **argv) {
    if(argc!=3)return 2;
    path=argv[2];
    if(strcmp(argv[1],"thread-fd")==0) {
        pthread_t thread;
        if(pthread_create(&thread,NULL,worker,NULL)!=0)return 3;
        if(pthread_join(thread,NULL)!=0)return 4;
        return worker_result;
    }
    int fd=open(path,O_RDONLY|O_CLOEXEC);if(fd<0){perror("open");return 5;}
    void *mapping=NULL;
    if(strcmp(argv[1],"mapping")==0) {
        mapping=mmap(NULL,4096,PROT_READ,MAP_SHARED,fd,0);
        if(mapping==MAP_FAILED){perror("mmap");return 6;}
        if(close(fd)!=0)return 7;
        fd=-1;
    } else if(strcmp(argv[1],"fd")!=0)return 8;
    printf("%ld\n",syscall(SYS_gettid));fflush(stdout);
    char c;while(read(STDIN_FILENO,&c,1)>0) {}
    if(mapping && munmap(mapping,4096)!=0)return 9;
    if(fd>=0 && close(fd)!=0)return 10;
    return 0;
}
