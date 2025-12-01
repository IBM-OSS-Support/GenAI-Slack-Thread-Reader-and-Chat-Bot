import json
import re
from typing import List, Optional, Union

from chains.llm_provider import get_llm

# Optional: provide your headings so the LLM prefers canonical wording without inventing new fields.
DEFAULT_HEADINGS: List[str] = [
    "Product name",
    "Other names this product is called (e.g. old names people still use)",
    "Support Owner",
    "2nd line or first line owner",
    "Brief Description of what product does for a client",
    "MMT Name (meeting where the cross functional team's meet and vote)",
    "Support Planner",
    "Product Manager",
    "Product mgmt VP",
    "Development VP",
    "Support Director",
    "MMT Name https://w3.ibm.com/w3publisher/software-support-community/new-page",
    "Pillar",
]

PROMPT_TEMPLATE = """You are *QueryClean*, a guardrail that ONLY:
- fixes typos/spelling,
- Match with existing product/field names from the org dataset,
- clarifies obvious grammar,
- expands trivial shorthands (e.g., 'mgr' -> 'manager'),
- keeps the *original meaning*,
- and NEVER adds facts, fields, or product names that the user did not provide.

Context (column headings from the org dataset):
{headings}

Rules:
- Do NOT answer the question.
- Do NOT infer or invent entities or owners.
- Preserve product/proper names (only correct obvious typos).
- Keep the user’s intent unchanged.
- If the question is already clear, return it unchanged.
- Output ONLY JSON with this shape:
  {{"normalized_query": "<cleaned-and-corrected-question>"}}

User question:
\"\"\"{question}\"\"\""""

# Non-greedy JSON object capture (first {} block)
_JSON_RE = re.compile(r"\{.*?\}", re.DOTALL)

def _coerce_text(raw: Union[str, object]) -> str:
    """
    Convert model output to text. Supports AIMessage-like objects with `.content`.
    Falls back to `str(raw)`.
    """
    if isinstance(raw, str):
        return raw
    # Common ChatMessage/AIMessage shapes
    content = getattr(raw, "content", None)
    if isinstance(content, str):
        return content
    return str(raw)

def _extract_json(text: str) -> Optional[dict]:
    """
    Robustly extract the first JSON object found in text.
    """
    if not isinstance(text, str):
        return None
    m = _JSON_RE.search(text.strip())
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None

def preanalyze_question(
    question: str,
    headings: Optional[List[str]] = None,
    max_len: int = 512
) -> str:
    """
    Calls your LLM with a strict JSON-only prompt to return a spelling/clarity-corrected question.
    Falls back to the original question on any error.
    """
    if not question:
        return question

    h = headings or DEFAULT_HEADINGS
    headings_str = "\n".join(f"- {x}" for x in h)

    prompt = PROMPT_TEMPLATE.format(
        headings=headings_str,
        question=question[:max_len]  # keep prompt small and deterministic
    )

    llm = get_llm()
    try:
        raw = llm.invoke(prompt)  # may be a str or an AIMessage-like object
        raw_text = _coerce_text(raw)
        data = _extract_json(raw_text) or {}
        normalized = (data.get("normalized_query") or "").strip()
        return normalized or question
    except Exception:
        return question
