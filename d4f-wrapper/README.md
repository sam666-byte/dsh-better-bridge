# DeepSeek Web (4Free) — V3 + R1 as extra dsh models

`d4f_wrapper.py` is a small OpenAI-compatible server (default port **20233**)
that lets dsh list and use **DeepSeek V3** and **DeepSeek R1** in its model
picker, next to the CHATBRAIN relay model (`deepseek-chat`). Both models run
through the **DeepSeek web chat** (chat.deepseek.com) — **no official API key,
no paid account**.

Transport: [xtekky/deepseek4free](https://github.com/xtekky/deepseek4free)
(`dsk.api`) against the chat.deepseek.com private API, with the **same
userToken the CHATBRAIN relay uses** — read at runtime from
`/data/dsh/chatbrain/.websession.json`. The token is never hard-coded, never
written anywhere, and only its length is logged.

## Patches vs upstream (the repo is Jan 2025, unmaintained)

Proven by live test 26 Oct 2026 (V3 Q&A, R1 thinking, V3+web search — all
clean). Applied as monkey-patches inside the wrapper; the `dsk` repo itself
is untouched:

1. `create_chat_session`: the API now returns
   `data.biz_data.chat_session.id` (the repo expects `data.biz_data.id`).
2. The SSE stream is now JSON-patch format (`{"p":...,"o":"APPEND","v":...}`),
   not OpenAI-style `choices`/`delta`. The first chunk nests fragments at
   `v.response.fragments` (not `v.fragments`) — missing that path drops the
   first characters of every answer ("Jakarta" → "arta").
3. `pkg_resources` is missing on Python 3.14 — the venv needs
   `setuptools==80.9.0`.

Two more bugs found during verification (26 Oct 2026) and fixed in the
wrapper: the SSE first-fragment path above, and R1 thinking fragments leaking
into the answer (list-APPEND ops on `response/fragments` were ignored).

27 Oct 2026 updates: the wrapper now binds `0.0.0.0:20233` (was `127.0.0.1`)
so the phone apps (Nusantara Bots / Razako BYO) can reach it over LAN — note
there is no auth, so anyone on the home WiFi can use it (it shares the
owner's DeepSeek token). It also strips the bare `< calls>` opener the R1
model emits instead of `<｜｜DSML｜｜ calls>` (observed 254x) — it survived the
DSML debris regex and leaked into chat as visible "`< calls>`" bubbles.

## DBS / tool-call handling

Prompt building (PREAMBLE, tool catalog, message rendering) and reply parsing
(`parse_reply`) are **imported from `relay2.py`** — the same code as the
DBS-patched relay. If that import ever fails, the wrapper **fails to start**
rather than serving answers without DBS protection. The wrapper adds its own
tolerant DSML recovery, argument unwrap, and guaranteed debris strip on top.

## Setup

```bash
# 1) Clone upstream + venv (Python 3.14)
git clone https://github.com/xtekky/deepseek4free /data/dsh-test/deepseek4free
cd /data/dsh-test/deepseek4free
python3 -m venv .venv
.venv/bin/pip install -e . setuptools==80.9.0

# 2) Copy this folder's wrapper next to it (or anywhere; see launcher)
cp -r d4f-wrapper /data/dsh/d4f-wrapper

# 3) Start it (or let start-all.sh do it when 20233 is not listening)
D4F_VENV=/data/dsh-test/deepseek4free/.venv ./d4f-wrapper/start-d4f-wrapper.sh
```

## Register in dsh (cordis patch, profile "web")

Add a provider under `llm-pi-ai` → `providers`, following the `chatbrain`
pattern exactly:

```yaml
      deepseek4free:
        displayName: "DeepSeek Web (4Free)"
        api: openai-completions
        baseURL: http://127.0.0.1:20233/v1
        apiKeyEnv: D4F_API_KEY   # any dummy value; wrapper is loopback-only
        models:
          - id: deepseek-v3-d4f
            contextWindow: 1000000
          - id: deepseek-r1-d4f
            contextWindow: 1000000
```

Export `D4F_API_KEY=local-d4f` (dummy) before starting dsh, restart dsh web,
then pick **"DeepSeek Web (4Free)"** → `deepseek-v3-d4f` (fast, general) or
`deepseek-r1-d4f` (reasoning — thinks first, better for hard problems) in the
model picker.

## Verify

```bash
curl http://127.0.0.1:20233/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v3-d4f","messages":[{"role":"user","content":"ping"}],"stream":false}'
```

## Notes / risks

- **Shared userToken:** the wrapper and the relay use the same DeepSeek
  account token. dsh only calls the model you selected, so they don't run at
  the same time in normal use. Don't call both backends simultaneously on
  purpose (session / rate-limit conflicts).
- **Upstream is stale and unofficial.** DeepSeek can change the private web
  API and break this; the account carries the usual ToS risk of
  reverse-engineered clients. The relay (browser-automation path) is the
  more resilient fallback.
- **Don't delete the deepseek4free clone + venv** — the wrapper imports
  `dsk` from it at runtime.
- Logs: `d4f-wrapper.log` (requests), `d4f-replies.log` (raw replies,
  truncated), `d4f-wrapper.out` (stdout/stderr). Stop with
  `fuser -k 20233/tcp`.
