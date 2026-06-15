# Dr. Yas – Iraniano Smart Assistant
### Python / FastAPI port of the `[CHATBOT][LIVE]` n8n workflow

---

## Architecture

```
Browser / Telegram
       │
       ▼
  FastAPI (main.py)
       │
       ├── POST /chat ──► pipeline:
       │                   1. Enrich query (follow-up detection)
       │                   2. OpenAI text-embedding-3-small
       │                   3. Qdrant vector search (app_posts)
       │                   4. Build context + share links
       │                   5. Gemini 2.5 Flash generation
       │                   6. Clean HTML + append iraniu links
       │                   7. Save to Postgres chat memory
       │
       ├── GET  /           → Chat UI (RTL Farsi/English widget)
       ├── GET  /admin      → Admin panel (config + run monitor)
       ├── GET  /admin/config   → read config (JSON)
       ├── POST /admin/config   → update config live (no restart)
       ├── GET  /admin/runs     → recent run log (JSON)
       ├── POST /admin/clear-history → flush run log
       └── GET  /health     → health check
```

---

## Quick Start

### 1. Clone / copy files

```
dryas_chatbot/
├── main.py
├── requirements.txt
├── .env.example
├── Dockerfile
├── docker-compose.yml
└── templates/
    ├── chat.html
    └── admin.html
```

### 2. Configure

```bash
cp .env.example .env
nano .env   # fill in your keys
```

Minimum required:
```
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=AIza...
QDRANT_API_KEY=...
QDRANT_URL=https://irdb.iraniu.uk
```

### 3. Run locally

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

- Chat UI  →  http://localhost:8000/
- Admin    →  http://localhost:8000/admin?token=iraniano-admin-2025

### 4. Deploy with Docker (Coolify-ready)

```bash
docker compose up -d
```

Point your Coolify service to this repo. The health check endpoint
(`/health`) is pre-configured for Coolify's health monitor.

---

## Admin Panel

Open `/admin?token=YOUR_ADMIN_TOKEN`

### Monitor tab
| Column | Description |
|---|---|
| Time | UTC timestamp of the run |
| Session | Last 10 chars of session ID |
| User Message | What the user sent (truncated) |
| Answer Preview | First 400 chars of Dr. Yas's reply |
| ms | End-to-end pipeline latency |
| Qdrant | Number of hits returned from vector search |
| Status | OK / Error with error message |

Auto-refresh options: 5s / 15s / 30s / off.

### Config tab
All settings can be changed **at runtime without restarting** the server:

| Setting | Default | Notes |
|---|---|---|
| OPENAI_API_KEY | – | For embeddings |
| GEMINI_API_KEY | – | For generation |
| QDRANT_API_KEY | – | Vector DB auth |
| QDRANT_URL | https://irdb.iraniu.uk | |
| QDRANT_COLLECTION | app_posts | |
| EMBED_MODEL | text-embedding-3-small | |
| GEMINI_MODEL | gemini-2.5-flash | |
| SCORE_THRESHOLD | 0.50 | Min cosine similarity |
| TOP_K | 5 | Max Qdrant results |
| MAX_HISTORY | 10 | Chat turns to load from Postgres |
| APP_BASE_URL | https://app.iraniu.uk | Used in share links |

API keys are shown masked (***last4) and only updated if you paste a new real value.

---

## Chat API

### POST /chat

```json
{
  "message": "چطور می‌توانم ویزای UK بگیرم؟",
  "session_id": "optional-string-for-continuity",
  "first_name": "Reza"
}
```

Response:
```json
{
  "answer": "<b>ویزای UK</b>\n\nبرای دریافت ویزا...",
  "session_id": "web_abc123"
}
```

The `answer` field is HTML-safe (only `<b>`, `<i>`, `<code>`, `<a>` tags).
Pass back the `session_id` on subsequent messages to maintain conversation history.

### Embed in any webpage

```html
<!-- Minimal embed snippet -->
<script>
async function askDrYas(message, sessionId) {
  const res = await fetch('https://your-domain/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, session_id: sessionId })
  });
  return res.json();
}
</script>
```

---

## Postgres Chat Memory

Set `POSTGRES_DSN` in `.env` to enable persistent conversation history.
The app auto-creates the `dr_yas_memory` table on first run:

```sql
CREATE TABLE dr_yas_memory (
    session_id TEXT,
    role       TEXT,        -- 'user' | 'assistant'
    content    TEXT,
    ts         TIMESTAMPTZ DEFAULT NOW()
);
```

If `POSTGRES_DSN` is not set, the app runs in stateless mode (no memory between requests, but still works).

---

## n8n → Python mapping

| n8n Node | Python equivalent |
|---|---|
| `Normalize Input` | `run_pipeline()` entry — detects source, sets session_id |
| `Enrich Query` | `is_follow_up()` + query enrichment |
| `Build OpenAI Embed Request` + `Embed Question` | `embed_query()` |
| `Build Qdrant Search Body` + `HTTP Request` | `qdrant_search()` |
| `Add Share Links` | `build_context()` + `append_share_links()` |
| `AI Agent Gemini` | `call_gemini()` with same system prompt |
| `Chunking for Telegram` | Not needed for web (handled client-side) |
| `Postgres Chat Memory` | `load_history()` + `save_turn()` |

---

## Security Notes

- Change `ADMIN_TOKEN` in `.env` before deploying
- Never expose `/admin` without authentication
- API keys are never logged or returned unmasked
- The system prompt confidentiality rules are preserved from the original n8n workflow
