/* Original MIT, guest-only ICCCM WM_DELETE_WINDOW sender; never kills X clients. */
#define _POSIX_C_SOURCE 200809L
#include <X11/Xlib.h>
#include <X11/Xatom.h>
#include <X11/Xutil.h>
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    char *end=NULL, exe[256], path[128], boot[4096];
    if (argc!=2 || geteuid()!=1000) return 2;
    errno=0; long pid=strtol(argv[1],&end,10);
    if(errno || !end || *end || pid<1 || pid>INT_MAX) return 2;
    FILE *f=fopen("/proc/cmdline","r");
    if(!f || !fgets(boot,sizeof(boot),f)) {if(f)fclose(f);return 2;}
    fclose(f);
    if(!strstr(boot,"RUSTUSB_QEMU_GUEST=1") || !strstr(boot,"rustusb.agent=1")) return 2;
    snprintf(path,sizeof(path),"/proc/%ld/exe",pid);
    ssize_t bytes=readlink(path,exe,sizeof(exe)-1);
    if(bytes<0) return 2;
    exe[bytes]='\0'; if(strcmp(exe,"/usr/bin/rustusb-gui")) return 2;
    Display *d=XOpenDisplay(":88"); if(!d) return 3;
    Window root=DefaultRootWindow(d), parent, actual_root, *children=NULL, target=0;
    unsigned int count=0, matches=0;
    Atom pid_atom=XInternAtom(d,"_NET_WM_PID",True), deletion=XInternAtom(d,"WM_DELETE_WINDOW",True);
    if(!pid_atom || !deletion || !XQueryTree(d,root,&actual_root,&parent,&children,&count) || count>4096) {XCloseDisplay(d);return 3;}
    for(unsigned int i=0;i<count;i++) {
        Atom type; int format; unsigned long n,left; unsigned char *data=NULL;
        XWindowAttributes attrs;
        int ok=XGetWindowProperty(d,children[i],pid_atom,0,1,False,XA_CARDINAL,&type,&format,&n,&left,&data);
        if(ok==Success && type==XA_CARDINAL && format==32 && n==1 && left==0 && data && *(unsigned long *)data==(unsigned long)pid && XGetWindowAttributes(d,children[i],&attrs)) {
            char *name=NULL;XFetchName(d,children[i],&name);
            fprintf(stderr,"pid-window=%lu mapped=%d name=%s\n",(unsigned long)children[i],attrs.map_state,name?name:"(none)");
            if(name)XFree(name);
            if(attrs.map_state==IsViewable) {target=children[i];matches++;}
        }
        if(data)XFree(data);
    }
    if(children)XFree(children);
    Atom *protocols=NULL;int total=0,advertised=0;
    if(matches!=1) {fprintf(stderr,"mapped-pid-matches=%u\n",matches);XCloseDisplay(d);return 4;}
    if(!XGetWMProtocols(d,target,&protocols,&total)) {fprintf(stderr,"WM_PROTOCOLS absent window=%lu\n",(unsigned long)target);XCloseDisplay(d);return 4;}
    for(int i=0;i<total;i++)if(protocols[i]==deletion)advertised=1;
    XFree(protocols);if(!advertised){XCloseDisplay(d);return 4;}
    Window stamp=XCreateSimpleWindow(d,root,0,0,1,1,0,0,0);
    XSelectInput(d,stamp,PropertyChangeMask);
    Atom clock_atom=XInternAtom(d,"RUSTUSB_FIXTURE_TIME",False);
    unsigned char byte=1;XChangeProperty(d,stamp,clock_atom,XA_INTEGER,8,PropModeReplace,&byte,1);
    XEvent event; XNextEvent(d,&event);
    if(event.type!=PropertyNotify){XDestroyWindow(d,stamp);XCloseDisplay(d);return 5;}
    Time time=event.xproperty.time;
    memset(&event,0,sizeof(event));event.xclient.type=ClientMessage;event.xclient.window=target;
    event.xclient.message_type=XInternAtom(d,"WM_PROTOCOLS",False);event.xclient.format=32;
    event.xclient.data.l[0]=(long)deletion;event.xclient.data.l[1]=(long)time;
    int sent=XSendEvent(d,target,False,NoEventMask,&event);XSync(d,False);
    printf("pid=%ld window=%lu protocol=WM_DELETE_WINDOW timestamp=%lu sent=%d\n",pid,(unsigned long)target,(unsigned long)time,sent);
    XDestroyWindow(d,stamp);XCloseDisplay(d);return sent?0:6;
}
