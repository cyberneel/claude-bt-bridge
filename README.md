# claude-bt-bridge

Pipe Claude Code (running in **WSL** on a Windows laptop) through a **second Linux
laptop's** internet connection, over a **Bluetooth serial link**.

## Why Bluetooth?

Sometimes you want one machine to reach the internet through another machine's
connection, but there's no convenient IP path between the two — no shared
Wi‑Fi/LAN, no Ethernet run between them, and standard tethering / connection
sharing isn't practical to set up. If both machines have Bluetooth, an **RFCOMM /
Serial Port Profile (SPP)** link gives you a dead-simple point-to-point byte pipe
with zero IP configuration — you don't have to touch either machine's networking.

So this proxies a single app's HTTP traffic (Claude Code, in WSL on a Windows
laptop) out to the internet via a second Linux laptop, entirely over that
Bluetooth serial link. We run a small TCP-over-serial mux across it, with
**per-frame zlib compression** — each frame is compressed on its own (stateless),
so a Bluetooth drop just reconnects with no desync. Raw link is ~1 Mbps;
compression cuts JSON/text several-fold.

> Note: compression must be per-frame/stateless here. Streaming zlib or `ssh -C`
> carry session state that desyncs when the flaky link drops (silent corruption or
> dead sessions) — both were tried and abandoned. See `test_perframe.py`.

## Path

```
Claude (WSL)
  -> 127.0.0.1:8080        bt-bridge-wsl.py   (mux: one stream per connection)
  -> tcp/20000             bt-forward.ps1     (Windows: dumb COM<->TCP pipe)
  -> COMx  -> Bluetooth -> Linux laptop
  -> bt-bridge-linux.py    (SPP server; demuxes to the reverse proxy)
  -> 127.0.0.1:8080        anthropic-reverse-proxy.py  (http -> https, Host rewrite)
  -> https://api.anthropic.com
```

`ANTHROPIC_BASE_URL=http://127.0.0.1:8080` in WSL points Claude Code at the mux.

## Files (where each runs)

| File | Runs on | Role |
|------|---------|------|
| `anthropic-reverse-proxy.py` | Linux laptop | http→https reverse proxy to `api.anthropic.com` (stdlib) |
| `bt-bridge-linux.py`         | Linux laptop | BlueZ SPP server + mux → reverse proxy (stdlib + dbus/gi) |
| `bt-pair-agent.py`           | Linux laptop | one-shot auto-accept pairing agent (run only while pairing) |
| `bt-spp-echo.py`             | Linux laptop | optional echo server to prove the serial pipe (gate test) |
| `bt-forward.ps1`             | Windows      | dumb COM↔TCP forwarder (native .NET, no install) |
| `bt-bridge-wsl.py`           | WSL          | mux client; listens on `127.0.0.1:8080` (stdlib) |

## Setup (once)

### Linux laptop
```bash
# Bluetooth on + pairable, then run the auto-accept agent while you pair from Windows
bluetoothctl power on; bluetoothctl pairable on; bluetoothctl discoverable on
python3 bt-pair-agent.py          # kill it after pairing (auto-accepts ANY device)
```
Pair the laptops from **Windows** → Settings → Bluetooth → Add device → pick this
laptop. Then Ctrl-C the pair agent and `bluetoothctl discoverable off`.

### Windows
Bind an **Outgoing** COM port to the SPP service:
Settings → Bluetooth → *More Bluetooth settings* → **COM Ports** → Add → Outgoing
→ the Linux laptop → the `ClaudeBridge` (SPP) service. Note the `COMx` number.

No firewall rule or admin needed: the forwarder **connects out** to the WSL mux
(WSL2 forwards the mux's listening port to Windows' loopback), so Windows Firewall
never prompts.

## Run (each session)

Order matters: start the WSL listener **before** the Windows forwarder dials it.

**1. Linux laptop:**
```bash
python3 anthropic-reverse-proxy.py &
python3 bt-bridge-linux.py &
```

**2. WSL** (starts the mux + listener):
```bash
python3 bt-bridge-wsl.py             # waits for the Windows forwarder on :20000
```

**3. Windows** (PowerShell — connects out to WSL, opens the COM port → Bluetooth):
```powershell
.\bt-forward.ps1 -COM COM5           # your COM number
```
Now `bt-bridge-linux.py`'s log prints `[bt] connection ...` and WSL prints
`Windows forwarder connected`.

**4. WSL** (test, then run Claude):
```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/   # expect 404
export ANTHROPIC_BASE_URL=http://127.0.0.1:8080
claude
```

## Monitoring (Linux laptop)

```bash
./monitor.sh          # one-screen live status, refresh every 2s (Ctrl-C to quit)
./monitor.sh 5        # refresh every 5s
```
Shows: reverse proxy up/down, bridge up/down, **BT link** state, API call counts
(2xx vs 4xx/5xx), and the most recent requests. Reads logs from
`~/.local/state/claude-bt-bridge/` (override with `BT_LOG_DIR`).

Note: the link state is derived from the bridge log's RFCOMM open/close events —
`bluetoothctl` reports `Connected: no` for an idle SPP link even while it works,
so don't trust that flag.

## Troubleshooting

- **`bt-bridge-wsl.py` can't connect** → it uses the WSL default gateway as the
  Windows host IP (NAT mode). Pass it explicitly: `python3 bt-bridge-wsl.py <host-ip> 20000`.
  Get the host IP with `ip route show default`. Also check the firewall rule above.
- **No `ClaudeBridge` service on Windows / can't add COM port** → the OS or
  Bluetooth stack is restricting SPP; nothing to do client-side.
- **`UUID already registered`** on the Linux bridge → an old `bt-spp-echo.py` /
  bridge is still holding SPP; kill it first.
- **Slow** → Bluetooth SPP is ~1 Mbps raw; per-frame zlib helps a lot on JSON/text.
  Watch the live ratio with `./monitor.sh` (the `compression:` line).
- **Claude hangs / no new requests** → the Bluetooth link dropped mid-request. All
  three pieces now auto-reconnect (the forwarder redials, the WSL mux re-accepts,
  the Linux bridge waits for a new SPP connection), so it self-heals within a few
  seconds — but the *in-flight* request is lost, so cancel it in Claude and resend.
  If it doesn't recover, restart the Windows forwarder (it reopens the COM port).
