#!/usr/bin/env python3
# ponytail: Linux end of the Bluetooth bridge. Registers SPP, and over the one
# RFCOMM byte stream from Windows runs a tiny stream-mux: each OPEN spawns a TCP
# connection to the local reverse proxy (-> api.anthropic.com), DATA relays both
# ways, CLOSE tears down. A PING heartbeat every few seconds lets each end notice
# a *silent* Bluetooth stall (half-open link) and force a reconnect.
# stdlib + dbus/gi only.
#
# Frame: >HBH (stream_id, type, length) + payload.  type: 0=OPEN 1=DATA 2=CLOSE 3=PING
import dbus, dbus.service, dbus.mainloop.glib, os, socket, struct, sys, threading, time, zlib
from gi.repository import GLib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from btdict import DICT

SPP_UUID = "00001101-0000-1000-8000-00805f9b34fb"
PROFILE_PATH = "/cyber/spp"
PROXY = ("127.0.0.1", 8080)
HDR = struct.Struct(">HBH")
OPEN, DATA, CLOSE, PING, DATA_Z, DATA_ZD = 0, 1, 2, 3, 4, 5   # _Z plain zlib, _ZD zlib+Claude dict (both stateless)

def _zdc(d):                                      # per-frame compress with the static Claude dict
    co = zlib.compressobj(6, zlib.DEFLATED, zlib.MAX_WBITS, zlib.DEF_MEM_LEVEL, zlib.Z_DEFAULT_STRATEGY, DICT)
    return co.compress(d) + co.flush()
def _zdd(d):                                      # per-frame decompress with the static Claude dict
    do = zlib.decompressobj(zlib.MAX_WBITS, DICT)
    return do.decompress(d) + do.flush()
HB_EVERY = 5       # send a heartbeat this often
HB_TIMEOUT = 15    # declare the link dead after this much silence

_sraw = _swire = 0                                # compression accounting (response direction)
_slock = threading.Lock()
def _stats_loop():
    prev = 0
    while True:
        time.sleep(15)
        with _slock: raw, wire = _sraw, _swire
        if raw > prev and wire:
            print(f"[stats] DATA out: {raw:,} raw -> {wire:,} on-wire  ({raw/wire:.1f}x, {(1-wire/raw)*100:.0f}% saved)", flush=True)
            prev = raw

class Mux:
    def __init__(self, fd):
        os.set_blocking(fd, True)
        self.fd = fd
        self.wlock = threading.Lock()
        self.slock = threading.Lock()
        self.streams = {}
        self.alive = True
        self.last_recv = time.monotonic()

    def send(self, sid, typ, payload=b""):
        raw = len(payload)
        if typ == DATA and raw > 32:                # per-frame dict compression (stateless)
            z = _zdc(payload)
            if len(z) < raw:
                typ, payload = DATA_ZD, z
        if typ in (DATA, DATA_Z, DATA_ZD):
            global _sraw, _swire
            with _slock:
                _sraw += raw; _swire += len(payload)
        frame = HDR.pack(sid, typ, len(payload)) + payload
        with self.wlock:
            try:
                while frame:                    # os.write may write only part when the BT buffer is full
                    n = os.write(self.fd, frame)
                    frame = frame[n:]
            except OSError:
                pass

    def _readn(self, n):
        buf = b""
        while len(buf) < n:
            try:
                chunk = os.read(self.fd, n - len(buf))
            except OSError:
                return None                    # link dropped -> clean link-down
            if not chunk:
                return None
            buf += chunk
        return buf

    def _heartbeat(self):
        while self.alive:
            time.sleep(HB_EVERY)
            self.send(0, PING)

    def _watchdog(self):                        # ponytail: os.close to unblock the read; tiny fd-reuse race, acceptable
        while self.alive:
            time.sleep(3)
            if time.monotonic() - self.last_recv > HB_TIMEOUT:
                print("[mux] no heartbeat -> forcing link down", flush=True)
                try: os.close(self.fd)
                except OSError: pass
                return

    def _pump_upstream(self, sid, sock):        # proxy -> RFCOMM
        try:
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                self.send(sid, DATA, data)
        except OSError:
            pass
        finally:
            self.send(sid, CLOSE)
            self._drop(sid)

    def _drop(self, sid):
        with self.slock:
            sock = self.streams.pop(sid, None)
        if sock:
            try: sock.close()
            except OSError: pass

    def run(self):
        print("[mux] link up", flush=True)
        threading.Thread(target=self._heartbeat, daemon=True).start()
        threading.Thread(target=self._watchdog, daemon=True).start()
        while True:
            hdr = self._readn(HDR.size)
            if not hdr:
                break
            self.last_recv = time.monotonic()
            sid, typ, ln = HDR.unpack(hdr)
            payload = self._readn(ln) if ln else b""
            if ln and payload is None:
                break
            if typ == PING:
                continue
            if typ in (DATA_Z, DATA_ZD):
                try:
                    payload = _zdd(payload) if typ == DATA_ZD else zlib.decompress(payload)
                except zlib.error:
                    print("[mux] bad compressed frame -> link down", flush=True)
                    break                        # mismatch/corruption: clean reconnect, never forward garbage
                typ = DATA
            if typ == OPEN:
                try:
                    sock = socket.create_connection(PROXY)
                except OSError as e:
                    print(f"[mux] proxy connect failed: {e}", flush=True)
                    self.send(sid, CLOSE)
                    continue
                with self.slock:
                    self.streams[sid] = sock
                threading.Thread(target=self._pump_upstream, args=(sid, sock), daemon=True).start()
            elif typ == DATA:
                with self.slock:
                    sock = self.streams.get(sid)
                if sock:
                    try: sock.sendall(payload)
                    except OSError: self._drop(sid)
            elif typ == CLOSE:
                self._drop(sid)
            else:
                print(f"[mux] unknown frame type {typ} -> link down", flush=True)
                break                            # version mismatch: clean reconnect
        self.alive = False
        print("[mux] link down", flush=True)
        with self.slock:
            for s in self.streams.values():
                try: s.close()
                except OSError: pass
            self.streams.clear()

class Profile(dbus.service.Object):
    @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
    def Release(self): pass
    @dbus.service.method("org.bluez.Profile1", in_signature="oha{sv}", out_signature="")
    def NewConnection(self, device, fd, props):
        rfd = fd.take()
        print(f"[bt] connection from {device}", flush=True)
        threading.Thread(target=lambda: Mux(rfd).run(), daemon=True).start()
    @dbus.service.method("org.bluez.Profile1", in_signature="o", out_signature="")
    def RequestDisconnection(self, device): print(f"[bt] disconnect {device}", flush=True)

dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
bus = dbus.SystemBus()
Profile(bus, PROFILE_PATH)
mgr = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"), "org.bluez.ProfileManager1")
mgr.RegisterProfile(PROFILE_PATH, SPP_UUID, {
    "Name": "ClaudeBridge",
    "Role": "server",
    "RequireAuthentication": dbus.Boolean(False),
    "RequireAuthorization": dbus.Boolean(False),
})
print("Linux BT bridge ready (SPP 'ClaudeBridge' -> reverse proxy 127.0.0.1:8080)", flush=True)
threading.Thread(target=_stats_loop, daemon=True).start()
GLib.MainLoop().run()
