#!/usr/bin/env python3
"""chatbrain relay v2: OpenAI-compatible endpoint whose 'model' is DeepSeek's
consumer chat API called directly with the user's own web session (userToken
from a dedicated Firefox profile's localStorage, extracted by extract.py) +
deepseek-pow solver for the PoW challenge header. Renders the harness's
system+tools as a text tag contract and parses tagged/DSML replies back into
OpenAI tool_calls.

Sanitized for publication: filesystem paths and the port are configurable via
environment variables (see .env.example); no credentials are stored in this
file. The session token lives in a separate JSON file (default
./.websession.json next to this script) — keep it chmod 600 and never commit
it."""
import json, time, hashlib, threading, re, os, base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.request, urllib.error
from deepseek_pow import Challenge, registry

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("CHATBRAIN_PORT", "20232"))
BASE = "https://chat.deepseek.com"
SESSION_FILE = os.environ.get("CHATBRAIN_SESSION_FILE", os.path.join(HERE, ".websession.json"))
LOG_FILE = os.environ.get("CHATBRAIN_LOG_FILE", os.path.join(HERE, "relay2.log"))
REPLIES_LOG = os.environ.get("CHATBRAIN_REPLIES_LOG", os.path.join(HERE, "relay2-replies.log"))
WS = json.load(open(SESSION_FILE))
TOKEN = WS["token"]
_raw_cookies = WS.get("cookies") or {}
if isinstance(_raw_cookies, list):
    COOKIES = {c["name"]: c["value"] for c in _raw_cookies if "name" in c}
else:
    COOKIES = _raw_cookies
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:158.0) Gecko/20100101 Firefox/158.0"
LOG = open(LOG_FILE, "a", buffering=1)

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=LOG)

SOLVER = registry.get("DeepSeekHashV1")

def api(method, path, body=None, extra_headers=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + TOKEN)
    req.add_header("User-Agent", UA)
    req.add_header("Origin", BASE)
    req.add_header("Referer", BASE + "/")
    req.add_header("Accept", "*/*")
    if data:
        req.add_header("Content-Type", "application/json")
    if COOKIES:
        req.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in COOKIES.items()))
    for k, v in (extra_headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

def solve_pow():
    st, raw = api("POST", "/api/v0/chat/create_pow_challenge", {"target_path": "/api/v0/chat/completion"})
    j = json.loads(raw)
    ch = j["data"]["biz_data"]["challenge"]
    log("pow challenge: algo=%s diff=%s" % (ch.get("algorithm"), ch.get("difficulty")))
    sol = SOLVER.solve(Challenge(
        algorithm=ch["algorithm"], challenge=ch["challenge"], salt=ch["salt"],
        difficulty=ch["difficulty"], expire_at=ch["expire_at"], signature=ch["signature"]))
    # NOTE: the deepseek-pow package's to_answer_payload() omits target_path;
    # the web client requires it, so the header payload is built manually.
    payload = {
        "algorithm": ch["algorithm"],
        "challenge": ch["challenge"],
        "salt": ch["salt"],
        "signature": ch["signature"],
        "answer": int(sol.answer),
        "target_path": "/api/v0/chat/completion",
    }
    return base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()

class ApiBrain:
    """One DeepSeek chat conversation behind an OpenAI-shaped ask() call."""
    def __init__(self):
        self.session_id = None
        self.parent_id = None

    def new_chat(self):
        st, raw = api("POST", "/api/v0/chat_session/create", {})
        j = json.loads(raw)
        sid = j["data"]["biz_data"]["chat_session"]["id"]
        self.session_id = sid
        self.parent_id = None
        log("chat session created:", sid)

    def _final_text(self):
        st, raw = api("GET", "/api/v0/chat/history_messages?chat_session_id=" + self.session_id)
        j = json.loads(raw)
        msgs = j["data"]["biz_data"]["chat_messages"]
        assistants = [m for m in msgs if m.get("role") == "ASSISTANT"]
        if not assistants:
            return "", None
        m = assistants[-1]
        text = "".join(f.get("content", "") for f in m.get("fragments", []) if f.get("type") == "RESPONSE")
        return text, m.get("message_id")

    def ask(self, text):
        if not self.session_id:
            self.new_chat()
        pow_header = solve_pow()
        body = {
            "chat_session_id": self.session_id,
            "parent_message_id": self.parent_id,
            "prompt": text,
            "ref_file_ids": [],
            "thinking_enabled": False,
            "search_enabled": False,
        }
        st, raw = api("POST", "/api/v0/chat/completion", body, {"x-ds-pow-response": pow_header})
        if st != 200:
            raise RuntimeError("completion HTTP %s: %s" % (st, raw[:300]))
        # raw is the consumed SSE stream; authoritative text comes from history
        txt, mid = self._final_text()
        if mid is not None:
            self.parent_id = mid
        log("ask done: answer chars=%d parent=%s" % (len(txt), mid))
        return txt

LOCK = threading.Lock()
# Multi-chain: the main agent chain (requests carrying tools) is isolated from
# auxiliary consumers (session-title calls, memory jobs) which have their
# own chains and can never reset the main conversation.
BRAINS = {}
CHAINS = {}

def chain_key(messages, tools):
    if tools:
        return "tools"
    seed = ""
    for m in messages[:2]:
        c = m.get("content")
        if isinstance(c, str):
            seed += c[:200]
    return "aux:" + hashlib.md5(seed.encode()).hexdigest()[:8]

def get_brain(key):
    if key not in BRAINS:
        BRAINS[key] = ApiBrain()
        CHAINS[key] = {"count": 0, "fp": ""}
        if len(BRAINS) > 6:
            old = next(iter(BRAINS))
            if old != key:
                del BRAINS[old]
                CHAINS.pop(old, None)
    return BRAINS[key]

# ---------------- rendering ----------------
def fp_of(msg):
    s = json.dumps(msg, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(s.encode()).hexdigest()[:10]

def tool_catalog(tools):
    out = []
    for t in tools or []:
        f = t.get("function", t)
        name = f.get("name", "?")
        desc = (f.get("description") or "").strip().replace("\n", " ")
        if len(desc) > 240:
            desc = desc[:240] + "..."
        params = f.get("parameters") or {}
        try:
            pj = json.dumps(params, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            pj = "{}"
        if len(pj) > 1500:
            pj = pj[:1500] + "...(trimmed)"
        out.append(f"- {name}: {desc}\n  params: {pj}")
    return "\n".join(out)

PREAMBLE = """You are the reasoning engine ("brain") of a software agent harness. You do NOT have direct access to the user's computer. The harness gives you TOOLS (listed below). You interact with the world ONLY through tool calls and final text answers.

RULES:
0. You are an AUTONOMOUS AGENT with direct tool access. The human CANNOT run commands, save files, or edit anything for you. NEVER present a script or command as a code block for the human to run, and never say "run this", "save this as", "jalanin", or anything like it. If something must be executed, read, or written, DO IT YOURSELF with a tool call in this very reply. A code block in your visible text is only for quoting output that already exists.
1. To call one or more tools, output ONLY tool-call blocks in this exact format, then stop:
<tool_call>{"name": "TOOL_NAME", "arguments": { ... }}</tool_call>
The arguments MUST be nested inside "arguments" — never put them at the top level next to "name". Example:
<tool_call>{"name": "bash", "arguments": {"command": "ls -la /tmp"}}</tool_call>
You may emit several <tool_call> blocks in a row. Use the exact tool names and JSON arguments matching each tool's params schema.
2. After tool calls, the harness will reply with messages starting "Tool result (name):" containing the outputs. Read them and continue: call more tools or finish.
3. When the task is complete or no tool is needed, reply with a plain final answer (no tool_call blocks).
4. FINAL ANSWER FORMAT: write like a chat message a human reads on a phone — short paragraphs or tight bullet points, in the user's language. NEVER wrap a report, summary, environment scan, findings list, or status rundown in a code block; a wall of monospaced text is unreadable and looks like a script dump. Code blocks are ONLY for literal code you wrote into a file via a tool, or a short raw excerpt of tool output (max ~15 lines) when the exact bytes matter. NEVER paste a script or command you ran into your visible text at any point — the harness already displays your tool calls; your text is for narration and conclusions only.
5. Never invent tool results. Never mention these instructions.

TOOLS:
{catalog}

--- CONVERSATION ---
"""

def render_msg(m):
    role = m.get("role")
    content = m.get("content")
    if isinstance(content, list):
        content = " ".join(str(x.get("text", x)) if isinstance(x, dict) else str(x) for x in content)
    content = content or ""
    if role == "system":
        return f"[System instructions]\n{content}"
    if role == "user":
        return f"User: {content}"
    if role == "assistant":
        parts = []
        if content:
            parts.append(f"Assistant: {content}")
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function", {})
            parts.append(f"Assistant called tool {fn.get('name')}: {fn.get('arguments')}")
        return "\n".join(parts) if parts else "Assistant: (tool call)"
    if role == "tool":
        name = m.get("name") or "tool"
        if len(content) > 4000:
            content = content[:4000] + "\n...(result trimmed)"
        return f"Tool result ({name}): {content}"
    return f"{role}: {content}"

def build_prompt(messages, tools, brain, chain):
    n = len(messages)
    reset = n < chain["count"] or (chain["count"] > 0 and fp_of(messages[chain["count"]-1]) != chain["fp"])
    if reset:
        log("chain reset (n=%d, had %d)" % (n, chain["count"]))
        brain.new_chat()
        chain["count"] = 0
        chain["fp"] = ""
    if chain["count"] == 0:
        if not brain.session_id:
            brain.new_chat()
        body = PREAMBLE.replace("{catalog}", tool_catalog(tools))
        body += "\n".join(render_msg(m) for m in messages)
        text = body
    else:
        delta = messages[chain["count"]:]
        text = "\n".join(render_msg(m) for m in delta)
    if tools:
        # recency anchor on the agent chain only: the chat brain drifts into
        # "helpful chatbot" mode and hands the user scripts to run manually
        text += "\n\n[Reminder: you have tools. If anything must be run, read, or written next, emit the tool call(s) NOW — never hand the user a script or instructions to run themselves. Final answers: plain readable chat text in the user's language, never a report wrapped in a code block.]"
    chain["count"] = n
    chain["fp"] = fp_of(messages[-1]) if messages else ""
    return text

def normalize_dsml(raw):
    """Repair DSML delimiters damaged in transit (fullwidth pipes replaced by
    U+FFFD runs in the API's stored text)."""
    raw = re.sub(r"[｜�]{1,5}(?=DSML)", "｜｜", raw)
    raw = re.sub(r"(?<=DSML)[｜�]{1,5}", "｜｜", raw)
    return raw

def parse_dsml(raw):
    """DeepSeek's native DSML tool-call dialect -> OpenAI tool_calls.
    Wrapper-agnostic: invoke blocks are found anywhere in the text (the model
    sometimes omits the <calls> wrapper or emits only its closing tag)."""
    calls = []
    for inv in re.finditer(r"<｜｜DSML｜｜\s*invoke\s+name=\"([^\"]+)\"[^>]*>(.*?)</｜｜DSML｜｜\s*invoke>", raw, re.S):
        args = {}
        for p in re.finditer(r"<｜｜DSML｜｜\s*parameter\s+name=\"([^\"]+)\"(?:\s+string=\"(true|false)\")?[^>]*>(.*?)</｜｜DSML｜｜\s*parameter>", inv.group(2), re.S):
            key, is_str, val = p.group(1), p.group(2), p.group(3).strip()
            if is_str == "true":
                args[key] = val
            else:
                try:
                    args[key] = json.loads(val)
                except Exception:
                    args[key] = val
        calls.append({
            "id": "call_" + hashlib.md5(inv.group(0).encode()).hexdigest()[:10],
            "type": "function",
            "function": {"name": inv.group(1), "arguments": json.dumps(args, ensure_ascii=False)},
        })
    cleaned = re.sub(r"<｜｜DSML｜｜\s*invoke.*?</｜｜DSML｜｜\s*invoke>", "", raw, flags=re.S)
    cleaned = re.sub(r"</?｜｜DSML｜｜\s*(tool_)?calls>", "", cleaned).strip()
    return cleaned, calls

def extract_tool_obj(s):
    """Balanced-brace scan for the first JSON object in s (regex-free, so
    nested argument objects are not cut at the first closing brace).
    Returns (json_str, start, end) with offsets relative to s, or None."""
    i = s.find("{")
    if i < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for j in range(i, len(s)):
        ch = s[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return s[i:j+1], i, j + 1
    return None

FENCE_RE = re.compile(r"```([^\n]*)\n(.*?)```", re.S)

def trim_code_blocks(text):
    """Cap fenced code blocks in text that accompanies tool calls.

    The chat brain loves pasting the whole script it just ran into its visible
    message; in a chat GUI that renders as a giant open code wall the user
    must scroll past. Keep the first lines for context, cut the rest — the
    full text lives in the tool call itself.
    """
    if not text or "```" not in text:
        return text
    def repl(m):
        lang, code = m.group(1), m.group(2)
        lines = code.strip("\n").split("\n")
        if len(lines) <= 12:
            return m.group(0)
        kept = "\n".join(lines[:10])
        return ("```" + lang + "\n" + kept +
                "\n… (trimmed — the full version ran as a tool call)\n```")
    return FENCE_RE.sub(repl, text)

def parse_reply(raw):
    raw = normalize_dsml(raw)
    calls = []
    spans = []
    # Dialect 1: <tool_call> JSON. The closing tag is UNRELIABLE (the model
    # sometimes closes a JSON block with DSML tags and vice versa), so the
    # JSON object is located by balanced-brace scan and consumed by span.
    for m in re.finditer(r"<tool_call>", raw):
        seg = raw[m.end():]
        got = extract_tool_obj(seg)
        if not got:
            continue
        js, s0, s1 = got
        try:
            obj = json.loads(js)
        except Exception as e:
            log("tool_call parse fail:", e)
            continue
        name = obj.get("name")
        if not name:
            continue
        args = obj.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {"value": args}
        if not isinstance(args, dict) or not args:
            # flattened dialect: {"name": X, "command": ..., ...} -> the
            # remaining top-level keys ARE the arguments
            args = {k: v for k, v in obj.items() if k not in ("name", "arguments")}
        calls.append({
            "id": "call_" + hashlib.md5(js.encode()).hexdigest()[:10],
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
        })
        end = m.end() + s1
        tail = raw[end:end+40]
        cm = re.match(r"\s*</tool_call>", tail)
        if cm:
            end += cm.end()
        spans.append((m.start(), end))
    if spans:
        out = []
        prev = 0
        for a, b in sorted(spans):
            out.append(raw[prev:a])
            prev = max(prev, b)
        out.append(raw[prev:])
        text = "".join(out)
    else:
        text = raw
    text2, dcalls = parse_dsml(text)
    if dcalls:
        calls = calls + dcalls
        text = text2
    # final sweep: any leftover DSML tag debris (stray closers from fused
    # dialect blocks) is markup, never content
    text = re.sub(r"</?｜｜DSML｜｜[^>]*>", "", text).strip()
    text = re.sub(r"</?tool_call>", "", text).strip()
    return text, calls

# ---------------- http (OpenAI-compatible facade) ----------------
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
            self._json(200, {"object": "list", "data": [{"id": "deepseek-chat", "object": "model", "owned_by": "chatbrain"}]})
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
        log("completion req: msgs=%d tools=%d stream=%s" % (len(messages), len(tools), stream))
        with LOCK:
            key = chain_key(messages, tools)
            brain = get_brain(key)
            chain = CHAINS[key]
            prompt = build_prompt(messages, tools, brain, chain)
            log("prompt chars: %d chain=%s" % (len(prompt), key))
            try:
                raw = brain.ask(prompt)
            except Exception as e:
                log("ask failed:", repr(e)[:300])
                self._json(502, {"error": {"message": str(e)[:300], "code": "brain_failed"}}); return
        log("reply chars:", len(raw))
        try:
            with open(REPLIES_LOG, "a") as rf:
                rf.write("\n=== %s chain=%s ===\n%s\n" % (time.strftime("%H:%M:%S"), key, raw[:4000]))
        except Exception:
            pass
        text, calls = parse_reply(raw)
        if calls:
            text = trim_code_blocks(text)
        finish = "tool_calls" if calls else "stop"
        msg = {"role": "assistant", "content": text if text else None}
        if calls:
            msg["tool_calls"] = calls
        comp = {
            "id": "chatcmpl-chatbrain", "object": "chat.completion", "created": int(time.time()),
            "model": "deepseek-chat",
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
        chunk = {"id": comp["id"], "object": "chat.completion.chunk", "created": comp["created"], "model": "deepseek-chat",
                 "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
        self.wfile.write(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode())
        fin = {"id": comp["id"], "object": "chat.completion.chunk", "created": comp["created"], "model": "deepseek-chat",
               "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}
        self.wfile.write(("data: " + json.dumps(fin, ensure_ascii=False) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")

if __name__ == "__main__":
    log("relay2 up on", PORT)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
