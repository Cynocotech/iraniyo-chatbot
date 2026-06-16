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
from email.mime.application import MIMEApplication
from email.utils import formataddr, formatdate
from typing import Optional
from pathlib import Path

import httpx
import asyncpg
from openai import AsyncOpenAI
from fastapi import FastAPI, HTTPException, Cookie, Response, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

from agents import AGENTS, get_agent, list_agents, update_agent, reset_agent, reload_agents, delete_agent
from admin_auth import authenticate_admin, generate_session_token
from pdf_generator import generate_transcript_pdf, generate_transcript_filename
from security import (
    SecurityMiddleware, InputValidator, rate_limiter, audit_logger,
    csrf_protection, SECURITY_HEADERS
)

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

    # ── Iraniu Directory API ────────────────────────────────
    DIRECTORY_API_KEY: str = os.getenv("DIRECTORY_API_KEY", "bd9a3134145e08761a65025ab204f27167bd91c1ec85276b995f17ad77734f52")
    DIRECTORY_API_URL: str = os.getenv("DIRECTORY_API_URL", "https://directory.iraniu.uk/chatbot/v1")


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
            await _pg_pool.execute("""
                CREATE TABLE IF NOT EXISTS iraniano_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
        except Exception as e:
            print(f"[Postgres] Connection failed: {e}")
    return _pg_pool

async def load_config_from_db():
    """Load configuration from database and update cfg object"""
    pool = await get_pg()
    if pool:
        try:
            rows = await pool.fetch("SELECT key, value FROM iraniano_config")
            for row in rows:
                key = row["key"]
                value = row["value"]
                if hasattr(cfg, key):
                    # Convert to appropriate type
                    if key in ["SCORE_THRESHOLD"]:
                        value = float(value)
                    elif key in ["TOP_K", "MAX_HISTORY", "SMTP_PORT"]:
                        value = int(value)
                    elif key == "SMTP_USE_TLS":
                        value = value.lower() == "true"
                    setattr(cfg, key, value)
            print(f"[Config] Loaded {len(rows)} settings from database")
        except Exception as e:
            print(f"[Config] Failed to load from database: {e}")

async def save_config_to_db(key: str, value):
    """Save a single config value to database"""
    pool = await get_pg()
    if pool:
        try:
            await pool.execute(
                """INSERT INTO iraniano_config (key, value, updated_at)
                   VALUES ($1, $2, NOW())
                   ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = NOW()""",
                key, str(value)
            )
        except Exception as e:
            print(f"[Config] Failed to save {key} to database: {e}")

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
# Iraniu Directory API helpers
# ─────────────────────────────────────────
async def directory_get_categories() -> list[dict]:
    """Fetch all business categories from the Directory API"""
    headers = {"X-Api-Key": cfg.DIRECTORY_API_KEY}
    url = f"{cfg.DIRECTORY_API_URL}/categories"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            return r.json().get("categories", [])
    except Exception as e:
        print(f"[Directory] Failed to fetch categories: {e}")
        return []

async def directory_search_businesses(category: Optional[str] = None,
                                     city: Optional[str] = None,
                                     query: Optional[str] = None,
                                     limit: int = 10,
                                     offset: int = 0) -> dict:
    """Search businesses in the Directory API"""
    headers = {"X-Api-Key": cfg.DIRECTORY_API_KEY}
    url = f"{cfg.DIRECTORY_API_URL}/businesses"
    params = {"limit": limit, "offset": offset}
    if category:
        params["category"] = category
    if city:
        params["city"] = city
    if query:
        params["q"] = query

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, headers=headers, params=params)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        print(f"[Directory] Failed to search businesses: {e}")
        return {"total": 0, "results": []}

async def directory_get_business(slug: str) -> Optional[dict]:
    """Get full details for a specific business"""
    headers = {"X-Api-Key": cfg.DIRECTORY_API_KEY}
    url = f"{cfg.DIRECTORY_API_URL}/businesses/{slug}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        print(f"[Directory] Failed to fetch business {slug}: {e}")
        return None

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
# Define Directory API function calling tools for Gemini
DIRECTORY_API_TOOLS = [{
    "function_declarations": [
        {
            "name": "get_directory_categories",
            "description": "Fetch all available business categories from the Iranian Business Directory",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "name": "search_directory_businesses",
            "description": "Search for Iranian businesses in the UK directory. Returns list of businesses with details.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Farsi category name (e.g. 'رستوران', 'آرایشگاه'). Must match exact category from get_directory_categories."
                    },
                    "city": {
                        "type": "string",
                        "description": "City name for partial match (e.g. 'London', 'Manchester', 'Birmingham')"
                    },
                    "query": {
                        "type": "string",
                        "description": "Keyword search query (searches in business name and description)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results to return (default: 10)",
                        "default": 10
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Pagination offset (default: 0)",
                        "default": 0
                    }
                },
                "required": []
            }
        },
        {
            "name": "get_directory_business_details",
            "description": "Get full details for a specific business by its slug",
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "The unique slug/ID of the business"
                    }
                },
                "required": ["slug"]
            }
        }
    ]
}]

async def execute_directory_function(func_name: str, args: dict) -> dict:
    """Execute a Directory API function call and return results"""
    try:
        if func_name == "get_directory_categories":
            categories = await directory_get_categories()
            return {"success": True, "categories": categories}

        elif func_name == "search_directory_businesses":
            result = await directory_search_businesses(
                category=args.get("category"),
                city=args.get("city"),
                query=args.get("query"),
                limit=args.get("limit", 10),
                offset=args.get("offset", 0)
            )
            return {"success": True, "result": result}

        elif func_name == "get_directory_business_details":
            business = await directory_get_business(args.get("slug"))
            if business:
                return {"success": True, "business": business}
            else:
                return {"success": False, "error": "Business not found"}

        else:
            return {"success": False, "error": f"Unknown function: {func_name}"}

    except Exception as e:
        return {"success": False, "error": str(e)}

async def call_gemini(system: str, messages: list[dict], max_tokens: int = 2048,
                     use_directory_tools: bool = False) -> str:
    """
    Call Gemini API with optional Directory API function calling support.
    If use_directory_tools=True, enables iterative function calling loop.
    """
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

    # Add Directory API tools if requested
    if use_directory_tools:
        body["tools"] = DIRECTORY_API_TOOLS
        print(f"[Gemini] Enabling Directory API tools for this request")

    async with httpx.AsyncClient(timeout=120) as client:
        max_iterations = 5  # Prevent infinite loops
        iteration = 0

        while iteration < max_iterations:
            iteration += 1
            r = await client.post(url, json=body)
            r.raise_for_status()
            response_data = r.json()

            if use_directory_tools:
                print(f"[Gemini] Response keys: {list(response_data.keys())}")
                print(f"[Gemini] Candidates: {len(response_data.get('candidates', []))}")

            candidate = response_data["candidates"][0]
            content = candidate["content"]

            if use_directory_tools:
                print(f"[Gemini] Content parts: {content.get('parts', [])}")

            # Check if there's a function call
            function_call = None
            text_response = None

            for part in content.get("parts", []):
                if "functionCall" in part:
                    function_call = part["functionCall"]
                if "text" in part:
                    text_response = part["text"]

            # If no function call, return the text response
            if not function_call:
                return text_response or ""

            # Execute the function call
            func_name = function_call["name"]
            func_args = function_call.get("args", {})

            print(f"[Gemini] Function call: {func_name}({func_args})")
            func_result = await execute_directory_function(func_name, func_args)

            # Add the function call and response to the conversation
            contents.append({
                "role": "model",
                "parts": [{"functionCall": function_call}]
            })
            contents.append({
                "role": "function",
                "parts": [{
                    "functionResponse": {
                        "name": func_name,
                        "response": func_result
                    }
                }]
            })

            # Update body for next iteration
            body["contents"] = contents

        # If we hit max iterations, return whatever we have
        return "متأسفم، نتوانستم پاسخ مناسبی تولید کنم. لطفاً دوباره تلاش کنید."

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

        # Enable Directory API function calling if agent supports it
        use_directory_tools = getattr(agent, "directory_enabled", False)
        print(f"[Pipeline] Agent: {agent_slug}, directory_enabled={use_directory_tools}")

        raw = await call_gemini(system, messages,
                               max_tokens=getattr(agent, "max_output_tokens", 2048),
                               use_directory_tools=use_directory_tools)
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
# Admin Authentication
# ─────────────────────────────────────────
# Store active sessions: token -> username
active_sessions: dict[str, str] = {}

def verify_admin_session(admin_session: Optional[str] = Cookie(None)):
    """Dependency to verify admin session cookie"""
    if not admin_session or admin_session not in active_sessions:
        raise HTTPException(401, "Unauthorized - Please login")
    return active_sessions[admin_session]

# ─────────────────────────────────────────
# FastAPI app
# ─────────────────────────────────────────
app = FastAPI(
    title="Iraniano Multi-Agent Chatbot",
    version="2.0",
    docs_url=None,  # Disable Swagger docs in production
    redoc_url=None,  # Disable ReDoc in production
)

# 🔒 Add security middleware
app.add_middleware(SecurityMiddleware, max_request_size=1_000_000)  # 1MB limit

_BASE_DIR = Path(__file__).resolve().parent

# Mount static files for fonts
app.mount("/static", StaticFiles(directory=_BASE_DIR / "static"), name="static")

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
    # Load config from database (overrides .env values)
    await load_config_from_db()
    print(f"[Startup] Config loaded from database")

# ── Public: agent list ─────────────────────────────────────
@app.get("/agents")
async def public_agents():
    return [{"slug": a.slug, "name": a.name, "icon": a.icon,
             "description": a.description, "chips": a.chips,
             "welcome_message": a.welcome_message,
             "use_client_history": a.use_client_history,
             "directory_enabled": a.directory_enabled,
             "directory_categories": a.directory_categories}
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
async def chat(req: ChatRequest, request: Request):
    # 🔒 Get client IP
    ip = request.client.host if request.client else "unknown"

    # 🔒 Input validation
    if not req.message.strip():
        raise HTTPException(400, "Empty message")

    # 🔒 Sanitize and validate inputs (with attack detection)
    try:
        message = InputValidator.sanitize_text(req.message, InputValidator.MAX_MESSAGE_LENGTH, ip=ip)
    except HTTPException as e:
        # Attack detected - already logged and IP blocked
        raise e

    if req.user_name:
        req.user_name = InputValidator.sanitize_name(req.user_name)

    if req.user_email and not InputValidator.validate_email(req.user_email):
        raise HTTPException(400, "Invalid email format")

    if not InputValidator.validate_slug(req.agent_slug):
        raise HTTPException(400, "Invalid agent slug")

    session_id = req.session_id or f"web_{uuid.uuid4().hex[:12]}"

    # 🔒 Audit log
    audit_logger.log_event("chat_request", {
        "agent": req.agent_slug,
        "session": session_id,
        "message_length": len(message)
    }, ip=ip, severity="info")

    try:
        answer = await run_pipeline(message, session_id, req.agent_slug,
                                    client_history=req.client_history,
                                    user_name=req.user_name, user_email=req.user_email)
        return {"answer": answer, "session_id": session_id, "agent_slug": req.agent_slug}
    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_event("chat_error", {
            "agent": req.agent_slug,
            "error": str(e)[:200]
        }, ip=ip, severity="high")
        raise HTTPException(500, "Internal server error")

# ── Admin: Login endpoint ─────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/admin/login")
async def admin_login(req: LoginRequest, response: Response, request: Request):
    # 🔒 Get client IP
    ip = request.client.host if request.client else "unknown"

    # 🔒 Validate input
    if not req.username or not req.password:
        raise HTTPException(400, "Username and password required")

    # Sanitize username (prevent injection)
    username = InputValidator.sanitize_name(req.username)

    # Authenticate
    user = authenticate_admin(username, req.password)

    if not user:
        # 🔒 Record failed login attempt
        is_blocked = rate_limiter.record_failed_login(ip)

        # 🔒 Audit log
        audit_logger.log_event("failed_login", {
            "username": username,
            "blocked": is_blocked
        }, ip=ip, severity="high" if is_blocked else "medium")

        if is_blocked:
            raise HTTPException(429, "Too many failed login attempts. Account locked for 15 minutes.")

        raise HTTPException(401, "Invalid username or password")

    # 🔒 Clear failed attempts on successful login
    rate_limiter.clear_login_attempts(ip)

    # Generate session token
    session_token = generate_session_token()
    active_sessions[session_token] = username

    # 🔒 Audit log successful login
    audit_logger.log_event("successful_login", {
        "username": username
    }, ip=ip, severity="info")

    # Set cookie (7 days expiry)
    response.set_cookie(
        key="admin_session",
        value=session_token,
        max_age=7 * 24 * 60 * 60,  # 7 days
        httponly=True,
        secure=True,  # HTTPS only
        samesite="lax"
    )

    return {"success": True, "username": username, "full_name": user.get("full_name", "")}

# ── Admin: Logout endpoint ─────────────────────────────────
@app.post("/admin/logout")
async def admin_logout(response: Response, admin_session: Optional[str] = Cookie(None)):
    if admin_session and admin_session in active_sessions:
        del active_sessions[admin_session]

    # Clear cookie
    response.delete_cookie("admin_session")
    return {"success": True}

# ── Admin: panel UI ────────────────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
async def admin_ui(admin_session: Optional[str] = Cookie(None)):
    # Check if user is logged in
    if not admin_session or admin_session not in active_sessions:
        # Return login page
        return HTMLResponse(read_template("login.html"))

    # User is authenticated, return admin panel
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
    directory_enabled: bool = False
    directory_categories: list[str] = []

def generate_agent_code(data: AgentCreate) -> str:
    """Generates the Python code for a new agent file."""
    slug = re.sub(r'[^a-z0-9\-]', '', data.slug.lower())
    if not slug:
        raise ValueError("Invalid slug. Use only lowercase letters, numbers, and hyphens.")

    chips_str = (",\n    ").join([repr(c) for c in data.chips])
    dir_cats_str = ", ".join([repr(c) for c in data.directory_categories])

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
directory_enabled = {repr(data.directory_enabled)}
directory_categories = [{dir_cats_str}]
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
async def admin_create_agent(body: AgentCreate, username: str = Depends(verify_admin_session)):
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
async def admin_list_agents(username: str = Depends(verify_admin_session)):
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
    directory_enabled: Optional[bool]    = None
    directory_categories: Optional[list[str]] = None

@app.post("/admin/agents/{slug}")
async def admin_update_agent(slug: str, body: AgentUpdate, username: str = Depends(verify_admin_session)):
    agent = update_agent(slug, **{k: v for k, v in body.model_dump().items()
                                   if v is not None})
    if not agent:
        raise HTTPException(404, f"Agent '{slug}' not found")
    return agent.to_dict()

# ── Admin: reset agent to default ─────────────────────────
@app.post("/admin/agents/{slug}/reset")
async def admin_reset_agent(slug: str, username: str = Depends(verify_admin_session)):
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
async def get_config(username: str = Depends(verify_admin_session)):
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
        "DIRECTORY_API_KEY": ("***" + cfg.DIRECTORY_API_KEY[-4:]) if cfg.DIRECTORY_API_KEY else "",
        "DIRECTORY_API_URL": cfg.DIRECTORY_API_URL,
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
    DIRECTORY_API_KEY: Optional[str]       = None
    DIRECTORY_API_URL: Optional[str]       = None

@app.post("/admin/config")
async def update_config(body: ConfigUpdate, username: str = Depends(verify_admin_session)):
    for attr in ["QDRANT_URL","QDRANT_COLLECTION","EMBED_MODEL","GEMINI_MODEL",
                 "SCORE_THRESHOLD","TOP_K","MAX_HISTORY","APP_BASE_URL",
                 "SMTP_HOST","SMTP_PORT","SMTP_USER","SMTP_FROM_EMAIL",
                 "SMTP_FROM_NAME","SMTP_USE_TLS","DIRECTORY_API_URL"]:
        val = getattr(body, attr)
        if val is not None:
            setattr(cfg, attr, val)
            await save_config_to_db(attr, val)
    for attr in ("OPENAI_API_KEY","GEMINI_API_KEY","QDRANT_API_KEY","SMTP_PASSWORD","DIRECTORY_API_KEY"):
        val = getattr(body, attr)
        if val and not val.startswith("***"):
            setattr(cfg, attr, val)
            await save_config_to_db(attr, val)
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
async def export_runs_csv(username: str = Depends(verify_admin_session)):
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
async def export_leads_csv(username: str = Depends(verify_admin_session)):
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

    greeting_name = f"{html_lib.escape(to_name)} عزیز" if to_name else "کاربر گرامی"
    agent_name_safe = html_lib.escape(agent_name)
    message_count = len(messages)

    # Short email body (full transcript is in PDF)
    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
</head>
<body style="margin:0;padding:0;background:#060b14;font-family:Tahoma,Arial,sans-serif;direction:rtl;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#060b14;padding:24px 0;direction:rtl;">
    <tr><td align="center">
      <table role="presentation" width="100%" style="max-width:600px;background:rgba(8,14,26,0.92);border-radius:16px;
                  overflow:hidden;border:1px solid rgba(255,255,255,.1);direction:rtl;">
        <tr><td style="padding:28px 24px;text-align:center;background:linear-gradient(135deg,#612a80,#79359e);direction:rtl;">
          <img src="{_LOGO_URL}" alt="ایرانیو" width="180" height="60" style="border-radius:10px;padding:8px 16px;display:block;margin:0 auto;max-width:100%;height:auto;">
          <h1 style="color:#f8fafc;font-size:18px;margin:14px 0 0;font-weight:700;font-family:Tahoma,Arial,sans-serif;direction:rtl;">دستیار هوشمند ایرانیو</h1>
        </td></tr>
        <tr><td style="padding:40px 24px;color:#f8fafc;direction:rtl;font-family:Tahoma,Arial,sans-serif;text-align:right;">
          <p style="font-size:16px;line-height:1.9;margin:0 0 25px;direction:rtl;">سلام {greeting_name} 👋</p>

          <div style="background:rgba(97,42,128,0.15);border-right:4px solid #612a80;padding:20px;border-radius:8px;margin:0 0 25px;">
            <p style="font-size:15px;line-height:1.9;color:#e8eaf0;margin:0 0 15px;direction:rtl;font-weight:600;">
              گفتگوی شما آماده است! 📄
            </p>
            <p style="font-size:14px;line-height:1.9;color:#cbd5e1;margin:0;direction:rtl;">
              فایل PDF حاوی {message_count} پیام از گفتگو با {agent_name_safe} ضمیمه این ایمیل است.
            </p>
          </div>

          <p style="font-size:13px;line-height:1.9;color:#94a3b8;margin:0;direction:rtl;">
            از اینکه از دستیار هوشمند ایرانیو استفاده کردید سپاسگزاریم. 💜
          </p>
        </td></tr>
        <tr><td style="padding:20px;text-align:center;font-size:11px;color:#94a3b8;border-top:1px solid rgba(255,255,255,.1);direction:rtl;font-family:Tahoma,Arial,sans-serif;">
          <p style="margin:0 0 8px;">https://iraniu.uk</p>
          <p style="margin:0;">© {datetime.now().year} ایرانیو — تمامی حقوق محفوظ است</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


def _send_email_sync(to_email: str, to_name: str, subject: str, html_body: str):
    if not cfg.SMTP_HOST or not cfg.SMTP_USER or not cfg.SMTP_PASSWORD:
        raise RuntimeError("SMTP is not configured")

    # Create plain text version for better deliverability
    import re
    text_body = re.sub('<[^<]+?>', '', html_body)  # Strip HTML tags
    text_body = re.sub(r'\s+', ' ', text_body).strip()  # Clean whitespace

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg.SMTP_FROM_NAME or "Iraniyo", cfg.SMTP_FROM_EMAIL or cfg.SMTP_USER))
    msg["To"] = formataddr((to_name or "", to_email))
    msg["Reply-To"] = cfg.SMTP_FROM_EMAIL or cfg.SMTP_USER

    # Anti-spam headers
    msg["Message-ID"] = f"<{uuid.uuid4()}@{cfg.SMTP_FROM_EMAIL.split('@')[1] if '@' in str(cfg.SMTP_FROM_EMAIL) else 'iraniu.uk'}>"
    msg["Date"] = formatdate(localtime=True)
    msg["MIME-Version"] = "1.0"
    msg["Content-Language"] = "fa"
    msg["X-Mailer"] = "Iraniyo Chatbot"
    msg["X-Priority"] = "3"
    msg["Importance"] = "Normal"

    # List-Unsubscribe header (required for bulk emails)
    msg["List-Unsubscribe"] = f"<mailto:{cfg.SMTP_FROM_EMAIL}?subject=unsubscribe>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    # Attach plain text first (best practice)
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    # Then HTML
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(cfg.SMTP_HOST, cfg.SMTP_PORT, timeout=20) as server:
        if cfg.SMTP_USE_TLS:
            server.starttls(context=ssl.create_default_context())
        server.login(cfg.SMTP_USER, cfg.SMTP_PASSWORD)
        server.sendmail(cfg.SMTP_FROM_EMAIL or cfg.SMTP_USER, [to_email], msg.as_string())


async def send_email_smtp(to_email: str, to_name: str, subject: str, html_body: str):
    await asyncio.to_thread(_send_email_sync, to_email, to_name, subject, html_body)


def _send_email_with_pdf_sync(to_email: str, to_name: str, subject: str, html_body: str,
                               pdf_bytes: Optional[bytes] = None, pdf_filename: Optional[str] = None):
    """Send email with optional PDF attachment"""
    if not cfg.SMTP_HOST or not cfg.SMTP_USER or not cfg.SMTP_PASSWORD:
        raise RuntimeError("SMTP is not configured")

    # Create plain text version
    import re
    text_body = re.sub('<[^<]+?>', '', html_body)
    text_body = re.sub(r'\s+', ' ', text_body).strip()

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg.SMTP_FROM_NAME or "Iraniyo", cfg.SMTP_FROM_EMAIL or cfg.SMTP_USER))
    msg["To"] = formataddr((to_name or "", to_email))
    msg["Reply-To"] = cfg.SMTP_FROM_EMAIL or cfg.SMTP_USER

    # Anti-spam headers
    msg["Message-ID"] = f"<{uuid.uuid4()}@{cfg.SMTP_FROM_EMAIL.split('@')[1] if '@' in str(cfg.SMTP_FROM_EMAIL) else 'iraniu.uk'}>"
    msg["Date"] = formatdate(localtime=True)
    msg["MIME-Version"] = "1.0"
    msg["Content-Language"] = "fa"
    msg["X-Mailer"] = "Iraniyo Chatbot"
    msg["X-Priority"] = "3"
    msg["Importance"] = "Normal"
    msg["List-Unsubscribe"] = f"<mailto:{cfg.SMTP_FROM_EMAIL}?subject=unsubscribe>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    # Create alternative part for text and HTML
    msg_alternative = MIMEMultipart("alternative")
    msg_alternative.attach(MIMEText(text_body, "plain", "utf-8"))
    msg_alternative.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(msg_alternative)

    # Attach PDF if provided
    if pdf_bytes and pdf_filename:
        pdf_attachment = MIMEApplication(pdf_bytes, _subtype="pdf")
        pdf_attachment.add_header('Content-Disposition', 'attachment', filename=pdf_filename)
        pdf_attachment.add_header('Content-Type', 'application/pdf', name=pdf_filename)
        msg.attach(pdf_attachment)

    with smtplib.SMTP(cfg.SMTP_HOST, cfg.SMTP_PORT, timeout=20) as server:
        if cfg.SMTP_USE_TLS:
            server.starttls(context=ssl.create_default_context())
        server.login(cfg.SMTP_USER, cfg.SMTP_PASSWORD)
        server.sendmail(cfg.SMTP_FROM_EMAIL or cfg.SMTP_USER, [to_email], msg.as_string())


async def send_email_with_pdf(to_email: str, to_name: str, subject: str, html_body: str,
                               pdf_bytes: Optional[bytes] = None, pdf_filename: Optional[str] = None):
    """Async wrapper for sending email with PDF"""
    await asyncio.to_thread(_send_email_with_pdf_sync, to_email, to_name, subject, html_body,
                           pdf_bytes, pdf_filename)


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
async def send_transcript(body: SendTranscriptRequest, request: Request):
    # 🔒 Input validation
    if not InputValidator.validate_email(body.to_email):
        raise HTTPException(400, "Invalid email address")

    if not InputValidator.validate_slug(body.agent_slug):
        raise HTTPException(400, "Invalid agent slug")

    if not cfg.SMTP_HOST or not cfg.SMTP_USER or not cfg.SMTP_PASSWORD:
        raise HTTPException(503, "Email sending is not configured")

    # 🔒 Sanitize inputs to prevent email header injection
    to_email = InputValidator.sanitize_email_header(body.to_email)
    to_name = InputValidator.sanitize_email_header(body.to_name or "")

    # Limit number of messages
    if len(body.messages) > 500:
        raise HTTPException(400, "Too many messages (max 500)")

    # 🔒 Audit log
    ip = request.client.host if request.client else "unknown"
    audit_logger.log_event("transcript_request", {
        "to_email": to_email[:20] + "...",  # Partial email for privacy
        "agent": body.agent_slug,
        "message_count": len(body.messages)
    }, ip=ip, severity="info")

    agent = get_agent(body.agent_slug)
    agent_name = agent.name if agent else body.agent_slug
    messages_dict = [m.model_dump() for m in body.messages]

    # Generate HTML for email
    html_body = build_transcript_email_html(to_name, agent_name, messages_dict)

    # Generate PDF attachment
    try:
        pdf_bytes = await asyncio.to_thread(
            generate_transcript_pdf, to_name, agent_name, messages_dict
        )
        pdf_filename = generate_transcript_filename(to_name or "user", agent_name)
    except Exception as e:
        print(f"[PDF] Generation failed: {e}")
        audit_logger.log_event("pdf_generation_error", {
            "error": str(e)[:200]
        }, ip=ip, severity="medium")
        pdf_bytes = None
        pdf_filename = None

    try:
        await send_email_with_pdf(
            to_email, to_name,
            "خلاصه گفتگوی شما با دستیار هوشمند ایرانیو",
            html_body, pdf_bytes, pdf_filename
        )
    except Exception as e:
        audit_logger.log_event("email_send_error", {
            "error": str(e)[:200]
        }, ip=ip, severity="high")
        raise HTTPException(500, "Failed to send email")
    return {"status": "sent"}

@app.post("/download-transcript-pdf")
async def download_transcript_pdf(body: SendTranscriptRequest):
    """Download transcript as PDF without sending email"""
    agent = get_agent(body.agent_slug)
    agent_name = agent.name if agent else body.agent_slug
    messages_dict = [m.model_dump() for m in body.messages]

    try:
        pdf_bytes = await asyncio.to_thread(
            generate_transcript_pdf, body.to_name or "", agent_name, messages_dict
        )
        pdf_filename = generate_transcript_filename(body.to_name or "user", agent_name)

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{pdf_filename}"'
            }
        )
    except Exception as e:
        raise HTTPException(500, f"Failed to generate PDF: {e}")

# ── Leads (pre-chat name/email capture) ─────────────────────
class LeadRequest(BaseModel):
    name: str
    email: str
    agent_slug: Optional[str] = None
    session_id: Optional[str] = None

@app.post("/leads")
async def create_lead(body: LeadRequest, request: Request):
    # 🔒 Input validation
    name = InputValidator.sanitize_name(body.name)
    email = body.email.strip()

    if not name or not InputValidator.validate_email(email):
        raise HTTPException(400, "Invalid name or email")

    if body.agent_slug and not InputValidator.validate_slug(body.agent_slug):
        raise HTTPException(400, "Invalid agent slug")

    # 🔒 Audit log
    ip = request.client.host if request.client else "unknown"
    audit_logger.log_event("lead_capture", {
        "agent": body.agent_slug or "unknown",
        "email_domain": email.split('@')[1] if '@' in email else "unknown"
    }, ip=ip, severity="info")

    pool = await get_pg()
    if pool:
        try:
            # 🔒 Parameterized query prevents SQL injection
            await pool.execute(
                "INSERT INTO iraniano_leads(name, email, agent_slug, session_id) VALUES($1,$2,$3,$4)",
                name, email, body.agent_slug, body.session_id,
            )
            return {"success": True}
        except Exception as e:
            print(f"[Postgres] create_lead failed, falling back to CSV: {e}")
            audit_logger.log_event("database_error", {
                "operation": "lead_insert",
                "error": str(e)[:200]
            }, ip=ip, severity="high")

    leads_file = _BASE_DIR.parent / "users.csv"
    is_new = not leads_file.exists() or leads_file.stat().st_size == 0
    now = datetime.now(timezone.utc)
    with open(leads_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["date", "time", "name", "email"])
        writer.writerow([now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), name, email])
    return {"success": True}

# ── Security Dashboard (Admin only) ─────────────────────────
@app.get("/admin/security/events")
async def get_security_events(
    limit: int = 100,
    severity: Optional[str] = None,
    username: str = Depends(verify_admin_session)
):
    """Get recent security events"""
    events = audit_logger.get_recent_events(limit, severity)
    return {"events": events, "count": len(events)}

@app.get("/admin/security/blocked-ips")
async def get_blocked_ips(username: str = Depends(verify_admin_session)):
    """Get list of currently blocked IPs"""
    now = time.time()
    blocked = []
    for ip, unblock_time in rate_limiter.blocked.items():
        if unblock_time > now:
            remaining = int(unblock_time - now)
            blocked.append({
                "ip": ip,
                "unblock_in_seconds": remaining,
                "unblock_at": datetime.fromtimestamp(unblock_time, tz=timezone.utc).isoformat()
            })
    return {"blocked_ips": blocked, "count": len(blocked)}

@app.post("/admin/security/unblock-ip")
async def unblock_ip(
    body: dict,
    username: str = Depends(verify_admin_session)
):
    """Manually unblock an IP address"""
    ip = body.get("ip")
    if not ip:
        raise HTTPException(400, "IP address required")

    if ip in rate_limiter.blocked:
        del rate_limiter.blocked[ip]
        audit_logger.log_event("manual_unblock", {
            "ip": ip,
            "admin": username
        }, ip="admin", severity="info")
        return {"status": "unblocked", "ip": ip}
    else:
        return {"status": "not_blocked", "ip": ip}

# ── Directory API Proxy (for chatbot agents) ──────────────
@app.get("/api/directory/categories")
async def api_directory_categories():
    """Public endpoint for agents to fetch business categories"""
    categories = await directory_get_categories()
    return {"categories": categories}

@app.get("/api/directory/businesses")
async def api_directory_businesses(
    category: Optional[str] = None,
    city: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 10,
    offset: int = 0
):
    """Public endpoint for agents to search businesses"""
    result = await directory_search_businesses(category, city, q, limit, offset)
    return result

@app.get("/api/directory/businesses/{slug}")
async def api_directory_business_detail(slug: str):
    """Public endpoint for agents to get full business details"""
    business = await directory_get_business(slug)
    if not business:
        raise HTTPException(404, "Business not found")
    return business

# ── Health ─────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "agents": list(AGENTS.keys()),
            "time": datetime.now(timezone.utc).isoformat()}