"""Stage 3: AI-powered question classification and condition generation.

Takes segmented blocks from Stage 2 and classifies each question into
a specific Forsta question type with all necessary attributes. Also
generates condition definitions for conditional logic / branching / termination.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

import re

from .ai_client import call_ai
from .config import OPENAI_MODEL, SEGMENTATION_CHUNK_SIZE, SEGMENTATION_CHUNK_OVERLAP, SELECT_TO_RADIO_MAX_OPTIONS
from .data.countries import COUNTRIES, COUNTRY_NAME_TO_CODE
from .data.us_states import US_STATES
from .prompts.classification import SYSTEM_PROMPT, build_classification_prompt

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Label helpers
# ---------------------------------------------------------------------------

def _block_label(name: str) -> str:
    """Convert a block name to a camelCase Forsta label with ``b`` prefix.

    ``"SUN CHASERS"`` -> ``"bSunChasers"``
    ``"RANDOMIZE PERSONA BLOCKS"`` -> ``"bRandomizePersonaBlocks"``
    """
    words = re.sub(r"[^a-zA-Z0-9\s]", "", name).split()
    if not words:
        return "b1"
    return "b" + "".join(w.capitalize() for w in words)


def _block_title(name: str) -> str:
    """Convert an ALL CAPS block name to title case for ``builder:title``.

    ``"SUN CHASERS"`` -> ``"Sun Chasers"``
    """
    return name.strip().title()


# ---------------------------------------------------------------------------
# Block-condition conversion
# ---------------------------------------------------------------------------

_BLOCK_COND_IF_RE = re.compile(
    r"IF\s+Q\.?\s*(\w[\w\s]*?)\s*(==|<>|!=)\s*(.+)",
    re.IGNORECASE,
)


def _convert_block_condition(raw_cond: str) -> Optional[Dict[str, str]]:
    """Convert a raw block condition like ``IF QCHILDREN == 1`` into a
    condition definition dict and a ``cond`` reference string.

    Returns ``{"label": "Has_Children", "cond": "(qChildren.r1)",
    "description": "...", "ref": "condition.Has_Children"}`` or *None*
    if the expression cannot be parsed.
    """
    m = _BLOCK_COND_IF_RE.match(raw_cond.strip())
    if not m:
        return None

    q_name_raw = m.group(1).strip()
    operator = m.group(2).strip()
    value_raw = m.group(3).strip()

    words = re.sub(r"[^a-zA-Z0-9\s]", "", q_name_raw).split()
    q_label = "q" + "".join(w.capitalize() for w in words)

    try:
        val_int = int(value_raw)
        row_label = f"r{val_int}"
    except ValueError:
        row_label = value_raw

    if operator in ("==", "="):
        cond_expr = f"({q_label}.{row_label})"
    else:
        cond_expr = f"not({q_label}.{row_label})"

    cond_label_words = [w.capitalize() for w in q_name_raw.split()]
    cond_label = "_".join(cond_label_words)
    if operator in ("==", "="):
        cond_label = f"Has_{cond_label}"
    else:
        cond_label = f"Not_{cond_label}"

    return {
        "label": cond_label,
        "cond": cond_expr,
        "description": raw_cond.strip(),
        "ref": f"condition.{cond_label}",
    }


# ---------------------------------------------------------------------------
# Chunking for classification (similar to segmenter but by segment count)
# ---------------------------------------------------------------------------

_CLASSIFICATION_CHUNK_SIZE = 30  # segments per chunk (questions are bigger than raw blocks)


def _chunk_segments(
    segments: List[dict],
    chunk_size: int = _CLASSIFICATION_CHUNK_SIZE,
) -> List[List[dict]]:
    """Split segments into chunks for classification.

    No overlap needed here because segments are already self-contained
    logical units from Stage 2.
    """
    if len(segments) <= chunk_size:
        return [segments]

    chunks = []
    for i in range(0, len(segments), chunk_size):
        chunks.append(segments[i : i + chunk_size])

    logger.info(f"Split {len(segments)} segments into {len(chunks)} classification chunks")
    return chunks


# ---------------------------------------------------------------------------
# Post-processing helpers
# ---------------------------------------------------------------------------

def _normalize_question(q: dict) -> dict:
    """Ensure all expected keys exist with sensible defaults."""
    defaults = {
        "forsta_type": "",
        "label": "",
        "title": "",
        "comment": None,
        "cond": None,
        "shuffle": False,
        "is_matrix": False,
        "answers": [],
        "matrix_cols": None,
        "matrix_rows": None,
        "special_handling": None,
        "verify": None,
        "size": None,
        "optional": 0,
        "atleast": None,
        "atmost": None,
        "content": None,
        "choices": [],
        "rows": [],
    }
    for key, default in defaults.items():
        if key not in q:
            q[key] = default
    return q


_LIKERT_TERMS = frozenset({
    "strongly agree", "agree", "slightly agree",
    "neutral",
    "slightly disagree", "disagree", "strongly disagree",
    "neither agree nor disagree",
})

_FOLLOWING_STATEMENT_RE = re.compile(r"following\s+statements?", re.IGNORECASE)


def _recover_statement_from_segment(seg: dict) -> Optional[str]:
    """Extract the agree/disagree statement from a raw segment.

    Checks two locations where the segmenter may have placed it:
    1. Appended to ``title_text`` after the "following statement" phrase.
    2. As the first entry in ``answer_lines`` (non-Likert text).
    """
    seg_title = seg.get("title_text", "")
    m = _FOLLOWING_STATEMENT_RE.search(seg_title)
    if m:
        after = seg_title[m.end():]
        after = re.sub(r'^[?.!:;\s]+', '', after).strip()
        if len(after) > 10:
            return after

    answer_lines = seg.get("answer_lines") or []
    if answer_lines:
        first = answer_lines[0].strip()
        if first.lower() not in _LIKERT_TERMS and len(first) > 10:
            return first

    return None


def _fix_agree_disagree_statements(
    questions: List[dict],
    original_segments: List[dict] = None,
) -> None:
    """Move misplaced statement text from answers into the title.

    Many agree/disagree questions arrive with the statement the respondent
    should evaluate embedded as the first answer row instead of being part
    of the title.  For single-statement matrix questions the statement may
    live in ``matrix_rows`` while the title only says "following statement".
    If the AI dropped the statement entirely, Case C recovers it from the
    original segment data.
    """
    seg_by_label: Dict[str, dict] = {}
    if original_segments:
        for seg in original_segments:
            lbl = seg.get("label", "")
            if lbl:
                seg_by_label[lbl] = seg

    for q in questions:
        ft = (q.get("forsta_type") or "").lower()
        if ft != "radio":
            continue
        title = q.get("title") or ""
        if not _FOLLOWING_STATEMENT_RE.search(title):
            continue

        statement_recovered = False

        # Case A: non-matrix radio with statement sitting in answers[0]
        if not q.get("is_matrix"):
            answers = q.get("answers") or []
            if len(answers) >= 2:
                first_text = (answers[0].get("text") or "").strip()
                if first_text.lower() not in _LIKERT_TERMS:
                    answers.pop(0)
                    for i, ans in enumerate(answers, 1):
                        ans["label"] = f"r{i}"
                    q["title"] = f"{title}\n\n{first_text}"
                    statement_recovered = True
                    logger.info(
                        f"Moved statement into title for {q.get('label')}: "
                        f"{first_text[:60]}..."
                    )

        # Case B: single-statement matrix -> convert to plain radio
        if q.get("is_matrix"):
            m_rows = q.get("matrix_rows") or []
            if len(m_rows) == 1:
                stmt = (m_rows[0].get("text") or str(m_rows[0])).strip()
                q["title"] = f"{title}\n\n{stmt}"
                q["is_matrix"] = False
                cols = q.get("matrix_cols") or []
                q["answers"] = [
                    {"label": f"r{i}", "text": c.get("text", str(c))}
                    for i, c in enumerate(cols, 1)
                ]
                q["matrix_cols"] = None
                q["matrix_rows"] = None
                statement_recovered = True
                logger.info(
                    f"Converted single-statement matrix to radio for "
                    f"{q.get('label')}: {stmt[:60]}..."
                )

        # Case C: AI dropped the statement entirely -- recover from segment
        if not statement_recovered and seg_by_label:
            current_title = q.get("title") or ""
            m_title = _FOLLOWING_STATEMENT_RE.search(current_title)
            if m_title:
                remainder = current_title[m_title.end():]
                remainder = re.sub(r'^[?.!:;\s]+', '', remainder).strip()
                if len(remainder) > 10:
                    continue

            answers = q.get("answers") or []
            all_likert = answers and all(
                (a.get("text") or "").strip().lower() in _LIKERT_TERMS
                for a in answers
            )
            if all_likert:
                seg = seg_by_label.get(q.get("label", ""))
                if seg:
                    statement = _recover_statement_from_segment(seg)
                    if statement:
                        q["title"] = f"{current_title}\n\n{statement}"
                        logger.info(
                            f"Recovered dropped statement for "
                            f"{q.get('label')}: {statement[:60]}..."
                        )


def _recover_title_newlines(
    questions: List[dict],
    original_segments: List[dict],
) -> None:
    """Restore newlines stripped by the classifier AI.

    If the segmenter preserved ``\\n`` characters in ``title_text`` but the
    classifier AI returned a flat single-line ``title``, this restores the
    original line breaks so ``_esc_title`` can render them as ``<br/>``.
    """
    seg_by_label: Dict[str, dict] = {}
    for seg in original_segments:
        lbl = seg.get("label", "")
        if lbl:
            seg_by_label[lbl] = seg

    for q in questions:
        title = q.get("title") or ""
        if not title or "\n" in title:
            continue

        seg = seg_by_label.get(q.get("label", ""))
        if not seg:
            continue

        seg_title = seg.get("title_text") or ""
        if "\n" not in seg_title:
            continue

        flat_seg = seg_title.replace("\r\n", " ").replace("\n", " ").replace("  ", " ").strip()
        flat_q = title.replace("  ", " ").strip()
        if flat_seg == flat_q or flat_q in flat_seg:
            q["title"] = seg_title
            logger.info(
                f"Recovered newlines in title for {q.get('label')} "
                f"from segment title_text"
            )


_STATEMENT_SPLIT_RE = re.compile(
    r"(following\s+statements?[?.!:;\s]+)",
    re.IGNORECASE,
)


def _format_statement_titles(questions: List[dict]) -> None:
    """Insert a line break between the question stem and inline statement.

    When the AI returns a title like "How much do you agree with the
    following statement? My family goes out of its way..." all on one line,
    this inserts ``\\n\\n`` so that ``_esc_title`` can later convert it to
    ``<br/><br/>`` for proper rendering in Forsta.
    """
    for q in questions:
        ft = (q.get("forsta_type") or "").lower()
        if ft in ("suspend", "block_start", "block_end", "note", ""):
            continue
        title = q.get("title") or ""
        if "\n" in title:
            continue

        m = _STATEMENT_SPLIT_RE.search(title)
        if not m:
            continue

        before = title[: m.end()].rstrip()
        after = title[m.end() :].strip()
        if len(after) > 10:
            q["title"] = f"{before}\n\n{after}"
            logger.info(
                f"Formatted statement line break in {q.get('label')}: "
                f"...{after[:50]}..."
            )


def _ensure_comments(questions: List[dict]) -> None:
    """Add default ``comment`` to radio/checkbox questions that lack one."""
    _TF = frozenset({"true", "false"})
    for q in questions:
        if q.get("comment"):
            continue
        ft = (q.get("forsta_type") or "").lower()
        if ft == "radio":
            answers = q.get("answers") or []
            texts = {(a.get("text") or "").strip().lower() for a in answers}
            if texts == _TF:
                continue
            if q.get("is_matrix"):
                q["comment"] = "Select one per row."
            else:
                q["comment"] = "Select one."
        elif ft == "checkbox":
            q["comment"] = "Select all that apply."


_ANCHOR_EXCLUSIVE_RE = re.compile(
    r"^(?:none(?:\s+of\s+the\s+(?:above|these))?|all\s+of\s+the\s+(?:above|these))$",
    re.IGNORECASE,
)

_ANCHOR_ONLY_RE = re.compile(
    r"^(?:"
    r"other(?:\s*[\(,].*)?|"
    r"(?:i\s+)?don'?t\s+know|"
    r"not\s+sure|unsure|"
    r"n/?a|not\s+applicable|"
    r"prefer\s+not\s+to\s+(?:answer|say|respond)"
    r")$",
    re.IGNORECASE,
)


def _enforce_anchor_exclusive(questions: List[dict]) -> None:
    """Auto-anchor catch-all answers and mark them exclusive on checkboxes.

    Ensures answers like "None of the above", "Other (specify)", "I don't
    know", etc. always have ``randomize="0"`` and, for checkbox questions,
    ``exclusive="1"`` where appropriate -- even if the document or AI did
    not explicitly mark them.
    """
    for q in questions:
        ft = (q.get("forsta_type") or "").lower()
        if ft not in ("radio", "checkbox"):
            continue

        is_checkbox = ft == "checkbox"
        for ans in q.get("answers") or []:
            text = (ans.get("text") or "").strip()
            if not text:
                continue

            if _ANCHOR_EXCLUSIVE_RE.match(text):
                if ans.get("randomize") is None:
                    ans["randomize"] = "0"
                    logger.info(f"Auto-anchored '{text}' in {q.get('label')}")
                if is_checkbox and not ans.get("exclusive"):
                    ans["exclusive"] = "1"
                    logger.info(f"Auto-exclusive '{text}' in {q.get('label')}")
            elif _ANCHOR_ONLY_RE.match(text):
                if ans.get("randomize") is None:
                    ans["randomize"] = "0"
                    logger.info(f"Auto-anchored '{text}' in {q.get('label')}")


_OPEN_END_INDICATOR_RE = re.compile(
    r"specify|open\s*end|open\-end|\[open\s*end\]|\[open\]",
    re.IGNORECASE,
)

_PLAIN_OTHER_RE = re.compile(r"^other$", re.IGNORECASE)


def _guard_other_open_end(
    questions: List[dict],
    original_segments: List[dict],
) -> None:
    """Strip ``open``/``openSize`` from "Other" rows unless the document
    explicitly requested an open-end (via "specify", "[OPEN END]", etc.).

    The AI sometimes adds ``open="1"`` to plain "Other" answers even when
    the questionnaire has no open-end indicator.
    """
    seg_by_label: Dict[str, dict] = {}
    for seg in original_segments:
        lbl = seg.get("label", "")
        if lbl:
            seg_by_label[lbl] = seg

    for q in questions:
        ft = (q.get("forsta_type") or "").lower()
        if ft not in ("radio", "checkbox"):
            continue

        for ans in q.get("answers") or []:
            if not ans.get("open"):
                continue

            text = (ans.get("text") or "").strip()
            if not _PLAIN_OTHER_RE.match(text):
                continue

            seg = seg_by_label.get(q.get("label", ""))
            if seg:
                for line in seg.get("answer_lines") or []:
                    if "other" in line.lower() and _OPEN_END_INDICATOR_RE.search(line):
                        break
                else:
                    ans.pop("open", None)
                    ans.pop("openSize", None)
                    logger.info(
                        f"Stripped open-end from plain 'Other' in "
                        f"{q.get('label')}: no open-end indicator in source"
                    )
            else:
                ans.pop("open", None)
                ans.pop("openSize", None)
                logger.info(
                    f"Stripped open-end from plain 'Other' in "
                    f"{q.get('label')}: no source segment to verify"
                )


def _guard_explicit_answers(
    questions: List[dict],
    original_segments: List[dict],
) -> None:
    """Override special_handling when the source segment provided explicit answers.

    The AI sometimes marks questions with ``special_handling: "numeric_range"``
    even when the document listed specific answer paragraphs (e.g. "None", "1",
    "2", "3", "4", "5 - 10", "More than 10").  When that happens the xml_builder
    would auto-generate a different option set.  This guard converts the
    original ``answer_lines`` to proper answer dicts and clears
    ``special_handling`` so the document's actual options are preserved.
    """
    seg_by_label: Dict[str, dict] = {}
    for seg in original_segments:
        lbl = seg.get("label", "")
        if lbl:
            seg_by_label[lbl] = seg

    for q in questions:
        sh = q.get("special_handling")
        if not sh:
            continue
        if sh in ("year_range", "us_states", "countries"):
            continue

        lbl = q.get("label", "")
        seg = seg_by_label.get(lbl)
        if not seg:
            continue

        answer_lines = seg.get("answer_lines") or []
        if len(answer_lines) < 2:
            continue

        logger.info(
            f"Guard: '{lbl}' has special_handling='{sh}' but segment "
            f"provided {len(answer_lines)} explicit answer_lines -- "
            f"overriding with explicit answers"
        )
        q["special_handling"] = None
        q["answers"] = [
            {"label": f"ch{i}", "text": text}
            for i, text in enumerate(answer_lines, 1)
        ]
        q["choices"] = []


_DROPDOWN_RE = re.compile(r"dropdown", re.IGNORECASE)


def _guard_select_without_dropdown(
    questions: List[dict],
    original_segments: List[dict],
    all_conditions: Optional[List[dict]] = None,
) -> None:
    """Convert ``select`` questions to ``radio`` when the source has no dropdown indicator.

    The LLM sometimes classifies short explicit-option single-select
    questions as ``select`` (dropdown) instead of ``radio``, and it does
    so inconsistently across classification chunks.  This guard enforces
    a deterministic rule: if the source segment has no ``[DROPDOWN]``
    indicator and the option count is within a configurable threshold,
    force the question to ``radio``.

    Must run **before** ``_resolve_cond_references`` so the condition
    resolver picks up the corrected ``r*`` labels naturally.
    """
    seg_by_label: Dict[str, dict] = {}
    for seg in original_segments:
        lbl = seg.get("label", "")
        if lbl:
            seg_by_label[lbl] = seg

    converted_labels: Dict[str, Dict[str, str]] = {}

    for q in questions:
        if q.get("forsta_type") != "select":
            continue
        if q.get("special_handling"):
            continue

        lbl = q.get("label", "")
        seg = seg_by_label.get(lbl)
        if not seg:
            continue

        modifiers = seg.get("inline_modifiers") or []
        title = seg.get("title_text") or ""
        if any(_DROPDOWN_RE.search(m) for m in modifiers) or _DROPDOWN_RE.search(title):
            continue

        options = q.get("choices") or q.get("answers") or []
        if len(options) > SELECT_TO_RADIO_MAX_OPTIONS:
            continue

        logger.info(
            f"Guard: '{lbl}' is select with {len(options)} options and no "
            f"[DROPDOWN] indicator -- converting to radio"
        )
        q["forsta_type"] = "radio"

        if q.get("choices") and not q.get("answers"):
            q["answers"] = q["choices"]
        q["choices"] = []

        label_map: Dict[str, str] = {}
        for i, ans in enumerate(q.get("answers") or [], 1):
            old_label = ans.get("label", "")
            if old_label.startswith("ch"):
                new_label = f"r{i}"
                label_map[old_label] = new_label
                ans["label"] = new_label

        if label_map:
            converted_labels[lbl] = label_map

    if not converted_labels:
        return

    # Safety-net sweep: update any stale (qLabel.chN) references that the
    # LLM may have emitted directly instead of using match= syntax.
    def _rewrite_cond(expr: str) -> str:
        if not expr:
            return expr
        for qlabel, lmap in converted_labels.items():
            for old, new in lmap.items():
                expr = expr.replace(f"{qlabel}.{old}", f"{qlabel}.{new}")
        return expr

    if all_conditions:
        for cond in all_conditions:
            c = cond.get("cond")
            if c:
                cond["cond"] = _rewrite_cond(c)

    for q in questions:
        c = q.get("cond")
        if c:
            q["cond"] = _rewrite_cond(c)


def _merge_conditions(all_conditions: List[dict]) -> List[dict]:
    """Deduplicate conditions by label, keeping the first occurrence."""
    seen = set()
    merged = []
    for cond in all_conditions:
        label = cond.get("label", "")
        if label and label not in seen:
            seen.add(label)
            merged.append(cond)
    return merged


def _build_conditions_context(conditions: List[dict]) -> str:
    """Build a text summary of known conditions for the AI prompt.

    This gives subsequent chunks awareness of conditions already defined
    in earlier chunks, preventing duplicate definitions and enabling
    cross-references.
    """
    if not conditions:
        return "None identified yet."

    lines = ["Previously identified conditions:"]
    for c in conditions:
        lines.append(
            f"  - {c.get('label', '?')}: {c.get('cond', '?')} "
            f"({c.get('description', '')})"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Condition reference resolver  (match=Value -> chN)
# ---------------------------------------------------------------------------

_MATCH_PATTERN = re.compile(
    r"(?P<neg>not)?\((?P<label>\w+)\.match=(?P<value>[^)]+)\)(?P<close>\))?"
)


def _build_question_lookup(
    questions: List[dict],
) -> Dict[str, dict]:
    """Build label -> question dict for quick lookups."""
    lookup: Dict[str, dict] = {}
    for q in questions:
        lbl = q.get("label")
        if lbl:
            lookup[lbl] = q
    return lookup


def _resolve_single_match(
    label: str,
    value: str,
    negated: bool,
    question: dict,
) -> Optional[str]:
    """Resolve one ``qLabel.match=Value`` expression to ``(qLabel.CODE)`` or ``(qLabel.chN)``."""
    sh = question.get("special_handling")
    value_lower = value.strip().lower()

    # --- Country lookups use ISO alpha-3 codes as labels ---
    if sh == "countries":
        code = COUNTRY_NAME_TO_CODE.get(value_lower)
        if code:
            ref = f"({label}.{code})"
            return f"not{ref}" if negated else ref
        logger.warning(
            f"Cannot resolve match='{value}' for {label}: country not found"
        )
        return None

    # --- Build a (label, text) pair list for other special_handling types ---
    choice_pairs: Optional[List[tuple]] = None  # [(choice_label, text), ...]

    if sh == "us_states":
        choice_pairs = [(f"ch{i}", s) for i, s in enumerate(US_STATES, 1)]
    elif sh == "year_range":
        y_start = question.get("year_start")
        y_end = question.get("year_end")
        if y_start is not None and y_end is not None:
            step = -1 if y_start > y_end else 1
            choice_pairs = [
                (f"ch{i}", str(y))
                for i, y in enumerate(range(int(y_start), int(y_end) + step, step), 1)
            ]
    elif sh == "numeric_range":
        r_start = question.get("range_start")
        r_end = question.get("range_end")
        if r_start is not None and r_end is not None:
            floor_label = question.get("floor_label")
            ceiling_label = question.get("ceiling_label")
            choice_pairs = []
            for i, n in enumerate(range(int(r_start), int(r_end) + 1), 1):
                if n == int(r_start) and floor_label:
                    choice_pairs.append((f"ch{i}", floor_label))
                elif n == int(r_end) and ceiling_label:
                    choice_pairs.append((f"ch{i}", ceiling_label))
                else:
                    choice_pairs.append((f"ch{i}", str(n)))

    # --- Fall back to explicit choices/answers from the AI ---
    if choice_pairs is None:
        answers = question.get("choices") or question.get("answers") or []
        if answers:
            choice_pairs = []
            country_hits = 0
            for a in answers:
                if isinstance(a, dict):
                    a_text = a.get("text", "")
                    code = COUNTRY_NAME_TO_CODE.get(a_text.lower())
                    if code:
                        country_hits += 1
                    choice_pairs.append((a.get("label", ""), a_text, code))
                else:
                    choice_pairs.append((f"ch{len(choice_pairs) + 1}", str(a), None))
            if country_hits >= 2:
                import re as _re
                choice_pairs = [
                    (code if code else _re.sub(r"[^A-Za-z0-9_]", "", text) or lbl, text, None)
                    for lbl, text, code in choice_pairs
                ]
            else:
                choice_pairs = [
                    (lbl or f"ch{i}", text, None)
                    for i, (lbl, text, _) in enumerate(choice_pairs, 1)
                ]
            choice_pairs = [(lbl, text) for lbl, text, _ in choice_pairs]

    if not choice_pairs:
        logger.warning(
            f"Cannot resolve match='{value}' for {label}: no choice list available"
        )
        return None

    for ch_label, item in choice_pairs:
        if item.strip().lower() == value_lower:
            ref = f"({label}.{ch_label})"
            return f"not{ref}" if negated else ref

    logger.warning(
        f"Cannot resolve match='{value}' for {label}: "
        f"value not found in {len(choice_pairs)}-item list"
    )
    return None


def _resolve_cond_expr(
    expr: str,
    q_lookup: Dict[str, dict],
) -> str:
    """Resolve all ``match=`` references inside a single cond expression string."""
    if not expr or "match=" not in expr:
        return expr

    def _replacer(m: re.Match) -> str:
        neg = m.group("neg") is not None
        label = m.group("label")
        value = m.group("value")
        question = q_lookup.get(label)
        if question is None:
            logger.warning(f"Condition references unknown question '{label}' -- defaulting to 1")
            return "1"
        resolved = _resolve_single_match(label, value, neg, question)
        if resolved is None:
            logger.warning(f"Unresolvable match='{value}' for {label} -- defaulting to 1")
            return "1"
        return resolved

    return _MATCH_PATTERN.sub(_replacer, expr)


def _normalize_cond_syntax(expr: str) -> str:
    """Fix common syntax issues in condition expressions.

    - Bare ``condition.X`` refs -> ``(condition.X)``
    - ``!(...)`` -> ``not(...)``
    - Numeric equality/comparison ``(qLabel=N)`` or ``(qLabel==N)``
      -> ``(qLabel.check('N'))``
    - Numeric inequality ``(qLabel<N)`` or ``(qLabel>N)`` or ``<=``/``>=``
      -> ``(qLabel.check('<N'))`` etc.
    """
    expr = re.sub(r"(?<!\()condition\.\w+", r"(\g<0>)", expr)
    expr = re.sub(r"!\(", "not(", expr)

    def _to_check(m: re.Match) -> str:
        label = m.group(1)
        op = m.group(2)
        value = m.group(3)
        if op in ("=", "=="):
            return f"{label}.check('{value}')"
        return f"{label}.check('{op}{value}')"

    expr = re.sub(
        r"(\b[a-zA-Z_]\w*)(==?|[<>]=?)\s*(\d[\d,\-]*)",
        _to_check,
        expr,
    )
    return expr


def _resolve_cond_references(
    conditions: List[dict],
    questions: List[dict],
) -> None:
    """Resolve all ``match=Value`` references in conditions and questions in-place."""
    q_lookup = _build_question_lookup(questions)

    for cond in conditions:
        expr = cond.get("cond")
        if expr:
            cond["cond"] = _normalize_cond_syntax(_resolve_cond_expr(expr, q_lookup))

    for q in questions:
        expr = q.get("cond")
        if expr:
            q["cond"] = _normalize_cond_syntax(_resolve_cond_expr(expr, q_lookup))


# ---------------------------------------------------------------------------
# Main classification function
# ---------------------------------------------------------------------------

def classify_segments(
    segments: List[dict],
    model: Optional[str] = None,
    progress_callback=None,
) -> Dict[str, List[dict]]:
    """Run AI classification on segmented blocks.

    Args:
        segments: Segmented blocks from segmenter.py
        model: OpenAI model override
        progress_callback: Optional callable(message: str) for UI updates

    Returns:
        Dict with:
            - "conditions": List of condition definition dicts
            - "questions": List of classified question dicts (in document order)
    """
    model = model or OPENAI_MODEL

    def _report(msg: str):
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

    # Filter to only question-type segments for classification.
    # Pagebreaks, metadata, and notes pass through directly.
    # Standalone "condition" segments are skipped: the segmenter already
    # attaches each condition to the adjacent question's "conditions" array,
    # so the AI sees the condition via the question and applies it as a
    # cond= attribute. Including standalone conditions would inflate the
    # classifiable count, causing the interleave pointers to desync.
    classifiable = []
    passthrough = []
    block_conditions: List[dict] = []

    for seg in segments:
        block_type = seg.get("block_type", "")
        sort_key = seg.get("paragraph_indices", [0])[0]
        if block_type in ("question", "text_screen", "term"):
            classifiable.append(seg)
        elif block_type == "pagebreak":
            passthrough.append({"forsta_type": "suspend", "_sort_key": sort_key})
        elif block_type == "block_marker":
            block_name = seg.get("block_name", seg.get("marker_type", ""))
            is_randomize = "RANDOMIZE" in block_name.upper()
            label = _block_label(block_name)
            entry: Dict[str, Any] = {
                "forsta_type": "block_start",
                "label": label,
                "block_name": block_name,
                "block_title": _block_title(block_name),
                "randomize_children": is_randomize,
                "_sort_key": sort_key,
            }
            raw_cond = seg.get("block_condition", "")
            if raw_cond:
                parsed = _convert_block_condition(raw_cond)
                if parsed:
                    entry["cond"] = parsed["ref"]
                    block_conditions.append({
                        "label": parsed["label"],
                        "cond": parsed["cond"],
                        "description": parsed["description"],
                    })
                    logger.info(
                        f"Block '{block_name}' -> cond={parsed['ref']}"
                    )
            passthrough.append(entry)
        elif block_type == "note":
            passthrough.append({
                "forsta_type": "note",
                "content": seg.get("content", ""),
                "_sort_key": sort_key,
            })
        elif block_type == "metadata":
            passthrough.append({
                "forsta_type": "block_end",
                "section_name": seg.get("content", "")[:80],
                "_sort_key": sort_key,
            })

    _report(
        f"Classifying {len(classifiable)} segments "
        f"({len(passthrough)} passthrough elements)..."
    )

    if not classifiable:
        _report("WARNING: No classifiable segments found -- nothing to send to AI.")
        return {
            "conditions": [],
            "questions": list(passthrough),
        }

    # Chunk the classifiable segments
    chunks = _chunk_segments(classifiable)

    all_conditions: List[dict] = []
    all_questions: List[dict] = []

    def _classify_chunk(i: int, chunk: List[dict]) -> Tuple[List[dict], List[dict]]:
        """Classify a single chunk through the AI (thread-safe)."""
        logger.info(f"Classifying chunk {i + 1}/{len(chunks)} ({len(chunk)} segments)...")
        blocks_json = json.dumps(chunk, separators=(",", ":"), default=str)
        user_prompt = build_classification_prompt(blocks_json)
        result = call_ai(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            model=model,
            expect_json=True,
        )
        if isinstance(result, dict):
            logger.info(f"Classification chunk {i + 1}: response keys = {list(result.keys())}")
            chunk_conditions = result.get("conditions", [])
            chunk_questions = result.get("questions", [])
            if not isinstance(chunk_conditions, list):
                logger.warning(f"Chunk {i + 1}: 'conditions' is {type(chunk_conditions).__name__}, not list")
                chunk_conditions = []
            if not isinstance(chunk_questions, list):
                logger.warning(f"Chunk {i + 1}: 'questions' is {type(chunk_questions).__name__}, not list")
                chunk_questions = []
        else:
            logger.warning(f"Unexpected response type from chunk {i + 1}: {type(result)}")
            chunk_conditions = []
            chunk_questions = []
        chunk_questions = [_normalize_question(q) for q in chunk_questions]

        # Attach _sort_key from input segment paragraph_indices so
        # the interleaver can place questions in correct document order
        # even when the AI returns more/fewer questions than input segments.
        # Uses label matching; AI-generated extras (e.g. term elements)
        # inherit the sort key of the nearest preceding match.
        seg_label_to_idx = {}
        for seg in chunk:
            seg_lbl = seg.get("label", "")
            if seg_lbl and seg_lbl != "?":
                seg_label_to_idx[seg_lbl] = seg.get("paragraph_indices", [0])[0]

        last_key = chunk[0].get("paragraph_indices", [0])[0] if chunk else 0
        for q in chunk_questions:
            qlabel = q.get("label", "")
            matched_key = seg_label_to_idx.get(qlabel)
            if matched_key is not None:
                q["_sort_key"] = matched_key
                last_key = matched_key
            else:
                q["_sort_key"] = last_key

        logger.info(
            f"Chunk {i + 1}: {len(chunk_questions)} questions, "
            f"{len(chunk_conditions)} conditions"
        )
        return chunk_conditions, chunk_questions

    if len(chunks) == 1:
        _report(f"Classifying chunk 1/1 ({len(chunks[0])} segments)...")
        conds, qs = _classify_chunk(0, chunks[0])
        all_conditions.extend(conds)
        all_questions.extend(qs)
        _report(f"Chunk 1: {len(qs)} questions, {len(conds)} conditions")
    else:
        max_workers = min(len(chunks), 5)
        _report(f"Classifying {len(chunks)} chunks in parallel (max_workers={max_workers})...")

        from .ai_client import get_client
        get_client()

        chunk_results: List[Tuple[List[dict], List[dict]]] = [([], []) for _ in chunks]
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx = {
                executor.submit(_classify_chunk, i, chunk): i
                for i, chunk in enumerate(chunks)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                chunk_results[idx] = future.result()
                _report(f"Classification chunk {idx + 1}/{len(chunks)} complete")
        for conds, qs in chunk_results:
            all_conditions.extend(conds)
            all_questions.extend(qs)

    # Include conditions generated from block markers (deterministic)
    all_conditions.extend(block_conditions)

    # Merge and deduplicate conditions
    all_conditions = _merge_conditions(all_conditions)

    # Convert select → radio when source has no [DROPDOWN] indicator
    _guard_select_without_dropdown(all_questions, classifiable, all_conditions)

    # Resolve any match=Value condition references to actual chN/rN indices
    _resolve_cond_references(all_conditions, all_questions)

    # Deterministic post-processing
    _guard_explicit_answers(all_questions, classifiable)
    _fix_agree_disagree_statements(all_questions, classifiable)
    _recover_title_newlines(all_questions, classifiable)
    _format_statement_titles(all_questions)
    _ensure_comments(all_questions)
    _enforce_anchor_exclusive(all_questions)
    _guard_other_open_end(all_questions, classifiable)

    # Now interleave passthrough elements (pagebreaks, comments) back
    # into the question list in their original document order.
    # We do this by tracking segment indices.
    final_questions = _interleave_passthrough(segments, all_questions, passthrough)

    _report(
        f"Classification complete: {len(final_questions)} elements, "
        f"{len(all_conditions)} conditions"
    )

    return {
        "conditions": all_conditions,
        "questions": final_questions,
    }


def _interleave_passthrough(
    original_segments: List[dict],
    classified_questions: List[dict],
    passthrough_elements: List[dict],
) -> List[dict]:
    """Reconstruct document order by merging classified questions with
    passthrough elements using their ``_sort_key`` (paragraph index).

    This is resilient to count mismatches between classifiable segments
    and AI output (e.g. the AI splitting one segment into two questions).
    """
    all_elements = list(classified_questions) + list(passthrough_elements)
    all_elements.sort(key=lambda x: x.get("_sort_key", 0))

    # Strip the internal sort key from the final output
    for el in all_elements:
        el.pop("_sort_key", None)

    return all_elements


# ---------------------------------------------------------------------------
# Convenience: full pipeline from segments
# ---------------------------------------------------------------------------

def classify_from_file(
    file_path: str,
    model: Optional[str] = None,
    progress_callback=None,
) -> Dict[str, List[dict]]:
    """Extract, segment, and classify a .docx file in one call."""
    from .segmenter import segment_from_file

    segments = segment_from_file(file_path, model=model, progress_callback=progress_callback)
    return classify_segments(segments, model=model, progress_callback=progress_callback)


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python -m survey_xml_generator.classifier <path_to_docx>")
        sys.exit(1)

    result = classify_from_file(sys.argv[1], progress_callback=print)
    print(json.dumps(result, indent=2, default=str))
    print(f"\n--- {len(result['conditions'])} conditions, {len(result['questions'])} questions ---")
