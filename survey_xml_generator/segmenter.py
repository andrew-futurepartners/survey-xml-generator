"""Stage 2: AI-powered document segmentation.

Takes the raw extracted blocks from Stage 1 and uses OpenAI to identify
logical survey boundaries: questions, text screens, page breaks, conditions,
section markers, terminations, and metadata.

Handles chunking for long documents to stay within token limits.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, List, Optional

from .ai_client import call_ai
from .config import (
    OPENAI_MODEL,
    SEGMENTATION_CHUNK_SIZE,
    SEGMENTATION_CHUNK_OVERLAP,
)
from .prompts.segmentation import SYSTEM_PROMPT, build_segmentation_prompt

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Chunking helpers
# ---------------------------------------------------------------------------

def _chunk_blocks(
    blocks: List[dict],
    chunk_size: int = SEGMENTATION_CHUNK_SIZE,
    overlap: int = SEGMENTATION_CHUNK_OVERLAP,
) -> List[List[dict]]:
    """Split blocks into overlapping chunks for AI processing.

    Overlap ensures that a question block sitting right at a boundary
    isn't sliced in half.
    """
    if len(blocks) <= chunk_size:
        return [blocks]

    chunks = []
    start = 0
    while start < len(blocks):
        end = min(start + chunk_size, len(blocks))
        chunks.append(blocks[start:end])
        # Advance by (chunk_size - overlap) so the tail of one chunk
        # overlaps with the head of the next
        start += chunk_size - overlap
        if start >= len(blocks):
            break

    logger.info(
        f"Split {len(blocks)} blocks into {len(chunks)} chunks "
        f"(size={chunk_size}, overlap={overlap})"
    )
    return chunks


# ---------------------------------------------------------------------------
# Deduplication for overlapping chunks
# ---------------------------------------------------------------------------

def _dedup_segments(all_segments: List[dict]) -> List[dict]:
    """Remove duplicate segments that appear in overlapping chunk regions.

    Tracks individual paragraph indices already claimed by earlier segments.
    A segment is dropped when all of its indices are already covered
    (i.e., its index set is a subset of the seen set).  The first segment
    encountered wins because it had more surrounding context.
    """
    seen_indices: set = set()
    deduped: List[dict] = []

    for seg in all_segments:
        indices = seg.get("paragraph_indices", [])
        if not indices:
            deduped.append(seg)
            continue

        idx_set = frozenset(indices)
        if idx_set <= seen_indices:
            logger.debug(f"Skipping duplicate segment with indices {sorted(idx_set)}")
            continue

        seen_indices |= idx_set
        deduped.append(seg)

    removed = len(all_segments) - len(deduped)
    if removed:
        logger.info(f"Deduplication removed {removed} overlapping segments")

    return deduped


# ---------------------------------------------------------------------------
# Sort segments back into document order
# ---------------------------------------------------------------------------

def _sort_segments(segments: List[dict]) -> List[dict]:
    """Sort segments by their first paragraph index to restore document order."""
    def sort_key(seg: dict) -> int:
        indices = seg.get("paragraph_indices", [])
        return min(indices) if indices else 0

    return sorted(segments, key=sort_key)


# ---------------------------------------------------------------------------
# Response extraction
# ---------------------------------------------------------------------------

_SEGMENT_KEYS = ("segments", "blocks", "results", "result", "data")


def _extract_segments_from_response(result: Any, chunk_index: int = 0) -> List[dict]:
    """Robustly extract the segments list from an AI response.

    Handles: bare list, dict with known key, dict with single unknown key.
    Logs the actual response structure so failures are diagnosable.
    """
    if isinstance(result, list):
        return result

    if not isinstance(result, dict):
        logger.warning(
            f"Chunk {chunk_index}: unexpected response type {type(result).__name__}"
        )
        return []

    logger.info(f"Chunk {chunk_index}: response keys = {list(result.keys())}")

    # Try known keys first (explicit check avoids the falsy-or-chain bug)
    for key in _SEGMENT_KEYS:
        if key in result:
            val = result[key]
            if isinstance(val, list):
                return val
            logger.warning(
                f"Chunk {chunk_index}: key '{key}' exists but is "
                f"{type(val).__name__}, not list"
            )

    # Fallback: look for ANY key whose value is a list
    for key, val in result.items():
        if isinstance(val, list):
            logger.info(
                f"Chunk {chunk_index}: using unexpected key '{key}' as segments"
            )
            return val

    logger.warning(
        f"Chunk {chunk_index}: no list found in response. "
        f"Keys: {list(result.keys())}"
    )
    return []


# ---------------------------------------------------------------------------
# Block-marker condition extraction
# ---------------------------------------------------------------------------

_BLOCK_COND_RE = re.compile(r":\s*(IF\s+.+?)\s*\]", re.IGNORECASE)


def _extract_block_condition(original_text: str) -> str:
    """Extract an embedded condition from a block marker's original text.

    Handles patterns like ``[BLOCK WILD BUNCH: IF QCHILDREN == 1]``
    and returns the condition portion (``IF QCHILDREN == 1``), or an
    empty string if none is found.
    """
    m = _BLOCK_COND_RE.search(original_text)
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# Post-segmentation reconciliation: ensure no questions are dropped
# ---------------------------------------------------------------------------

_Q_LABEL_RE = re.compile(
    r"^Q\d*\.?\s+(.+)",
    re.IGNORECASE,
)


def _detect_question_labels(blocks: List[dict]) -> List[dict]:
    """Scan extracted blocks for ``Q. LABEL`` patterns and return metadata
    about each detected question (block index, raw label text, and the
    next block's text which is typically the question title).
    """
    detected = []
    for i, b in enumerate(blocks):
        if b.get("block_type") not in ("paragraph",):
            continue
        text = (b.get("text") or "").strip()
        m = _Q_LABEL_RE.match(text)
        if not m:
            continue
        raw_label = m.group(1).strip()
        title_text = ""
        for j in range(i + 1, min(i + 3, len(blocks))):
            nxt = blocks[j]
            if nxt.get("block_type") == "paragraph" and not nxt.get("is_list_item"):
                nt = (nxt.get("text") or "").strip()
                if nt and not _Q_LABEL_RE.match(nt):
                    title_text = nt
                    break
        detected.append({
            "block_index": b.get("index", i),
            "raw_label": raw_label,
            "title_text": title_text,
        })
    return detected


def _label_to_camel(raw: str) -> str:
    """Convert a raw question label like ``TRIPS N12M`` to ``qTripsN12m``."""
    clean = re.sub(r"[^a-zA-Z0-9\s]", "", raw)
    words = clean.split()
    if not words:
        return "q1"
    return "q" + "".join(w.capitalize() for w in words)


def _reconcile_missing_questions(
    all_segments: List[dict],
    blocks: List[dict],
) -> List[dict]:
    """Check that every ``Q. LABEL`` found in extracted blocks has a
    corresponding question segment.  Inject synthetic segments for any
    that are missing so no questions are silently dropped.
    """
    detected = _detect_question_labels(blocks)
    if not detected:
        return all_segments

    covered_indices: set = set()
    for seg in all_segments:
        if seg.get("block_type") == "question":
            covered_indices.update(seg.get("paragraph_indices", []))

    injected = 0
    for det in detected:
        idx = det["block_index"]
        if idx in covered_indices:
            continue

        nearby_covered = any(
            i in covered_indices for i in range(idx - 1, idx + 4)
        )
        if nearby_covered:
            continue

        label = _label_to_camel(det["raw_label"])
        title = det["title_text"]

        scan_start = idx + 1
        answer_lines = []
        paragraph_indices = [idx]
        condition_block = None

        if idx > 0:
            prev = blocks[idx - 1] if idx - 1 < len(blocks) else None
            if prev and (prev.get("text") or "").strip().startswith("[IF"):
                condition_block = (prev.get("text") or "").strip()

        for j in range(scan_start, min(scan_start + 20, len(blocks))):
            b = blocks[j]
            bt = b.get("block_type", "")
            if bt in ("pagebreak", "block_marker"):
                break
            txt = (b.get("text") or "").strip()
            if _Q_LABEL_RE.match(txt):
                break
            paragraph_indices.append(b.get("index", j))
            if b.get("is_list_item") and txt:
                answer_lines.append(txt)
            elif not title and txt:
                title = txt

        synthetic = {
            "block_type": "question",
            "label": label,
            "title_text": title,
            "instruction_text": None,
            "answer_lines": answer_lines,
            "answer_modifiers": {},
            "inline_modifiers": [],
            "conditions": [condition_block] if condition_block else [],
            "termination_conditions": [],
            "answer_terminations": {},
            "is_matrix": False,
            "matrix_statements": [],
            "matrix_scale": [],
            "paragraph_indices": paragraph_indices,
        }
        all_segments.append(synthetic)
        injected += 1
        logger.info(
            f"Reconciliation: injected missing question '{label}' "
            f"(title: {title[:60]}...) at index {idx}"
        )

    if injected:
        logger.info(f"Reconciliation injected {injected} missing question(s)")

    return all_segments


# ---------------------------------------------------------------------------
# Main segmentation function
# ---------------------------------------------------------------------------

def segment_blocks(
    blocks: List[dict],
    model: Optional[str] = None,
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
    progress_callback=None,
) -> List[dict]:
    """Run AI segmentation on extracted document blocks.

    Args:
        blocks: Raw extracted blocks from extractor.py
        model: OpenAI model override (defaults to config)
        chunk_size: Override chunk size (defaults to config)
        chunk_overlap: Override overlap (defaults to config)
        progress_callback: Optional callable(message: str) for UI updates

    Returns:
        List of segmented block dicts, sorted in document order.
    """
    model = model or OPENAI_MODEL
    cs = chunk_size or SEGMENTATION_CHUNK_SIZE
    co = chunk_overlap or SEGMENTATION_CHUNK_OVERLAP

    def _report(msg: str):
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

    # Filter out completely empty blocks (shouldn't happen, but be safe)
    blocks = [b for b in blocks if b.get("text") or b.get("rows") or b.get("block_type") in ("pagebreak", "block_marker")]

    # Pull pagebreak blocks out -- handled deterministically, not sent to AI.
    # Block markers are recorded for deterministic injection but KEPT in the
    # AI input so the model has section-boundary context for segmentation.
    pagebreak_indices = []
    block_markers = []  # list of (index, block_name, original_text)
    content_blocks = []
    for b in blocks:
        bt = b.get("block_type")
        if bt == "pagebreak":
            pagebreak_indices.append(b.get("index", 0))
        else:
            content_blocks.append(b)
            if bt == "block_marker":
                block_markers.append((
                    b.get("index", 0),
                    b.get("block_name", ""),
                    b.get("text", ""),
                ))

    logger.info(
        f"Separated {len(pagebreak_indices)} pagebreaks; "
        f"recorded {len(block_markers)} block markers; "
        f"sending {len(content_blocks)} content blocks to AI"
    )

    _report(f"Segmenting {len(content_blocks)} blocks...")

    # Split into chunks
    chunks = _chunk_blocks(content_blocks, chunk_size=cs, overlap=co)

    all_segments: List[dict] = []

    def _process_chunk(i: int, chunk: List[dict]) -> List[dict]:
        """Process a single chunk through the AI (thread-safe).

        Only uses the logger for progress inside worker threads;
        the Streamlit progress_callback is called from the main thread.
        """
        logger.info(f"Processing chunk {i + 1}/{len(chunks)} ({len(chunk)} blocks)...")
        blocks_json = json.dumps(chunk, separators=(",", ":"), default=str)
        user_prompt = build_segmentation_prompt(blocks_json)
        result = call_ai(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            model=model,
            expect_json=True,
        )
        segments = _extract_segments_from_response(result, chunk_index=i + 1)
        logger.info(f"Chunk {i + 1} returned {len(segments)} segments")
        return segments

    if len(chunks) == 1:
        _report(f"Processing chunk 1/1 ({len(chunks[0])} blocks)...")
        all_segments = _process_chunk(0, chunks[0])
        _report(f"Chunk 1 returned {len(all_segments)} segments")
    else:
        max_workers = min(len(chunks), 5)
        _report(f"Processing {len(chunks)} chunks in parallel (max_workers={max_workers})...")

        from .ai_client import get_client
        get_client()

        chunk_results: List[List[dict]] = [[] for _ in chunks]
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx = {
                executor.submit(_process_chunk, i, chunk): i
                for i, chunk in enumerate(chunks)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                chunk_results[idx] = future.result()
                _report(f"Chunk {idx + 1}/{len(chunks)} complete")
        for segments in chunk_results:
            all_segments.extend(segments)

    # Strip any AI-generated pagebreak/block_marker segments (we inject deterministically)
    all_segments = [
        s for s in all_segments
        if s.get("block_type") not in ("pagebreak", "block_marker")
    ]

    # Inject deterministic pagebreak segments from the extractor
    for idx in pagebreak_indices:
        all_segments.append({
            "block_type": "pagebreak",
            "paragraph_indices": [idx],
        })

    # Inject deterministic block_marker segments from the extractor
    for idx, block_name, original_text in block_markers:
        seg = {
            "block_type": "block_marker",
            "marker_type": "block_start",
            "block_name": block_name,
            "paragraph_indices": [idx],
        }
        block_cond = _extract_block_condition(original_text)
        if block_cond:
            seg["block_condition"] = block_cond
            logger.info(f"Block marker '{block_name}' has condition: {block_cond}")
        all_segments.append(seg)

    # Dedup overlapping segments and restore document order
    all_segments = _dedup_segments(all_segments)

    # Reconcile: ensure every Q. LABEL from the source has a segment
    all_segments = _reconcile_missing_questions(all_segments, blocks)

    all_segments = _sort_segments(all_segments)

    _report(f"Segmentation complete: {len(all_segments)} segments identified")
    return all_segments


# ---------------------------------------------------------------------------
# Convenience: segment from file
# ---------------------------------------------------------------------------

def segment_from_file(
    file_path: str,
    model: Optional[str] = None,
    progress_callback=None,
) -> List[dict]:
    """Extract and segment a .docx file in one call."""
    from .extractor import extract_from_file

    blocks = extract_from_file(file_path)
    return segment_blocks(blocks, model=model, progress_callback=progress_callback)


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python -m survey_xml_generator.segmenter <path_to_docx>")
        sys.exit(1)

    segments = segment_from_file(sys.argv[1], progress_callback=print)
    print(json.dumps(segments, indent=2, default=str))
    print(f"\n--- {len(segments)} segments ---")
