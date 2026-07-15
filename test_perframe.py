#!/usr/bin/env python3
# Per-frame (stateless) plain-zlib compression test. Each DATA frame is compressed
# on its own -- no shared state, so a mid-stream drop + reconnect can't desync.
# Many concurrent framed streams of random + text data over a socketpair, asserting
# byte-exact, at the real CHUNK size the mux uses.
import os, socket, struct, threading, random, sys, zlib
HDR = struct.Struct(">HBH")
DATA, CLOSE, DATA_Z = 1, 2, 4
def enc(typ, p):
    if typ == DATA and len(p) > 64:
        z = zlib.compress(p, 6)
        if len(z) < len(p): return DATA_Z, z
    return typ, p
def dec(typ, p):
    return (DATA, zlib.decompress(p)) if typ == DATA_Z else (typ, p)

def run_once(nstreams, seed):
    rng = random.Random(seed)
    a, b = socket.socketpair()
    originals, received, closed = {}, {}, set()
    for sid in range(1, nstreams + 1):
        size = rng.randint(50_000, 2_000_000)
        originals[sid] = os.urandom(size) if rng.random() < 0.5 else (b'def f():\n    return 42  # comment\n' * (size // 30))[:size]
        received[sid] = bytearray()
    def recvn(sock, n):
        buf = b""
        while len(buf) < n:
            c = sock.recv(n - len(buf))
            if not c: return None
            buf += c
        return buf
    def receiver():
        while len(closed) < nstreams:
            hdr = recvn(b, HDR.size)
            if hdr is None: break
            sid, typ, ln = HDR.unpack(hdr)
            payload = recvn(b, ln) if ln else b""
            if payload is None: break
            typ, payload = dec(typ, payload)
            if typ == DATA: received[sid] += payload
            elif typ == CLOSE: closed.add(sid)
    rt = threading.Thread(target=receiver); rt.start()
    wlock = threading.Lock()
    def send(sid, typ, payload=b""):
        t, p = enc(typ, payload)
        with wlock: a.sendall(HDR.pack(sid, t, len(p)) + p)
    def sender(sid):
        data = originals[sid]; i = 0
        while i < len(data):
            chunk = data[i:i + rng.randint(1, 65535)]; i += len(chunk)   # up to the frame cap
            send(sid, DATA, chunk)
        send(sid, CLOSE)
    ts = [threading.Thread(target=sender, args=(s,)) for s in originals]
    for t in ts: t.start()
    for t in ts: t.join()
    rt.join(timeout=60); a.close(); b.close()
    return all(bytes(received[s]) == originals[s] for s in originals), sum(len(originals[s]) for s in originals)

if __name__ == "__main__":
    allok = True; grand = 0
    for seed in range(8):
        ok, tot = run_once(6, seed); grand += tot
        print(f"seed {seed}: {'OK' if ok else 'FAIL'}  ({tot:,} bytes)"); allok &= ok
    print(f"\n{'ALL PASS' if allok else 'FAILURES'}  ({grand:,} bytes byte-exact, frames up to 65535)")
    sys.exit(0 if allok else 1)
