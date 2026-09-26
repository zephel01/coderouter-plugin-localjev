<h1 align="center">coderouter-plugin-localjev</h1>

<p align="center">
  <strong>Ask a local Jev "what kind of job is this?" before the request goes out,<br>and let the answer switch your CodeRouter profile automatically.</strong>
</p>

<p align="center">
  <a href="https://github.com/zephel01/coderouter-plugin-localjev/releases"><img src="https://img.shields.io/badge/version-0.2.0-blue" alt="version"></a>
  <img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="python">
  <img src="https://img.shields.io/badge/deps-0-brightgreen" alt="deps">
  <img src="https://img.shields.io/badge/tests-17%20passed-brightgreen" alt="tests">
  <img src="https://img.shields.io/badge/status-alpha-orange" alt="status">
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-yellow" alt="license"></a>
</p>

<p align="center">
  <strong>English</strong> · <a href="./README.md">日本語</a> ·
  <a href="#-quick-start">Quick start</a> ·
  <a href="#%EF%B8%8F-configuration">Configuration</a> ·
  <a href="#-design-notes">Design notes</a>
</p>

---

## ✨ What it does — in 30 seconds

```
Claude Code ──▶ CodeRouter :8088
                   │
                   │  InputFilter: localjev
                   │     └──▶ LocalJev :8080 ──▶ llama-server :8000
                   │           POST /v1/systemone
                   ▼
              profile = coding | reasoning | general
```

Through the [CodeRouter](https://github.com/zephel01/CodeRouter) Plugin SDK (`coderouter.input_filter` / `coderouter.observer`),
this plugin calls `POST /v1/systemone` on [githubnext/localjev](https://github.com/githubnext/localjev) and:

- 🧭 **Routes automatically** — classifies the turn as implementation, design / root-cause, or chat / Q&A and sets `request.profile`
- 📝 **Injects the judgment** — appends "needs tools?", "high risk?", "needs a stronger model?" to `system` as advisory context
- 🛡️ **Fails safe** — passes requests through when LocalJev is down (`fail_open`) and never adopts an unknown profile (allowlist)
- 🪶 **Zero dependencies** — Python standard library only; CodeRouter itself is left untouched

---

## 🚀 Quick start

### 1. Run LocalJev (upstream = llama-server)

LocalJev needs only two things from its upstream; oMLX is not required (verified by reading `localjev/src/engine.ts`).

| Purpose | Endpoint | Requirement |
|---|---|---|
| `/ready` check | `GET {upstream}/v1/models` | `data[].id` must contain an id that **exactly matches** `LOCALJEV_UPSTREAM_MODEL` |
| Inference | `POST {upstream}/v1/chat/completions` | Must accept `response_format: {"type":"json_schema",…}`, `seed`, and `chat_template_kwargs` |

llama.cpp's `llama-server` supports all of these
(`response_format.json_schema.schema` is converted to GBNF, `--alias` becomes the `/v1/models` `id`, and `chat_template_kwargs` is implemented).

```bash
# upstream: always pass --alias, it becomes the id in /v1/models
llama-server -m ~/models/<a-small-instruct-model>.gguf \
  --alias jev-backend --host 127.0.0.1 --port 8000 --jinja

# LocalJev
git clone https://github.com/githubnext/localjev && cd localjev
bun install
cp .env.example .env
```

<details>
<summary><code>.env</code> contents</summary>

```ini
LOCALJEV_UPSTREAM=http://127.0.0.1:8000
LOCALJEV_UPSTREAM_MODEL=jev-backend   # ← must match llama-server's --alias
LOCALJEV_UPSTREAM_API_KEY=            # leave empty if llama-server has no --api-key
LOCALJEV_PORT=8080
LOCALJEV_TIMEOUT=180
LOCALJEV_MAX_INFLIGHT=2
```

</details>

```bash
bun run start
curl http://127.0.0.1:8080/ready
# => {"status":"ready","upstream_model":"jev-backend"}
```

> [!TIP]
> **If `/ready` returns 503**, the problem is the upstream, not LocalJev.
> Check that an `id` from `curl http://127.0.0.1:8000/v1/models` matches `LOCALJEV_UPSTREAM_MODEL` exactly
> (without `--alias`, the id becomes the full path of the gguf file).

<details>
<summary>Check a judgment directly with curl</summary>

```bash
curl http://127.0.0.1:8080/v1/systemone -H 'Content-Type: application/json' -d '{
  "model":"jev-latest",
  "state":"add a retry to the uploader and run the tests",
  "questions":{"route":{"type":"choice","instructions":"Which profile?",
    "criteria":{"coding":"impl","reasoning":"design","general":"chat"}}}}'
```

The response always has this shape (there is no top-level `nouls` / `choices`):

```json
{"model":"localjev-0.2",
 "answers":{"route":{"type":"choice","choice":"coding",
            "probabilities":{"coding":0.6,"reasoning":0.2,"general":0.2},
            "confidence":0.135}},
 "usage":{"input_tokens":123,"output_tokens":45}}
```

</details>

### 2. Install the plugin

```bash
uv pip install git+https://github.com/zephel01/coderouter-plugin-localjev
# or from a local checkout: uv pip install ./coderouter-plugin-localjev

python3 scripts/live_smoke.py "fix the tests"   # query LocalJev once and print the result
```

### 3. Enable it in `providers.yaml`

`~/.coderouter/providers.yaml` (full example: [`examples/providers.localjev.yaml`](./examples/providers.localjev.yaml)):

```yaml
plugins:
  enabled: [localjev]
  config:
    localjev:
      base_url: http://127.0.0.1:8080
      model: jev-latest
      fail_open: true
      first_user_only: true
      inject: true
      inject_position: append
      set_profile: true
      profiles_available: [coding, reasoning, general]   # ★ must be profiles that actually exist
      profile_map: {coding: coding, reasoning: reasoning, general: general}
```

> [!IMPORTANT]
> Installing is not enough — the plugin is active only when listed in `plugins.enabled` (two-stage gate).
> It is active when the startup log shows **two** `plugin-loaded plugin=localjev` lines (input_filter and observer).

---

## ⚙️ Configuration

| Key | Default | Description |
|---|---|---|
| `base_url` | `http://127.0.0.1:8080` | LocalJev origin |
| `api_key` | `""` | Needed only when LocalJev sets `LOCALJEV_API_KEY` |
| `model` | `jev-latest` | LocalJev alias (`jev-latest` / `localjev-latest` / `localjev-0.2`) |
| `timeout_s` | `20` | HTTP timeout (seconds) |
| `fail_open` | `true` | Pass through when LocalJev fails |
| `first_user_only` | `true` | Judge only the first turn (avoids per-turn latency) |
| `inject` | `true` | Append the judgment to `system` |
| `inject_position` | `append` | `prepend` breaks the prompt-cache prefix |
| `set_profile` | `true` | route choice → `request.profile` |
| `route_question` | `route` | Name of the choice question used for the profile |
| `min_confidence` | `0.0` | Don't change the profile below this confidence |
| `profiles_available` | none | **Allowlist of existing profiles (strongly recommended)** |
| `profile_map` | coding / reasoning / general | choice label → profile name |
| `questions` | 4 questions (3 noul + 1 choice) | Replace the question set |
| `state_max_chars` | `4000` | Max length of the state sent to LocalJev |

<details>
<summary>Default question set (<code>defaults.py</code>)</summary>

| Name | Type | Question |
|---|---|---|
| `needs_tools` | noul | Does the turn require tool calls, file edits, or running commands? |
| `high_risk` | noul | Would a wrong action be hard to undo (delete, deploy, secrets, production, force-push)? |
| `needs_strong_model` | noul | Does it need a stronger model than a small local coder? |
| `route` | choice | Which profile should handle it: `coding` / `reasoning` / `general`? |

Decomposing into independent nouls follows the Jev sample finding that a single 4-way choice is poorly calibrated.

</details>

---

## 🧠 Design notes

Points confirmed by reading the CodeRouter source.

<details open>
<summary><b>The profile allowlist is not decoration</b></summary>

Ingress profile validation (`ingress/anthropic_routes.py`) finishes **before** the input filter runs.
A name that is not in providers.yaml slips past validation and raises `KeyError` in `config.profile_by_name` (= HTTP 500).
With `profiles_available` set, unknown profiles are ignored and only a warning is logged.

</details>

<details>
<summary><b>Why overriding the profile works</b></summary>

The input filter runs in `FallbackEngine._generate_anthropic_impl` **before** chain resolution.
Returning a new object discards the M11 prepared dispatch, so the chain is re-resolved.
It also runs **after** `auto_router`, so this plugin's decision wins.

</details>

<details>
<summary><b>Two entry-point groups = two instances</b></summary>

The loader calls `cls(**config)` once per group, so the observer is a different object from the filter.
Decisions are shared through a class variable (last 32 entries), and the observer matches them by a fingerprint of the state.
If you only want routing, remove the `coderouter.observer` line from `pyproject.toml`.

</details>

<details>
<summary><b>Why <code>append</code> is the default</b></summary>

Claude Code sends `system` as an array of blocks with `cache_control`.
Inserting at the front changes the cache prefix and invalidates the prompt cache, so the default appends at the end.

</details>

<details>
<summary><b>Probabilities are not read from logits</b></summary>

LocalJev has the model **self-report** probabilities via a classification prompt + JSON schema (stated in LocalJev's README too).
Don't treat them as equivalent to upstream Jev's calibrated probabilities. `min_confidence` is a threshold on this self-reported value.

</details>

<details>
<summary><b>Latency</b></summary>

Each judgment runs one upstream generation. Disabling `first_user_only` adds it to every turn.

</details>

---

## 🧪 Tests

```bash
uv pip install -e ".[dev]"
python3 -m pytest -q                                   # 17 passed
LOCALJEV_BASE_URL=http://127.0.0.1:8080 python3 scripts/live_smoke.py "fix the tests"
```

`scripts/stub_upstream.py` is a stub standing in for LocalJev's upstream, handy for checking the wiring without a real model.

## 📁 Layout

```
coderouter_plugin_localjev/
├── plugin.py      # LocalJevPlugin (InputFilter + Observer)
├── client.py      # stdlib-only HTTP client for /v1/systemone
├── defaults.py    # default question set and profile_map
└── __init__.py
examples/providers.localjev.yaml   # minimal providers.yaml
scripts/live_smoke.py              # connectivity check against a real LocalJev
scripts/stub_upstream.py           # upstream stub
tests/test_plugin.py
```

## 🔗 Related

- [CodeRouter](https://github.com/zephel01/CodeRouter) — the router for running Claude Code on local LLMs
- [githubnext/localjev](https://github.com/githubnext/localjev) — a local implementation of Jev System One

## 📄 License

[MIT](./LICENSE)
