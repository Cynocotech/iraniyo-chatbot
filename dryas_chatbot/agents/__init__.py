"""
agents/__init__.py — Auto-discovery registry
=============================================
Scans this folder for agent modules and loads them automatically.

TO ADD A NEW AGENT:
  1. Create agents/your_agent_name.py  (copy any existing agent as template)
  2. Fill in: slug, name, icon, description, system_prompt, welcome_message, chips
  3. Restart the server — it appears automatically everywhere

TO UPDATE AN AGENT:
  Edit only its own .py file. Nothing else is affected.

TO DISABLE AN AGENT:
  Set  enabled = False  in its .py file, or toggle it in the admin panel.

Required fields in each agent module:
  slug            str   — unique URL key, e.g. "dr-yas"
  name            str   — display name (Farsi ok)
  icon            str   — single emoji
  description     str   — short subtitle
  system_prompt   str   — full Gemini system instruction
  welcome_message str   — first message shown in chat UI (HTML ok)
  chips           list  — suggestion chips shown on welcome screen
  enabled         bool  — True/False (default True if missing)
  rag_enabled     bool  — True = uses Qdrant RAG (default False if missing)
"""

import importlib
import pkgutil
import copy
from dataclasses import dataclass, field, asdict
from typing import Optional
from pathlib import Path


# ─────────────────────────────────────────────────────────
# Agent dataclass
# ─────────────────────────────────────────────────────────
@dataclass
class Agent:
    slug:            str
    name:            str
    icon:            str
    description:     str
    system_prompt:   str
    welcome_message: str
    chips:           list
    enabled:         bool = True
    rag_enabled:     bool = False   # only agents that need Qdrant set this True
    max_output_tokens: int = 2048   # increase for agents that generate long content (e.g. trip plans)
    followup_enrichment: bool = True  # set False for state-machine agents (trip planner) to avoid message merging
    use_client_history: bool = False  # set True to use localStorage history sent from frontend instead of server-side storage

    def to_dict(self):
        return asdict(self)


# ─────────────────────────────────────────────────────────
# Auto-discovery
# ─────────────────────────────────────────────────────────
def _discover_agents() -> dict[str, Agent]:
    """
    Scan agents/ for .py files (excluding __init__.py).
    Each file that defines a `slug` is loaded as an agent.
    Order is alphabetical by filename — rename files to reorder.
    """
    registry: dict[str, Agent] = {}
    package_dir = Path(__file__).parent

    for finder, module_name, _ in sorted(pkgutil.iter_modules([str(package_dir)])):
        if module_name.startswith("_"):
            continue  # skip __init__, _template, etc.
        module = importlib.import_module(f"agents.{module_name}")

        # Must have a slug to be treated as an agent
        if not hasattr(module, "slug"):
            continue

        agent = Agent(
            slug            = module.slug,
            name            = module.name,
            icon            = module.icon,
            description     = module.description,
            system_prompt   = module.system_prompt,
            welcome_message = module.welcome_message,
            chips           = list(module.chips),
            enabled           = getattr(module, "enabled", True),
            rag_enabled       = getattr(module, "rag_enabled", False),
            max_output_tokens     = getattr(module, "max_output_tokens", 2048),
            followup_enrichment  = getattr(module, "followup_enrichment", True),
            use_client_history  = getattr(module, "use_client_history", False),
        )
        registry[agent.slug] = agent

    return registry


# ─────────────────────────────────────────────────────────
# Runtime registry  (loaded once on startup, editable via admin)
# ─────────────────────────────────────────────────────────
# _defaults holds the original file values for "Reset to default"
_defaults: dict[str, Agent] = _discover_agents()
AGENTS:    dict[str, Agent] = {k: copy.deepcopy(v) for k, v in _defaults.items()}


def reload_agents():
    """Rescan the agents directory and reload the AGENTS registry at runtime."""
    global _defaults, AGENTS
    _defaults = _discover_agents()
    AGENTS = {k: copy.deepcopy(v) for k, v in _defaults.items()}
    print(f"[Agents] Reloaded. Found: {list(AGENTS.keys())}")


# ─────────────────────────────────────────────────────────
# Public API used by main.py
# ─────────────────────────────────────────────────────────
def get_agent(slug: str) -> Optional[Agent]:
    return AGENTS.get(slug)


def list_agents(include_disabled: bool = False) -> list[Agent]:
    return [a for a in AGENTS.values() if include_disabled or a.enabled]


def update_agent(slug: str, **kwargs) -> Optional[Agent]:
    """Update agent fields at runtime (admin panel). Returns updated agent."""
    if slug not in AGENTS:
        return None
    for k, v in kwargs.items():
        if hasattr(AGENTS[slug], k) and v is not None:
            setattr(AGENTS[slug], k, v)
    return AGENTS[slug]


def reset_agent(slug: str) -> Optional[Agent]:
    """Reset a single agent back to its file-defined defaults."""
    if slug not in _defaults:
        return None
    AGENTS[slug] = copy.deepcopy(_defaults[slug])
    return AGENTS[slug]


def delete_agent(slug: str) -> bool:
    """Delete an agent file and reload the registry."""
    if slug not in AGENTS:
        return False
    agent_path = Path(__file__).parent / f"{slug}.py"
    if agent_path.exists():
        agent_path.unlink()
    reload_agents()
    return True