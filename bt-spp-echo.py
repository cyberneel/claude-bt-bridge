#!/usr/bin/env python3
# ponytail: minimal SPP gate test. Registers a Serial Port Profile via BlueZ so
# Windows can bind an outgoing COM port, then ECHOES bytes back. Proves the BT
# serial pipe works end-to-end before we build the real mux bridge.
# Replace handle() with the mux once this passes.
import dbus, dbus.service, dbus.mainloop.glib, os, threading
from gi.repository import GLib

SPP_UUID = "00001101-0000-1000-8000-00805f9b34fb"
PROFILE_PATH = "/cyber/spp"

def handle(fd, device):
    print(f"[conn] open from {device} fd={fd}", flush=True)
    os.set_blocking(fd, True)   # BlueZ hands us a non-blocking fd; block on reads
    try:
        while True:
            data = os.read(fd, 4096)
            if not data:
                break
            print(f"[rx] {len(data)}B {data[:60]!r}", flush=True)
            os.write(fd, data)          # echo back
    except OSError as e:
        print(f"[conn] closed {device}: {e}", flush=True)
    finally:
        try: os.close(fd)
        except OSError: pass

class Profile(dbus.service.Object):
    @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
    def Release(self): print("[profile] released", flush=True)
    @dbus.service.method("org.bluez.Profile1", in_signature="oha{sv}", out_signature="")
    def NewConnection(self, device, fd, props):
        rfd = fd.take()
        threading.Thread(target=handle, args=(rfd, str(device)), daemon=True).start()
    @dbus.service.method("org.bluez.Profile1", in_signature="o", out_signature="")
    def RequestDisconnection(self, device): print(f"[profile] disconnect {device}", flush=True)

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
print("SPP 'ClaudeBridge' registered (echo mode). Add an outgoing COM port to it on Windows.", flush=True)
GLib.MainLoop().run()
