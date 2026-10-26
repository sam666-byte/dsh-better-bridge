#!/usr/bin/env python3
"""deepseek4free wrapper: OpenAI-compatible endpoint so dsh can list and use
DeepSeek web-chat models (V3 + R1) in its model picker, next to the CHATBRAIN
relay model (deepseek-chat).

Transport: xtekky/deepseek4free (dsk.api) against the chat.deepseek.com private
API with the SAME userToken the CHATBRAIN relay uses (read at runtime from
/data/dsh/chatbrain/.websession.json; never logged, never written anywhere).

Patches vs upstream repo (Jan 2025, unmaintained), proven by live test 22:11:
  1) create_chat_session: API now returns data.biz_data.chat_session.id
     (repo expects data.biz_data.id) -> monkey-patched below.
  2) SSE format is now JSON-patch ({"p":...,"o":"APPEND","v":...}), not
     OpenAI-style choices/delta -> custom streaming parser below. NOTE: the
     first chunk nests fragments at v.response.fragments (NOT v.fragments);
     missing that path drops the first characters of every answer ("Jakarta"
     -> "arta"). REPLACE/SET ops set a fragment buffer, APPEND appends.
  3) pkg_resources missing on Python 3.14 -> venv has setuptools==80.9.0.

DBS handling: prompt building (PREAMBLE/tool catalog/message rendering) and
reply parsing (parse_reply: DSML unwrap, <tool_call> drop-on-parse-fail,
native-token strip, bare-JSON) are imported from relay2.py
(/data/dsh/chatbrain/relay2.py) — the same code as the DBS-patched relay
(22:03). If that import ever fails, this wrapper FAILS TO START rather than
serving answers without DBS protection.

Design: stateless per request. Fresh DeepSeek session + full rendered prompt
(PREAMBLE + full history, same text relay2 sends on a chain start) each call.
No shared chains, no parent tracking. A global LOCK serialises DeepSeek calls
(one userToken shared with the relay; dsh only calls the model the user
picked, so the two never run concurrently in normal use).

Endpoints (same facade as relay2.py):
  GET  /v1/models            -> deepseek-v3-d4f, deepseek-r1-d4f
  POST /v1/chat/completions  -> OpenAI chat completion (stream or not)
"""
import sys
sys.path.insert(0, "/data/dsh-test/deepseek4free")
# deepseek_pow (relay2 dependency) lives in the system user site-packages
sys.path.insert(0, "/home/liveuser/.local/lib/python3.14/site-packages")
sys.path.insert(0, "/data/dsh/chatbrain")

import json, time, hashlib, threading, re, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dsk.api import DeepSeekAPI
from curl_cffi import requests
import relay2  # DBS handling: parse_reply, PREAMBLE, tool_catalog, render_msg

PORT = 20233
WS = "/data/dsh/chatbrain/.websession.json"
LOGF = "/data/dsh/d4f-wrapper/d4f-wrapper.log"
REPLIES_LOG = "/data/dsh/d4f-wrapper/d4f-replies.log"

LOG = open(LOGF, "a", buffering=1)

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=LOG)

_token = json.load(open(WS))["token"]
log("d4f wrapper starting, token len=%d" % len(_token))
api = DeepSeekAPI(_token)

# --- Patch 1: session id path (API now: data.biz_data.chat_session.id) ---
def _create_chat_session_fixed(self):
    resp = self._make_request("POST", "/chat_session/create", {"character_id": None})
    bd = resp["data"]["biz_data"]
    if "chat_session" in bd:
        return bd["chat_session"]["id"]
    return bd["id"]
DeepSeekAPI.create_chat_session = _create_chat_session_fixed


def ask_raw(prompt, thinking):
    """One prompt -> final RESPONSE text (thinking fragments excluded)."""
    sid = api.create_chat_session()
    pow_resp = api.pow_solver.solve_challenge(api._get_pow_challenge())
    headers = api._get_headers(pow_response=pow_resp)
    r = requests.post(
        f"{api.BASE_URL}/chat/completion",
        headers=headers,
        json={"chat_session_id": sid, "parent_message_id": None, "prompt": prompt,
              "ref_file_ids": [], "thinking_enabled": thinking, "search_enabled": False},
        cookies={}, impersonate="chrome120", stream=True, timeout=180)
    if r.status_code != 200:
        first = next(r.iter_lines(), b"")
        raise RuntimeError("completion HTTP %s: %r" % (r.status_code, first[:200]))
    frag_types = {}
    bufs = {}
    cur = None
    for line in r.iter_lines():
        if not line.startswith(b"data: "):
            continue
        try:
            d = json.loads(line[6:])
        except Exception:
            continue
        v = d.get("v")
        # First chunk: fragments live at v.response.fragments (NOT v.fragments).
        if isinstance(v, dict):
            frags = v.get("fragments") or (v.get("response") or {}).get("fragments")
            if frags:
                for i, fr in enumerate(frags):
                    frag_types[i] = fr.get("type", "?")
                    if fr.get("content"):
                        bufs[i] = bufs.get(i, "") + fr["content"]
                continue
        p, o = d.get("p"), d.get("o")
        if p == "response/status" and v in ("FINISHED", "ABORTED", "FAILED"):
            break
        # New fragments arrive late as a LIST append (R1: THINK streams first,
        # the RESPONSE fragment is appended afterwards):
        # {"p": "response/fragments", "o": "APPEND", "v": [{...fragment...}]}
        if p == "response/fragments" and o == "APPEND" and isinstance(v, list):
            for fr in v:
                if isinstance(fr, dict):
                    i = len(frag_types)
                    frag_types[i] = fr.get("type", "?")
                    if fr.get("content"):
                        bufs[i] = bufs.get(i, "") + fr["content"]
            continue
        if isinstance(p, str) and p.endswith("/content") and isinstance(v, str):
            try:
                idx = int(p.split("/")[2])
            except Exception:
                idx = -1
            if idx == -1:
                idx = max(frag_types.keys()) if frag_types else 0
            cur = idx
            if o in ("REPLACE", "SET"):
                bufs[idx] = v
            elif o == "APPEND":
                bufs[idx] = bufs.get(idx, "") + v
        elif o is None and p is None and isinstance(v, str) and cur is not None:
            bufs[cur] = bufs.get(cur, "") + v
    text = "".join(bufs[i] for i in sorted(bufs)
                   if "THINK" not in str(frag_types.get(i, "")))
    if not text.strip():
        text = "".join(bufs[i] for i in sorted(bufs))
    return text


def build_prompt_d4f(messages, tools):
    """Same text relay2 sends on a chain start: PREAMBLE + full history."""
    body = relay2.PREAMBLE.replace("{catalog}", relay2.tool_catalog(tools))
    body += "\n".join(relay2.render_msg(m) for m in messages)
    if tools:
        body += ("\n\n[Reminder: you have tools. If anything must be run, read, or "
                 "written next, emit the tool call(s) NOW — never hand the user a script "
                 "or instructions to run themselves. Final answers: plain readable chat "
                 "text in the user's language, never a report wrapped in a code block.]")
    return body


LOCK = threading.Lock()

# --- DBS hardening beyond relay2.parse_reply (proven gaps, 22:20-22:23) ---
# Gap 1: the model sometimes omits the DSML "name" parameter, so parse_dsml's
# unwrap (which requires args["name"] == invoked name) does not fire and the
# call reaches dsh as {"arguments": {"command": ...}} -> "invalid arguments".
# Gap 2: the model sometimes writes a BROKEN closer, e.g. a timeoutMs value
# followed by "900000｜｜DSML｜｜>" instead of "</｜｜DSML｜｜ parameter>", so
# the invoke block never matches and raw DSML leaks to the user. Fix (1):
# tolerant recovery — find the opener + first JSON object and rebuild the call.
# Fix (2): guaranteed strip — any DSML debris left in visible text is removed,
# even malformed fragments without a leading "<".
def unwrap_tool_calls(calls):
    """One-level unwrap of {"arguments": {...}} nesting in tool-call args."""
    out = []
    for tc in calls or []:
        try:
            fn = tc.get("function", {})
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:
            out.append(tc)
            continue
        if isinstance(args, dict) and isinstance(args.get("arguments"), dict) \
                and set(args.keys()) <= {"arguments", "name"}:
            args = args["arguments"]
            tc = dict(tc)
            tc["function"] = dict(fn, arguments=json.dumps(args, ensure_ascii=False))
        out.append(tc)
    return out

def recover_dsml_call(raw):
    """Lenient DSML recovery on the RAW text: opener + first JSON object ->
    tool call. Only used when parse_reply found no calls (i.e. the block
    failed to parse, e.g. a broken closer like '900000｜｜DSML｜｜>').
    Returns (calls, cleaned_raw) with the recovered span cut out; any broken
    closer left behind is removed later by strip_dsml_debris."""
    m = re.search(r"<｜｜DSML｜｜\s*invoke\s+name=\"([^\"]+)\"[^>]*>", raw)
    if not m:
        return [], raw
    got = relay2.extract_tool_obj(raw[m.end():])
    if not got:
        return [], raw
    js, s0, s1 = got
    try:
        obj = json.loads(js)
    except Exception:
        return [], raw
    if not isinstance(obj, dict):
        return [], raw
    if isinstance(obj.get("arguments"), dict):
        args = obj["arguments"]
    else:
        args = {k: v for k, v in obj.items() if k != "name"}
    name = obj.get("name") if isinstance(obj.get("name"), str) else m.group(1)
    if not args:
        return [], raw
    call = {"id": "call_" + hashlib.md5(js.encode()).hexdigest()[:10],
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
    log("recovered DSML call (tolerant):", name)
    cleaned = raw[:m.start()] + raw[m.end() + s1:]
    return [call], cleaned

DSML_DEBRIS_RE = re.compile(r"</?[^\s<>]*｜｜DSML｜｜[^>]*>")

def strip_dsml_debris(text):
    """Remove any leftover DSML markup from visible text, including malformed
    fragments without a leading '<' (e.g. '900000｜｜DSML｜｜>'). The literal
    '｜｜DSML｜｜' never occurs in legitimate content, so anything attached to
    it on the same token is debris."""
    if not text or "｜｜DSML｜｜" not in text:
        return text
    return DSML_DEBRIS_RE.sub("", text).strip()


def process_reply(raw):
    """Full reply pipeline: relay2 parse -> tolerant DSML recovery (when the
    parse produced no calls, or calls whose arguments contain DSML debris —
    the signature of a broken closer making the parameter regex span across
    tags) -> argument unwrap -> guaranteed DSML debris strip -> trim."""
    text, calls = relay2.parse_reply(raw)
    if "｜｜DSML｜｜" in raw:
        mangled = (not calls) or any(
            "｜｜DSML｜｜" in (c.get("function", {}).get("arguments") or "")
            for c in calls)
        if mangled:
            rec_calls, raw2 = recover_dsml_call(raw)
            if rec_calls:
                text, calls = relay2.parse_reply(raw2)
                calls = calls + rec_calls
    calls = unwrap_tool_calls(calls)
    text = strip_dsml_debris(text)
    if calls:
        text = relay2.trim_code_blocks(text)
    return text, calls

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            self._json(200, {"object": "list", "data": [
                {"id": "deepseek-v3-d4f", "object": "model", "owned_by": "deepseek4free"},
                {"id": "deepseek-r1-d4f", "object": "model", "owned_by": "deepseek4free"}]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/v1/chat/completions"):
            self._json(404, {"error": "not found"}); return
        ln = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(ln) or b"{}")
        messages = req.get("messages") or []
        tools = req.get("tools") or []
        stream = bool(req.get("stream"))
        model = req.get("model") or "deepseek-v3-d4f"
        thinking = "r1" in str(model)
        log("completion req: model=%s msgs=%d tools=%d stream=%s" % (model, len(messages), len(tools), stream))
        prompt = build_prompt_d4f(messages, tools)
        log("prompt chars: %d" % len(prompt))
        with LOCK:
            try:
                raw = ask_raw(prompt, thinking)
            except Exception as e:
                log("ask failed:", repr(e)[:300])
                self._json(502, {"error": {"message": str(e)[:300], "code": "d4f_failed"}}); return
        log("reply chars:", len(raw))
        try:
            with open(REPLIES_LOG, "a") as rf:
                rf.write("\n=== %s model=%s ===\n%s\n" % (time.strftime("%H:%M:%S"), model, raw[:4000]))
        except Exception:
            pass
        text, calls = process_reply(raw)
        finish = "tool_calls" if calls else "stop"
        msg = {"role": "assistant", "content": text if text else None}
        if calls:
            msg["tool_calls"] = calls
        comp = {
            "id": "chatcmpl-d4f", "object": "chat.completion", "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        if not stream:
            self._json(200, comp); return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        delta = {"role": "assistant"}
        if text: delta["content"] = text
        if calls: delta["tool_calls"] = calls
        chunk = {"id": comp["id"], "object": "chat.completion.chunk", "created": comp["created"], "model": model,
                 "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
        self.wfile.write(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode())
        fin = {"id": comp["id"], "object": "chat.completion.chunk", "created": comp["created"], "model": model,
               "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}
        self.wfile.write(("data: " + json.dumps(fin, ensure_ascii=False) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")

if __name__ == "__main__":
    log("d4f wrapper up on", PORT)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
