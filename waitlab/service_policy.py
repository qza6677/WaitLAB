"""Policy values and status helpers for the application service layer."""

from __future__ import annotations


def normal_status(status: str) -> str:
    """Normalize lifecycle statuses from different Codex integrations."""

    return "".join(character for character in status.casefold() if character.isalnum())


AI_RUNNING_STATUSES = {"inprogress", "running"}
AI_ATTENTION_STATUSES = {
    "needsattention",
    "needsinput",
    "needsapproval",
    "waitingforinput",
    "waitingforapproval",
}
AI_STALE_AFTER_SECONDS = 5 * 60
AI_MISSING_AFTER_SECONDS = 5 * 60
AI_INITIAL_PROMPT_GRACE_SECONDS = 5 * 60
STATS_CACHE_TTL_SECONDS = 5.0
MAX_REMEMBERED_TURNS = 4096
