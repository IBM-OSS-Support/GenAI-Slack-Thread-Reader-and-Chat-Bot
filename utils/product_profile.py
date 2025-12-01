# utils/product_profile.py
import re
import difflib
from typing import Dict, Optional, Tuple, List

import pandas as pd

from utils.thread_store import EXCEL_TABLES
from utils.global_kb import EXCEL_TABLES_GLOBAL

# Canonical column aliases (lowercased -> canonical key)
# Expand/adjust to your sheet headers as needed
COL_ALIASES = {
    "product name": "product_name",
    "product": "product_name",
    "name": "product_name",

    "other names this product is called (e.g. old names people still use)": "other_names",
    "aka": "other_names",
    "aliases": "other_names",

    "support owner": "support_owner",
    "2nd line or first line owner": "second_line_owner",
    "product manager": "product_manager",
    "product mgmt vp": "product_mgmt_vp",
    "development vp": "development_vp",
    "support director": "support_director",
    "support planner": "support_planner",
    "pillar": "pillar",

    "brief description of what product does for a client": "description",
    "mmt name (meeting where the cross functional team's meet and vote)": "mmt_name",
    "mmt name https://w3.ibm.com/w3publisher/software-support-community/new-page": "mmt_url",
}

PREFERRED_COL_ORDER = [
    "product_name",
    "other_names",
    "description",
    "product_manager",
    "product_mgmt_vp",
    "development_vp",
    "support_owner",
    "second_line_owner",
    "support_director",
    "support_planner",
    "pillar",
    "mmt_name",
    "mmt_url",
]

def _lower_cols(df: pd.DataFrame) -> Dict[str, str]:
    """
    Map df columns (lowercased) to canonical keys via COL_ALIASES.
    Returns mapping: {original_col_name: canonical_key}
    """
    mapping = {}
    for col in df.columns:
        lc = str(col).strip().lower()
        if lc in COL_ALIASES:
            mapping[col] = COL_ALIASES[lc]
        else:
            # attempt fuzzy match to aliases if exact not found
            best = difflib.get_close_matches(lc, COL_ALIASES.keys(), n=1, cutoff=0.92)
            if best:
                mapping[col] = COL_ALIASES[best[0]]
    return mapping

def _best_product_match(name: str, candidates: List[str]) -> Optional[str]:
    """
    Fuzzy pick the best product name from list of candidates.
    """
    if not candidates:
        return None
    # Try exact/ci first
    ci_map = {c: c for c in candidates}
    for c in candidates:
        if c.strip().lower() == name.strip().lower():
            return c
    # Fuzzy
    best = difflib.get_close_matches(name, candidates, n=1, cutoff=0.7)
    return best[0] if best else None

def _extract_row_profile(df: pd.DataFrame, row_idx: int, colmap: Dict[str, str]) -> Dict[str, str]:
    row = df.iloc[row_idx]
    profile: Dict[str, str] = {}
    for orig_col, canon in colmap.items():
        val = row.get(orig_col, "")
        if pd.isna(val):
            continue
        sval = str(val).strip()
        if sval:
            profile[canon] = sval
    # Normalize some keys if missing
    if "product_name" not in profile:
        # pick first available name-like value
        name_like = next((profile.get(k) for k in ("product", "name") if profile.get(k)), None)
        if name_like:
            profile["product_name"] = name_like
    return profile

def _format_slack_profile(profile: Dict[str, str], source_name: str) -> str:
    """
    Produce a compact Slack-friendly block of text.
    """
    lines = []
    title = profile.get("product_name") or "(unknown product)"
    lines.append(f"*• Product:* {title}")
    if profile.get("other_names"):
        lines.append(f"*• Also known as:* {profile['other_names']}")
    if profile.get("description"):
        lines.append(f"*• Description:* {profile['description']}")
    if profile.get("product_manager"):
        lines.append(f"*• Product Manager:* {profile['product_manager']}")
    if profile.get("product_mgmt_vp"):
        lines.append(f"*• Product Mgmt VP:* {profile['product_mgmt_vp']}")
    if profile.get("development_vp"):
        lines.append(f"*• Development VP:* {profile['development_vp']}")
    if profile.get("support_owner"):
        lines.append(f"*• Support Owner:* {profile['support_owner']}")
    if profile.get("second_line_owner"):
        lines.append(f"*• 2nd/1st Line Owner:* {profile['second_line_owner']}")
    if profile.get("support_director"):
        lines.append(f"*• Support Director:* {profile['support_director']}")
    if profile.get("support_planner"):
        lines.append(f"*• Support Planner:* {profile['support_planner']}")
    if profile.get("pillar"):
        lines.append(f"*• Pillar:* {profile['pillar']}")
    if profile.get("mmt_name"):
        lines.append(f"*• MMT:* {profile['mmt_name']}")
    if profile.get("mmt_url"):
        lines.append(f"*• MMT URL:* {profile['mmt_url']}")
    lines.append(f"_source: {source_name}_")
    return "\n".join(lines)

def _search_one_df(df: pd.DataFrame, product_query: str) -> Optional[Tuple[Dict[str, str], str]]:
    """
    Try to find a single best row for product_query in df.
    Return (profile, inferred_product_name) or None.
    """
    if df is None or df.empty:
        return None
    colmap = _lower_cols(df)
    # Identify product-name column(s)
    prod_cols = [orig for orig, canon in colmap.items() if canon == "product_name"]
    if not prod_cols:
        # heuristic: pick first column containing 'product' in header
        prod_cols = [c for c in df.columns if "product" in str(c).lower()]
        if not prod_cols:
            return None

    # Gather candidate names
    candidates: List[str] = []
    for c in prod_cols:
        try:
            series = df[c].dropna().astype(str).str.strip()
        except Exception:
            continue
        candidates.extend([x for x in series.tolist() if x])

    best = _best_product_match(product_query, candidates)
    if not best:
        # try contains match as last resort
        lc = product_query.lower()
        for c in prod_cols:
            mask = df[c].astype(str).str.lower().str.contains(re.escape(lc), na=False)
            idxs = mask[mask].index.tolist()
            if idxs:
                row_idx = idxs[0]
                prof = _extract_row_profile(df, row_idx, colmap)
                prof.setdefault("product_name", str(df.loc[row_idx, c]))
                return prof, prof.get("product_name", product_query)
        return None

    # find the row where product name equals best
    for c in prod_cols:
        mask = df[c].astype(str).str.strip() == best
        idxs = mask[mask].index.tolist()
        if idxs:
            row_idx = idxs[0]
            prof = _extract_row_profile(df, row_idx, colmap)
            prof.setdefault("product_name", best)
            return prof, best
    return None

def get_product_profile(product_query: str, thread_id: Optional[str] = None) -> Optional[str]:
    """
    Look in thread-local Excel first, then global Excel tables.
    Return a Slack-formatted string or None if not found.
    """
    # 1) Thread-local
    if thread_id and thread_id in EXCEL_TABLES:
        df = EXCEL_TABLES[thread_id]
        hit = _search_one_df(df, product_query)
        if hit:
            prof, pname = hit
            return _format_slack_profile(prof, f"thread_excel:{pname}")

    # 2) Global tables
    for fname, df in EXCEL_TABLES_GLOBAL:
        hit = _search_one_df(df, product_query)
        if hit:
            prof, pname = hit
            return _format_slack_profile(prof, f"{fname}:{pname}")

    return None
