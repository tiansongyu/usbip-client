from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlsplit
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs): super().__init__(*args,directory="/tmp/rustusb-data-link-site-preview-20261024",**kwargs)
    def translate_path(self,path):
        if urlsplit(path).path.startswith("/nojs/"): path=path[len("/nojs"):]
        return super().translate_path(path)
    def end_headers(self):
        self.send_header("Cache-Control","no-store")
        if urlsplit(self.path).path.startswith("/nojs/"): self.send_header("Content-Security-Policy","script-src 'none'")
        super().end_headers()
ThreadingHTTPServer(("127.0.0.1",4174),Handler).serve_forever()
