"""Diagnostic script: runs each pipeline stage independently and dumps
intermediate results so we can pinpoint exactly where data drops off.

Usage:
    python diagnose.py tests/Survey\ Programming\ Question\ Examples.docx

Writes JSON snapshots to a `_diagnostics/` folder for each stage.
"""

import json
import logging
import os
import sys
from pathlib import Path

# Ensure project root is on path
_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger("diagnose")

OUT_DIR = _ROOT / "_diagnostics"
OUT_DIR.mkdir(exist_ok=True)


def _dump(name: str, data, summary: str = ""):
    path = OUT_DIR / f"{name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    size_kb = path.stat().st_size / 1024
    logger.info(f"  -> wrote {path.name} ({size_kb:.1f} KB) {summary}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python diagnose.py <path_to_docx>")
        sys.exit(1)

    docx_path = sys.argv[1]
    if not os.path.isfile(docx_path):
        print(f"File not found: {docx_path}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  PIPELINE DIAGNOSTICS")
    print(f"  Input: {docx_path}")
    print(f"  Output: {OUT_DIR}/")
    print(f"{'='*60}\n")

    # ------------------------------------------------------------------
    # Stage 1: Extraction (no AI)
    # ------------------------------------------------------------------
    print("[Stage 1] Extracting blocks from .docx ...")
    from survey_xml_generator.extractor import extract_from_file

    blocks = extract_from_file(docx_path)
    _dump("stage1_blocks", blocks, f"({len(blocks)} blocks)")

    block_types = {}
    for b in blocks:
        bt = str(b.get("block_type", "unknown"))
        block_types[bt] = block_types.get(bt, 0) + 1
    print(f"  Extracted {len(blocks)} blocks: {block_types}")

    if not blocks:
        print("\n  *** STOP: Zero blocks extracted. The .docx is empty or unreadable.")
        return

    # Show first 3 blocks as a sanity check
    print("  First 3 blocks:")
    for b in blocks[:3]:
        text = b.get("text", "(no text)")[:80]
        print(f"    [{b.get('block_type')}] {text}")

    # ------------------------------------------------------------------
    # Stage 2: Segmentation (AI)
    # ------------------------------------------------------------------
    print(f"\n[Stage 2] Sending {len(blocks)} blocks to AI for segmentation ...")
    from survey_xml_generator.segmenter import segment_blocks

    segments = segment_blocks(blocks, progress_callback=lambda m: print(f"  {m}"))
    _dump("stage2_segments", segments, f"({len(segments)} segments)")

    seg_types = {}
    for s in segments:
        bt = s.get("block_type", "unknown")
        seg_types[bt] = seg_types.get(bt, 0) + 1
    print(f"  Segmented into {len(segments)} blocks: {seg_types}")

    if not segments:
        print("\n  *** STOP: Zero segments returned by AI.")
        print("  Check _diagnostics/stage1_blocks.json to verify input is correct.")
        print("  Check logs above for response key warnings.")
        return

    # Show first 3 segments
    print("  First 3 segments:")
    for s in segments[:3]:
        label = s.get("label", s.get("content", "(no label)"))
        print(f"    [{s.get('block_type')}] {label}")

    # ------------------------------------------------------------------
    # Stage 3: Classification (AI)
    # ------------------------------------------------------------------
    print(f"\n[Stage 3] Classifying {len(segments)} segments ...")
    from survey_xml_generator.classifier import classify_segments

    classified = classify_segments(segments, progress_callback=lambda m: print(f"  {m}"))
    _dump("stage3_classified", classified,
          f"({len(classified.get('questions', []))} questions, "
          f"{len(classified.get('conditions', []))} conditions)")

    questions = classified.get("questions", [])
    conditions = classified.get("conditions", [])
    print(f"  {len(questions)} questions, {len(conditions)} conditions")

    q_types = {}
    for q in questions:
        ft = q.get("forsta_type", "unknown")
        q_types[ft] = q_types.get(ft, 0) + 1
    print(f"  Question types: {q_types}")

    if not questions:
        print("\n  *** STOP: Zero questions classified.")
        print("  Check _diagnostics/stage2_segments.json -- do segments have correct block_type values?")
        return

    # ------------------------------------------------------------------
    # Stage 4+5: XML Assembly (no AI)
    # ------------------------------------------------------------------
    print(f"\n[Stage 4-5] Building and assembling XML ...")
    from survey_xml_generator.assembler import assemble_xml

    xml_output, warnings = assemble_xml(
        classified,
        survey_name="DiagnosticTest",
        progress_callback=lambda m: print(f"  {m}"),
    )

    xml_path = OUT_DIR / "stage5_output.xml"
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(xml_output)

    xml_lines = xml_output.count("\n") + 1
    print(f"  Generated {xml_lines} lines of XML")

    if warnings:
        print(f"  {len(warnings)} warnings:")
        for w in warnings:
            print(f"    - {w}")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    print(f"  Stage 1 (extract):   {len(blocks)} blocks")
    print(f"  Stage 2 (segment):   {len(segments)} segments  {seg_types}")
    print(f"  Stage 3 (classify):  {len(questions)} questions, {len(conditions)} conditions")
    print(f"  Stage 4-5 (XML):     {xml_lines} lines, {len(warnings)} warnings")
    print(f"\n  All output in: {OUT_DIR}/")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
