"""Stage 5: Final XML assembly and validation.

Takes classified questions and conditions from Stage 3, runs each through
the deterministic xml_builder (Stage 4), wraps everything in the Forsta
<survey> root element, and performs basic validation.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .config import SURVEY_NAMESPACES, SURVEY_ROOT_DEFAULTS
from .xml_builder import (
    build_question,
    build_condition,
    build_suspend,
    build_block_open,
    build_block_close,
    build_zipcode_block,
    _is_zipcode_question,
    build_age_block,
    _is_age_question,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _deduplicate_labels(questions: List[dict]) -> List[str]:
    """Auto-number duplicate labels and return informational messages.

    Mutates ``questions`` in-place so that every element has a unique label.
    When a label appears N times, each occurrence gets a numeric suffix
    (e.g. textLeisureTrips -> textLeisureTrips1, textLeisureTrips2).
    """
    info: List[str] = []
    count: Dict[str, int] = {}
    for q in questions:
        label = q.get("label", "")
        if not label:
            continue
        count[label] = count.get(label, 0) + 1

    duplicates = {lbl for lbl, n in count.items() if n > 1}
    if not duplicates:
        return info

    counters: Dict[str, int] = {lbl: 0 for lbl in duplicates}
    for q in questions:
        label = q.get("label", "")
        if label in duplicates:
            counters[label] += 1
            new_label = f"{label}{counters[label]}"
            q["label"] = new_label
            info.append(f"Renamed duplicate label '{label}' -> '{new_label}'")

    return info


def _validate_labels(questions: List[dict]) -> List[str]:
    """Check for duplicate labels and return a list of warnings."""
    warnings = []
    seen: Dict[str, int] = {}

    for i, q in enumerate(questions):
        label = q.get("label", "")
        if not label:
            continue
        if label in seen:
            warnings.append(
                f"Duplicate label '{label}' at positions {seen[label]} and {i}"
            )
        else:
            seen[label] = i

    return warnings


def _validate_conditions(
    conditions: List[dict], questions: List[dict]
) -> List[str]:
    """Check that referenced conditions are defined."""
    warnings = []
    defined = {f"condition.{c['label']}" for c in conditions if c.get("label")}

    for q in questions:
        cond = q.get("cond", "")
        if cond and cond.startswith("condition.") and cond not in defined:
            warnings.append(
                f"Question '{q.get('label', '?')}' references undefined "
                f"condition '{cond}'"
            )

    return warnings


def _validate_xml_wellformed(xml: str) -> List[str]:
    """Basic well-formedness checks on the generated XML."""
    warnings = []

    # Count opening vs closing tags for key elements
    for tag in ("radio", "checkbox", "select", "text", "textarea", "number", "block"):
        opens = len(re.findall(rf"<{tag}\b", xml))
        closes = len(re.findall(rf"</{tag}>", xml))
        if opens != closes:
            warnings.append(
                f"Tag mismatch: {opens} opening <{tag}> vs {closes} closing </{tag}>"
            )

    # Check for unescaped ampersands (common issue)
    # Strip XML comments first -- they don't process entities
    xml_no_comments = re.sub(r"<!--.*?-->", "", xml, flags=re.DOTALL)
    bad_amps = re.findall(r"&(?!amp;|lt;|gt;|quot;|apos;|#)", xml_no_comments)
    if bad_amps:
        warnings.append(f"Found {len(bad_amps)} potentially unescaped ampersands")

    return warnings


# ---------------------------------------------------------------------------
# Term suspend enforcement
# ---------------------------------------------------------------------------

_COND_LABEL_RE = re.compile(r"\((\w+)[.=]")


def _extract_referenced_labels(
    cond_expr: str,
    conditions: List[dict],
) -> Set[str]:
    """Extract question labels referenced by a term's ``cond`` expression.

    Handles direct references like ``(qAge.ch1)`` and indirect references
    through ``condition.XYZ`` definitions.
    """
    if not cond_expr or cond_expr.strip() == "1":
        return set()

    # Indirect: condition.XYZ -> look up the condition and recurse
    if cond_expr.startswith("condition."):
        cond_label = cond_expr.split("condition.", 1)[1]
        for c in conditions:
            if c.get("label") == cond_label:
                return _extract_referenced_labels(c.get("cond", ""), conditions)
        return set()

    return set(_COND_LABEL_RE.findall(cond_expr))


def _ensure_suspend_before_terms(
    questions: List[dict],
    conditions: List[dict],
) -> List[dict]:
    """Inject ``suspend`` elements before terms that lack a page break
    after the question they reference.

    Forsta requires the referenced question to be on a previous page
    (separated by ``<suspend/>``) for ``<term>`` to evaluate correctly.
    """
    label_to_idx: Dict[str, int] = {}
    for i, q in enumerate(questions):
        lbl = q.get("label")
        if lbl:
            label_to_idx[lbl] = i

    insert_before: List[int] = []

    for term_idx, q in enumerate(questions):
        if q.get("forsta_type") != "term":
            continue

        ref_labels = _extract_referenced_labels(q.get("cond", ""), conditions)
        if not ref_labels:
            continue

        for ref in ref_labels:
            ref_idx = label_to_idx.get(ref)
            if ref_idx is None:
                continue

            has_suspend = any(
                questions[j].get("forsta_type") == "suspend"
                for j in range(ref_idx + 1, term_idx)
            )
            if not has_suspend:
                insert_before.append(term_idx)
                break

    # Insert from end to start so earlier indices stay valid.
    for idx in reversed(sorted(set(insert_before))):
        questions.insert(idx, {"forsta_type": "suspend"})
        logger.info(
            f"Injected suspend before term at position {idx} "
            f"(label={questions[idx + 1].get('label', '?')})"
        )

    return questions


# ---------------------------------------------------------------------------
# Term condition inheritance
# ---------------------------------------------------------------------------

def _propagate_conditions_to_terms(
    questions: List[dict],
    conditions: List[dict],
) -> List[dict]:
    """AND the parent question's visibility condition into dependent terms.

    When a term references a question that itself has conditional visibility
    (a ``cond`` attribute), respondents who are never shown the question
    would otherwise be falsely terminated.  This pass appends the question's
    ``cond`` to the term's ``cond`` with an ``and`` clause so the term only
    fires for respondents who actually saw the question.

    Example::

        qTripsP3Y  cond="condition.US_Respondent"
        termTripsP3Y  cond="(qTripsP3Y.check('0'))"
        ->  termTripsP3Y  cond="(qTripsP3Y.check('0')) and (condition.US_Respondent)"
    """
    label_to_cond: Dict[str, str] = {}
    for q in questions:
        lbl = q.get("label")
        cond = q.get("cond")
        if lbl and cond and q.get("forsta_type") != "term":
            label_to_cond[lbl] = cond

    for q in questions:
        if q.get("forsta_type") != "term":
            continue

        term_cond = q.get("cond", "")
        if not term_cond:
            continue

        ref_labels = _extract_referenced_labels(term_cond, conditions)
        if not ref_labels:
            continue

        extra_conds: List[str] = []
        for ref in sorted(ref_labels):
            q_cond = label_to_cond.get(ref)
            if not q_cond:
                continue
            if q_cond in term_cond:
                continue
            if q_cond not in extra_conds:
                extra_conds.append(q_cond)

        if not extra_conds:
            continue

        for ec in extra_conds:
            wrapped = f"({ec})" if not ec.startswith("(") else ec
            term_cond = f"{term_cond} and {wrapped}"

        q["cond"] = term_cond
        logger.info(
            f"Propagated condition to term '{q.get('label', '?')}': "
            f"{q['cond']}"
        )

    return questions


# ---------------------------------------------------------------------------
# Block XML emission helper
# ---------------------------------------------------------------------------

def _emit_block_xml(block_xml: str, indent: str, xml_lines: List[str]):
    """Append block XML lines, indenting XML tags but keeping exec/validate
    content at column 0 (Forsta requires Python code to have no extra indent).
    """
    in_script = False
    for line in block_xml.split("\n"):
        stripped = line.strip()
        if stripped.startswith(("<exec", "<validate")):
            in_script = True
            xml_lines.append(f"{indent}{line}")
        elif stripped.startswith(("</exec", "</validate")):
            in_script = False
            xml_lines.append(f"{indent}{line}")
        elif in_script:
            xml_lines.append(line)
        else:
            xml_lines.append(f"{indent}{line}")
    xml_lines.append("")


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def assemble_xml(
    classified: Dict[str, List[dict]],
    survey_name: str = "Survey",
    progress_callback=None,
) -> Tuple[str, List[str]]:
    """Assemble final Forsta XML from classified questions and conditions.

    Args:
        classified: Dict with "conditions" and "questions" from classifier.py
        survey_name: Name for the <survey> root element
        progress_callback: Optional callable(message: str) for UI updates

    Returns:
        Tuple of (xml_string, warnings_list)
    """
    def _report(msg: str):
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

    conditions = classified.get("conditions", [])
    questions = classified.get("questions", [])
    warnings: List[str] = []

    _report(f"Assembling XML: {len(conditions)} conditions, {len(questions)} elements...")

    # --- Deduplicate labels ---
    dedup_info = _deduplicate_labels(questions)
    for msg in dedup_info:
        logger.info(msg)

    # --- Ensure suspend before terms ---
    questions = _ensure_suspend_before_terms(questions, conditions)

    # --- Propagate question visibility conditions to dependent terms ---
    questions = _propagate_conditions_to_terms(questions, conditions)

    # --- Build root attributes ---
    root_attrs = dict(SURVEY_ROOT_DEFAULTS)
    root_attrs["name"] = survey_name
    root_attrs.update(SURVEY_NAMESPACES)

    attr_str = " ".join(f'{k}="{v}"' for k, v in root_attrs.items())
    xml_lines = [f"<survey {attr_str}>", ""]

    # --- Conditions section (only emit referenced conditions) ---
    if conditions:
        referenced = set()
        for q in questions:
            cond_expr = q.get("cond") or ""
            for c in conditions:
                clabel = c.get("label", "")
                if f"condition.{clabel}" in cond_expr:
                    referenced.add(clabel)
        for cond in conditions:
            clabel = cond.get("label", "")
            if clabel not in referenced:
                logger.info(f"Skipping unreferenced condition: {clabel}")
                continue
            try:
                xml_lines.append(f"  {build_condition(cond)}")
            except Exception as e:
                warnings.append(f"Error building condition '{cond.get('label', '?')}': {e}")
        xml_lines.append("")

    # --- Sample sources ---
    xml_lines.append('  <samplesources default="0">')
    xml_lines.append('    <samplesource list="0">')
    xml_lines.append('      <title>Open Survey</title>')
    xml_lines.append('      <invalid>You are missing information in the URL. Please verify the URL with the original invite.</invalid>')
    xml_lines.append('      <completed>It seems you have already completed this survey.</completed>')
    xml_lines.append('      <exit cond="terminated">Thank you for taking our survey.</exit>')
    xml_lines.append('      <exit cond="qualified">Thank you for taking our survey. Your efforts are greatly appreciated!</exit>')
    xml_lines.append('      <exit cond="overquota">Thank you for taking our survey.</exit>')
    xml_lines.append('    </samplesource>')
    xml_lines.append('  </samplesources>')
    xml_lines.append("")

    # --- Questions section (with block nesting) ---
    indent_level = 1  # base level inside <survey>
    block_stack: List[dict] = []  # stack of {"label": str, "is_parent": bool}
    skip_indices: set = set()

    for qi, q in enumerate(questions):
        if qi in skip_indices:
            continue

        forsta_type = q.get("forsta_type", "").lower()
        indent = "  " * indent_level

        if forsta_type == "block_start":
            is_parent = q.get("randomize_children", False)

            if not is_parent:
                # Close any open sibling (non-parent) block
                if block_stack and not block_stack[-1]["is_parent"]:
                    block_stack.pop()
                    indent_level -= 1
                    xml_lines.append(f"{'  ' * indent_level}{build_block_close()}")
                    xml_lines.append("")

            indent = "  " * indent_level
            inside_randomize_parent = block_stack and block_stack[-1]["is_parent"]
            xml_lines.append(
                f"{indent}{build_block_open(q['label'], title=q.get('block_title'), randomize_children=is_parent, randomize=inside_randomize_parent and not is_parent, cond=q.get('cond'))}"
            )
            block_stack.append({"label": q["label"], "is_parent": is_parent})
            indent_level += 1
            continue

        if forsta_type == "block_end":
            while block_stack:
                block_stack.pop()
                indent_level -= 1
                xml_lines.append(f"{'  ' * indent_level}{build_block_close()}")
                xml_lines.append("")
            continue

        if forsta_type == "suspend":
            xml_lines.append(f"{indent}{build_suspend()}")
            xml_lines.append("")
            continue

        if forsta_type in ("note", "comment"):
            continue

        # Replace zip code questions with the standard Forsta zip code block
        if _is_zipcode_question(q):
            zip_xml = build_zipcode_block(
                label=q.get("label", "qZipCode"),
                title=q.get("title", ""),
                cond=q.get("cond", ""),
            )
            _emit_block_xml(zip_xml, indent, xml_lines)
            continue

        # Replace age questions with the standard Forsta age/generation block.
        # Look ahead to absorb any term elements whose condition references
        # the age question label (e.g., qAge) so they live inside the block.
        if _is_age_question(q):
            age_label = (q.get("label") or "qAge").lower()
            age_terms: List[dict] = []
            for j in range(qi + 1, len(questions)):
                fj = (questions[j].get("forsta_type") or "").lower()
                if fj == "suspend":
                    continue
                if fj == "term":
                    term_cond = (questions[j].get("cond") or "").lower()
                    if age_label in term_cond:
                        age_terms.append(questions[j])
                        skip_indices.add(j)
                        continue
                break
            age_xml = build_age_block(q, terms=age_terms or None)
            _emit_block_xml(age_xml, indent, xml_lines)
            continue

        # Build the question XML
        try:
            xml = build_question(q)
            if xml:
                for line in xml.split("\n"):
                    xml_lines.append(f"{indent}{line}")
                xml_lines.append("")
            else:
                warnings.append(
                    f"Unknown forsta_type '{forsta_type}' for "
                    f"label '{q.get('label', '?')}' -- skipped"
                )
        except Exception as e:
            warnings.append(
                f"Error building '{q.get('label', '?')}' "
                f"(type={forsta_type}): {e}"
            )

    # Close all remaining open blocks
    while block_stack:
        block_stack.pop()
        indent_level -= 1
        xml_lines.append(f"{'  ' * indent_level}{build_block_close()}")
        xml_lines.append("")

    # --- Strip consecutive <suspend/> tags ---
    cleaned: List[str] = []
    for line in xml_lines:
        stripped = line.strip()
        if stripped == "<suspend/>":
            prev_meaningful = ""
            for prev in reversed(cleaned):
                if prev.strip():
                    prev_meaningful = prev.strip()
                    break
            if prev_meaningful == "<suspend/>":
                continue
        cleaned.append(line)
    xml_lines = cleaned

    while xml_lines and not xml_lines[-1].strip():
        xml_lines.pop()

    xml_lines.append("")
    xml_lines.append("</survey>")

    xml_output = "\n".join(xml_lines)

    # --- Validation ---
    _report("Running validation checks...")
    warnings.extend(_validate_labels(questions))
    warnings.extend(_validate_conditions(conditions, questions))
    warnings.extend(_validate_xml_wellformed(xml_output))

    if warnings:
        _report(f"Assembly complete with {len(warnings)} warning(s)")
        for w in warnings:
            logger.warning(f"  - {w}")
    else:
        _report("Assembly complete -- no warnings")

    return xml_output, warnings


# ---------------------------------------------------------------------------
# Full pipeline: file -> XML
# ---------------------------------------------------------------------------

def process_file(
    file_path: str,
    survey_name: str = "Survey",
    model: Optional[str] = None,
    progress_callback=None,
) -> Tuple[str, List[str], Dict[str, Any]]:
    """Run the full pipeline: extract -> segment -> classify -> assemble.

    Args:
        file_path: Path to the .docx file
        survey_name: Name for the survey root element
        model: OpenAI model override
        progress_callback: Optional callable for progress updates

    Returns:
        Tuple of (xml_string, warnings, debug_info)
        debug_info contains intermediate results for debugging.
    """
    from .extractor import extract_from_file
    from .segmenter import segment_blocks
    from .classifier import classify_segments

    def _report(msg: str):
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

    debug_info: Dict[str, Any] = {}

    from .ai_client import get_client
    get_client()

    # Stage 1: Extract
    _report("Stage 1: Extracting document...")
    blocks = extract_from_file(file_path)
    debug_info["extracted_blocks"] = len(blocks)
    _report(f"Extracted {len(blocks)} blocks")

    # Stage 2: Segment
    _report("Stage 2: AI segmentation...")
    segments = segment_blocks(blocks, model=model, progress_callback=progress_callback)
    debug_info["segments"] = len(segments)
    debug_info["segment_types"] = {}
    for seg in segments:
        bt = seg.get("block_type", "unknown")
        debug_info["segment_types"][bt] = debug_info["segment_types"].get(bt, 0) + 1

    if not segments and blocks:
        _report(
            f"WARNING: Segmentation returned 0 segments from {len(blocks)} blocks. "
            "Check AI response parsing."
        )

    # Stage 3: Classify
    _report("Stage 3: AI classification...")
    classified = classify_segments(segments, model=model, progress_callback=progress_callback)
    debug_info["conditions"] = len(classified.get("conditions", []))
    debug_info["classified_questions"] = len(classified.get("questions", []))

    # Stage 4+5: Build XML + Assemble
    _report("Stage 4-5: Building and assembling XML...")
    xml_output, warnings = assemble_xml(
        classified,
        survey_name=survey_name,
        progress_callback=progress_callback,
    )
    debug_info["warnings"] = len(warnings)
    debug_info["xml_lines"] = xml_output.count("\n") + 1

    _report(f"Pipeline complete! {debug_info['xml_lines']} lines of XML generated.")
    return xml_output, warnings, debug_info


def process_bytes(
    file_bytes,
    survey_name: str = "Survey",
    model: Optional[str] = None,
    progress_callback=None,
) -> Tuple[str, List[str], Dict[str, Any]]:
    """Run the full pipeline from a file-like object (Streamlit upload).

    Same as process_file but accepts bytes/BytesIO instead of a path.
    """
    from .extractor import extract_from_bytes
    from .segmenter import segment_blocks
    from .classifier import classify_segments

    def _report(msg: str):
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

    debug_info: Dict[str, Any] = {}

    from .ai_client import get_client
    get_client()

    # Stage 1: Extract
    _report("Stage 1: Extracting document...")
    blocks = extract_from_bytes(file_bytes)
    debug_info["extracted_blocks"] = len(blocks)
    _report(f"Extracted {len(blocks)} blocks")

    # Stage 2: Segment
    _report("Stage 2: AI segmentation...")
    segments = segment_blocks(blocks, model=model, progress_callback=progress_callback)
    debug_info["segments"] = len(segments)
    debug_info["segment_types"] = {}
    for seg in segments:
        bt = seg.get("block_type", "unknown")
        debug_info["segment_types"][bt] = debug_info["segment_types"].get(bt, 0) + 1

    if not segments and blocks:
        _report(
            f"WARNING: Segmentation returned 0 segments from {len(blocks)} blocks. "
            "Check AI response parsing."
        )

    # Stage 3: Classify
    _report("Stage 3: AI classification...")
    classified = classify_segments(segments, model=model, progress_callback=progress_callback)
    debug_info["conditions"] = len(classified.get("conditions", []))
    debug_info["classified_questions"] = len(classified.get("questions", []))

    # Stage 4+5: Build XML + Assemble
    _report("Stage 4-5: Building and assembling XML...")
    xml_output, warnings = assemble_xml(
        classified,
        survey_name=survey_name,
        progress_callback=progress_callback,
    )
    debug_info["warnings"] = len(warnings)
    debug_info["xml_lines"] = xml_output.count("\n") + 1

    _report(f"Pipeline complete! {debug_info['xml_lines']} lines of XML generated.")
    return xml_output, warnings, debug_info
