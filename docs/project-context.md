# ТокТок туслах — төслийн контекст (шинэ чатанд үргэлжлүүлэх)

> Шинэ Claude чат эхлэхдээ энэ файлыг уншуулаад "энэ төслийг үргэлжлүүлье" гэж хэлнэ.
> TokTok-ийн техникийн багт өгөх заавар: `docs/handoff-toktok.md`. Сүүлд шинэчилсэн: 2026-10-15.

## 1. Юу вэ
TokTok (toktok.mn, хоол хүргэлт) компанийн **хүргэгч, ресторан (харилцагч)** болон **операторуудад**
зориулсан AI туслах. Захиалагчийн бот одоохондоо зогссон (код нь бий, `/demo`-д нуусан).
- TokTok-ийн одоогийн вэб, аппад **хавчуулагдана**: ресторан вэбэд `widget.js` (баруун доод буланд шар
  дүрс → popup), хүргэгчийн аппад "Тусламж" товч → WebView, операторын вэбэд (cc.toktok.mn) цэс.
- **Operation баг** (оператор) мэдлэгийн сан болон ботын зааврыг өөрсдөө засна, **админ** батална.
- Чат бүр PostgreSQL-д хадгалагдана (ирээдүйн анализд чухал).

## 2. Бүтэц
```
frontend (Next.js 16, порт 3000)          backend (Python FastAPI + LangGraph, порт 8000)
  /                → popup туршилт          мессеж → хамгаалалт → ангилал → мэдлэг хайх → AI хариулт
  /widget-demo.html  (TokTok сайтын дуураймал)   → хэрэгсэл (оператор руу шилжүүлэх гэх мэт)
  /embed           → popup / WebView дотрох чат
  /operator        → Чат | Мэдлэгийн сан | Ботын заавар (ноорог бичих, илгээх)
  /admin           → Үзүүлэлт | Яриа | Мэдлэгийн сан | Хянах | Ботын заавар | Сайжруулалт | Ажилтнууд | Аудит
  /demo            → хуучин бүтэн дэлгэцийн чат
Өгөгдөл: PostgreSQL 16 + pgvector (байнгын), Redis 7 (идэвхтэй яриа 24ц), Qdrant 1.19 (хайлтын индекс)
AI: одоо Google Gemini (үнэгүй, туршилт). Claude-д ч бэлэн. Эцсийн сонголтыг TokTok шийднэ.
```
Гол файлууд:
| Юу | Хаана |
|---|---|
| Ботын урсгал | `backend/app/orchestrator/graph.py`, `service.py` |
| Түгжээтэй дүрэм + засагдах зааврын анхны утга | `backend/app/personas/prompts.py` |
| Мэдлэгийн сан / ботын заавар (ноорог→хянах→нийтлэх) | `backend/app/cms/service.py`, `store.py` |
| TokTok-той холбох цэг (одоо хуурамч өгөгдөл) | `backend/app/tools/gateway.py` (`TokTokGateway`) |
| Чатын түүх, жагсаалт, CSV | `backend/app/memory/repository.py` |
| AI (Gemini, нөөц загвар) | `backend/app/llm/gemini_provider.py` |
| Эрх | `backend/app/staff/store.py` (operator, admin) |
| Popup | `frontend/public/widget.js` |
| Чатын цонх | `frontend/components/ChatWindow.tsx`, `RichText.tsx` |

## 3. Гол шийдвэрүүд (яагаад)
- **Эрх 2:** *operator* = чат + мэдлэг/зааврын ноорог; *admin* = батлах, ажилтан, аудит, үзүүлэлт, яриа.
  Батлах зөвхөн `/admin`-д (оператор хуудсанд админ байсан ч батлах товч гарахгүй). Админ өөрийн өөрчлөлтийг батлаж болно.
- **Ботын заавар 2 хэсэг:** түгжээтэй (аюулгүй байдал, кодонд) + засагдах (өнгө аяс, хэл, сэдэв; `/operator`-оос).
  Хариултын хэл бизнесийн шийдвэр тул засагдах хэсэгт. Зөрчилдвөл түгжээтэй давамгайлна.
- **Шат:** 1-р шат `GATEWAY_MODE=faq_only` — хувийн мэдээллийн хэрэгсэлгүй, нэргүй хэрэглэгч
  (`anon:<persona>:<random>`), TokTok зөвхөн скрипт нэмнэ. 2-р шат — TokTok API + хэрэглэгч таних (JWT санал болгосон).
  `ENV=prod` үед `mock` (хуурамч өгөгдөл) хориотой.
- **Чат хадгалах:** PostgreSQL `conversations`, `turns` (хувийн мэдээллийг нууцалж: утас → `[PHONE]`), `handoff_messages`.
  Хэрэглэгчийн ID хэшлэгдэнэ. Хадгалах хугацааг TokTok шийднэ (хараахан автомат устгалгүй).
- **Тусдаа сан:** чатбот TokTok-ийн санд хүрэхгүй, өөрийн PostgreSQL-тэй.
- **Өнгө:** TokTok шар `#FFB600`, ногоон `#82B812`, цайвар. Шар дээр хар бичиг (уншигдахуйц).

## 4. Одоогийн байдал
Хийгдсэн, туршсан (68 автомат тест, жинхэнэ PostgreSQL/Redis/Qdrant дээр ч):
мэдлэгийн сан + ботын заавар засах/батлах/буцаах, оператор руу шилжүүлэх ба операторын хариу шууд ирэх,
popup-ын уншаагүй тоо, refresh хийхэд яриа сэргэх, тодруулсан бичиг, `/admin` → Яриа (шүүх, CSV),
операторын мэдэгдэл (табын гарчиг, тоо, дуу, Windows мэдэгдэл), IP хязгаар, "зөвхөн FAQ" горим.

2026-10-16 (branch `feature/chat-history-admin`, 79 тест): оператор дээр "Түүх" таб ба нэмэлт шүүлтүүр
(intent, ангилал, оператор, алдаа, ID хайлт); мэдлэгийн сан — шүүлт, эрэмбэ, хуудаслалт, агуулгаар хайх,
давхардал/зөрчил шалгах (`cms/quality.py`), ашиглалт/хуучирсан баримт, Excel/CSV оруулах (`samples/kb-import-sample.xlsx`);
"Хянах" → "Хянасан түүх" (аудит логоос); эх сурвалж тодорхойлох (full горим) + `scripts/backfill_sources.py`;
"git"/"default" зохиогчийг "Анхны жишээ"/"Анхны заавар" гэж харуулна; refresh-гүй шинэчлэлт (10 сек);
хэрэглэгчийн чат `localStorage`-д (таб хаасан ч үлдэнэ, "Шинэ яриа" цэвэрлэнэ); оператор хүлээж байхад "Шинэ яриа"
дарвал хүсэлт хаагдана (`POST /api/chat/handoff/close`); Jev (TypeSafe) туршилтын модуль `routing/jev.py` +
`scripts/compare_router.py` — `TYPESAFE_API_KEY` (кредит шаардлагатай) оруулбал ажиллана, чатад холбогдоогүй.

PR: #1 merge хийгдсэн (2026-10-15). Үүнээс хойших ажил `feature/chat-history-admin` branch-д (PR хараахан нээгээгүй).

## 5. Үлдсэн ажил
Манай тал:
- TokTok API ирэхэд `gateway.py`-г холбох, токен (JWT) шалгах.
- Чатын хадгалах хугацаа (TokTok шийдвэл автомат устгал).
- Чанарын үнэлгээ: 24 сценари (`backend/evals/`) жинхэнэ загвараар, мэдлэгийн хайлтын тест.
- Ботын зааврын хэв маяг (илүү үг, өөрөө хийх алхмыг эхэлж хэлэх) — Operation багтай.
TokTok тал (`docs/handoff-toktok.md`): AWS (RDS, ElastiCache, Qdrant), домэйн `chat.toktok.mn`, Microsoft 365 SSO,
AI төлбөртэй түлхүүр, скрипт/цэс/апп товч нэмэх, нөөцлөлт, хуулийн зөвшөөрөл.
Operation баг: мэдлэгийн сангийн `[X₮]` орлуулагчуудыг бөглөх.

## 6. Компьютер дээрээ ажиллуулах (Windows)
```powershell
docker compose up -d postgres redis qdrant          # D:\Toktok_chatbot
cd backend; .\.venv\Scripts\python -m uvicorn app.main:app --port 8000 --loop asyncio:SelectorEventLoop
cd frontend; npm run dev                             # http://localhost:3000
```
- `.env` (git-д ордоггүй): `LLM_PROVIDER=gemini`, `GEMINI_API_KEY`, `AGENT_MODEL=gemini-3.5-flash`,
  `CLASSIFIER_MODEL=gemini-3.5-flash-lite`, `GEMINI_FALLBACK_MODELS=["gemini-3.1-flash-lite"]`,
  `DATABASE_URL=postgresql://toktok:toktok@localhost:5432/toktok_chatbot`, `REDIS_URL=redis://localhost:6379/0`,
  `QDRANT_URL=http://localhost:6333`. Жагсаалт: `.env.example`.
- Windows дээр `--loop asyncio:SelectorEventLoop` заавал (psycopg Proactor-д ажилладаггүй).
- Нэвтрэх (хөгжүүлэлт): имэйл + эрх (`operator`/`admin`). Туршилтын токен: `demo-driver`, `demo-driver-new`, `demo-client`.
- Өгөгдлийн сан харах: Adminer http://localhost:8081 (System **PostgreSQL**, server `postgres`, `toktok`/`toktok`, `toktok_chatbot`).
- Тест: `cd backend; .\.venv\Scripts\python -m pytest -q`
  (жинхэнэ сантай: `TEST_DATABASE_URL=postgresql://toktok:toktok@localhost:5432/toktok_test`, `TEST_REDIS_URL=redis://localhost:6379/1`, `TEST_QDRANT_URL=http://localhost:6333`).
- Docker асахгүй бол: `%LOCALAPPDATA%\Docker\run` болон `%LOCALAPPDATA%\docker-secrets-engine` хавтсыг өөр нэрээр сольж дахин асаа
  (Windows дээр гацсан socket файл). Өөр төсөл (CVAT) Docker-той хамт асдаг: `docker compose -p cvat stop`.

## 7. Хамтран ажиллах дүрэм (хэрэглэгчийн хүсэлт)
- Хариултыг **монголоор**, энгийн тайлбартай.
- **Push / PR / merge-ийг зөвхөн тодорхой зөвшөөрлөөр.** Эхлээд компьютер дээрээ хийж, үр дүнг харуулна.
- Ахлах PR-ийг шалгаж merge хийнэ. Үндсэн branch: `claude/modest-tesla-rguq34` (`main` байхгүй).
- Нууц түлхүүр (`.env`, `.env.*`) хэзээ ч git-д оруулахгүй.
