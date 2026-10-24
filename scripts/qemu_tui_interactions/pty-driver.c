/* Guest-only real TUI hotplug, settings reload and service permission refusal. */
#define _GNU_SOURCE
#include <pty.h>
#include <termios.h>
#include <sys/ioctl.h>
#include <sys/wait.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <poll.h>
#include <unistd.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <time.h>

static long long now(void){struct timespec t;if(clock_gettime(CLOCK_MONOTONIC,&t))exit(3);return (long long)t.tv_sec*1000+t.tv_nsec/1000000;}
static int same(const struct termios *a,const struct termios *b){return a->c_line==b->c_line&&a->c_iflag==b->c_iflag&&a->c_oflag==b->c_oflag&&a->c_cflag==b->c_cflag&&a->c_lflag==b->c_lflag&&!memcmp(a->c_cc,b->c_cc,NCCS)&&cfgetispeed(a)==cfgetispeed(b)&&cfgetospeed(a)==cfgetospeed(b);}
static int copy(const char *from,const char *to){int in=open(from,O_RDONLY),out=open(to,O_CREAT|O_EXCL|O_WRONLY,0600);if(in<0||out<0)return -1;char b[4096];ssize_t n;while((n=read(in,b,sizeof b))>0)if(write(out,b,n)!=n)return -1;close(in);close(out);return n<0?-1:0;}
static int mark(const char *prefix, const char *phase) {
    char path[512];
    if (snprintf(path, sizeof path, "%s.%s", prefix, phase) >= (int)sizeof path) return -1;
    FILE *file = fopen(path, "wx");
    if (!file) return -1;
    int result = fprintf(file, "%lld\n", now()) < 0 ? -1 : 0;
    if (fclose(file)) result = -1;
    return result;
}
static int ready(const char *prefix, const char *phase) {
    char path[512]; struct stat value;
    if (snprintf(path, sizeof path, "%s.%s", prefix, phase) >= (int)sizeof path) return 0;
    return !lstat(path, &value) && S_ISREG(value.st_mode) && value.st_uid == 0 && value.st_nlink == 1;
}
int main(int argc,char **argv){
    if(argc!=3)return 2;
    if(!getenv("RUSTUSB_QEMU_GUEST") || strcmp(getenv("RUSTUSB_QEMU_GUEST"),"1"))return 2;
    FILE *guard=fopen("/proc/cmdline","r");char boot[8192]={0};if(!guard||!fgets(boot,sizeof boot,guard)||!strstr(boot,"rustusb.tui=integration"))return 2;fclose(guard);
    const char *mode=argv[1],*prefix=argv[2];
    if(strcmp(mode,"hotplug") && strcmp(mode,"preferences") && strcmp(mode,"reload-error") && strcmp(mode,"deny")) return 2;
    int expected_exit = !strcmp(mode,"deny") ? 1 : 0;
    int master,slave,status=0,done=0,raw=0,sent=0,resized=0,saw_enter=0,saw_leave=0;size_t total=0;
    struct termios before,after;struct winsize size={.ws_row=30,.ws_col=120};
    if(openpty(&master,&slave,NULL,NULL,&size)||tcgetattr(slave,&before))return 3;
    pid_t pid=fork();if(pid<0)return 3;
    if(!pid){close(master);if(setsid()<0||ioctl(slave,TIOCSCTTY,0)<0)return 3;for(int i=0;i<3;i++)if(dup2(slave,i)<0)return 3;if(slave>2)close(slave);setenv("TERM","xterm-256color",1);
    if(!strcmp(mode,"preferences") || !strcmp(mode,"reload-error"))
        execl("/usr/bin/rustusb","rustusb","tui","--config","/etc/rustusb/agent.json","--preferences-directory","/home/operator/.config/rustusb",NULL);
    else
        execl("/usr/bin/rustusb","rustusb","tui","--config","/etc/rustusb/agent.json","--no-preferences","--no-auto-refresh",NULL);
    _exit(127);}
    char path[512],source[128];snprintf(path,sizeof path,"%s.raw",prefix);int output=open(path,O_CREAT|O_EXCL|O_WRONLY,0600);if(output<0){kill(pid,SIGKILL);waitpid(pid,NULL,0);return 3;}
    long long begin=now(), phase_at=begin;char captured[524289]={0};
    while(now()-begin<90000){
        struct pollfd p={.fd=master,.events=POLLIN};if(poll(&p,1,30)>0){char b[8192];ssize_t n=read(master,b,sizeof b);if(n>0){if(total+(size_t)n>sizeof captured-1)break;memcpy(captured+total,b,n);total+=(size_t)n;if(write(output,b,n)!=n)break;}}
        if(!raw&&tcgetattr(slave,&after)==0&&!(after.c_lflag&(ICANON|ECHO))){raw=1;
            snprintf(path,sizeof path,"%s.pid",prefix);FILE *pf=fopen(path,"wx");if(!pf)break;fprintf(pf,"%d\n",pid);if(fclose(pf))break;
            snprintf(source,sizeof source,"/proc/%d/status",pid);snprintf(path,sizeof path,"%s.status",prefix);if(copy(source,path))break;
            snprintf(source,sizeof source,"/proc/%d/stat",pid);snprintf(path,sizeof path,"%s.stat",prefix);if(copy(source,path))break;
            snprintf(source,sizeof source,"/proc/%d/exe",pid);snprintf(path,sizeof path,"%s.exe-sha256",prefix);
            pid_t hasher=fork();if(!hasher){int fd=open(path,O_CREAT|O_EXCL|O_WRONLY,0600);if(fd<0)_exit(3);dup2(fd,1);execl("/bin/busybox","busybox","sha256sum",source,NULL);_exit(127);}int hs;if(hasher<0||waitpid(hasher,&hs,0)!=hasher||!WIFEXITED(hs)||WEXITSTATUS(hs))break;
        }
        if(!resized&&now()-begin>1500){size.ws_col=60;size.ws_row=18;if(ioctl(master,TIOCSWINSZ,&size))break;resized=1;}
        long long elapsed=now()-begin;
        if(!strcmp(mode,"hotplug")) {
            if(!sent && raw && elapsed>2500) {
                const char *keys="/0627:0001\r";
                if(write(master,keys,strlen(keys))!=(ssize_t)strlen(keys) || mark(prefix,"ready")) break;
                sent=1;
            }
            if(sent==1 && ready(prefix,"added")) {phase_at=now();sent=2;}
            if(sent==2 && now()-phase_at>1500) {if(write(master,"\r",1)!=1)break;phase_at=now();sent=3;}
            if(sent==3 && now()-phase_at>400) {
                const char *keys="\033[B\033[B\033[B\033[B\033[B\033[B";
                if(write(master,keys,strlen(keys))!=(ssize_t)strlen(keys))break;
                phase_at=now();sent=4;
            }
            if(sent==4 && now()-phase_at>1200) {if(mark(prefix,"added-shown"))break;sent=5;}
            if(sent==5 && ready(prefix,"removed")) {phase_at=now();sent=6;}
            if(sent==6 && now()-phase_at>2000) {if(mark(prefix,"removed-shown"))break;sent=7;}
            if(sent==7 && ready(prefix,"readded")) {phase_at=now();sent=8;}
            if(sent==8 && now()-phase_at>1500) {
                const char *keys="\033[B\033[B\033[B\033[B\033[B\033[B";
                if(write(master,keys,strlen(keys))!=(ssize_t)strlen(keys))break;
                phase_at=now();sent=9;
            }
            if(sent==9 && now()-phase_at>1200) {if(mark(prefix,"readded-shown"))break;sent=10;}
            if(sent==10 && ready(prefix,"finish")) {if(write(master,"q",1)!=1)break;sent=11;}
        } else if(!strcmp(mode,"preferences")) {
            if(!sent && elapsed>2500) {if(write(master,"6",1)!=1)break;sent=1;}
            if(sent==1 && elapsed>3500) {if(write(master,"+",1)!=1)break;sent=2;}
            if(sent==2 && elapsed>4100) {if(write(master,"m",1)!=1)break;sent=3;}
            if(sent==3 && elapsed>4700) {if(write(master,"a",1)!=1)break;sent=4;}
            if(sent==4 && elapsed>5300) {if(write(master,"d",1)!=1)break;sent=5;}
            if(sent==5 && elapsed>6500) {if(mark(prefix,"defaults-shown"))break;sent=6;}
            if(sent==6 && ready(prefix,"changed")) {if(write(master,"l",1)!=1)break;phase_at=now();sent=7;}
            if(sent==7 && now()-phase_at>2000) {if(write(master,"q",1)!=1)break;sent=8;}
        } else if(!strcmp(mode,"reload-error")) {
            if(!sent && elapsed>2500) {if(write(master,"6",1)!=1)break;sent=1;}
            if(sent==1 && elapsed>3500) {if(mark(prefix,"ready"))break;sent=2;}
            if(sent==2 && ready(prefix,"changed")) {if(write(master,"l",1)!=1)break;phase_at=now();sent=3;}
            if(sent==3 && now()-phase_at>2000) {if(write(master,"q",1)!=1)break;sent=4;}
        } else {
            if(!sent && elapsed>3000) {if(write(master,"e",1)!=1)break;sent=1;}
            if(sent==1 && elapsed>3500) {if(write(master,"\033[C\r",4)!=4)break;sent=2;}
            if(sent==2 && elapsed>5500) {if(write(master,"e",1)!=1)break;sent=3;}
            if(sent==3 && elapsed>6000) {if(write(master,"\033[C\r",4)!=4)break;sent=4;}
            if(sent==4 && elapsed>8000) {if(write(master,"5",1)!=1)break;sent=5;}
            if(sent==5 && elapsed>8500) {if(write(master,"r",1)!=1)break;sent=6;}
            if(sent==6 && elapsed>10500) {if(write(master,"q",1)!=1)break;sent=7;}
        }
        pid_t result=waitpid(pid,&status,WNOHANG);if(result==pid){done=1;break;}if(result<0)break;
    }
    /* Drain returned output after the real child wait; never fake its exit. */
    for(int i=0;i<10;i++){struct pollfd p={.fd=master,.events=POLLIN};if(poll(&p,1,20)<=0)break;char b[8192];ssize_t n=read(master,b,sizeof b);if(n<=0)break;if(total+(size_t)n>=sizeof captured)break;memcpy(captured+total,b,n);total+=(size_t)n;if(write(output,b,n)!=n)break;}
    close(output);captured[total]=0;saw_enter=strstr(captured,"\033[?1049h")!=NULL;saw_leave=strstr(captured,"\033[?1049l")!=NULL;
    int restored=tcgetattr(slave,&after)==0&&same(&before,&after);
    snprintf(path,sizeof path,"%s.termios",prefix);FILE *tf=fopen(path,"wx");if(!tf)return 3;
    const struct termios *ts[2]={&before,&after};const char *labels[2]={"before","after"};
    for(int k=0;k<2;k++){fprintf(tf,"%s_flags=%lx:%lx:%lx:%lx:%x\n%s_speeds=%u:%u\n%s_cc=",labels[k],(unsigned long)ts[k]->c_iflag,(unsigned long)ts[k]->c_oflag,(unsigned long)ts[k]->c_cflag,(unsigned long)ts[k]->c_lflag,ts[k]->c_line,labels[k],(unsigned)cfgetispeed(ts[k]),(unsigned)cfgetospeed(ts[k]),labels[k]);for(int c=0;c<NCCS;c++)fprintf(tf,"%02x",ts[k]->c_cc[c]);fprintf(tf,"\n");}fclose(tf);
    if(!done){kill(pid,SIGKILL);waitpid(pid,&status,0);}
    snprintf(path,sizeof path,"%s.result",prefix);FILE *f=fopen(path,"wx");if(!f)return 3;
    fprintf(f,"pid=%d\nuid=%d\nmode=%s\nexpected_exit=%d\nwaited=%d\nexit=%d\nraw_observed=%d\nresized=%d\nrestored=%d\nentered=%d\nleft=%d\nbytes=%zu\n",pid,getuid(),mode,expected_exit,done,WIFEXITED(status)?WEXITSTATUS(status):-1,raw,resized,restored,saw_enter,saw_leave,total);fclose(f);close(master);close(slave);
    return !(done&&WIFEXITED(status)&&WEXITSTATUS(status)==expected_exit&&raw&&resized&&restored&&saw_enter&&saw_leave&&strstr(captured,"RustUSB"));
}
