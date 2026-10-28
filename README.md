# dsh-better-bridge

**Run [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (dsh) on the *free* DeepSeek web chat instead of the metered platform API — plus a bridge to the Better DeepSeek browser extension.**

DeepSeek Harness is a capable open-source agent harness, but its built-in
account provider bills against the DeepSeek *platform* wallet. The consumer
chat at chat.deepseek.com is free. This project connects the two worlds:

1. **chatbrain relay** (`relay/`) — a small OpenAI-compatible HTTP relay that
   registers itself in dsh as a custom provider. Under the hood it drives
   DeepSeek's consumer web-chat API with **your own logged-in web session**,
   solving the web client's proof-of-work challenge and translating the
   harness's tool calls into a text-tag contract the chat model can follow.
2. **BDS bridge plugin** (external dependency, see [`PLUGIN.md`](PLUGIN.md)) —
   the upstream `dsh-better-deepseek` plugin that lets the **Better DeepSeek**
   browser extension use dsh as its execution side.

## 💛 Dukung Kami

This project is free and open source. If it helps you, consider supporting
its development — any amount, no minimum burden (from Rp1.000).

<p align="center">
  <a href="https://saweria.co/Nusantaraeaters">
    <img src="assets/saweria-qr.png" alt="Support via Saweria — scan this QR" width="220">
  </a>
</p>

<p align="center">
  👉 <b><a href="https://saweria.co/Nusantaraeaters">saweria.co/Nusantaraeaters</a></b>
</p>

> "Dukung Nusantara Bots - aplikasi AI agent gratis di HP. Donasi seikhlasnya buat pengembangan aplikasi. Terima kasih!"

## Architecture

```
                        your machine
 ┌───────────────────────────────────────────────────────────────┐
 │                                                               │
 │   Browser                      DeepSeek Harness (dsh)         │
 │  ┌──────────────────┐         ┌────────────────────────────┐  │
 │  │ chat.deepseek.com│         │  agent loop, tools, memory │  │
 │  │  + Better        │  ping / │                            │  │
 │  │    DeepSeek ext  ├────────►│  dsh-better-deepseek plugin│  │
 │  └────────┬─────────┘  bridge └──────────────┬─────────────┘  │
 │           │                                  │ provider:      │
 │           │ web session                      │ "chatbrain"    │
 │           │ (extract.py)                     ▼                │
 │           │                        ┌────────────────────────┐  │
 │           └───────────────────────►│  chatbrain relay       │  │
 │              .websession.json    │  relay/relay2.py       │  │
 │              (chmod 600)         │  OpenAI API on :20232  │  │
 │                                  └───────────┬────────────┘  │
 └──────────────────────────────────────────────┼───────────────┘
                                                │ HTTPS + PoW header
                                                ▼
                                     chat.deepseek.com web API
                                     (/api/v0/chat/completion)
```

## Components

| Path | What it is |
|---|---|
| `relay/relay2.py` | The relay: OpenAI-compatible `/v1/chat/completions` + `/v1/models` on loopback, backed by the DeepSeek web-chat API. |
| `relay/extract.py` | One-shot extractor: pulls `userToken` + cookies out of a dedicated Firefox profile (where you logged in manually) into `.websession.json`. |
| `PLUGIN.md` | The bridge plugin is an **upstream package** (`dsh-better-deepseek` by EdgeTypE, MIT) — not vendored here. Documents its role and ping contract. |
| `.env.example` | All configuration variables with placeholder values. |

## How it works

### Session extraction
You log in to chat.deepseek.com once, manually, in a dedicated Firefox
profile. `extract.py` re-opens that profile with Selenium/geckodriver, reads
`localStorage["userToken"]`, and stores `{token, cookies}` as JSON with file
mode `600`. The relay then authenticates to the web API exactly like the web
client does. When the token dies, re-run the extractor (or log in again).

### Proof-of-work header
Every web-chat completion requires an `x-ds-pow-response` header: the client
requests a challenge (`/api/v0/chat/create_pow_challenge`), solves it, and
sends back a base64 JSON payload. The relay uses the
[`deepseek-pow`](https://pypi.org/project/deepseek-pow/) package
(WASM solver) for the hash.

> **Gotcha:** the package's `to_answer_payload()` omits `target_path`, and
> the API rejects the header without it. The relay builds the payload
> manually: `{algorithm, challenge, salt, signature, answer: int,
> target_path: "/api/v0/chat/completion"}`.

### Tool-call tag contract
The web chat has no native function calling, so the relay renders the
harness's full system prompt + tool catalog **once per conversation** as a
text preamble, then sends only deltas. The model answers with tool calls as
text tags:

```
<tool_call>{"name": "bash", "arguments": {"command": "ls -la"}}</tool_call>
```

Replies are parsed back into OpenAI `tool_calls`. The parser is deliberately
paranoid, because the model mixes dialects:

- `<tool_call>` JSON is located by **balanced-brace scan** (closing tags are
  unreliable — JSON blocks get closed with DSML tags and vice versa);
- DeepSeek's native **DSML** dialect (`<｜｜DSML｜｜invoke …>`) is parsed too,
  including repair of mojibake-damaged fullwidth delimiters;
- "flattened" tool JSON (arguments at top level) is re-nested;
- leftover tag debris is swept from the visible text;
- long fenced code blocks riding along with tool calls are trimmed (the full
  text lives in the tool call itself).

A short preamble + a per-turn recency reminder keep the chat model behaving
like an autonomous agent (use tools yourself, never hand the user a script;
final answers as readable chat text, not code-block reports).

### Multi-chain isolation
A harness makes auxiliary calls too (session titles, memory jobs). Requests
carrying tools go to the **main agent chain**; everything else gets its own
chain keyed by a hash of its opening messages. Aux chains can never reset or
pollute the main conversation. Chains are capped (≈6) with simple eviction.

### Conversation mapping
Each chain maps 1:1 to a server-side web-chat session
(`/api/v0/chat_session/create`). The relay keeps a message-count watermark
and a fingerprint of the last forwarded message; if the harness's history
diverges (edit, rollback, new session), the chain resets into a fresh chat
and the full render is sent again. The authoritative assistant text is read
back from `/api/v0/chat/history_messages` (RESPONSE fragments), not from the
SSE stream.

## Install & usage

**Prerequisites**

- Python 3.11+
- Node.js LTS (the harness itself runs on Node; Python is only for the relay)
- [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)
  installed separately
- Firefox + geckodriver (for the one-time login/extraction)
- A DeepSeek account you can log in to at chat.deepseek.com

**Steps**

```bash
git clone <this-repo> dsh-better-bridge
cd dsh-better-bridge
pip install -r requirements.txt

# 1) One-time: create a dedicated Firefox profile and log in
firefox -ProfileManager            # create e.g. "chatbrain", log in to
                                   # chat.deepseek.com, then close Firefox
CHATBRAIN_FF_PROFILE=/path/to/profile python3 relay/extract.py
#    → writes ./.websession.json (mode 600)

# 2) Start the relay
python3 relay/relay2.py            # listens on 127.0.0.1:20232

# 3) Register the relay as a dsh provider (cordis patch, profile "web"):
#    an OpenAI-compatible provider entry, e.g.
#      chatbrain:
#        baseURL: http://127.0.0.1:20232/v1
#        apiKeyEnv: CHATBRAIN_API_KEY   # any dummy value; relay is loopback
#        models: [deepseek-chat]        # contextWindow 1000000
#    then select provider "chatbrain", model "deepseek-chat" in dsh.

# 4) (Optional) Add the Better DeepSeek bridge plugin — see PLUGIN.md.
```

Sanity check the relay on its own:

```bash
curl http://127.0.0.1:20232/v1/models
curl http://127.0.0.1:20232/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"ping"}]}'
```

### DeepSeek Web (4Free) — V3 + R1 as extra models (no official API)

`d4f-wrapper/` adds **DeepSeek V3** (`deepseek-v3-d4f`) and **DeepSeek R1**
(`deepseek-r1-d4f`) to the dsh model picker, through the same DeepSeek web
chat the relay uses — no official API key. It is an OpenAI-compatible server
(port 20233) built on [xtekky/deepseek4free](https://github.com/xtekky/deepseek4free),
sharing the relay's userToken (read at runtime from `.websession.json`, never
stored in the repo). Setup, provider YAML, and verification are in
[`d4f-wrapper/README.md`](d4f-wrapper/README.md). Upstream is Jan 2025 and
unmaintained — the wrapper carries the patches that keep it working (session
path, JSON-patch SSE, first-fragment path, R1 thinking leak) plus the same
DBS/tool-call handling as the relay (imported from `relay2.py`).

### Windows (native, without WSL)

The same flow works on Windows; the differences are the shell syntax and a
few paths. Keep **3 separate terminals** open while in use:

1. **Install Node.js LTS** from nodejs.org, then clone + install Python deps:
   ```cmd
   git clone https://github.com/sam666-byte/dsh-better-bridge
   cd dsh-better-bridge
   pip install -r requirements.txt
   ```

2. **Create a dedicated Firefox profile:** press `Win+R`, type
   `firefox -ProfileManager`, create a new profile (e.g. "chatbrain"), log in
   to chat.deepseek.com in that profile, then **close Firefox completely** —
   the extractor cannot read a profile that is still open. The profile path is
   something like
   `C:\Users\<you>\AppData\Roaming\Mozilla\Firefox\Profiles\xxxx.chatbrain`

3. **Set environment variables in CMD** (the Windows equivalent of `export`):
   ```cmd
   set CHATBRAIN_FF_PROFILE=C:\Users\<you>\AppData\Roaming\Mozilla\Firefox\Profiles\xxxx.chatbrain
   set FIREFOX_BIN=C:\Program Files\Mozilla Firefox\firefox.exe
   set GECKODRIVER_PATH=C:\path\to\geckodriver.exe
   ```
   `set` only applies to the terminal it runs in, so run steps 4–5 from the
   same terminal (or use `setx` to make the variables permanent).

4. **Extract the session** (Firefox must be fully closed; use `python`, not
   `python3`):
   ```cmd
   python relay\extract.py
   ```
   Success = `.websession.json` appears in the repo folder.

5. **Start the relay** and leave the terminal open:
   ```cmd
   python relay\relay2.py
   ```
   Check from another terminal: `curl http://127.0.0.1:20232/v1/models`

6. **Start dsh in a new terminal** (if PowerShell complains about execution
   policy, use CMD instead). Install dsh globally first — do **not** use
   `npx` (the `npx @deepseek-ai/dsh` download is currently broken: it is
   missing the `@deepseek-ai/cordis-plugin-group` dependency and fails with
   `ERR_MODULE_NOT_FOUND`):
   ```cmd
   npm install -g @deepseek-ai/dsh
   dsh web --host 127.0.0.1 --port 3080 --no-open
   ```
   If the global install still reports the same missing package, install it
   explicitly first: `npm install -g @deepseek-ai/cordis-plugin-group`, then
   run `dsh` again.
   Do not use `--host 0.0.0.0` — dsh rejects wildcards. On a VPS, loopback is
   enough: you reach the GUI from inside the VPS (e.g. over RDP). Open the
   token URL printed in the console and log in with your DeepSeek account.

7. **Register the relay as a dsh provider:** edit
   `C:\Users\<you>\.dsh\profiles\web\cordis.patch.yml` and add an
   OpenAI-compatible provider entry:
   ```yaml
   chatbrain:
     baseURL: http://127.0.0.1:20232/v1
     apiKeyEnv: CHATBRAIN_API_KEY   # any dummy value; relay is loopback
     models: [deepseek-chat]
   ```
   Set `CHATBRAIN_API_KEY` to any dummy value, restart dsh, then select
   provider **chatbrain** / model **deepseek-chat** in the GUI. Done — dsh now
   runs on the free web chat.

Notes: all ports are loopback, so no VPS firewall rules are needed. If the
chat later errors or returns empty, the session token expired — just re-run
step 4. The Better DeepSeek bridge plugin (see `PLUGIN.md`) is optional and
can be added later.

## Configuration

All configuration is via environment variables (see `.env.example`):

| Variable | Default | Used by | Meaning |
|---|---|---|---|
| `CHATBRAIN_PORT` | `20232` | relay | Loopback port for the OpenAI-compatible API |
| `CHATBRAIN_SESSION_FILE` | `./.websession.json` | relay, extractor | Session `{token, cookies}` file |
| `CHATBRAIN_LOG_FILE` | `./relay2.log` | relay | Main relay log |
| `CHATBRAIN_REPLIES_LOG` | `./relay2-replies.log` | relay | Raw model replies (private — contains conversations) |
| `CHATBRAIN_FF_PROFILE` | `./ff-profile` | extractor | Dedicated Firefox profile directory |
| `FIREFOX_BIN` | `/usr/bin/firefox` | extractor | Firefox binary |
| `GECKODRIVER_PATH` | `geckodriver` | extractor | geckodriver binary (PATH lookup by default) |

## Security notes

- **Your session token is your account.** `.websession.json` contains a live
  `userToken` equivalent to being logged in. Keep it `chmod 600`, on a
  machine you trust, and **never commit it** (it is in `.gitignore`).
- The relay listens on **loopback only**. Don't expose it: anyone who can
  reach the port can spend your chat session.
- The replies log contains your raw conversations — treat it like the token.
- Use a dedicated Firefox profile (and ideally a dedicated account) so the
  blast radius of any mistake stays small.

## Disclaimer

This is an **unofficial** project. It is not affiliated with, endorsed by, or
sponsored by DeepSeek, Anysphere, or EdgeTypE.

It interacts with DeepSeek's **consumer web-chat surface using your own
account session**, rather than the official platform API. That may conflict
with DeepSeek's Terms of Service, and account consequences (throttling,
restriction, or termination) are possible. Provided for educational and
personal use, **at your own risk**. If you need a supported integration, use
the official DeepSeek platform API.

## Credits & attribution

- **DeepSeek Harness** © DeepSeek — MIT License —
  [github.com/deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)
- **Better DeepSeek** © EdgeTypE — MIT License —
  [github.com/EdgeTypE/better-deepseek](https://github.com/EdgeTypE/better-deepseek)
  (including the `dsh-better-deepseek` plugin package)
- **deepseek-pow** — the PoW solver package used by the relay
  ([PyPI](https://pypi.org/project/deepseek-pow/))

## License

MIT — see [`LICENSE`](LICENSE).
