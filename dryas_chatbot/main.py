"""
Iraniano Multi-Agent Chatbot
FastAPI backend supporting multiple AI agents (Dr. Yas, Trip Planner, …)
Each agent has its own system prompt, welcome message, and suggestion chips.
All agents are manageable via /admin.

Endpoints:
  GET  /                      → agent selector UI
  GET  /chat/{agent_slug}     → chatbot UI for a specific agent
  POST /chat                  → chat API (send message)
  GET  /agents                → list enabled agents (JSON)
  GET  /admin                 → admin panel
  GET  /admin/agents          → list all agents including disabled (JSON)
  POST /admin/agents/{slug}   → update agent fields
  POST /admin/agents/{slug}/reset → reset agent to built-in default
  GET  /admin/config          → global config (JSON)
  POST /admin/config          → update global config
  GET  /admin/runs            → recent run log (JSON)
  GET  /admin/runs.csv        → export run log (CSV)
  GET  /admin/leads.csv       → export collected user leads (CSV)
  POST /admin/clear-history   → flush run log
  GET  /admin/ads             → ad videos + question threshold (JSON)
  POST /admin/ads             → update ad videos / threshold
  POST /send-transcript       → email the conversation transcript to the user
  POST /leads                 → save a collected user lead (name/email)
  GET  /health                → health check
"""

import os, time, uuid, re, json, csv, io, smtplib, ssl, asyncio
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Optional
from pathlib import Path

import httpx
import asyncpg
from openai import AsyncOpenAI
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from agents import AGENTS, get_agent, list_agents, update_agent, reset_agent, reload_agents, delete_agent

load_dotenv()

# ─────────────────────────────────────────
# Global config
# ─────────────────────────────────────────
class Config:
    OPENAI_API_KEY: str    = os.getenv("OPENAI_API_KEY", "")
    GEMINI_API_KEY: str    = os.getenv("GEMINI_API_KEY", "")
    QDRANT_URL: str        = os.getenv("QDRANT_URL", "https://irdb.iraniu.uk")
    QDRANT_API_KEY: str    = os.getenv("QDRANT_API_KEY", "")
    QDRANT_COLLECTION: str = os.getenv("QDRANT_COLLECTION", "app_posts")
    POSTGRES_DSN: str      = os.getenv("POSTGRES_DSN", "")
    EMBED_MODEL: str       = os.getenv("EMBED_MODEL", "text-embedding-3-small")
    GEMINI_MODEL: str      = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    SCORE_THRESHOLD: float = float(os.getenv("SCORE_THRESHOLD", "0.50"))
    TOP_K: int             = int(os.getenv("TOP_K", "5"))
    MAX_HISTORY: int       = int(os.getenv("MAX_HISTORY", "10"))
    APP_BASE_URL: str      = os.getenv("APP_BASE_URL", "https://app.iraniu.uk")

    # ── SMTP (end-of-chat transcript email) ────────────────
    SMTP_HOST: str         = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int         = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str         = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str     = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM_EMAIL: str   = os.getenv("SMTP_FROM_EMAIL", "")
    SMTP_FROM_NAME: str    = os.getenv("SMTP_FROM_NAME", "ایرانیو")
    SMTP_USE_TLS: bool     = os.getenv("SMTP_USE_TLS", "true").lower() != "false"


cfg = Config()

# ─────────────────────────────────────────
# Run log
# ─────────────────────────────────────────
run_log: list[dict] = []
MAX_LOG = 200

async def log_run(agent_slug: str, session_id: str, user_msg: str, answer: str,
                   duration_ms: int, qdrant_hits: int, error: str = "",
                   user_name: str = "", user_email: str = ""):
    entry = {
        "id":          str(uuid.uuid4())[:8],
        "ts":          datetime.now(timezone.utc).isoformat(),
        "agent":       agent_slug,
        "session_id":  session_id,
        "user_msg":    user_msg[:2000],
        "answer":      answer[:8000],
        "duration_ms": duration_ms,
        "qdrant_hits": qdrant_hits,
        "error":       error,
        "user_name":   user_name,
        "user_email":  user_email,
    }
    pool = await get_pg()
    if pool:
        try:
            await pool.execute(
                "INSERT INTO iraniano_run_log "
                "(id, ts, agent_slug, session_id, user_msg, answer, duration_ms, qdrant_hits, error, user_name, user_email) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
                entry["id"], datetime.now(timezone.utc), entry["agent"], entry["session_id"],
                entry["user_msg"], entry["answer"], entry["duration_ms"], entry["qdrant_hits"],
                entry["error"], entry["user_name"], entry["user_email"],
            )
            return
        except Exception as e:
            print(f"[Postgres] log_run failed, falling back to in-memory: {e}")

    run_log.insert(0, entry)
    if len(run_log) > MAX_LOG:
        run_log.pop()

# ─────────────────────────────────────────
# In-memory history fallback
# Used when Postgres is not configured.
# Keyed by (session_id, agent_slug) → list of {role, content}
# ─────────────────────────────────────────
from collections import defaultdict
_mem_history: dict = defaultdict(list)
MEM_MAX_TURNS = 40  # max messages per session kept in memory

def _mem_key(session_id: str, agent_slug: str) -> str:
    return f"{session_id}::{agent_slug}"

def mem_load(session_id: str, agent_slug: str) -> list[dict]:
    return list(_mem_history[_mem_key(session_id, agent_slug)])

def mem_save(session_id: str, agent_slug: str, user_msg: str, assistant_msg: str):
    key = _mem_key(session_id, agent_slug)
    _mem_history[key].append({"role": "user",      "content": user_msg})
    _mem_history[key].append({"role": "assistant",  "content": assistant_msg})
    # Trim to last MEM_MAX_TURNS messages
    if len(_mem_history[key]) > MEM_MAX_TURNS:
        _mem_history[key] = _mem_history[key][-MEM_MAX_TURNS:]

# ─────────────────────────────────────────
# Postgres chat memory
# ─────────────────────────────────────────
_pg_pool: Optional[asyncpg.Pool] = None

async def get_pg():
    global _pg_pool
    if _pg_pool is None and cfg.POSTGRES_DSN:
        try:
            _pg_pool = await asyncpg.create_pool(cfg.POSTGRES_DSN, min_size=1, max_size=5)
            await _pg_pool.execute("""
                CREATE TABLE IF NOT EXISTS iraniano_chat_memory (
                    session_id TEXT,
                    agent_slug TEXT,
                    role TEXT,
                    content TEXT,
                    ts TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            await _pg_pool.execute("""
                CREATE TABLE IF NOT EXISTS iraniano_run_log (
                    id TEXT PRIMARY KEY,
                    ts TIMESTAMPTZ DEFAULT NOW(),
                    agent_slug TEXT,
                    session_id TEXT,
                    user_msg TEXT,
                    answer TEXT,
                    duration_ms INTEGER,
                    qdrant_hits INTEGER,
                    error TEXT,
                    user_name TEXT,
                    user_email TEXT
                )
            """)
            await _pg_pool.execute("""
                CREATE INDEX IF NOT EXISTS iraniano_run_log_ts_idx ON iraniano_run_log (ts DESC)
            """)
            await _pg_pool.execute("""
                CREATE TABLE IF NOT EXISTS iraniano_leads (
                    id SERIAL PRIMARY KEY,
                    ts TIMESTAMPTZ DEFAULT NOW(),
                    name TEXT,
                    email TEXT,
                    agent_slug TEXT,
                    session_id TEXT
                )
            """)
        except Exception as e:
            print(f"[Postgres] Connection failed: {e}")
    return _pg_pool

async def load_history(session_id: str, agent_slug: str) -> list[dict]:
    pool = await get_pg()
    if not pool:
        return []
    try:
        rows = await pool.fetch(
            "SELECT role, content FROM iraniano_chat_memory "
            "WHERE session_id=$1 AND agent_slug=$2 ORDER BY ts DESC LIMIT $3",
            session_id, agent_slug, cfg.MAX_HISTORY
        )
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    except Exception as e:
        print(f"[Postgres] load_history failed, continuing without history: {e}")
        return []

async def save_turn(session_id: str, agent_slug: str, user_msg: str, assistant_msg: str):
    pool = await get_pg()
    if not pool:
        return
    try:
        async with pool.acquire() as conn:
            await conn.executemany(
                "INSERT INTO iraniano_chat_memory(session_id, agent_slug, role, content) VALUES($1,$2,$3,$4)",
                [(session_id, agent_slug, "user", user_msg),
                 (session_id, agent_slug, "assistant", assistant_msg)]
            )
    except Exception as e:
        print(f"[Postgres] save_turn failed: {e}")

# ─────────────────────────────────────────
# RAG pipeline (Dr. Yas only)
# ─────────────────────────────────────────
async def embed_query(text: str) -> list[float]:
    client = AsyncOpenAI(api_key=cfg.OPENAI_API_KEY)
    resp = await client.embeddings.create(model=cfg.EMBED_MODEL, input=text)
    return resp.data[0].embedding

async def qdrant_search(vector: list[float]) -> list[dict]:
    payload = {
        "vector":          vector,
        "limit":           cfg.TOP_K,
        "with_payload":    True,
        "score_threshold": cfg.SCORE_THRESHOLD,
        "filter":          {"must": [{"key": "status", "match": {"value": 1}}]}
    }
    headers = {"Content-Type": "application/json"}
    if cfg.QDRANT_API_KEY:
        headers["api-key"] = cfg.QDRANT_API_KEY
    url = f"{cfg.QDRANT_URL}/collections/{cfg.QDRANT_COLLECTION}/points/search"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            return r.json().get("result", [])
    except Exception as e:
        print(f"[Qdrant] Unavailable, skipping RAG: {e}")
        return []

def build_context(results: list[dict]) -> tuple[str, list[dict]]:
    parts, links = [], []
    for i, hit in enumerate(results, 1):
        p = hit.get("payload", {})
        title   = p.get("title", "")
        content = p.get("content", p.get("body", ""))[:600]
        slug    = p.get("slug", "")
        score   = hit.get("score", 0)
        link    = p.get("share_link") or (
            f"{cfg.APP_BASE_URL}/fa/news/{slug}?language_id=2&share=true" if slug else "")
        parts.append(f"[Result {i} | score={score:.2f}]\nTitle: {title}\nContent: {content}")
        if link and title:
            links.append({"title": title, "url": link})
    return "\n\n---\n\n".join(parts), links

# ─────────────────────────────────────────
# Gemini generation
# ─────────────────────────────────────────
async def call_gemini(system: str, messages: list[dict], max_tokens: int = 2048) -> str:
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{cfg.GEMINI_MODEL}:generateContent?key={cfg.GEMINI_API_KEY}")
    contents = []
    for m in messages:
        role = "user" if m["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": max_tokens}
    }
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(url, json=body)
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]

# ─────────────────────────────────────────
# Post-processing
# ─────────────────────────────────────────
FOLLOW_UP_PATTERNS = [
    re.compile(r'^(بیشتر|توضیح بده|ادامه بده|بگو|چطور|چرا|کِی|کجا|چقدر)'),
    re.compile(r'^(explain|tell me more|more detail|continue|go on|what about)', re.I),
    re.compile(r'^(اون|این|همون|منظورت|منظور)'),
    re.compile(r'^(yes|بله|آره|ok|باشه|درسته)', re.I),
    re.compile(r'بیشتر$'), re.compile(r'more$', re.I), re.compile(r'^\d+$'),
]
def is_follow_up(msg: str) -> bool:
    msg = msg.strip()
    return len(msg) < 20 or any(p.search(msg) for p in FOLLOW_UP_PATTERNS)

def clean_html(text: str) -> str:
    text = re.sub(r'<h[1-6][^>]*>(.*?)</h[1-6]>', r'<b>\1</b>', text, flags=re.I|re.S)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'#{1,6}\s+(.*?)(\n|$)', r'<b>\1</b>\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def append_share_links(answer: str, links: list[dict]) -> str:
    if not links:
        return answer
    block = "\n\n📱 <b>مطالعه بیشتر در ایرانیو:</b>\n"
    for i, lnk in enumerate(links[:5], 1):
        block += f"\n{i}. {lnk['title']}\n{lnk['url']}"
    return answer + block

# ─────────────────────────────────────────
# Core pipeline
# ─────────────────────────────────────────
async def run_pipeline(user_message: str, session_id: str, agent_slug: str,
                       client_history: Optional[list[dict]] = None,
                       user_name: Optional[str] = None, user_email: Optional[str] = None) -> str:
    t0 = time.monotonic()
    qdrant_hits = 0

    agent = get_agent(agent_slug)
    if not agent:
        raise HTTPException(404, f"Agent '{agent_slug}' not found")
    if not agent.enabled:
        raise HTTPException(403, f"Agent '{agent_slug}' is disabled")

    try:
        # Load history:
        # - If client sent history (localStorage agents like trip planner) → use it directly
        # - Else if Postgres available → load from DB
        # - Else → use in-memory fallback
        if client_history is not None:
            # Validate and sanitise — only allow role/content keys
            history = [{"role": m["role"], "content": m["content"]}
                       for m in client_history
                       if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
        else:
            pg_pool = await get_pg()
            if pg_pool:
                history = await load_history(session_id, agent_slug)
            else:
                history = mem_load(session_id, agent_slug)

        # Follow-up enrichment (disabled for state-machine agents like trip planner)
        enriched = user_message
        if getattr(agent, "followup_enrichment", True) and is_follow_up(user_message) and history:
            last_user = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
            if last_user:
                enriched = f"{last_user} {user_message}"

        # RAG — only for agents that declare rag_enabled = True
        context_text, share_links = "", []
        if agent.rag_enabled:
            try:
                vector = await embed_query(enriched)
                results = await qdrant_search(vector)
                qdrant_hits = len(results)
                context_text, share_links = build_context(results)
            except Exception as e:
                print(f"[RAG] Skipped: {e}")

        # Build system prompt
        system = agent.system_prompt
        if context_text:
            system += f"\n\n[LOCAL KNOWLEDGE BASE — use as primary source]\n{context_text}"

        messages = list(history)
        messages.append({"role": "user", "content": user_message})

        raw = await call_gemini(system, messages, max_tokens=getattr(agent, "max_output_tokens", 2048))
        clean = clean_html(raw)
        final = append_share_links(clean, share_links)

        # Save history server-side only for non-client-history agents
        if client_history is None:
            pg_pool = await get_pg()
            if pg_pool:
                await save_turn(session_id, agent_slug, user_message, final)
            else:
                mem_save(session_id, agent_slug, user_message, final)

        elapsed = int((time.monotonic() - t0) * 1000)
        await log_run(agent_slug, session_id, user_message, final, elapsed, qdrant_hits,
                       user_name=user_name or "", user_email=user_email or "")
        return final

    except HTTPException:
        raise
    except Exception as e:
        elapsed = int((time.monotonic() - t0) * 1000)
        await log_run(agent_slug, session_id, user_message, "", elapsed, qdrant_hits, error=str(e),
                       user_name=user_name or "", user_email=user_email or "")
        raise

# ─────────────────────────────────────────
# FastAPI app
# ─────────────────────────────────────────
app = FastAPI(title="Iraniano Multi-Agent Chatbot", version="2.0")

_BASE_DIR = Path(__file__).resolve().parent

def read_template(name: str) -> str:
    return (_BASE_DIR / "templates" / name).read_text(encoding="utf-8")

@app.on_event("startup")
async def startup_checks():
    print(f"[Startup] Base dir: {_BASE_DIR}")
    for name in ["selector.html", "chat.html", "admin.html"]:
        exists = (_BASE_DIR / "templates" / name).exists()
        print(f"[Startup] templates/{name}: {'OK' if exists else 'MISSING'}")
    print(f"[Startup] Agents loaded: {list(AGENTS.keys())}")
    print(f"[Startup] Gemini key set: {bool(cfg.GEMINI_API_KEY)}")

# ── Public: agent list ─────────────────────────────────────
@app.get("/agents")
async def public_agents():
    return [{"slug": a.slug, "name": a.name, "icon": a.icon,
             "description": a.description, "chips": a.chips,
             "welcome_message": a.welcome_message,
             "use_client_history": a.use_client_history}
            for a in list_agents()]

# ── Public: agent selector UI ─────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def selector_ui():
    return HTMLResponse(read_template("selector.html"))

# ── Public: chat UI for a specific agent ──────────────────
# Agents can have a custom template file: templates/{slug}.html
# Falls back to the generic chat.html if not found
@app.get("/chat/{agent_slug}", response_class=HTMLResponse)
async def chat_ui(agent_slug: str):
    agent = get_agent(agent_slug)
    if not agent or not agent.enabled:
        raise HTTPException(404, "Agent not found")
    custom = _BASE_DIR / "templates" / f"{agent_slug}.html"
    template = f"{agent_slug}.html" if custom.exists() else "chat.html"
    return HTMLResponse(read_template(template))

# ── Chat API ───────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    agent_slug: str = "dr-yas"
    client_history: Optional[list[dict]] = None  # sent by frontend for localStorage-based agents
    user_name: Optional[str] = None
    user_email: Optional[str] = None

@app.post("/chat")
async def chat(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(400, "Empty message")
    session_id = req.session_id or f"web_{uuid.uuid4().hex[:12]}"
    try:
        answer = await run_pipeline(req.message, session_id, req.agent_slug,
                                    client_history=req.client_history,
                                    user_name=req.user_name, user_email=req.user_email)
        return {"answer": answer, "session_id": session_id, "agent_slug": req.agent_slug}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

# ── Admin: panel UI ────────────────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
async def admin_ui():
    return HTMLResponse(read_template("admin.html"))

# ── Admin: create new agent ────────────────────────────────
class AgentCreate(BaseModel):
    slug: str
    name: str
    icon: str = "🤖"
    description: str = ""
    system_prompt: str = ""
    welcome_message: str = ""
    chips: list[str] = []
    enabled: bool = True
    rag_enabled: bool = False

def generate_agent_code(data: AgentCreate) -> str:
    """Generates the Python code for a new agent file."""
    slug = re.sub(r'[^a-z0-9\-]', '', data.slug.lower())
    if not slug:
        raise ValueError("Invalid slug. Use only lowercase letters, numbers, and hyphens.")

    chips_str = (",\n    ").join([repr(c) for c in data.chips])

    # Escape triple quotes in system prompt to avoid breaking the string literal
    safe_prompt = data.system_prompt.replace('"""', '\\"\\"\\"')

    return f'''"""
{data.name}
=================================================
This file was auto-generated by the admin panel.
"""

slug              = {repr(slug)}
name              = {repr(data.name)}
icon              = {repr(data.icon)}
description       = {repr(data.description)}
enabled           = {repr(data.enabled)}
rag_enabled       = {repr(data.rag_enabled)}
max_output_tokens = 2048
followup_enrichment = True
use_client_history  = False

welcome_message = (
    {repr(data.welcome_message)}
)

chips = [
    {chips_str}
]

system_prompt = """{safe_prompt}"""
'''

@app.post("/admin/agents", status_code=201)
async def admin_create_agent(body: AgentCreate):
    slug = re.sub(r'[^a-z0-9\-]', '', body.slug.lower())
    if not slug:
        raise HTTPException(400, "Invalid slug. Use only lowercase letters, numbers, and hyphens.")
    agent_path = _BASE_DIR / "agents" / f"{slug}.py"
    if agent_path.exists():
        raise HTTPException(400, f"Agent with slug '{slug}' already exists.")
    agent_code = generate_agent_code(body)
    agent_path.write_text(agent_code, encoding="utf-8")
    reload_agents()
    new_agent = get_agent(slug)
    if not new_agent:
        raise HTTPException(500, "Agent file was created, but failed to load. Please restart the server.")
    return new_agent.to_dict()

# ── Admin: list all agents ─────────────────────────────────
@app.get("/admin/agents")
async def admin_list_agents():
    return [a.to_dict() for a in list_agents(include_disabled=True)]

# ── Admin: update agent ────────────────────────────────────
class AgentUpdate(BaseModel):
    name:            Optional[str]       = None
    icon:            Optional[str]       = None
    description:     Optional[str]       = None
    system_prompt:   Optional[str]       = None
    welcome_message: Optional[str]       = None
    chips:           Optional[list[str]] = None
    enabled:         Optional[bool]      = None

@app.post("/admin/agents/{slug}")
async def admin_update_agent(slug: str, body: AgentUpdate):
    agent = update_agent(slug, **{k: v for k, v in body.model_dump().items()
                                   if v is not None})
    if not agent:
        raise HTTPException(404, f"Agent '{slug}' not found")
    return agent.to_dict()

# ── Admin: reset agent to default ─────────────────────────
@app.post("/admin/agents/{slug}/reset")
async def admin_reset_agent(slug: str):
    agent = reset_agent(slug)
    if not agent:
        raise HTTPException(404, f"Agent '{slug}' not found or no default available")
    return agent.to_dict()

# ── Admin: delete agent ────────────────────────────────────
@app.delete("/admin/agents/{slug}")
async def admin_delete_agent(slug: str):
    if not delete_agent(slug):
        raise HTTPException(404, f"Agent '{slug}' not found")
    return {"status": "deleted", "slug": slug}

# ── Admin: global config ───────────────────────────────────
@app.get("/admin/config")
async def get_config():
    return {
        "QDRANT_URL":        cfg.QDRANT_URL,
        "QDRANT_COLLECTION": cfg.QDRANT_COLLECTION,
        "EMBED_MODEL":       cfg.EMBED_MODEL,
        "GEMINI_MODEL":      cfg.GEMINI_MODEL,
        "SCORE_THRESHOLD":   cfg.SCORE_THRESHOLD,
        "TOP_K":             cfg.TOP_K,
        "MAX_HISTORY":       cfg.MAX_HISTORY,
        "APP_BASE_URL":      cfg.APP_BASE_URL,
        "OPENAI_API_KEY":    ("***" + cfg.OPENAI_API_KEY[-4:]) if cfg.OPENAI_API_KEY else "",
        "GEMINI_API_KEY":    ("***" + cfg.GEMINI_API_KEY[-4:]) if cfg.GEMINI_API_KEY else "",
        "QDRANT_API_KEY":    ("***" + cfg.QDRANT_API_KEY[-4:]) if cfg.QDRANT_API_KEY else "",
        "SMTP_HOST":         cfg.SMTP_HOST,
        "SMTP_PORT":         cfg.SMTP_PORT,
        "SMTP_USER":         cfg.SMTP_USER,
        "SMTP_FROM_EMAIL":   cfg.SMTP_FROM_EMAIL,
        "SMTP_FROM_NAME":    cfg.SMTP_FROM_NAME,
        "SMTP_USE_TLS":      cfg.SMTP_USE_TLS,
        "SMTP_PASSWORD":     ("***" + cfg.SMTP_PASSWORD[-4:]) if cfg.SMTP_PASSWORD else "",
    }

class ConfigUpdate(BaseModel):
    QDRANT_URL:        Optional[str]       = None
    QDRANT_COLLECTION: Optional[str]       = None
    EMBED_MODEL:       Optional[str]       = None
    GEMINI_MODEL:      Optional[str]       = None
    SCORE_THRESHOLD:   Optional[float]     = None
    TOP_K:             Optional[int]       = None
    MAX_HISTORY:       Optional[int]       = None
    APP_BASE_URL:      Optional[str]       = None
    OPENAI_API_KEY:    Optional[str]       = None
    GEMINI_API_KEY:    Optional[str]       = None
    QDRANT_API_KEY:    Optional[str]       = None
    SMTP_HOST:         Optional[str]       = None
    SMTP_PORT:         Optional[int]       = None
    SMTP_USER:         Optional[str]       = None
    SMTP_PASSWORD:     Optional[str]       = None
    SMTP_FROM_EMAIL:   Optional[str]       = None
    SMTP_FROM_NAME:    Optional[str]       = None
    SMTP_USE_TLS:      Optional[bool]      = None

@app.post("/admin/config")
async def update_config(body: ConfigUpdate):
    for attr in ["QDRANT_URL","QDRANT_COLLECTION","EMBED_MODEL","GEMINI_MODEL",
                 "SCORE_THRESHOLD","TOP_K","MAX_HISTORY","APP_BASE_URL",
                 "SMTP_HOST","SMTP_PORT","SMTP_USER","SMTP_FROM_EMAIL",
                 "SMTP_FROM_NAME","SMTP_USE_TLS"]:
        val = getattr(body, attr)
        if val is not None:
            setattr(cfg, attr, val)
    for attr in ("OPENAI_API_KEY","GEMINI_API_KEY","QDRANT_API_KEY","SMTP_PASSWORD"):
        val = getattr(body, attr)
        if val and not val.startswith("***"):
            setattr(cfg, attr, val)
    return {"status": "ok"}

# ── Admin: run log ─────────────────────────────────────────
async def fetch_run_log(limit: int = MAX_LOG) -> list[dict]:
    """Return recent run-log entries, newest first. Postgres-backed when configured,
    falling back to the in-memory run_log otherwise."""
    pool = await get_pg()
    if pool:
        try:
            rows = await pool.fetch(
                "SELECT id, ts, agent_slug, session_id, user_msg, answer, duration_ms, "
                "qdrant_hits, error, user_name, user_email FROM iraniano_run_log "
                "ORDER BY ts DESC LIMIT $1",
                limit,
            )
            return [{
                "id":          r["id"],
                "ts":          r["ts"].isoformat(),
                "agent":       r["agent_slug"],
                "session_id":  r["session_id"],
                "user_msg":    r["user_msg"],
                "answer":      r["answer"],
                "duration_ms": r["duration_ms"],
                "qdrant_hits": r["qdrant_hits"],
                "error":       r["error"],
                "user_name":   r["user_name"],
                "user_email":  r["user_email"],
            } for r in rows]
        except Exception as e:
            print(f"[Postgres] fetch_run_log failed, falling back to in-memory: {e}")
    return run_log[:limit]

@app.get("/admin/runs")
async def get_runs(limit: int = 100):
    return await fetch_run_log(limit)

# ── Admin: export run log (Q&A) as CSV ─────────────────────
@app.get("/admin/runs.csv")
async def export_runs_csv():
    rows = await fetch_run_log(10000)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["time", "agent", "session_id", "user_name", "user_email",
                      "user_message", "answer", "duration_ms", "qdrant_hits", "error"])
    for r in rows:
        answer_text = re.sub(r"<[^>]+>", " ", r["answer"])
        answer_text = re.sub(r"\s+", " ", answer_text).strip()
        writer.writerow([r["ts"], r["agent"], r["session_id"], r.get("user_name", ""), r.get("user_email", ""),
                          r["user_msg"], answer_text, r["duration_ms"], r["qdrant_hits"], r["error"]])
    headers = {"Content-Disposition": "attachment; filename=conversations.csv"}
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers=headers)

# ── Admin: export collected user leads (name/email) as CSV ─
@app.get("/admin/leads.csv")
async def export_leads_csv():
    pool = await get_pg()
    if pool:
        try:
            rows = await pool.fetch(
                "SELECT ts, name, email, agent_slug, session_id FROM iraniano_leads ORDER BY ts DESC"
            )
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["date", "time", "name", "email", "agent", "session_id"])
            for r in rows:
                ts = r["ts"]
                writer.writerow([ts.strftime("%Y-%m-%d"), ts.strftime("%H:%M:%S"),
                                  r["name"], r["email"], r["agent_slug"] or "", r["session_id"] or ""])
            headers = {"Content-Disposition": "attachment; filename=leads.csv"}
            return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers=headers)
        except Exception as e:
            print(f"[Postgres] export_leads_csv failed, falling back to CSV file: {e}")

    leads_file = _BASE_DIR.parent / "users.csv"
    if not leads_file.exists():
        raise HTTPException(404, "No leads collected yet")
    return FileResponse(leads_file, media_type="text/csv", filename="users.csv")

@app.post("/admin/clear-history")
async def clear_history():
    run_log.clear()
    pool = await get_pg()
    if pool:
        try:
            await pool.execute("DELETE FROM iraniano_run_log")
        except Exception as e:
            print(f"[Postgres] clear_history failed: {e}")
    return {"status": "cleared"}

# ── Admin: paywall ad videos + question threshold ──────────
_VIDEOS_FILE = _BASE_DIR.parent / "videos.json"
_ADS_CONFIG_FILE = _BASE_DIR.parent / "ads-config.json"
DEFAULT_AD_THRESHOLD = 5

@app.get("/admin/ads")
async def get_ads_config():
    videos: list[str] = []
    threshold = DEFAULT_AD_THRESHOLD
    if _VIDEOS_FILE.exists():
        try:
            videos = json.loads(_VIDEOS_FILE.read_text(encoding="utf-8")).get("videos", [])
        except Exception:
            pass
    if _ADS_CONFIG_FILE.exists():
        try:
            threshold = json.loads(_ADS_CONFIG_FILE.read_text(encoding="utf-8")).get("threshold", DEFAULT_AD_THRESHOLD)
        except Exception:
            pass
    return {"videos": videos, "threshold": threshold}

class AdsUpdate(BaseModel):
    videos: Optional[list[str]] = None
    threshold: Optional[int] = None

@app.post("/admin/ads")
async def update_ads_config(body: AdsUpdate):
    if body.videos is not None:
        _VIDEOS_FILE.write_text(json.dumps({"videos": body.videos}, indent=4, ensure_ascii=False), encoding="utf-8")
    if body.threshold is not None:
        _ADS_CONFIG_FILE.write_text(json.dumps({"threshold": body.threshold}, indent=4), encoding="utf-8")
    return {"status": "ok"}

# ── End-of-chat transcript email ────────────────────────────
_LOGO_URL = "https://panel.cybercina.co.uk//storage/logos/N0yQlVchcj4ucrQfVJwbXXB13FhWTMFccUBmWLpI.png"

def build_transcript_email_html(to_name: str, agent_name: str, messages: list[dict]) -> str:
    import html as html_lib

    rows = []
    for m in messages:
        text = html_lib.escape(str(m.get("text", "")).strip()).replace("\n", "<br>")
        if not text:
            continue
        if m.get("role") == "user":
            rows.append(f'''
            <tr><td style="padding:6px 0;text-align:left;">
              <div style="display:inline-block;max-width:80%;background:#612a80;color:#f8fafc;
                          border-radius:14px;padding:10px 14px;font-size:13.5px;line-height:1.8;text-align:right">
                {text}
              </div>
            </td></tr>''')
        else:
            rows.append(f'''
            <tr><td style="padding:6px 0;text-align:right;">
              <div style="display:inline-block;max-width:80%;background:#334155;color:#f8fafc;
                          border-radius:14px;padding:10px 14px;font-size:13.5px;line-height:1.8;
                          text-align:right;border:1px solid rgba(255,255,255,.08)">
                {text}
              </div>
            </td></tr>''')

    transcript_html = "".join(rows) or (
        '<tr><td style="color:#94a3b8;font-size:13px;text-align:center">گفتگویی ثبت نشده است.</td></tr>'
    )
    greeting_name = f"{html_lib.escape(to_name)} عزیز" if to_name else "کاربر گرامی"
    agent_name_safe = html_lib.escape(agent_name)

    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#060b14;font-family:Tahoma,'Segoe UI',Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#060b14;padding:24px 0;">
    <tr><td align="center">
      <table role="presentation" width="100%" style="max-width:600px;background:rgba(8,14,26,0.92);border-radius:16px;
                  overflow:hidden;border:1px solid rgba(255,255,255,.1)">
        <tr><td style="padding:28px 24px;text-align:center;background:linear-gradient(135deg,#612a80,#79359e)">
          <img src="{_LOGO_URL}" alt="ایرانیو" width="180" height="60" style="border-radius:10px;background:#fff;padding:8px 16px;display:inline-block">
          <h1 style="color:#f8fafc;font-size:18px;margin:14px 0 0;font-weight:700">دستیار هوشمند ایرانیو</h1>
        </td></tr>
        <tr><td style="padding:24px;color:#f8fafc">
          <p style="font-size:15px;line-height:1.9;margin:0 0 14px">سلام {greeting_name} 👋</p>
          <p style="font-size:13.5px;line-height:1.9;color:#94a3b8;margin:0 0 22px">
            از اینکه از دستیار هوشمند ایرانیو ({agent_name_safe}) استفاده کردید سپاسگزاریم.
            خلاصه گفتگوی شما در زیر آمده است.
          </p>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{transcript_html}</table>
          <p style="font-size:12.5px;line-height:1.9;color:#94a3b8;margin-top:24px">
            در صورت نیاز به مشاوره بیشتر، هر زمان می‌توانید دوباره به ایرانیو مراجعه کنید.
          </p>
        </td></tr>
        <tr><td style="padding:16px;text-align:center;font-size:11px;color:#94a3b8;border-top:1px solid rgba(255,255,255,.1)">
          © {datetime.now().year} ایرانیو — تمامی حقوق محفوظ است
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


def _send_email_sync(to_email: str, to_name: str, subject: str, html_body: str):
    if not cfg.SMTP_HOST or not cfg.SMTP_USER or not cfg.SMTP_PASSWORD:
        raise RuntimeError("SMTP is not configured")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg.SMTP_FROM_NAME or "Iraniyo", cfg.SMTP_FROM_EMAIL or cfg.SMTP_USER))
    msg["To"] = formataddr((to_name or "", to_email))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(cfg.SMTP_HOST, cfg.SMTP_PORT, timeout=20) as server:
        if cfg.SMTP_USE_TLS:
            server.starttls(context=ssl.create_default_context())
        server.login(cfg.SMTP_USER, cfg.SMTP_PASSWORD)
        server.sendmail(cfg.SMTP_FROM_EMAIL or cfg.SMTP_USER, [to_email], msg.as_string())


async def send_email_smtp(to_email: str, to_name: str, subject: str, html_body: str):
    await asyncio.to_thread(_send_email_sync, to_email, to_name, subject, html_body)


class TranscriptMessage(BaseModel):
    role: str
    text: str = ""

class SendTranscriptRequest(BaseModel):
    to_email: str
    to_name: Optional[str] = None
    agent_slug: str = "dr-yas"
    messages: list[TranscriptMessage] = []

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

@app.post("/send-transcript")
async def send_transcript(body: SendTranscriptRequest):
    if not _EMAIL_RE.match(body.to_email):
        raise HTTPException(400, "Invalid email address")
    if not cfg.SMTP_HOST or not cfg.SMTP_USER or not cfg.SMTP_PASSWORD:
        raise HTTPException(503, "Email sending is not configured")

    agent = get_agent(body.agent_slug)
    agent_name = agent.name if agent else body.agent_slug
    html_body = build_transcript_email_html(
        body.to_name or "", agent_name, [m.model_dump() for m in body.messages]
    )
    try:
        await send_email_smtp(
            body.to_email, body.to_name or "",
            "خلاصه گفتگوی شما با دستیار هوشمند ایرانیو", html_body,
        )
    except Exception as e:
        raise HTTPException(500, f"Failed to send email: {e}")
    return {"status": "sent"}

# ── Leads (pre-chat name/email capture) ─────────────────────
class LeadRequest(BaseModel):
    name: str
    email: str
    agent_slug: Optional[str] = None
    session_id: Optional[str] = None

@app.post("/leads")
async def create_lead(body: LeadRequest):
    name = body.name.strip()[:100]
    email = body.email.strip()
    if not name or not _EMAIL_RE.match(email):
        raise HTTPException(400, "Invalid name or email")

    pool = await get_pg()
    if pool:
        try:
            await pool.execute(
                "INSERT INTO iraniano_leads(name, email, agent_slug, session_id) VALUES($1,$2,$3,$4)",
                name, email, body.agent_slug, body.session_id,
            )
            return {"success": True}
        except Exception as e:
            print(f"[Postgres] create_lead failed, falling back to CSV: {e}")

    leads_file = _BASE_DIR.parent / "users.csv"
    is_new = not leads_file.exists() or leads_file.stat().st_size == 0
    now = datetime.now(timezone.utc)
    with open(leads_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["date", "time", "name", "email"])
        writer.writerow([now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), name, email])
    return {"success": True}

# ── Health ─────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "agents": list(AGENTS.keys()),
            "time": datetime.now(timezone.utc).isoformat()}