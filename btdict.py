#!/usr/bin/env python3
# Static preset dictionary for per-frame zlib on Claude Code traffic. Shared,
# fixed, known to both ends -> each DATA frame is compressed against it with a
# FRESH compressobj (stateless, so a Bluetooth drop just reconnects, no desync).
# It primes every frame with Claude's boilerplate (HTTP headers, SSE event
# scaffolding, messages/tools JSON shapes). Most-common sequences go LAST (zlib
# favors recent dictionary bytes with shorter back-references). Keep <= 32 KB.
#
# If this ever changes, bump DICT_VERSION and redeploy BOTH ends together; a
# mismatch is caught safely (decompress error / unknown frame type -> link down).
DICT_VERSION = 1

DICT = b"".join([
    b'accept-encoding: gzip\r\nconnection: keep-alive\r\n',
    b'HTTP/1.1 200 OK\r\ncontent-type: text/event-stream; charset=utf-8\r\n',
    b'cache-control: no-cache\r\ntransfer-encoding: chunked\r\nrequest-id: req_\r\n',
    b'POST /v1/messages?beta=true HTTP/1.1\r\nhost: api.anthropic.com\r\n',
    b'content-type: application/json\r\nanthropic-version: 2023-06-01\r\n',
    b'anthropic-beta: prompt-caching-2024-07-31\r\nx-api-key: \r\nauthorization: Bearer \r\n',
    b'"cache_control":{"type":"ephemeral"}',
    b'"stop_reason":"end_turn","stop_sequence":null',
    b'"usage":{"input_tokens":,"cache_creation_input_tokens":,"cache_read_input_tokens":,"output_tokens":',
    b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_","type":"message","role":"assistant","model":"claude-',
    b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"',
    b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
    b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"',
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
    b'event: ping\ndata: {"type": "ping"}\n\n',
    b'"input_schema":{"type":"object","properties":{',
    b'"type":"string","description":"',
    b'}},"required":[',
    b'"model":"claude-opus-4-6","max_tokens":,"stream":true,',
    b'"system":[{"type":"text","text":"',
    b'"tools":[{"name":"","description":"',
    b'{"role":"user","content":[{"type":"text","text":"',
    b'{"role":"assistant","content":[{"type":"tool_use","id":"toolu_","name":"","input":{',
    b'{"type":"tool_result","tool_use_id":"toolu_","content":"',
    b'"is_error":true',
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"',
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"',
])
