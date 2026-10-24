from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from pathlib import Path
import json,os
root=Path('/tmp/rustusb-prepare-preview-root-20261024');root.mkdir(exist_ok=False);(root/'usbip-client').symlink_to('/home/ubuntu/github/usbip-client/website',target_is_directory=True)
class Handler(SimpleHTTPRequestHandler):
 def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(root),**kwargs)
server=ThreadingHTTPServer(('127.0.0.1',0),Handler);port=server.server_address[1]
Path('/tmp/rustusb-prepare-docs-site-20261024/preview.json').write_text(json.dumps(dict(pid=os.getpid(),host='127.0.0.1',port=port,url=f'http://127.0.0.1:{port}/usbip-client/',script=__file__),indent=2)+'\n');print(f'PREVIEW http://127.0.0.1:{port}/usbip-client/',flush=True)
server.serve_forever()
