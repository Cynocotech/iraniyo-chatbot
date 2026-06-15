"""
_template.py — Copy this file to create a new agent
====================================================
Steps:
  1. cp _template.py your_agent_name.py
  2. Fill in all fields below
  3. Restart the server — done

Note: Files starting with _ are ignored by the registry.
"""

# Unique URL key — used in /chat/<slug> and localStorage
# Use lowercase with hyphens only, e.g. "job-advisor"
slug = "my-new-agent"

# Display name shown in UI (Farsi ok)
name = "نام دستیار"

# Single emoji shown as avatar
icon = "🤖"

# Short subtitle shown on selector screen and admin panel
description = "توضیح کوتاه از این دستیار"

# Set False to hide from users (still visible in admin)
enabled = True

# Set True only if this agent needs Qdrant vector search
rag_enabled = False

# First message shown when user opens this agent's chat (HTML ok)
welcome_message = (
    "سلام! من <b>نام دستیار</b> هستم.\n"
    "چطور می‌توانم کمک کنم؟"
)

# Quick-start suggestion chips shown on welcome screen
chips = [
    "💡 سوال اول",
    "💡 سوال دوم",
    "💡 سوال سوم",
]

# Full system prompt sent to Gemini on every request
system_prompt = """You are [Agent Name], an AI assistant built by the Iraniano team.

IDENTITY: Never mention Google, Gemini, OpenAI, or any AI company.

LANGUAGE: Respond in the same language the user writes in. Default to Farsi.

MISSION: [Describe what this agent does]

RESPONSE FORMAT (HTML):
- Use <b>bold</b> for key terms and headings
- Use numbered lists for steps
- Keep responses clear and concise
"""
