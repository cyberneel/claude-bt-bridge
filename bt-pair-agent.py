#!/usr/bin/env python3
# ponytail: throwaway auto-accept BlueZ pairing agent so pairing needs no
# interactive passkey confirm on this end. Kill it after pairing (auto-accepts
# ANY device while running). stdlib + dbus/gi only.
import dbus, dbus.service, dbus.mainloop.glib
from gi.repository import GLib

AGENT_PATH = "/cyber/pairagent"
CAP = "NoInputNoOutput"   # "just works" pairing, auto-accept

class Agent(dbus.service.Object):
    def _ok(self, what, dev, extra=""):
        print(f"{what} {dev} {extra} -> accept", flush=True)

    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Release(self): pass
    @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
    def AuthorizeService(self, device, uuid): self._ok("AuthorizeService", device, uuid)
    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="s")
    def RequestPinCode(self, device): self._ok("RequestPinCode", device); return "0000"
    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="u")
    def RequestPasskey(self, device): self._ok("RequestPasskey", device); return dbus.UInt32(0)
    @dbus.service.method("org.bluez.Agent1", in_signature="ouq", out_signature="")
    def DisplayPasskey(self, device, passkey, entered):
        print(f"DisplayPasskey {device} {int(passkey):06} entered={entered}", flush=True)
    @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
    def DisplayPinCode(self, device, pincode):
        print(f"DisplayPinCode {device} {pincode}", flush=True)
    @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="")
    def RequestConfirmation(self, device, passkey):
        print(f"RequestConfirmation {device} {int(passkey):06} -> auto-accept", flush=True)  # return=accept
    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="")
    def RequestAuthorization(self, device): self._ok("RequestAuthorization", device)
    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Cancel(self): print("Cancel", flush=True)

dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
bus = dbus.SystemBus()
agent = Agent(bus, AGENT_PATH)
mgr = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"), "org.bluez.AgentManager1")
mgr.RegisterAgent(AGENT_PATH, CAP)
mgr.RequestDefaultAgent(AGENT_PATH)
print("PAIR-AGENT READY (auto-accepting). Add 'dell-arch-cyber' from Windows now.", flush=True)
GLib.MainLoop().run()
