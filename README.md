# ТокТок AI туслах — TokTok サポートチャットボット

RFP「20260831 RFP training request for Khyazgaargui」の **1〜3章（Driver / Client / Customer 向けチャットボット）** と、その運用基盤（ナレッジCMS・有人引き継ぎ・改善ループ）を実装したリポジトリです。
TokTok（www.toktok.mn, フードデリバリー）のアプリ・Web に埋め込む前提で、モンゴル語（キリル文字・ラテン文字表記の両方）に対応します。

```
[利用者]  /embed（アプリ・Web埋め込み）  /（デモ）
     │ SSE ストリーミング / 有人対応中はポーリング
[スタッフ]  /admin（Operation team）  /operator（オペレーター）   ← SSO（Google / Microsoft）
     │
[FastAPI + LangGraph]
   ① ガードレール → 意図分類（Claude Haiku 5.5）
   ② ナレッジ:  規模に応じて自動切替
        ・全文モード: 公開済みFAQ全体をキャッシュ付きシステムプロンプトへ（検索漏れなし）
        ・RAGモード : Qdrant ハイブリッド検索（BM25 + ベクトル, RRF）＋任意で Rerank / Contextual Retrieval
   ③ エージェント（Claude Opus 5.5 + Tool Use）→ 注文確認・住所変更・加盟店リクエスト・有人引き継ぎ
   ④ 有人引き継ぎ: 同じチャットでオペレーターが対応 → 解決後ボットに戻る（会話内容を引き継ぐ）
   ⑤ 評価: 一部の回答を AI 審査員が採点（根拠性）→ 改善ループが FAQ 下書きを作成
     │
[データ]  PostgreSQL（ログ・ナレッジ版管理・スタッフ・監査・引き継ぎ）
          Redis（会話セッション・ロック・レート制限・ナレッジパック）
          Qdrant（検索索引。全レプリカ共有）
[可観測性]  Langfuse（トレース・ユーザー評価・AI審査スコア）
```

## RFP 要件との対応

| RFP 要件 | 実装 |
|---|---|
| Level 1 Direct Answer | 検証済みナレッジのみを根拠に回答。ナレッジにない規則・金額は作らない |
| Level 2 Guided Resolution | ナレッジのトラブルシュート手順を番号付きで提示 |
| Level 3 Escalation | 同じチャット内でオペレーターに引き継ぎ（`/operator`）。緊急時用に電話番号も表示 |
| 推測で答えない | 全文モードでは検索漏れ自体がない。RAG では再検索 → それでも無ければエスカレーション。`[X₮]` などの未確定値は「オペレーターに確認」と回答 |
| ラテン文字表記の理解 | 分類モデルがキリル文字に書き換え＋検索は表記非依存の音韻キー（`zahialga` = `захиалга`） |
| 内部情報の保護 | ツールは認証済み本人のデータのみ。`internal` 文書はボットに渡らない。ログは PII マスク・ID ハッシュ化 |
| **Operation team がナレッジ更新** | `/admin` の CMS: 下書き → 承認 → 公開、版管理・差し戻し・アーカイブ、Excel/CSV 一括取込、公開前プレビュー |
| 回答の監視・改善 / 解決率分析 | 解決率（目標 ≥70%）・満足度（目標 ≥85%）・根拠性（AI 審査）・頻出インテント、改善ループで不足 FAQ を下書き |
| オペレーター番号へつなぐ | 有人引き継ぎ（チャット内）＋電話番号 |
| Customer: 注文状況・住所変更・配達⇔受取 | ツールで実行。変更系は**ユーザーが別ターンで明示確認した場合のみ**（サーバー側で強制） |
| Client: 営業時間・休業・ユーザー追加 等 | Partner team への構造化リクエスト（口座変更はチャット不可） |
| Driver: 新人オンボーディング | プロフィールの未完了ステップを確認して案内 |

RFP 4章以降（CRM セグメント、メニュー OCR、ルート最適化、制服チェック）は別フェーズです。

## スタッフの使い方

### 権限

| ロール | できること |
|---|---|
| オペレーター | `/operator` で有人対応（受け取り・返信・解決）。同じ画面の「Мэдлэгийн сан」「Ботын заавар」タブでナレッジとボット指示の下書き作成・編集・提出、一括取込、プレビュー |
| 管理者 | すべて（`/admin`）: 承認/差し戻し・公開、ロールバック、アーカイブ、指標、改善分析、スタッフ管理、監査ログ。自分の変更も承認できます |

オペレーターが提出した変更は管理者が承認して初めてボットに反映されます。ログインは会社の Google / Microsoft アカウント（SSO）です。

### ナレッジ更新の流れ（`/admin` → Мэдлэгийн сан）

1. 文書を開いて編集 →「Ноорог хадгалах（下書き保存）」
2. 「Урьдчилж харах（プレビュー）」で、公開した場合のボットの回答を確認
3. 「Хянуулахаар илгээх（レビュー提出）」
4. 管理者が `/admin` の「Хянах（レビュー）」タブで差分を確認 →「Батлах（承認）」で即時公開
   差し戻しにはコメントが必須です。
5. 間違えた場合は「Түүх（履歴）」から任意の版に戻せます（履歴は上書きされません）

- **Excel / Google スプレッドシートから一括登録**: 列は `persona, question, answer`（任意で `id, tags, visibility`）です。取り込んだ行はレビュー待ちになります。
- 公開中の会話にも、次のメッセージから更新内容が反映されます（`<knowledge_updates>` として追記）。

### ボットの指示（`/admin` → Ботын заавар）

システムプロンプトは 2 部構成です。

- **固定ルール**（コードで管理、開発者のみ変更）: 根拠のない回答の禁止、個人情報保護、確認が必要な操作、緊急時のエスカレーション等。
- **運用指示**（ペルソナごと、Operation team が編集）: 口調、回答の長さ、重点トピック、エスカレーションの方針、ツールの使い方。固定ルールと矛盾した場合は固定ルールが優先されます。

運用指示はナレッジと同じ流れ（オペレーターが下書き・提出 → 管理者が承認して公開、履歴から差し戻し）で管理し、監査ログには `prompt.*` として記録されます。公開した内容は**新しく始まる会話**から適用されます（進行中の会話はシステムプロンプトを固定したまま）。プレビューでは、同じ質問に対する現在の指示と編集中の指示の回答を並べて比較できます。初期値は `backend/app/personas/prompts.py` の `DEFAULT_INSTRUCTIONS` で、初回起動時に v1 として登録されます。

### 有人対応（`/operator`）

ボットがエスカレーションすると「Хүлээгдэж буй（待機中）」に表示されます。画面では、ボットとの会話履歴と AI による要約を確認できます。

1. 「Хариуцаж авах（担当する）」で担当になります。
2. 返信すると、利用者のチャット画面に届きます。
3. 「Шийдсэн（解決）」を押すとボットに戻ります。解決メモとやり取りの内容はボットに引き継がれます。

既存のヘルプデスクを使う場合は、`HANDOFF_WEBHOOK_URL` に設定すると全イベントが HMAC 署名付きで転送されます。

### 改善ループ（`/admin` → Сайжруулалт）

オペレーターに回った質問・👎 が付いた回答・AI 審査で「根拠なし」とされた回答を、トピック別に分類します。ナレッジに無いトピックについては、FAQ の下書きを自動作成します。

- 下書きは**自動公開されません**。
- 料金・期限など AI が知り得ない値は `[ТОДРУУЛАХ: …]` として残るので、Operation team が埋めてからレビューに出します。

## 起動方法

### Docker Compose（推奨）

```bash
cp .env.example .env     # ANTHROPIC_API_KEY、OIDC_*、STAFF_SESSION_SECRET（32文字以上）等を設定
docker compose up --build
# http://localhost:3000           デモ（利用者）
# http://localhost:3000/admin     Operation（ナレッジ・指標・改善）
# http://localhost:3000/operator  オペレーター
```

### ローカル開発（API キー・SSO なしでも動作）

```bash
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
LLM_PROVIDER=fake uvicorn app.main:app --port 8000   # DB/Redis/Qdrant 未設定ならインメモリ
# Windows で DATABASE_URL を使う場合: psycopg は Proactor ループ非対応のため
#   uvicorn app.main:app --port 8000 --loop asyncio:SelectorEventLoop
cd frontend && npm install && npm run dev
```

- 開発環境（`ENV=dev`）では、`/admin` と `/operator` に開発用ログイン（任意の メール + ロール）が表示されます。本番では無効です。
- 利用者のデモトークン: `demo-customer`、`demo-driver`、`demo-driver-new`、`demo-client`

### SSO の設定

| IdP | 設定 |
|---|---|
| Google Workspace | Google Cloud Console → OAuth クライアント（Web）。リダイレクト URI に `https://<api>/api/auth/callback`。`OIDC_ISSUER=https://accounts.google.com` |
| Microsoft Entra ID | アプリ登録 → Web リダイレクト URI に同上。`OIDC_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0` |

最初の管理者は `STAFF_BOOTSTRAP_ADMINS=["you@toktok.mn"]` で作成します。以降のスタッフは `/admin` → Ажилтнууд で追加してください。`OIDC_ALLOWED_DOMAINS` 以外のドメインはログインできません。

### テスト・評価

```bash
cd backend
pytest -q                     # ユニット + API + SSO（偽 IdP）+ 引き継ぎ + CMS
TEST_DATABASE_URL=... TEST_REDIS_URL=... TEST_QDRANT_URL=... pytest -q   # 実ストレージ結合テスト
python -m evals.run_eval --min-pass-rate 0.85   # 実モデルで RFP シナリオ 24 件（API キー・課金あり）
```

CI では `ci.yml` がテストとビルドを実行します。`evals.yml` はプロンプト・ナレッジ・ツールの変更時に実モデル評価を行い、合格率 85% 未満で失敗します。リポジトリシークレット `ANTHROPIC_API_KEY` を設定すると有効になります。

## TokTok 本番システムとの接続

1. **ゲートウェイ**: `backend/app/tools/gateway.py` の `TokTokGateway` を TokTok の API / DB で実装し、`MockTokTokGateway` と差し替えます。`resolve_user(token)` が利用者認証の起点です。
2. **埋め込み**: `/embed` を iframe / WebView で開き、ホストから `postMessage({type: "toktok-auth", token, persona})` でトークンを渡します（URL にトークンを載せません）。
3. **ナレッジ**: `backend/knowledge/` の文書は初回起動時に v1 として取り込まれる**見本**です（金額は `[X₮]` 等のプレースホルダー）。以降は CMS が正本になります。

## 設計判断

- **モデル**: 応答は `claude-opus-5-5`（`effort: low`）、分類と AI 審査は `claude-haiku-5-5`、改善分析は Opus。拒否時は `fallbacks: "default"` で自動フォールバックします。
- **全文モード（既定 auto）**: FAQ が `FULL_CONTEXT_MAX_TOKENS`（既定 5万）以下ならシステムプロンプトに全文を入れます。Opus 5.5 のキャッシュ読込は $0.20/MTok なので、5万トークンでも 1 ターン約 $0.01 です。システムプロンプトは会話ごとに固定し、更新は追記で届けるため、キャッシュと thinking の整合性が保たれます。
- **履歴は追記のみ**: Opus 5.5 の thinking ブロックは直前の会話に紐づくため、過去ターンは書き換えません。
- **Qdrant**: キーワード検索（表記ゆれ吸収済み）をスパースベクトル化し、密ベクトルとサーバー側で RRF 融合します。索引は全レプリカで共有し、公開の変化はリビジョン番号で各レプリカが検知します。
- **セキュリティ**: スタッフは SSO（PKCE・state・nonce・JWKS 検証）を使い、Cookie 認証の書き込みには CSRF ヘッダーを必須にしています。全操作は監査ログに記録されます。本番起動時は、秘密情報が既定値のまま・SSO 未設定・fake LLM・DB/Redis 未設定のいずれかがあると起動を拒否します。

## 未検証の点

- この環境には Anthropic / Voyage / Langfuse / 実 IdP の資格情報がないため、次は未確認です。
  - 実モデルでの応答品質（evals）
  - Voyage（埋め込み・リランク）、Langfuse、実際の Google / Microsoft ログインとの通信
- Claude 呼び出しは実 SDK をモック HTTP で、SSO は RS256 署名の偽 IdP で、ストレージは実 PostgreSQL 16 + pgvector / Redis / Qdrant 1.19 で検証済みです。画面はブラウザで、利用者 → オペレーター → 解決 → ボット復帰、編集 → レビュー → 承認、の流れを確認しました。
- TokTok 実 API との接続は、仕様の受領待ちです。
