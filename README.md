<h1 align="center">coderouter-plugin-localjev</h1>

<p align="center">
  <strong>リクエストを投げる前に、ローカルの Jev に「これは何の仕事か」を聞く。<br>判定で CodeRouter の profile を自動で切り替える plugin です。</strong>
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
  <a href="./README.en.md">English</a> · <strong>日本語</strong> ·
  <a href="#-クイックスタート">クイックスタート</a> ·
  <a href="#%EF%B8%8F-設定キー">設定</a> ·
  <a href="#-設計メモ">設計メモ</a>
</p>

---

## ✨ 何ができるか — 30 秒で

```
Claude Code ──▶ CodeRouter :8088
                   │
                   │  InputFilter: localjev
                   │     └──▶ LocalJev :8080 ──▶ llama-server :8000
                   │           POST /v1/systemone
                   ▼
              profile = coding | reasoning | general
```

[CodeRouter](https://github.com/zephel01/CodeRouter) の Plugin SDK（`coderouter.input_filter` / `coderouter.observer`）から
[githubnext/localjev](https://github.com/githubnext/localjev) の `POST /v1/systemone` を呼び、

- 🧭 **自動ルーティング** — 「実装」「設計・原因調査」「雑談・Q&A」を判定して `request.profile` を切り替える
- 📝 **判定の注入** — ツール要否・リスク・強いモデルが要るか、を `system` 末尾に参考情報として足す
- 🛡️ **安全側に倒す** — LocalJev が落ちていても素通し（`fail_open`）、未知の profile は採用しない（allowlist）
- 🪶 **ゼロ依存** — Python 標準ライブラリのみ。CodeRouter 本体には一切手を入れない

---

## 🚀 クイックスタート

### 1. LocalJev を立てる（upstream = llama-server）

LocalJev が upstream に要求するのは 2 つだけで、oMLX は必須ではありません（`localjev/src/engine.ts` を実読して確認）。

| 用途 | エンドポイント | 要求 |
|---|---|---|
| `/ready` 判定 | `GET {upstream}/v1/models` | `data[].id` に `LOCALJEV_UPSTREAM_MODEL` と**完全一致**する id があること |
| 推論 | `POST {upstream}/v1/chat/completions` | `response_format: {"type":"json_schema",…}` / `seed` / `chat_template_kwargs` を受けること |

llama.cpp の `llama-server` はこのすべてに対応しています
（`response_format.json_schema.schema` → GBNF 変換、`--alias` が `/v1/models` の `id` になる、`chat_template_kwargs` 実装済み）。

```bash
# upstream: --alias が /v1/models の id になるので必ず付ける
llama-server -m ~/models/<小さめの instruct モデル>.gguf \
  --alias jev-backend --host 127.0.0.1 --port 8000 --jinja

# LocalJev
git clone https://github.com/githubnext/localjev && cd localjev
bun install
cp .env.example .env
```

<details>
<summary><code>.env</code> の中身</summary>

```ini
LOCALJEV_UPSTREAM=http://127.0.0.1:8000
LOCALJEV_UPSTREAM_MODEL=jev-backend   # ← llama-server の --alias と一致させる
LOCALJEV_UPSTREAM_API_KEY=            # llama-server が --api-key 無しなら空でよい
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
> **`/ready` が 503 のとき**は LocalJev ではなく upstream 側の問題です。
> `curl http://127.0.0.1:8000/v1/models` の `id` と `LOCALJEV_UPSTREAM_MODEL` の文字列一致を確認してください
> （`--alias` を付け忘れると id が gguf のフルパスになります）。

<details>
<summary>判定そのものを curl で確認する</summary>

```bash
curl http://127.0.0.1:8080/v1/systemone -H 'Content-Type: application/json' -d '{
  "model":"jev-latest",
  "state":"add a retry to the uploader and run the tests",
  "questions":{"route":{"type":"choice","instructions":"Which profile?",
    "criteria":{"coding":"impl","reasoning":"design","general":"chat"}}}}'
```

応答は常にこの形です（トップレベルに `nouls` / `choices` は存在しません）:

```json
{"model":"localjev-0.2",
 "answers":{"route":{"type":"choice","choice":"coding",
            "probabilities":{"coding":0.6,"reasoning":0.2,"general":0.2},
            "confidence":0.135}},
 "usage":{"input_tokens":123,"output_tokens":45}}
```

</details>

### 2. プラグインを入れる

```bash
uv pip install git+https://github.com/zephel01/coderouter-plugin-localjev
# またはローカルから: uv pip install ./coderouter-plugin-localjev

python3 scripts/live_smoke.py "テストを直して"   # LocalJev に 1 回問い合わせて結果を表示
```

### 3. `providers.yaml` で有効化

`~/.coderouter/providers.yaml`（全体は [`examples/providers.localjev.yaml`](./examples/providers.localjev.yaml)）:

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
      profiles_available: [coding, reasoning, general]   # ★必ず実在する profile 名
      profile_map: {coding: coding, reasoning: reasoning, general: general}
```

> [!IMPORTANT]
> `plugins.enabled` に書かないと、インストールしただけでは有効になりません（二段ゲート）。
> 起動ログに `plugin-loaded plugin=localjev` が **2 行**（input_filter と observer）出れば有効です。

---

## ⚙️ 設定キー

| キー | 既定 | 説明 |
|---|---|---|
| `base_url` | `http://127.0.0.1:8080` | LocalJev の origin |
| `api_key` | `""` | LocalJev 側で `LOCALJEV_API_KEY` を設定したときだけ必要 |
| `model` | `jev-latest` | LocalJev の alias（`jev-latest` / `localjev-latest` / `localjev-0.2`） |
| `timeout_s` | `20` | HTTP タイムアウト（秒） |
| `fail_open` | `true` | LocalJev 障害時に素通し |
| `first_user_only` | `true` | 1 ターン目のみ判定（毎ターンの遅延を避ける） |
| `inject` | `true` | 判定を `system` に足す |
| `inject_position` | `append` | `prepend` は prompt cache の prefix を壊す |
| `set_profile` | `true` | route choice → `request.profile` |
| `route_question` | `route` | profile に使う choice 質問の名前 |
| `min_confidence` | `0.0` | この値未満の confidence では profile を変えない |
| `profiles_available` | なし | **実在する profile の allowlist（強く推奨）** |
| `profile_map` | coding / reasoning / general | choice ラベル → profile 名 |
| `questions` | 4 問（noul×3 + choice×1） | 質問セットの差し替え |
| `state_max_chars` | `4000` | LocalJev に送る state の上限 |

<details>
<summary>既定の質問セット（<code>defaults.py</code>）</summary>

| 名前 | 種別 | 問い |
|---|---|---|
| `needs_tools` | noul | ツール呼び出し・ファイル編集・コマンド実行が必要か |
| `high_risk` | noul | 誤操作が取り返しにくいか（削除・デプロイ・秘密情報・本番・force-push） |
| `needs_strong_model` | noul | 小さなローカル coder より強いモデルが必要か |
| `route` | choice | `coding` / `reasoning` / `general` のどの profile で処理するか |

単一の 4 択より、独立した noul に分解したほうが校正が良いという Jev サンプルの知見に合わせています。

</details>

---

## 🧠 設計メモ

CodeRouter の実装を読んで確定させた点です。

<details open>
<summary><b>profile の allowlist は飾りではない</b></summary>

ingress の profile 検証（`ingress/anthropic_routes.py`）は input filter より**前**に終わっています。
ここで providers.yaml に無い名前を書くと検証をすり抜け、`config.profile_by_name` の `KeyError`（= HTTP 500）になります。
`profiles_available` を設定すれば、未知の profile は無視されて warn ログだけが出ます。

</details>

<details>
<summary><b>profile の上書きが効く理由</b></summary>

input filter は `FallbackEngine._generate_anthropic_impl` でチェーン解決の**前**に走り、
新しいオブジェクトを返すと M11 の prepared dispatch が破棄されてチェーンが再解決されます。
`auto_router` の判定より**後**なので、この plugin の判断が勝ちます。

</details>

<details>
<summary><b>entry point は 2 グループ = 2 インスタンス</b></summary>

loader はグループごとに `cls(**config)` するため、observer は filter とは別オブジェクトになります。
判定はクラス変数（直近 32 件）で共有し、observer は state のフィンガープリントで突き合わせます。
routing だけでよければ `pyproject.toml` の `coderouter.observer` の行を消してください。

</details>

<details>
<summary><b><code>append</code> が既定の理由</b></summary>

Claude Code は `system` を `cache_control` 付きのブロック配列で送ります。
先頭に差し込むとキャッシュ prefix が変わって prompt cache が無効になるため、既定では末尾に足します。

</details>

<details>
<summary><b>確率は logit 直読みではない</b></summary>

LocalJev は分類プロンプト + JSON schema でモデルに確率を**自己申告**させています（LocalJev の README にも明記）。
本家 Jev の校正済み確率と同一視しないでください。`min_confidence` で足切りする場合も、この自己申告値に対する閾値です。

</details>

<details>
<summary><b>レイテンシ</b></summary>

判定 1 回につき upstream の生成が 1 回走ります。`first_user_only: true` を外すと毎ターン乗ります。

</details>

---

## 🧪 テスト

```bash
uv pip install -e ".[dev]"
python3 -m pytest -q                                   # 17 passed
LOCALJEV_BASE_URL=http://127.0.0.1:8080 python3 scripts/live_smoke.py "テストを直して"
```

`scripts/stub_upstream.py` は LocalJev の upstream を模したスタブで、実モデル無しで配線を確かめるのに使えます。

## 📁 構成

```
coderouter_plugin_localjev/
├── plugin.py      # LocalJevPlugin（InputFilter + Observer）
├── client.py      # /v1/systemone を叩く標準ライブラリだけの HTTP クライアント
├── defaults.py    # 既定の質問セットと profile_map
└── __init__.py
examples/providers.localjev.yaml   # providers.yaml の最小構成
scripts/live_smoke.py              # 実 LocalJev への疎通確認
scripts/stub_upstream.py           # upstream スタブ
tests/test_plugin.py
```

## 🔗 関連

- [CodeRouter](https://github.com/zephel01/CodeRouter) — ローカル LLM で Claude Code を動かすためのルーター本体
- [githubnext/localjev](https://github.com/githubnext/localjev) — Jev System One のローカル実装

## 📄 ライセンス

[MIT](./LICENSE)
