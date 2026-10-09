# ТокТок AI туслах — TokTok サポートチャットボット

RFP「20260831 RFP training request for Khyazgaargui」の **1〜3章（Driver / Client / Customer 向けチャットボット）** を実装したリポジトリです。
TokTok（www.toktok.mn, フードデリバリー）のアプリ・Web に埋め込む前提で、モンゴル語（キリル文字・ラテン文字表記の両方）に対応します。

```
[クライアント UI]  Next.js 16 / React 19
   /           デモ（ペルソナ切替）
   /embed      TokTok アプリ・Web に iframe / WebView で埋め込むウィジェット
   /admin      Operation team 用（KPI・エスカレーション一覧・ナレッジ編集）
        │  SSE（fetch + ReadableStream でトークン単位ストリーミング）
        ▼
[バックエンド / オーケストレーション]  FastAPI + LangGraph
   ① ガードレール & ルーティング
        - 入力: カード番号/パスワードのマスク、プロンプトインジェクション検知（英語・モンゴル語）
        - 意図分類: Claude Haiku 5.5（Structured Outputs）→ 失敗時はキーワードルーター
        - ラテン文字モンゴル語 → キリル文字の検索クエリに正規化
   ② エージェント推論: Claude Opus 5.5 + Tool Use（ストリーミング・ツールループ）
        ├─ ナレッジ検索: Qdrant ハイブリッド検索（BM25 スパース + 密ベクトル、サーバー側 RRF 融合）
        └─ 外部 API 実行: 注文状況、住所変更、受取方法変更、加盟店リクエスト、オペレーター転送
   ③ セッション・メモリ: Redis（会話履歴・ロック・レート制限）
                         PostgreSQL（ログ・フィードバック・エスカレーション・ナレッジ本文）
                         Qdrant（検索索引。全レプリカで共有）
        │
[基盤モデル]  Claude（Anthropic 公式 SDK）。LLM 層は LLMProvider インターフェースで差し替え可能
        │
[オブザーバビリティ / 評価]  Langfuse（トレース・ユーザー評価スコア）、evals/（回帰テストセット）
```

## RFP 要件との対応

| RFP 要件 | 実装 |
|---|---|
| Level 1 Direct Answer | 検証済みナレッジ（`backend/knowledge/`）のみを根拠に回答。システムプロンプトで「ナレッジにない規則・金額は作らない」を強制 |
| Level 2 Guided Resolution | ナレッジのトラブルシュート手順を番号付きで提示（例: アプリが動かない、通知が来ない） |
| Level 3 Escalation | `escalate_to_human` ツールがチケットを作成し、ペルソナ別のオペレーター電話番号を返す。UI に発信ボタン付きカードを表示 |
| 推測で答えない | ナレッジ未ヒット時は再検索 → それでも無ければエスカレーション。プレースホルダー値（`[X₮]`）は「オペレーターに確認」と回答 |
| モンゴル語で簡潔に | 全ペルソナ共通ルール＋ペルソナ別トーン（ドライバーは最短・最重要アクション先頭） |
| ラテン文字表記の理解 | 分類モデルがキリル文字クエリに書き換え＋BM25 は表記非依存の音韻キーで索引（`zahialga` = `захиалга`） |
| 不明確な質問には聞き返す | ルーターの `needs_clarification` とプロンプト規則 |
| 内部情報の保護 | ツールは認証済みユーザー ID でのみデータ取得（他人の注文は「見つかりません」）、`visibility: internal` 文書は索引しない、ログは PII マスク・ユーザー ID はハッシュ化 |
| Operation team がナレッジ更新 | `/admin` で編集 → 即時再索引。Git 管理の Markdown を DB 上の編集で上書き可能 |
| 回答の監視・改善 / 解決率分析 | 👍👎 フィードバック、`/admin` で解決率（目標 ≥70%）・満足度（目標 ≥85%）・頻出インテント・遅延、Langfuse トレース |
| Customer: 注文はどこ・いつ届く | `list_my_recent_orders` / `get_order_status`（ETA・ドライバー段階） |
| Customer: 住所間違い・配達⇔受取 | `change_delivery_address` / `switch_fulfillment_type`。**ユーザーが別ターンで明示確認した場合のみ実行**（サーバー側で強制） |
| Client: 営業時間・一時休業・ユーザー追加 等 | `submit_merchant_request` で Partner team へ構造化リクエスト（口座変更はチャットでは不可） |
| Client: 支店が表示されない・精算 | `get_my_merchant_profile` / `get_settlement_summary` |
| Driver: 新人オンボーディング | `get_my_driver_profile` で未完了ステップ・書類状況を確認して案内 |

RFP の 4章以降（CRM セグメンテーション、メニュー OCR、ルート最適化、制服チェック等）は今回の範囲外です。

## ディレクトリ

```
backend/
  app/
    llm/            LLMProvider インターフェース、Claude 実装、オフライン用 fake
    orchestrator/   LangGraph グラフ（guard → route → retrieve → agent → finalize）とチャットサービス
    guardrails/     入力チェック・PII マスク・ラテン⇔キリル正規化
    routing/        インテント定義（RFP のユースケース一覧）とルーター
    rag/            Markdown 文書・チャンク分割・BM25・ベクトル索引・ハイブリッド検索
    tools/          ツール定義、TokTok ゲートウェイ（現在はモック）
    memory/         セッションストア（Redis）、リポジトリ（PostgreSQL）
    personas/       ペルソナ別システムプロンプト
    observability/  Langfuse トレーサー
  knowledge/        ナレッジ文書（driver / client / customer）※現在はサンプル
  evals/            回帰評価セット（RFP ユースケース 24 件）と実行スクリプト
  db/schema.sql     PostgreSQL スキーマ（起動時に自動適用）
frontend/           Next.js（デモ・埋め込みウィジェット・管理画面）
```

## 起動方法

### Docker Compose（推奨）

```bash
cp .env.example .env          # ANTHROPIC_API_KEY を設定
docker compose up --build
# http://localhost:3000        デモ
# http://localhost:3000/admin  管理画面（ADMIN_API_KEY）
```

### ローカル開発

```bash
# バックエンド
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp ../.env.example ../.env    # DATABASE_URL / REDIS_URL を空にするとインメモリで動作
uvicorn app.main:app --reload --port 8000

# API キーなしで UI を触る場合（応答はダミー、理解はしない）
LLM_PROVIDER=fake uvicorn app.main:app --port 8000

# フロントエンド
cd frontend && npm install && npm run dev
```

デモ用トークン（モックゲートウェイ）: `demo-customer`、`demo-driver`、`demo-driver-new`、`demo-client`。

### テスト・評価

```bash
cd backend
pytest -q                                   # ユニット + API（Claude SDK はモック HTTP で実 SDK を通す）
TEST_DATABASE_URL=postgresql://... TEST_REDIS_URL=redis://... TEST_QDRANT_URL=http://localhost:6333 pytest -q   # 結合テスト
python -m evals.run_eval                     # 実際の Claude で RFP シナリオ 24 件を評価（API キー必要・課金あり）
python -m evals.run_eval --only cli-         # 加盟店ケースのみ
```

## TokTok 本番システムとの接続

1. **ゲートウェイ実装**: `backend/app/tools/gateway.py` の `TokTokGateway` プロトコルを、TokTok の注文・ドライバー・加盟店 API（または DB）で実装し、`MockTokTokGateway` と差し替えます。`resolve_user(token)` で TokTok のアプリトークンを検証してユーザー（ペルソナ・ID）を返す部分が認証の起点です。
2. **アプリ / Web への埋め込み**: `/embed` を iframe・WebView で開き、ホスト側からトークンを `postMessage` で渡します（URL にトークンを載せない）。許可するオリジンは `NEXT_PUBLIC_EMBED_PARENT_ORIGINS` と `EMBED_ALLOWED_ORIGINS`（CSP `frame-ancestors`）で設定します。

   ```js
   const frame = document.getElementById("toktok-chat");
   window.addEventListener("message", (e) => {
     if (e.data?.type === "toktok-chat-ready")
       frame.contentWindow.postMessage({ type: "toktok-auth", token: userToken, persona: "customer" }, CHATBOT_ORIGIN);
   });
   ```
3. **ナレッジの差し替え**: `backend/knowledge/` の文書はすべて構成見本です（金額・日数は `[X₮]` などのプレースホルダー）。Operation / Partner / Finance 各チームの正式な情報に置き換えてください。`visibility: internal` の文書はチャットボットに一切渡りません。

## モデル・コストの設計判断

- **応答生成**: `claude-opus-5-5`、`effort: low`（チャット用途。eval で品質が上がる場合のみ引き上げ）。thinking は Opus 5.5 では常時オン（無効化は API が 400 を返す）。
- **意図分類**: `claude-haiku-5-5`（$0.10 / $0.50 per MTok）で Structured Outputs。
- **プロンプトキャッシュ**: システムプロンプトとツール定義をペルソナごとに固定し、自動キャッシュを有効化。ターンごとの情報（検索結果・時刻・ルーティング結果）は会話途中の `role: "system"` メッセージとして追記し、プレフィックスを壊さない。
- **履歴は追記のみ**: Opus 5.5 の thinking ブロックは直前までの会話に紐づくため、過去ターンを書き換えない。長い会話（既定 20 ターン超）は境界で一度だけ thinking を除去して切り詰め。
- **拒否時のフォールバック**: `fallbacks: "default"`（サーバー側で推奨モデルに自動再実行）。Bedrock / Vertex では `LLM_REFUSAL_FALLBACKS=false`。
- **検索索引（Qdrant）**: キーワード検索はラテン/キリル表記を同一キーにそろえた独自特徴量をスパースベクトル化し、Qdrant の IDF で BM25 を計算。密ベクトルと 1 回のクエリで RRF 融合します。索引が全レプリカ共有なので、管理画面での編集は即座に全サーバーへ反映されます。`VECTOR_STORE=pgvector` で従来方式にも切替可能。
- **埋め込み**: Claude には埋め込み API がないため、ベクトル検索は Voyage AI（`EMBEDDING_PROVIDER=voyage`）。未設定時はキーワード検索（BM25）のみで動作。

## 未検証の点・次のステップ

- この環境には Anthropic / Voyage / Langfuse の API キーがないため、**実モデルでの応答品質（evals）と Voyage・Langfuse との実通信は未確認**です。Claude 呼び出しは実 SDK をモック HTTP で通すテストで検証済み。まず `python -m evals.run_eval` で基準値を取ってください。
- TokTok 実 API の仕様が届き次第、ゲートウェイを実装。
- ナレッジを正式情報に置き換え、低評価（👎）の会話を `evals/cases.yaml` に追加して回帰テストを育てる。
- RFP 4章以降（CRM セグメント、メニュー OCR など）は別フェーズ。
