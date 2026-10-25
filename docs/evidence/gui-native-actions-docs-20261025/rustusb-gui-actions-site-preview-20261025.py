from pathlib import Path
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from functools import partial
import json
base=Path('/tmp/rustusb-gui-actions-site-preview-20261025');base.mkdir(exist_ok=False)
for name in ['usbip-client','no-script']:(base/name).symlink_to('/home/ubuntu/github/usbip-client/website',target_is_directory=True)
class Handler(SimpleHTTPRequestHandler):
 def end_headers(self):
  if self.path.startswith('/no-script/'):self.send_header('Content-Security-Policy',"script-src 'none'")
  super().end_headers()
server=ThreadingHTTPServer(('127.0.0.1',0),partial(Handler,directory=str(base)))
(base/'server.json').write_text(json.dumps({'host':'127.0.0.1','port':server.server_port})+'\n')
print(server.server_port,flush=True);server.serve_forever()
