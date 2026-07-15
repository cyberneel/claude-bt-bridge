#!/usr/bin/env python3
# Per-frame (stateless) compression test, now with the static Claude dictionary
# (DATA_ZD). Each DATA frame is dict-compressed on its own -- no shared state, so a
# mid-stream drop + reconnect can't desync. Test: many concurrent framed streams of
# random + Claude-like data over a socketpair, asserting byte-exact, plus a ratio
# check on realistic Claude traffic.
import os, socket, struct, threading, random, sys, zlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from btdict import DICT

HDR = struct.Struct(">HBH")
DATA, CLOSE, DATA_ZD = 1, 2, 5

def zdc(d):
    co = zlib.compressobj(6, zlib.DEFLATED, zlib.MAX_WBITS, zlib.DEF_MEM_LEVEL, zlib.Z_DEFAULT_STRATEGY, DICT)
    return co.compress(d) + co.flush()
def zdd(d):
    do = zlib.decompressobj(zlib.MAX_WBITS, DICT)
    return do.decompress(d) + do.flush()
def enc(typ, p):
    if typ == DATA and len(p) > 32:
        z = zdc(p)
        if len(z) < len(p): return DATA_ZD, z
    return typ, p
def dec(typ, p):
    return (DATA, zdd(p)) if typ == DATA_ZD else (typ, p)

def run_once(nstreams, seed):
    rng = random.Random(seed)
    a, b = socket.socketpair()
    originals, received, closed = {}, {}, set()
    unit = (b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
            b'"delta":{"type":"text_delta","text":"the quick brown fox "}}\n\n')
    for sid in range(1, nstreams + 1):
        size = rng.randint(50_000, 1_500_000)
        if rng.random() < 0.5:
            originals[sid] = os.urandom(size)                       # incompressible
        else:
            originals[sid] = (unit * (size // len(unit) + 1))[:size]  # Claude-like SSE
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
            chunk = data[i:i + rng.randint(1, 32768)]; i += len(chunk)
            send(sid, DATA, chunk)
        send(sid, CLOSE)
    ts = [threading.Thread(target=sender, args=(s,)) for s in originals]
    for t in ts: t.start()
    for t in ts: t.join()
    rt.join(timeout=60); a.close(); b.close()
    ok = all(bytes(received[s]) == originals[s] for s in originals)
    return ok, sum(len(originals[s]) for s in originals)

if __name__ == "__main__":
    allok = True; grand = 0
    for seed in range(8):
        ok, tot = run_once(6, seed); grand += tot
        print(f"seed {seed}: {'OK' if ok else 'FAIL'}  ({tot:,} bytes)"); allok &= ok
    # ratio on realistic Claude SSE, framed @512 (small streaming frames)
    sse = (b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
           b'"delta":{"type":"text_delta","text":"explaining the code now "}}\n\n') * 300
    plain = sum(len(zlib.compress(sse[i:i+512], 6)) for i in range(0, len(sse), 512))
    dic   = sum(len(enc(DATA, sse[i:i+512])[1]) for i in range(0, len(sse), 512))
    print(f"\nClaude SSE @512:  raw={len(sse):,}  plain={plain:,} ({len(sse)/plain:.1f}x)  dict={dic:,} ({len(sse)/dic:.1f}x)")
    print(f"{'ALL PASS' if allok else 'FAILURES'}  ({grand:,} bytes byte-exact)")
    sys.exit(0 if allok else 1)
