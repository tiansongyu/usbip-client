from pathlib import Path
import http.server,json,os
root=Path('/tmp/rustusb-authentication-admission-preview-20261024');root.mkdir(exist_ok=True)
for name in ['usbip-client','usbip-client-nojs']:
 if not (root/name).exists():(root/name).symlink_to('/home/ubuntu/github/usbip-client/website',target_is_directory=True)
class Handler(http.server.SimpleHTTPRequestHandler):
 def __init__(self,*a,**kw):super().__init__(*a,directory=str(root),**kw)
 def end_headers(self):
  if self.path.startswith('/usbip-client-nojs/'):self.send_header('Content-Security-Policy',"script-src 'none'")
  super().end_headers()
p=Path('/tmp/rustusb-authentication-admission-docs-site-20261024/preview.json');p.write_text(json.dumps(dict(pid=os.getpid(),host='127.0.0.1',port=4187,root=str(root)))+'\n')
try:http.server.ThreadingHTTPServer(('127.0.0.1',4187),Handler).serve_forever()
except KeyboardInterrupt:pass
