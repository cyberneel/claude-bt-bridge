#!/usr/bin/env python3
# ponytail: Linux end of the Bluetooth bridge. Registers SPP, and over the one
# RFCOMM byte stream from Windows runs a tiny stream-mux: each OPEN spawns a TCP
# connection to the local reverse proxy (-> api.anthropic.com), DATA relays both
# ways, CLOSE tears down. A PING heartbeat every few seconds lets each end notice
# a *silent* Bluetooth stall (half-open link) and force a reconnect.
# stdlib + dbus/gi only.
#
# Frame: >HBH (stream_id, type, length) + payload.  type: 0=OPEN 1=DATA 2=CLOSE 3=PING
import dbus, dbus.service, dbus.mainloop.glib, os, socket, struct, threading, time, zlib
from gi.repository import GLib

SPP_UUID = "00001101-0000-1000-8000-00805f9b34fb"
PROFILE_PATH = "/cyber/spp"
PROXY = ("127.0.0.1", 8080)
HDR = struct.Struct(">HBH")
OPEN, DATA, CLOSE, PING, DATA_Z = 0, 1, 2, 3, 4   # DATA_Z = per-frame zlib DATA (stateless)
CHUNK = 4096                                      # read size; small frames = better interleaving, fewer drops
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
        # wrap the RFCOMM fd as a socket: recv/sendall/shutdown. Python sockets go
        # to fd -1 on close, so there's no fd-reuse race like a bare os.close(fd).
        self.sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM, fileno=fd)
        self.sock.setblocking(True)
        self.wlock = threading.Lock()
        self.slock = threading.Lock()
        self.streams = {}
        self.alive = True
        self.last_recv = time.monotonic()

    def send(self, sid, typ, payload=b""):
        raw = len(payload)
        if typ == DATA and raw > 64:                # per-frame zlib (stateless)
            z = zlib.compress(payload, 6)
            if len(z) < raw:
                typ, payload = DATA_Z, z
        if typ in (DATA, DATA_Z):
            global _sraw, _swire
            with _slock:
                _sraw += raw; _swire += len(payload)
        frame = HDR.pack(sid, typ, len(payload)) + payload
        with self.wlock:
            try:
                self.sock.sendall(frame)        # loops internally; close-safe
            except OSError:
                pass

    def _readn(self, n):
        buf = b""
        while len(buf) < n:
            try:
                chunk = self.sock.recv(n - len(buf))
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

    def _watchdog(self):
        while self.alive:
            time.sleep(3)
            if time.monotonic() - self.last_recv > HB_TIMEOUT:
                print("[mux] no heartbeat -> forcing link down", flush=True)
                try: self.sock.shutdown(socket.SHUT_RDWR)   # unblock the reader; no fd-reuse race
                except OSError: pass
                return

    def _pump_upstream(self, sid, sock):        # proxy -> RFCOMM
        try:
            while True:
                data = sock.recv(CHUNK)
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
            if typ == DATA_Z:
                try:
                    payload = zlib.decompress(payload)
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
        try: self.sock.close()
        except OSError: pass

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
