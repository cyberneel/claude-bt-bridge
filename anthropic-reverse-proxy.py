#!/usr/bin/env python3
# ponytail: stdlib-only reverse proxy for ANTHROPIC_BASE_URL.
# Plaintext HTTP in (from Windows over the direct cable), TLS out to Anthropic.
# Ceiling: no TLS on the listener -> only safe on a private link. Upgrade path:
# terminate TLS here with a cert Claude Code trusts (NODE_EXTRA_CA_CERTS) if the
# hop stops being a direct cable.
import http.client, http.server, socketserver, ssl, sys

def logline(*a):
    print(*a, file=sys.stderr, flush=True)

UPSTREAM = "api.anthropic.com"
LISTEN = ("127.0.0.1", 8080)  # localhost; the BT bridge connects here
HOP = {"host", "connection", "keep-alive", "proxy-authenticate",
       "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"}

class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def _proxy(self):
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(n) if n else None
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
            up = http.client.HTTPSConnection(UPSTREAM, 443, timeout=600,
                                             context=ssl.create_default_context())
            up.request(self.command, self.path, body=body, headers=headers)
            r = up.getresponse()
            logline(f"{self.client_address[0]} {self.command} {self.path} -> {r.status}")
            self.send_response(r.status, r.reason)
            for k, v in r.getheaders():
                if k.lower() in HOP or k.lower() == "content-length":
                    continue
                self.send_header(k, v)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            while True:
                chunk = r.read(65536)           # stream SSE, don't buffer
                if not chunk:
                    break
                self.wfile.write(b"%X\r\n" % len(chunk) + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            up.close()
        except Exception as e:
            logline(f"{self.client_address[0]} {self.command} {self.path} !! {e!r}")
            try:
                self.send_error(502, "upstream error", str(e))
            except Exception:
                pass
    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _proxy
    def log_message(self, *a):
        pass

class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    def handle_error(self, request, client_address):
        e = sys.exc_info()[1]                   # BT-tunneled client blips reset the socket; expected
        if isinstance(e, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
            return                              # swallow: no scary traceback in the log
        super().handle_error(request, client_address)

if __name__ == "__main__":
    print(f"reverse proxy: http://{LISTEN[0]}:{LISTEN[1]} -> https://{UPSTREAM}")
    S(LISTEN, H).serve_forever()
