# coderouter-plugin-localjev

CodeRouter の Plugin SDK（`coderouter.input_filter` / `coderouter.observer`）から
[githubnext/localjev](https://github.com/githubnext/localjev) の
`POST /v1/systemone` を呼び、判定を system に注入しつつ profile を切り替える。

```
Claude Code → CodeRouter :8088
                 │ InputFilter localjev
                 │        └─ LocalJev :8080 → llama-server :8000
                 ▼
            profile = coding | reasoning | general
```

コア依存は増やさない（Python 標準ライブラリのみ）。CodeRouter 本体には一切手を入れない。

---

## 1. LocalJev を立てる（llama-server を upstream にする場合）

LocalJev が upstream に要求するのは 2 つだけで、oMLX は必須ではない
（`localjev/src/engine.ts` を実読して確認）。

| 用途 | エンドポイント | 要求 |
|---|---|---|
| `/ready` 判定 | `GET {upstream}/v1/models` | `data[].id` に `LOCALJEV_UPSTREAM_MODEL` と**完全一致**する id があること |
| 推論 | `POST {upstream}/v1/chat/completions` | `response_format: {"type":"json_schema","json_schema":{"schema":…}}` / `seed` / `chat_template_kwargs` を受けること |

llama.cpp の `llama-server` はこの 3 つすべてに対応している
（`tools/server/server-common.cpp` が `response_format.json_schema.schema` を読んで GBNF に変換、
`--alias` が `/v1/models` の `id` になる、`chat_template_kwargs` も実装済み）。

```bash
# 1) upstream: llama-server。--alias が /v1/models の id になるので必ず付ける
llama-server -m ~/models/<小さめの instruct モデル>.gguf \
  --alias jev-backend --host 127.0.0.1 --port 8000 --jinja

# 2) LocalJev
git clone https://github.com/githubnext/localjev && cd localjev
bun install
cp .env.example .env
```

`.env`:

```ini
LOCALJEV_UPSTREAM=http://127.0.0.1:8000
LOCALJEV_UPSTREAM_MODEL=jev-backend   # ← llama-server の --alias と一致させる
LOCALJEV_UPSTREAM_API_KEY=            # llama-server が --api-key 無しなら空でよい
LOCALJEV_PORT=8080
LOCALJEV_TIMEOUT=180
LOCALJEV_MAX_INFLIGHT=2
```

```bash
bun run start
curl http://127.0.0.1:8080/ready
# => {"status":"ready","upstream_model":"jev-backend"}
```

**`/ready` が 503 のとき**は LocalJev ではなく upstream 側の問題。
`curl http://127.0.0.1:8000/v1/models` の `id` と `LOCALJEV_UPSTREAM_MODEL` の文字列一致を見る
（`--alias` を付け忘れると id が gguf のフルパスになる）。

判定そのものの確認:

```bash
curl http://127.0.0.1:8080/v1/systemone -H 'Content-Type: application/json' -d '{
  "model":"jev-latest",
  "state":"add a retry to the uploader and run the tests",
  "questions":{"route":{"type":"choice","instructions":"Which profile?",
    "criteria":{"coding":"impl","reasoning":"design","general":"chat"}}}}'
```

応答は常にこの形（トップレベルに `nouls` / `choices` は存在しない）:

```json
{"model":"localjev-0.2",
 "answers":{"route":{"type":"choice","choice":"coding",
            "probabilities":{"coding":0.6,"reasoning":0.2,"general":0.2},
            "confidence":0.135}},
 "usage":{"input_tokens":123,"output_tokens":45}}
```

## 2. プラグインを入れる

```bash
uv pip install ./coderouter-plugin-localjev      # または pip install
python3 coderouter-plugin-localjev/scripts/live_smoke.py "テストを直して"
```

`~/.coderouter/providers.yaml`（全体は `examples/providers.localjev.yaml`）:

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

`plugins.enabled` に書かないと、インストールしただけでは有効にならない（二段ゲート）。
起動ログに `plugin-loaded plugin=localjev` が 2 行（input_filter と observer）出れば有効。

## 3. 設定キー

| キー | 既定 | 説明 |
|---|---|---|
| `base_url` | `http://127.0.0.1:8080` | LocalJev の origin |
| `api_key` | `""` | LocalJev 側で `LOCALJEV_API_KEY` を設定したときだけ必要 |
| `model` | `jev-latest` | LocalJev の alias（`jev-latest` / `localjev-latest` / `localjev-0.2`） |
| `timeout_s` | `20` | HTTP タイムアウト |
| `fail_open` | `true` | LocalJev 障害時に素通し |
| `first_user_only` | `true` | 1 ターン目のみ判定（毎ターンの遅延を避ける） |
| `inject` | `true` | 判定を `system` に足す |
| `inject_position` | `append` | `prepend` は prompt cache の prefix を壊す |
| `set_profile` | `true` | route choice → `request.profile` |
| `route_question` | `route` | profile に使う choice 質問の名前 |
| `min_confidence` | `0.0` | この値未満の confidence では profile を変えない |
| `profiles_available` | なし | **実在する profile の allowlist（強く推奨）** |
| `profile_map` | coding/reasoning/general | choice ラベル → profile 名 |
| `questions` | 4 問（noul×3 + choice×1） | 質問セットの差し替え |
| `state_max_chars` | `4000` | LocalJev に送る state の上限 |

## 4. 設計上の注意（CodeRouter の実装を読んで確定させた点）

- **profile の allowlist は飾りではない。** ingress の profile 検証
  (`ingress/anthropic_routes.py`) は input filter より**前**に終わっている。
  ここで providers.yaml に無い名前を書くと検証をすり抜け、
  `config.profile_by_name` の `KeyError`（= HTTP 500）になる。
  `profiles_available` を設定すれば、未知の profile は無視されて warn ログだけが出る。
- **profile の上書きが効く理由。** input filter は
  `FallbackEngine._generate_anthropic_impl` でチェーン解決の**前**に走り、
  新しいオブジェクトを返すと M11 の prepared dispatch が破棄されてチェーンが
  再解決される。なお `auto_router` の判定より**後**なので、この plugin の判断が勝つ。
- **entry point は 2 グループ = 2 インスタンス。** loader はグループごとに
  `cls(**config)` するため、observer は filter とは別オブジェクトになる。
  判定はクラス変数（直近 32 件）で共有し、observer は state のフィンガープリントで
  突き合わせる。routing だけでよければ `coderouter.observer` の行を消せばよい。
- **`append` が既定の理由。** Claude Code は `system` を `cache_control` 付きの
  ブロック配列で送る。先頭に差し込むとキャッシュ prefix が変わって
  prompt cache が無効になるため、既定では末尾に足す。
- **確率は logit 直読みではない。** LocalJev は分類プロンプト + JSON schema で
  モデルに確率を**自己申告**させている（README にも明記あり）。
  本家 Jev の校正済み確率と同一視しないこと。`min_confidence` で足切りする場合も、
  この自己申告値に対する閾値だと理解して使う。
- **レイテンシ。** 判定 1 回につき upstream の生成が 1 回走る。
  `first_user_only: true` を外すと毎ターン乗る。

## 5. テスト

```bash
cd coderouter-plugin-localjev && python3 -m pytest -q     # 17 passed
LOCALJEV_BASE_URL=http://127.0.0.1:8080 python3 scripts/live_smoke.py "テストを直して"
```

## ライセンス

MIT
