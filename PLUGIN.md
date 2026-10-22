# The dsh ↔ Better DeepSeek bridge plugin (external dependency)

The second half of this project is **not** code in this repository. It is a
third-party plugin that we install, not something we wrote:

| | |
|---|---|
| Package | `dsh-better-deepseek` |
| Version used | `0.1.0` |
| License | MIT |
| Source | [EdgeTypE/better-deepseek](https://github.com/EdgeTypE/better-deepseek), directory `packages/extensions/better-deepseek` |
| Role | A [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (dsh) plugin that bridges dsh with the **Better DeepSeek** browser extension, so the extension (running on the free chat.deepseek.com web UI) can reach dsh as its execution side |

Because it is an upstream package, its code is intentionally **excluded** from
this repo. We document the integration contract instead.

## What it does

Installed into a dsh profile (it lands in
`<dsh-profile>/node_modules/dsh-better-deepseek/` with a small
`cordis.patch.yml`), the plugin exposes an HTTP route inside the dsh web
server that the Better DeepSeek extension talks to. The division of labour:

- **Better DeepSeek extension** (in the browser, on chat.deepseek.com) —
  plans and converses using the free web chat, emits tool calls.
- **This plugin** — carries those calls into dsh.
- **dsh** — executes them locally with dsh's own provider and tool surface.

Note the direction: the plugin does **not** make dsh's own turns free. For
that, see the chatbrain relay in [`relay/`](relay/) — the two pieces are
complementary and are usually run together.

## Health-check contract

```
GET /api/better-deepseek/ping
→ 200 {"active": true, "version": "1.6.0", ...}
```

A `200` with `"active": true` means the plugin is loaded in the running dsh
web profile and ready to accept the extension's connection.

## Install sketch

1. Install DeepSeek Harness (`dsh`) and create/use a web profile.
2. Add the plugin to that profile using dsh's plugin mechanism
   (`dsh plugin --profile web add dsh-better-deepseek`, or drop the package
   into the profile's `node_modules` plus its `cordis.patch.yml` entry).
3. Restart the dsh web server and verify with the ping endpoint above.
4. Install the Better DeepSeek extension in your browser, open
   chat.deepseek.com, and connect it — it should report the bridge as active.
