#!/usr/bin/env python3
# Runs in WSL. Claude connects to 127.0.0.1:8080; we mux every connection over a
# single TCP link to the Windows COM-forwarder, which pipes it over Bluetooth to
# the Linux laptop's bridge -> reverse proxy -> api.anthropic.com. stdlib only.
#
# The Windows forwarder CONNECTS to us (outbound -> no Windows Firewall prompt).
# Self-heals: the Claude listener stays up across drops; we re-accept the forwarder
# on reconnect; and a PING heartbeat + watchdog detects *silent* Bluetooth stalls
# (half-open link) and tears the transport down so the forwarder redials.
#
# Frame: >HBH (stream_id, type, length) + payload.  type: 0=OPEN 1=DATA 2=CLOSE 3=PING
# Usage: python3 bt-bridge-wsl.py [TRANSPORT_PORT]   (default 20000)
import os, socket, struct, sys, threading, time, zlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from btdict import DICT

CLAUDE = ("127.0.0.1", 8080)
XPORT = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
HDR = struct.Struct(">HBH")
OPEN, DATA, CLOSE, PING, DATA_Z, DATA_ZD = 0, 1, 2, 3, 4, 5   # _Z plain zlib, _ZD zlib+Claude dict (both stateless)

def _zdc(d):
    co = zlib.compressobj(6, zlib.DEFLATED, zlib.MAX_WBITS, zlib.DEF_MEM_LEVEL, zlib.Z_DEFAULT_STRATEGY, DICT)
    return co.compress(d) + co.flush()
def _zdd(d):
    do = zlib.decompressobj(zlib.MAX_WBITS, DICT)
    return do.decompress(d) + do.flush()
HB_EVERY = 5
HB_TIMEOUT = 15

current = {"mux": None}
clock = threading.Lock()

class Mux:
    def __init__(self, transport):
        self.t = transport
        self.wlock = threading.Lock()
        self.slock = threading.Lock()
        self.streams = {}
        self.next_sid = 1
        self.alive = True
        self.last_recv = time.monotonic()

    def send(self, sid, typ, payload=b""):
        if typ == DATA and len(payload) > 32:       # per-frame dict compression (stateless)
            z = _zdc(payload)
            if len(z) < len(payload):
                typ, payload = DATA_ZD, z
        with self.wlock:
            try: self.t.sendall(HDR.pack(sid, typ, len(payload)) + payload)
            except OSError: pass

    def _recvn(self, n):
        buf = b""
        while len(buf) < n:
            try: chunk = self.t.recv(n - len(buf))
            except OSError: return None
            if not chunk: return None
            buf += chunk
        return buf

    def _drop(self, sid):
        with self.slock:
            sock = self.streams.pop(sid, None)
        if sock:
            try: sock.close()
            except OSError: pass

    def add_client(self, sock):
        with self.slock:
            sid = self.next_sid; self.next_sid = (self.next_sid % 65535) + 1
            self.streams[sid] = sock
        self.send(sid, OPEN)
        threading.Thread(target=self._pump_client, args=(sid, sock), daemon=True).start()

    def _pump_client(self, sid, sock):          # Claude -> mux
        try:
            while self.alive:
                data = sock.recv(4096)
                if not data: break
                self.send(sid, DATA, data)
        except OSError:
            pass
        finally:
            self.send(sid, CLOSE)
            self._drop(sid)

    def _heartbeat(self):
        while self.alive:
            time.sleep(HB_EVERY)
            self.send(0, PING)

    def _watchdog(self):
        while self.alive:
            time.sleep(3)
            if time.monotonic() - self.last_recv > HB_TIMEOUT:
                print("[wsl] no heartbeat -> dropping transport (forwarder will redial)", flush=True)
                try: self.t.shutdown(socket.SHUT_RDWR)
                except OSError: pass
                return

    def reader(self):                           # mux -> Claude; blocks until transport dies
        threading.Thread(target=self._heartbeat, daemon=True).start()
        threading.Thread(target=self._watchdog, daemon=True).start()
        while True:
            hdr = self._recvn(HDR.size)
            if not hdr: break
            self.last_recv = time.monotonic()
            sid, typ, ln = HDR.unpack(hdr)
            payload = self._recvn(ln) if ln else b""
            if ln and payload is None: break
            if typ == PING:
                continue
            if typ in (DATA_Z, DATA_ZD):
                try:
                    payload = _zdd(payload) if typ == DATA_ZD else zlib.decompress(payload)
                except zlib.error:
                    print("[wsl] bad compressed frame -> link down", flush=True)
                    break                        # mismatch/corruption: clean reconnect, never forward garbage
                typ = DATA
            if typ == DATA:
                with self.slock: sock = self.streams.get(sid)
                if sock:
                    try: sock.sendall(payload)
                    except OSError: self._drop(sid)
            elif typ == CLOSE:
                self._drop(sid)
            else:
                print(f"[wsl] unknown frame type {typ} -> link down", flush=True)
                break                            # version mismatch: clean reconnect

    def shutdown(self):
        self.alive = False
        with self.slock:
            socks = list(self.streams.values()); self.streams.clear()
        for s in socks:
            try: s.close()
            except OSError: pass

def claude_listener():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(CLAUDE); srv.listen(64)
    print(f"[wsl] Claude -> http://{CLAUDE[0]}:{CLAUDE[1]}", flush=True)
    while True:
        sock, _ = srv.accept()
        with clock: mux = current["mux"]
        if mux is None:
            sock.close()                        # link down: fail fast (Claude retries)
        else:
            mux.add_client(sock)

threading.Thread(target=claude_listener, daemon=True).start()

srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("0.0.0.0", XPORT)); srv.listen(1)
print(f"[wsl] waiting for the Windows forwarder on :{XPORT} ...", flush=True)
while True:
    t, addr = srv.accept()
    mux = Mux(t)
    with clock: current["mux"] = mux
    print(f"[wsl] forwarder connected from {addr} - link up", flush=True)
    mux.reader()                                # blocks until transport dies
    with clock:
        if current["mux"] is mux: current["mux"] = None
    mux.shutdown()
    print("[wsl] link down - waiting for forwarder to reconnect", flush=True)
