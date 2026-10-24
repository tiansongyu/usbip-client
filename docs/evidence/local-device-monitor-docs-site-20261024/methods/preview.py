from pathlib import Path
import http.server,json,os
out=Path('/tmp/rustusb-hotplug-docs-site-20261024')
root=Path('/tmp/rustusb-hotplug-preview-20261024');root.mkdir(exist_ok=False)
for name in ['usbip-client','usbip-client-nojs']:(root/name).symlink_to('/home/ubuntu/github/usbip-client/website',target_is_directory=True)
class Handler(http.server.SimpleHTTPRequestHandler):
 def __init__(self,*a,**kw):super().__init__(*a,directory=str(root),**kw)
 def end_headers(self):
  if self.path.startswith('/usbip-client-nojs/'):self.send_header('Content-Security-Policy',"script-src 'none'")
  super().end_headers()
 def log_message(self,fmt,*args):
  with (out/'preview-http.log').open('a') as f:f.write(fmt%args+'\n')
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
(out/'preview.json').write_text(json.dumps(dict(pid=os.getpid(),host='127.0.0.1',port=server.server_port,root=str(root)))+'\n')
print('preview port',server.server_port,flush=True)
try:server.serve_forever()
except KeyboardInterrupt:pass
finally:server.server_close()
