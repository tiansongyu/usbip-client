from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from pathlib import Path
root=Path('/tmp/rustusb-rule-decisions-site-preview-20261025');root.mkdir(exist_ok=False);(root/'nojs').mkdir();website=Path('/home/ubuntu/github/usbip-client/website');(root/'usbip-client').symlink_to(website,target_is_directory=True);(root/'nojs/usbip-client').symlink_to(website,target_is_directory=True)
class Handler(SimpleHTTPRequestHandler):
 def __init__(self,*a,**k):super().__init__(*a,directory=str(root),**k)
 def end_headers(self):
  if self.path.startswith('/nojs/'):self.send_header('Content-Security-Policy',"script-src 'none'")
  super().end_headers()
ThreadingHTTPServer(('127.0.0.1',4178),Handler).serve_forever()
