"""Stage 4: Deterministic XML generation from classified question objects.

Each build_* function takes a classified question dict and returns a Forsta
XML string. No AI calls -- just clean template mapping.
"""

from __future__ import annotations

import html as html_mod
import re
from typing import Any, Dict, List, Optional

from .data.us_states import US_STATES
from .data.countries import COUNTRIES, COUNTRY_NAME_TO_CODE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    """Escape text for XML content (ampersand, angle brackets)."""
    if not text:
        return ""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _esc_title(text: str) -> str:
    """Escape title text and convert newlines to <br/> tags."""
    escaped = _esc(text)
    escaped = escaped.replace("\r\n", "\n").replace("\r", "\n")
    escaped = escaped.replace("\n\n", "<br/><br/>")
    escaped = escaped.replace("\n", "<br/>")
    return escaped


def _attr_str(attrs: Dict[str, str]) -> str:
    """Build an attribute string from a dict, skipping None/empty values."""
    parts = []
    for k, v in attrs.items():
        if v is not None and v != "":
            parts.append(f'{k}="{v}"')
    return " ".join(parts)


def _indent(lines: List[str], level: int = 1) -> List[str]:
    """Indent a list of XML lines."""
    prefix = "  " * level
    return [f"{prefix}{line}" for line in lines]


def _str_or(value, default):
    """Convert value to string, using default if value is None."""
    if value is None:
        return str(default)
    return str(value)


def _apply_country_codes(choices: List[Dict]) -> List[Dict]:
    """Replace generic labels (ch1, ch2...) with ISO alpha-3 country codes.

    Only activates when at least 2 choices match a known country name.
    Non-country entries (e.g. "Other") get the text itself as the label.
    """
    mapped = []
    country_hits = 0
    for ch in choices:
        text = ch.get("text", "")
        code = COUNTRY_NAME_TO_CODE.get(text.lower())
        if code:
            country_hits += 1
            mapped.append({"label": code, "text": text, "_matched": True})
        else:
            mapped.append({**ch, "_matched": False})

    if country_hits < 2:
        return choices

    result = []
    for m in mapped:
        matched = m.pop("_matched", False)
        if not matched:
            text = m.get("text", m.get("label", ""))
            m["label"] = re.sub(r"[^A-Za-z0-9_]", "", text) or m.get("label", "other")
        result.append(m)
    return result


# ---------------------------------------------------------------------------
# Row / choice / column builders
# ---------------------------------------------------------------------------

def _build_row(label: str, text: str, attrs: Optional[Dict] = None) -> str:
    """Build a <row> element."""
    extra = ""
    if attrs:
        extra_parts = []
        for k, v in attrs.items():
            if v is not None:
                extra_parts.append(f'{k}="{v}"')
        if extra_parts:
            extra = " " + " ".join(extra_parts)
    return f'<row label="{label}"{extra}>{_esc(text)}</row>'


def _build_choice(label: str, text: str) -> str:
    """Build a <choice> element."""
    return f'<choice label="{label}">{_esc(text)}</choice>'


def _build_col(label: str, text: str, attrs: Optional[Dict] = None) -> str:
    """Build a <col> element."""
    extra = ""
    if attrs:
        extra_parts = [f'{k}="{v}"' for k, v in attrs.items() if v is not None]
        if extra_parts:
            extra = " " + " ".join(extra_parts)
    return f'<col label="{label}"{extra}>{_esc(text)}</col>'


# ---------------------------------------------------------------------------
# Question type builders
# ---------------------------------------------------------------------------

def build_radio(q: Dict[str, Any]) -> str:
    """Single-select radio question."""
    attrs = {"label": q["label"]}
    if q.get("shuffle"):
        attrs["shuffle"] = "rows"
    if q.get("cond"):
        attrs["cond"] = q["cond"]
    if q.get("values"):
        attrs["values"] = q["values"]
    if q.get("averages"):
        attrs["averages"] = q["averages"]

    lines = [f"<radio {_attr_str(attrs)}>"]
    lines.append(f"  <title>{_esc_title(q.get('title', ''))}</title>")
    if q.get("comment"):
        lines.append(f"  <comment>{_esc(q['comment'])}</comment>")

    # Matrix: cols then rows
    if q.get("is_matrix") and q.get("matrix_cols"):
        for col in q["matrix_cols"]:
            col_attrs = {}
            if col.get("value"):
                col_attrs["value"] = col["value"]
            lines.append(f"  {_build_col(col['label'], col['text'], col_attrs or None)}")

    # Rows (answers or matrix rows)
    answer_key = "matrix_rows" if q.get("is_matrix") else "answers"
    answers_list = q.get(answer_key, [])
    if answer_key == "answers" and answers_list:
        answers_list = _apply_country_codes(answers_list)
    for ans in answers_list:
        row_attrs = {}
        if ans.get("randomize") is not None:
            row_attrs["randomize"] = str(ans["randomize"])
        if ans.get("open"):
            row_attrs["open"] = "1"
            row_attrs["openSize"] = str(ans.get("openSize", 25))
        if ans.get("cond"):
            row_attrs["cond"] = ans["cond"]
        lines.append(f"  {_build_row(ans['label'], ans['text'], row_attrs or None)}")

    lines.append("</radio>")
    return "\n".join(lines)


def build_checkbox(q: Dict[str, Any]) -> str:
    """Multi-select checkbox question."""
    attrs = {"label": q["label"]}
    atleast = q.get("atleast")
    if atleast is not None:
        attrs["atleast"] = str(atleast)
    else:
        attrs["atleast"] = "1"
    atmost = q.get("atmost")
    if atmost is not None:
        attrs["atmost"] = str(atmost)
    if q.get("shuffle"):
        attrs["shuffle"] = "rows"
    if q.get("cond"):
        attrs["cond"] = q["cond"]

    lines = [f"<checkbox {_attr_str(attrs)}>"]
    lines.append(f"  <title>{_esc_title(q.get('title', ''))}</title>")
    if q.get("comment"):
        lines.append(f"  <comment>{_esc(q['comment'])}</comment>")

    # If this is a matrix checkbox (cols present)
    if q.get("matrix_cols"):
        for col in q["matrix_cols"]:
            col_attrs = {}
            if col.get("exclusive"):
                col_attrs["exclusive"] = "1"
            if col.get("randomize") is not None:
                col_attrs["randomize"] = str(col["randomize"])
            lines.append(f"  {_build_col(col['label'], col['text'], col_attrs or None)}")

    for ans in q.get("answers", []):
        row_attrs = {}
        if ans.get("exclusive"):
            row_attrs["exclusive"] = "1"
        if ans.get("randomize") is not None:
            row_attrs["randomize"] = str(ans["randomize"])
        if ans.get("open"):
            row_attrs["open"] = "1"
            row_attrs["openSize"] = str(ans.get("openSize", 25))
            if ans.get("openOptional"):
                row_attrs["openOptional"] = "1"
        if ans.get("cond"):
            row_attrs["cond"] = ans["cond"]
        lines.append(f"  {_build_row(ans['label'], ans['text'], row_attrs or None)}")

    lines.append("</checkbox>")
    return "\n".join(lines)


def build_select(q: Dict[str, Any]) -> str:
    """Dropdown select question."""
    attrs = {"label": q["label"]}
    if q.get("cond"):
        attrs["cond"] = q["cond"]

    lines = [f"<select {_attr_str(attrs)}>"]
    lines.append(f"  <title>{_esc_title(q.get('title', ''))}</title>")
    if q.get("comment"):
        lines.append(f"  <comment>{_esc(q['comment'])}</comment>")

    # Explicit choices from the document always take precedence over auto-population.
    choices = q.get("choices") or q.get("answers") or []
    if choices:
        choices = _apply_country_codes(choices)
    elif q.get("special_handling") == "us_states":
        choices = [{"label": f"ch{i}", "text": s} for i, s in enumerate(US_STATES, 1)]
    elif q.get("special_handling") == "countries":
        choices = [{"label": code, "text": name} for code, name in COUNTRIES]
    elif q.get("special_handling") == "year_range":
        from datetime import date
        current_year = date.today().year
        default_start = current_year - 17
        default_end = current_year - 100
        start = q.get("year_start", default_start)
        end = q.get("year_end", default_end)
        step = -1 if start > end else 1
        years = list(range(start, end + step, step))
        choices = []
        for i, y in enumerate(years, 1):
            if y == years[0]:
                text = f"{y} or later"
            elif y == years[-1]:
                text = f"{y} or earlier"
            else:
                text = str(y)
            choices.append({"label": f"ch{i}", "text": text})
    elif q.get("special_handling") == "numeric_range":
        start = q.get("range_start", 1)
        end = q.get("range_end", 10)
        floor_label = q.get("floor_label", "")
        ceiling_label = q.get("ceiling_label", q.get("range_suffix", ""))
        step = 1 if start <= end else -1
        nums = list(range(start, end + step, step))
        choices = []
        for i, n in enumerate(nums, 1):
            text = str(n)
            if floor_label and n == nums[0]:
                text = floor_label
            elif ceiling_label and n == nums[-1]:
                text = ceiling_label
            choices.append({"label": f"ch{i}", "text": text})

    for ch in choices:
        lines.append(f"  {_build_choice(ch['label'], ch['text'])}")

    lines.append("</select>")
    return "\n".join(lines)


def build_text(q: Dict[str, Any]) -> str:
    """Open-ended text input."""
    attrs = {
        "label": q["label"],
        "optional": _str_or(q.get("optional"), 0),
        "size": _str_or(q.get("size"), 25),
    }
    if q.get("verify"):
        attrs["verify"] = q["verify"]
    if q.get("cond"):
        attrs["cond"] = q["cond"]

    lines = [f"<text {_attr_str(attrs)}>"]
    lines.append(f"  <title>{_esc_title(q.get('title', ''))}</title>")
    if q.get("comment"):
        lines.append(f"  <comment>{_esc(q['comment'])}</comment>")

    # Multi-row text (e.g., "first three words that come to mind")
    if q.get("rows"):
        for row in q["rows"]:
            row_attrs = {}
            if row.get("optional"):
                row_attrs["optional"] = "1"
            lines.append(f"  {_build_row(row['label'], row['text'], row_attrs or None)}")

    lines.append("</text>")
    return "\n".join(lines)


def build_textarea(q: Dict[str, Any]) -> str:
    """Long-form open-ended textarea."""
    attrs = {
        "label": q["label"],
        "optional": _str_or(q.get("optional"), 0),
    }
    if q.get("width"):
        attrs["width"] = str(q["width"])
    if q.get("height"):
        attrs["height"] = str(q["height"])
    if q.get("cond"):
        attrs["cond"] = q["cond"]

    lines = [f"<textarea {_attr_str(attrs)}>"]
    lines.append(f"  <title>{_esc_title(q.get('title', ''))}</title>")
    if q.get("comment"):
        lines.append(f"  <comment>{_esc(q['comment'])}</comment>")
    lines.append("</textarea>")
    return "\n".join(lines)


def build_number(q: Dict[str, Any]) -> str:
    """Numeric input."""
    attrs = {
        "label": q["label"],
        "optional": _str_or(q.get("optional"), 0),
        "size": _str_or(q.get("size"), 10),
    }
    if q.get("verify"):
        attrs["verify"] = q["verify"]
    if q.get("cond"):
        attrs["cond"] = q["cond"]

    lines = [f"<number {_attr_str(attrs)}>"]
    lines.append(f"  <title>{_esc_title(q.get('title', ''))}</title>")
    if q.get("comment"):
        lines.append(f"  <comment>{_esc(q['comment'])}</comment>")

    # Multi-row number (e.g., domestic + international spend)
    if q.get("rows"):
        for row in q["rows"]:
            row_attrs = {}
            if row.get("verify"):
                row_attrs["verify"] = row["verify"]
            lines.append(f"  {_build_row(row['label'], row['text'], row_attrs or None)}")

    lines.append("</number>")
    return "\n".join(lines)


def build_html_block(q: Dict[str, Any]) -> str:
    """Informational HTML display block (not a question)."""
    attrs = {"label": q["label"], "where": "survey"}
    if q.get("cond"):
        attrs["cond"] = q["cond"]

    content = q.get("content", q.get("title", ""))
    return f'<html {_attr_str(attrs)}>{_esc(content)}</html>'


def build_term(q: Dict[str, Any]) -> str:
    """Termination block."""
    attrs = {"label": q["label"]}
    if q.get("cond"):
        attrs["cond"] = q["cond"]

    content = q.get("content", q.get("title", ""))
    return f'<term {_attr_str(attrs)}>{_esc(content)}</term>'


def build_condition(cond: Dict[str, Any]) -> str:
    """Build a <condition> definition (goes at the top of the survey)."""
    label = cond["label"]
    expression = cond["cond"]
    description = cond.get("description", label)
    return f'<condition label="{label}" cond="{_esc(expression)}">{_esc(description)}</condition>'


def build_suspend() -> str:
    """Page break / suspend tag."""
    return "<suspend/>"


def build_block_open(
    label: str,
    title: Optional[str] = None,
    randomize_children: bool = False,
    randomize: bool = False,
    cond: Optional[str] = None,
) -> str:
    """Opening tag for a Forsta <block> element."""
    attrs: Dict[str, str] = {"label": label}
    if cond:
        attrs["cond"] = cond
    if title:
        attrs["builder:title"] = title
    if randomize_children:
        attrs["randomizeChildren"] = "1"
    if randomize:
        attrs["randomize"] = "1"
    return f"<block {_attr_str(attrs)}>"


def build_block_close() -> str:
    """Closing tag for a Forsta <block> element."""
    return "</block>"


# ---------------------------------------------------------------------------
# Standard zip code block
# ---------------------------------------------------------------------------

def build_zipcode_block(label: str, title: str, cond: str = "") -> str:
    """Build the standard Forsta zip code block with hidden backend questions.

    Includes the zip code text input with validation, plus hidden questions
    for DMA market, state, division, and region derived from FPzipcodes.dat.
    """
    from .data.zipcode_block import (
        DMA_MARKETS, ZIP_STATES, ZIP_DIVISIONS, ZIP_REGIONS,
    )

    cond_attr = f' cond="{cond}"' if cond else ""
    title_esc = _esc(title) if title else "What is your five-digit zip code?"

    lines = [
        f'<block label="bZipCode"{cond_attr} builder:title="Zip Code">',
        '  <exec when="init">',
        'dataFile = File("FPzipcodes.dat", "ZIP")',
        '  </exec>',
        '',
        f'  <text',
        f'  label="{label}"',
        '  optional="0"',
        '  randomize="0"',
        '  size="5"',
        '  verify="zipcode">',
        f'    <title>{title_esc}</title>',
        '    <validate>',
        f'#RECORD = dataFile.get( {label}.val )',
        '',
        '#if not(RECORD):',
        '  #error(res.zipError)',
        '    </validate>',
        '',
        '  </text>',
        '',
        '  <suspend/>',
        '',
        '  <text',
        '  label="vRESPDATA"',
        '  randomize="0"',
        '  where="execute,survey,report">',
        '    <title>HIDDEN - Respondent Data</title>',
        '    <exec>',
        f'zipx={label}.val',
        'respData = dataFile.get(zipx)',
        '',
        'if respData:',
        ' vRESPDATA.r1.val = zipx',
        " vRESPDATA.r2.val = respData['state']",
        " vRESPDATA.r3.val = respData['dma name']",
        '    </exec>',
        '',
        '    <row label="r1">zip</row>',
        '    <row label="r2">State</row>',
        '    <row label="r3">DMA Name</row>',
        '  </text>',
        '',
        '  <suspend/>',
        '',
        '  <exec>',
        'for x in qZipMarket.rows:',
        ' if x.text==vRESPDATA.r3.val:',
        '  qZipMarket.val=x.index',
        '  </exec>',
        '',
        '  <suspend/>',
        '',
        '  <radio',
        '  label="qZipMarket"',
        '  optional="1"',
        '  randomize="0"',
        '  where="execute,survey,report">',
        '    <title><p>HIDDEN - Market by Zip Code</p></title>',
    ]

    for lbl, name in DMA_MARKETS:
        lines.append(f'    <row label="{lbl}">{_esc(name)}</row>')

    lines += [
        '  </radio>',
        '',
        '  <exec>',
        'for x in qZipState.rows:',
        ' if x.text==vRESPDATA.r2.val:',
        '  qZipState.val=x.index',
        '  </exec>',
        '',
        '  <suspend/>',
        '',
        '  <radio',
        '  label="qZipState"',
        '  optional="1"',
        '  randomize="0"',
        '  where="execute,survey,report">',
        '    <title>HIDDEN - State by Zip Code</title>',
    ]

    for lbl, name in ZIP_STATES:
        lines.append(f'    <row label="{lbl}">{_esc(name)}</row>')

    lines += [
        '  </radio>',
        '',
        '  <suspend/>',
        '',
        '  <exec cond="qZipState.any">',
        "cat = qZipState.selected.label",
        "qZipRegion.val = int(cat[2:3]) - 1",
        "qZipDivision.val = int(cat[3:4]) - 1",
        '  </exec>',
        '',
        '  <radio',
        '  label="qZipDivision"',
        '  optional="1"',
        '  randomize="0"',
        '  where="execute,survey,report">',
        '    <title>HIDDEN - Division by Zip Code</title>',
    ]

    for lbl, name in ZIP_DIVISIONS:
        lines.append(f'    <row label="{lbl}">{_esc(name)}</row>')

    lines += [
        '  </radio>',
        '',
        '  <radio',
        '  label="qZipRegion"',
        '  optional="1"',
        '  randomize="0"',
        '  where="execute,survey,report">',
        '    <title>HIDDEN - Region by Zip Code</title>',
    ]

    for lbl, name in ZIP_REGIONS:
        lines.append(f'    <row label="{lbl}">{_esc(name)}</row>')

    lines += [
        '  </radio>',
        '</block>',
    ]

    return "\n".join(lines)


def _is_zipcode_question(q: Dict[str, Any]) -> bool:
    """Detect whether a classified question is a zip code question."""
    label = (q.get("label") or "").lower()
    verify = (q.get("verify") or "").lower()
    return (
        "zip" in label
        or verify == "zipcode"
        or verify == "range(10000,99999)"
    )


# ---------------------------------------------------------------------------
# Standard age / generation block
# ---------------------------------------------------------------------------

_GENERATIONS = [
    ("r1", "Gen Z (1997+)"),
    ("r2", "Millennials (1981-1996)"),
    ("r3", "Gen X (1965-1980)"),
    ("r4", "Baby Boomers+ (1964 or earlier)"),
]


def build_age_block(q: Dict[str, Any], terms: Optional[List[Dict[str, Any]]] = None) -> str:
    """Build the standard Forsta age block with a hidden generation question.

    Wraps the original age select dropdown in a block and appends an exec
    script that computes the respondent's generation from the selected birth
    year, storing the result in a hidden ``qGeneration`` radio.

    If *terms* is provided, they are emitted inside the block between the
    age select suspend and the generation exec script.
    """
    label = q.get("label", "qAge")
    select_xml = build_select(q)

    lines = [
        '<block label="bAge" builder:title="Age">',
    ]

    for line in select_xml.split("\n"):
        lines.append(f"  {line}")

    lines += [
        '',
        '  <suspend/>',
        '',
    ]

    if terms:
        for t in terms:
            lines.append(f"  {build_term(t)}")
            lines.append('')
            lines.append('  <suspend/>')
            lines.append('')

    lines += [
        '  <exec>',
        f'selectedText = {label}.selected.text if {label}.any else ""',
        'year = 0',
        'if selectedText:',
        ' parts = selectedText.split()',
        ' try:',
        '  year = int(parts[0])',
        ' except:',
        '  year = 0',
        '',
        'if year >= 1997:',
        ' qGeneration.val = 0',
        'elif year >= 1981:',
        ' qGeneration.val = 1',
        'elif year >= 1965:',
        ' qGeneration.val = 2',
        'elif year > 0:',
        ' qGeneration.val = 3',
        '  </exec>',
        '',
        '  <radio',
        '  label="qGeneration"',
        '  optional="1"',
        '  randomize="0"',
        '  where="execute,survey,report">',
        '    <title>HIDDEN - Generation by Age</title>',
    ]

    for lbl, name in _GENERATIONS:
        lines.append(f'    <row label="{lbl}">{name}</row>')

    lines += [
        '  </radio>',
        '</block>',
    ]

    return "\n".join(lines)


def _is_age_question(q: Dict[str, Any]) -> bool:
    """Detect whether a classified question is an age/birth-year question."""
    label = (q.get("label") or "").lower()
    forsta_type = (q.get("forsta_type") or "").lower()
    special = (q.get("special_handling") or "").lower()
    return (
        "age" in label
        and forsta_type == "select"
    ) or special == "year_range"


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_BUILDERS = {
    "radio": build_radio,
    "checkbox": build_checkbox,
    "select": build_select,
    "text": build_text,
    "textarea": build_textarea,
    "number": build_number,
    "html": build_html_block,
    "term": build_term,
}


def build_question(q: Dict[str, Any]) -> Optional[str]:
    """Route a classified question dict to the right builder.

    Returns the XML string, or None if the type is unknown.
    """
    forsta_type = q.get("forsta_type", "").lower()
    builder = _BUILDERS.get(forsta_type)
    if builder:
        return builder(q)
    return None
