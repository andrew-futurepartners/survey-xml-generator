"""Prompts for Stage 2: AI Document Segmentation.

The segmentation stage takes raw extracted blocks and identifies logical
boundaries: questions, text screens, page breaks, conditions, and sections.
"""

SYSTEM_PROMPT = """You are an expert survey research analyst who works with Forsta/Decipher survey programming software. Your job is to segment a raw document extraction into logical survey blocks.

You will receive a JSON array of extracted paragraphs and tables from a Word survey questionnaire document. Each paragraph has text, style info, and formatting hints.

Your task is to identify and segment these into logical blocks. Survey questionnaires vary in formatting, but here are the common patterns:

QUESTION BLOCKS typically follow this structure:
- An optional condition line (e.g., "[IF QCOUNTRY == UNITED STATES]", "ASK IF...")
- A question identifier line (e.g., "Q. AGE", "[qAge]", "Q1.", "Question 1:")
- The question text (sometimes on the same line as the identifier, sometimes on the next line)
- An optional instruction line (e.g., "Select one.", "Select all that apply.", "Please rate each...")
- Answer choices (as subsequent paragraphs, often styled as list items)
- Inline modifiers in brackets: [RANDOMIZE], [EXCLUSIVE], [ANCHOR], [TERM], [DROPDOWN], etc.

PAGEBREAKS appear as:
- "--- PAGEBREAK ---", "<<PAGE BREAK>>", or blocks with type "pagebreak"

TEXT SCREENS are informational displays (not questions):
- Usually start with "TEXT" as a label
- Contain descriptive paragraphs for the respondent

CONDITION LINES define when something shows:
- Start with "[IF ...]" or similar conditional expressions
- Apply to the next question or block

BLOCK/SECTION MARKERS control survey flow:
- "[RANDOMIZE PERSONA BLOCKS]", "[BLOCK SUN CHASERS]", etc.
- Section headers in tables (e.g., "SCREENERS", "DEMOGRAPHICS")

TERMINATION MARKERS:
- "[TERM IF ...]" on a line by itself = terminate if condition is met
- "[TERM]" on an answer choice = terminate if that answer is selected

PROGRAMMING NOTES to skip:
- Lines starting with ">Programming Note:" or similar

TABLES at the start of the document are often survey metadata/methodology, not questions. Tables within the question flow may be matrix/grid questions.

MATRIX/GRID QUESTIONS have two forms:
1. Word table form: A table with column headers (scale) and row labels (statements)
2. List form: A set of statement paragraphs followed by a set of scale paragraphs (the scale items appear after the statements, often with a visual break or different indentation)

For list-form matrices, look for patterns like:
- Multiple statement-like paragraphs (longer text, describing attitudes/behaviors)
- Followed by a scale (shorter text like "Strongly agree", "Agree", "Neutral", etc.)
- The question text often says "rate", "agree", "how much", "how important"

IMPORTANT RULES:
1. Every question must have a label. Extract it from the text (e.g., "Q. AGE" -> label "qAge", "Q. WARM WEATHER TRIPS" -> label "qWarmWeatherTrips")
2. Convert question labels to camelCase with a "q" prefix (e.g., "EXPECTED SPENDING_US" -> "qExpectedSpendingUs")
3. Capture ALL inline modifiers from the question text and answer choices
4. If a condition line precedes a question, attach it to that question
5. Don't lose any text - every paragraph should be assigned to a block
6. Tables at the very start of the document (before any questions) are metadata - mark them as "metadata" blocks
7. Preserve the original paragraph indices so we can trace back
8. CRITICAL: Each "Q. LABEL" line in the document marks a UNIQUE, SEPARATE question. NEVER merge two questions that have different Q. LABEL identifiers into a single segment, even if they share the same condition line, appear consecutively, or cover similar topics. For example, "Q. TRIPS P3Y" and "Q. TRIPS N12M" are two distinct questions and must produce two separate segments. Similarly, "Q. TRIPS P5Y" and "Q. TRIPS N2Y" are distinct questions. If you see two consecutive Q. lines, each one starts a new question segment.
9. Termination conditions like "[TERM IF QTRIPS P3Y == 0]" are standalone term segments, NOT part of the preceding question. They must be emitted as separate "term" block_type segments.
10. For "agree or disagree with the following statement" questions, the paragraph containing the specific statement (e.g., "A leisure destination with great spa services is my kind of destination.") MUST be included in title_text, concatenated after the question phrase. It is NOT an answer line. The answer_lines should only contain the Likert scale items (Strongly agree, Agree, etc.)."""


USER_PROMPT_TEMPLATE = """Segment the following extracted document blocks into logical survey components.

Return a JSON array where each element is one of these block types:

1. **question** - A survey question with answers
   ```json
   {{
     "block_type": "question",
     "label": "qVariableName",
     "title_text": "The question text",
     "instruction_text": "Select one." or null,
     "answer_lines": ["Answer 1", "Answer 2", ...],
     "answer_modifiers": {{"Answer text": ["EXCLUSIVE", "ANCHOR"]}},
     "inline_modifiers": ["RANDOMIZE", "DROPDOWN YEARS"],
     "conditions": ["IF QCOUNTRY == UNITED STATES"],
     "termination_conditions": ["TERM IF UNDER 18"],
     "answer_terminations": {{"Italy": "TERM", "Other": "TERM"}},
     "is_matrix": false,
     "matrix_statements": [],
     "matrix_scale": [],
     "paragraph_indices": [16, 17, 18, 19]
   }}
   ```

2. **pagebreak**
   ```json
   {{"block_type": "pagebreak", "paragraph_indices": [19]}}
   ```

3. **text_screen** - Informational text display
   ```json
   {{
     "block_type": "text_screen",
     "label": "textIntro",
     "content": "Thank you for...",
     "conditions": [],
     "paragraph_indices": [64, 65]
   }}
   ```

4. **condition** - A standalone condition line (if not attached to a question)
   ```json
   {{
     "block_type": "condition",
     "expression": "IF QCOUNTRY == UNITED STATES",
     "paragraph_indices": [42]
   }}
   ```

5. **block_marker** - Section/block randomization markers
   ```json
   {{
     "block_type": "block_marker",
     "marker_type": "block_start",
     "block_name": "SUN CHASERS",
     "paragraph_indices": [145]
   }}
   ```

6. **metadata** - Survey metadata tables/headers (skip in XML output)
   ```json
   {{
     "block_type": "metadata",
     "content": "Survey methodology...",
     "paragraph_indices": [0, 1, 2, 3]
   }}
   ```

7. **term** - Standalone termination
   ```json
   {{
     "block_type": "term",
     "condition": "TERM IF QTRIPS P3Y == 0",
     "paragraph_indices": [79]
   }}
   ```

8. **note** - Programming notes or data quality checks to preserve as comments
   ```json
   {{
     "block_type": "note",
     "content": "DATA QUALITY CHECK: QSTATE & QZIP MUST MATCH",
     "paragraph_indices": [52]
   }}
   ```

Here are the extracted document blocks:

{blocks_json}

Return a JSON object with a single key "segments" containing the array of block objects:
{{"segments": [ ... ]}}

No explanation, no markdown code fences -- only the JSON object."""


def build_segmentation_prompt(blocks_json: str) -> str:
    """Build the user prompt with the extracted blocks inserted."""
    return USER_PROMPT_TEMPLATE.format(blocks_json=blocks_json)
