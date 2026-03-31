# AI Survey XML Generator - Attack Plan

## The Problem

The current script is a rigid state machine that relies on exact formatting conventions to detect questions and generate Forsta XML. It expects things like `[label]` for question labels, `<<PAGE BREAK>>` for page breaks, and specific keyword patterns for question type detection.

The real world doesn't cooperate. The AOT FINAL.docx uses completely different conventions:

- Questions use `Q. LABEL_NAME` instead of `[label]`
- Page breaks use `--- PAGEBREAK ---` instead of `<<PAGE BREAK>>`
- Instructions and modifiers are embedded inline: `[DROPDOWN YEARS] [TERM IF UNDER 18]`
- Conditional logic appears on separate lines: `[IF QCOUNTY == UNITED STATES]`
- Block randomization: `[RANDOMIZE PERSONA BLOCKS]`, `[BLOCK SUN CHASERS]`
- Matrix questions are presented as statement lists + scale (not Word tables)
- Answer styles vary between "List Paragraph", "paragraph", and "Normal"

Every new survey from a new researcher will have its own formatting quirks. The parser needs to understand *intent*, not match exact patterns.

---

## Architecture Overview

Replace the single-pass state machine with a multi-stage AI pipeline. Each stage has a clear responsibility, and the output of each stage feeds the next.

```
.docx upload
     |
     v
[Stage 1] Raw Extraction
     |  Extract all text, tables, styles, and structural hints from the .docx
     |  Output: structured JSON of paragraphs with metadata
     v
[Stage 2] AI Document Segmentation
     |  LLM identifies question boundaries, informational blocks,
     |  page breaks, conditional logic, and section groupings
     |  Output: array of "question block" objects
     v
[Stage 3] AI Question Classification
     |  For each block, LLM determines question type, modifiers,
     |  answer choices, instructions, labels, and special attributes
     |  Output: enriched question objects with type + metadata
     v
[Stage 4] XML Generation (Deterministic)
     |  Template-based XML builder using the classified data
     |  No AI needed here - just clean mapping from type to Forsta XML
     |  Output: XML string per question
     v
[Stage 5] Assembly + Validation
     |  Join all XML blocks, add suspend tags, validate structure
     |  Output: final XML file
```

---

## Stage 1: Raw Extraction (No AI)

This is the only stage that touches the .docx file directly. It extracts everything the AI stages will need to work with.

**What it extracts per paragraph:**
- Raw text content
- Word style name (Normal, List Paragraph, Heading 1, etc.)
- Formatting hints (bold, italic, underline)
- Indentation level
- Whether it's part of a numbered/bulleted list
- Paragraph index (position in document)

**What it extracts from tables:**
- Row/column text content
- Header row detection
- Table position relative to surrounding paragraphs

**What it extracts globally:**
- Page break markers (both explicit text markers and Word section breaks)
- Document metadata (title, etc.)

**Output format:** A JSON array of "block" objects:
```json
[
  {"type": "paragraph", "index": 16, "text": "Q. AGE", "style": "Normal", "bold": true},
  {"type": "paragraph", "index": 17, "text": "In what year were you born? [DROPDOWN YEARS] [TERM IF UNDER 18]", "style": "Normal"},
  {"type": "pagebreak", "index": 19},
  {"type": "table", "index": 40, "rows": [["col1", "col2"], ["row1", "row2"]]}
]
```

**Implementation:** Python, using python-docx. No AI, no API calls. This is deterministic extraction.

---

## Stage 2: AI Document Segmentation

This is the first AI pass. The LLM receives the extracted block array and segments it into logical "question blocks."

**What it identifies:**
- Where each question starts and ends
- Which paragraphs are the question label/title vs. instruction vs. answer choices
- Informational text screens (TEXT blocks)
- Conditional logic lines (IF statements)
- Block/section markers (BLOCK, RANDOMIZE PERSONA BLOCKS)
- Termination conditions (TERM lines)
- Page break positions
- Data quality check notes
- Programming notes to skip

**Prompt strategy:** Provide the LLM with the full extracted content (or chunked if very long) and ask it to return a structured JSON array of segmented blocks. Include a system prompt with examples of what question blocks look like in various survey formats.

**Output format:**
```json
[
  {
    "block_type": "question",
    "label": "AGE",
    "title_text": "In what year were you born?",
    "instruction_text": null,
    "answer_lines": [],
    "inline_modifiers": ["DROPDOWN YEARS", "TERM IF UNDER 18"],
    "conditions": [],
    "paragraph_indices": [16, 17]
  },
  {
    "block_type": "pagebreak",
    "paragraph_indices": [19]
  },
  {
    "block_type": "condition",
    "expression": "IF QCOUNTY == UNITED STATES",
    "applies_to_next": true,
    "paragraph_indices": [42]
  },
  {
    "block_type": "question",
    "label": "STATE",
    "title_text": "In which state do you currently live?",
    "instruction_text": null,
    "answer_lines": [],
    "inline_modifiers": ["DROPDOWN STATES"],
    "conditions": ["IF QCOUNTY == UNITED STATES"],
    "paragraph_indices": [43, 44]
  },
  {
    "block_type": "text_screen",
    "content": "The next questions refer to leisure trips...",
    "paragraph_indices": [64, 65]
  },
  {
    "block_type": "block_marker",
    "marker_type": "randomize_blocks",
    "paragraph_indices": [143]
  },
  {
    "block_type": "block_marker",
    "marker_type": "block_start",
    "block_name": "SUN CHASERS",
    "paragraph_indices": [145]
  }
]
```

**Chunking strategy:** For long surveys (600+ paragraphs like the AOT doc), split into overlapping chunks of ~150 paragraphs. Overlap by ~20 paragraphs to avoid cutting a question block in half. Merge results by paragraph index.

---

## Stage 3: AI Question Classification

Second AI pass. For each question block from Stage 2, the LLM determines the exact Forsta question type and all attributes.

**What it determines:**

| Attribute | Description |
|-----------|-------------|
| `forsta_type` | radio, checkbox, select, text, number, html, term |
| `label` | Clean variable label (e.g., "qAge", "qCountry") |
| `title` | Question title text |
| `comment` | Instruction/comment text |
| `answers` | Array of answer choices with attributes |
| `shuffle` | Whether to randomize rows |
| `atleast` / `atmost` | Min/max selections for checkbox |
| `verify` | Validation type (zipcode, email, number, digits, range) |
| `size` | Input size for text/number fields |
| `optional` | Whether the field is optional |
| `condition` | Display condition expression |
| `special_handling` | Known label handling (state dropdown, country dropdown, year dropdown) |
| `termination` | Termination conditions |
| `is_matrix` | Whether this is a matrix/grid question |
| `matrix_rows` | Statement rows for matrix questions |
| `matrix_cols` | Scale columns for matrix questions |

**Question type classification rules the AI should follow:**

The prompt will include these rules as guidance, but the AI can override them when the context makes it clear:

1. **Select (dropdown):** Any question with explicit dropdown indicators, year ranges, state lists, country lists, or a large number of sequential numeric options.

2. **Radio (single select):** Questions with a small set of answer choices where only one can be selected. Default when instruction says "select one" or doesn't specify multi-select. Includes Likert scales, True/False, Yes/No.

3. **Checkbox (multi-select):** Questions where multiple answers can be selected. Triggered by "select all that apply," "up to N," or exclusive/anchor modifiers on answers.

4. **Text (open end):** Questions asking for free-text input with no predefined answers. Triggered by open-end indicators, blank lines, or underscores.

5. **Number (numeric):** Questions asking for a numeric value. Triggered by "enter a number," "numeric open end," or range validators.

6. **Matrix/Grid:** Questions with a set of row statements rated on a common column scale. The AI should detect these even when they aren't formatted as Word tables (like the AOT doc's agreement scales).

7. **HTML (text screen):** Informational text blocks, section intros, thank-you messages. Not a question.

8. **Term (termination):** Termination points with conditions.

**Prompt strategy:** Provide each question block with its surrounding context (previous and next question for reference). Include the Forsta XML examples from the README as few-shot examples so the AI understands the target format.

**Output format per question:**
```json
{
  "forsta_type": "radio",
  "label": "qWarmWeatherImportance",
  "title": "When you pick your vacation destinations, how important is it that they be a warm and sunny place?",
  "comment": "Select one.",
  "answers": [
    {"text": "Very important", "label": "r1"},
    {"text": "Important", "label": "r2"},
    {"text": "Neutral", "label": "r3"},
    {"text": "Unimportant", "label": "r4"},
    {"text": "Very unimportant", "label": "r5"}
  ],
  "shuffle": false,
  "condition": null
}
```

Matrix example:
```json
{
  "forsta_type": "radio",
  "is_matrix": true,
  "label": "qArizonaAgreement",
  "title": "How much do you agree with the following statements?",
  "comment": "Select one per row.",
  "shuffle": true,
  "matrix_rows": [
    {"text": "An Arizona vacation is a perfect fit for travelers like me.", "label": "r1"},
    {"text": "It is important that I travel in a manner that protects the environment.", "label": "r2"}
  ],
  "matrix_cols": [
    {"text": "Strongly agree", "label": "c1"},
    {"text": "Agree", "label": "c2"},
    {"text": "Slightly agree", "label": "c3"},
    {"text": "Neutral", "label": "c4"},
    {"text": "Slightly disagree", "label": "c5"},
    {"text": "Disagree", "label": "c6"},
    {"text": "Strongly disagree", "label": "c7"}
  ]
}
```

---

## Stage 4: XML Generation (No AI)

Deterministic template-based XML builder. Takes the classified question objects and produces Forsta XML strings. This is the one stage where the output should be highly predictable and consistent.

**Template registry:** A Python dictionary mapping each `forsta_type` to an XML template function. Each function takes the question object and returns a formatted XML string.

The existing `finalize_question()` logic is a good starting point, but refactored into clean, testable functions:

- `build_radio(q)` - single-select radio
- `build_checkbox(q)` - multi-select checkbox
- `build_select(q)` - dropdown select
- `build_text(q)` - open-end text
- `build_number(q)` - numeric input
- `build_html(q)` - informational HTML block
- `build_term(q)` - termination block
- `build_matrix(q)` - matrix/grid (radio with rows + cols)
- `build_suspend()` - page break suspend tag

**Special handlers preserved from current script:**
- US States dropdown (auto-populates 50 states + DC)
- Country dropdown (auto-populates country list)
- Year dropdown (generates year range)
- Zip code text input with verification
- Exclusive/anchor modifiers on checkbox rows

**New handlers needed:**
- Condition wrapping (cond attributes on questions)
- Block randomization markers
- Termination on specific answer choices
- Data quality check comments (as XML comments)
- Matrix/grid from non-table formats

---

## Stage 5: Assembly + Validation (No AI)

Joins all XML blocks with proper spacing, adds structural elements, and validates the output.

**Assembly:**
- Join blocks with `\n\n` separator
- Insert `<suspend/>` tags at page break positions
- Add any block randomization wrappers
- Prepend any survey-level metadata if needed

**Validation:**
- Well-formed XML check (parseable)
- Label uniqueness check (no duplicate labels)
- Row/choice label uniqueness within each question
- Required attributes present for each question type
- Answer count sanity checks (radio needs 2+ answers, etc.)

**Output:** Final XML string, ready for Forsta import.

---

## Question Types Not Currently Handled

These are common survey question types the AI should be able to detect and handle, beyond what the current script supports:

| Type | Description | Forsta XML |
|------|-------------|------------|
| Likert scale | Agreement/satisfaction ratings | `<radio>` with scale labels |
| Matrix (non-table) | Statements rated on common scale | `<radio>` with rows + cols |
| Ranking | Drag-and-drop or numbered ranking | `<radio>` with rank attributes |
| Slider/scale | Numeric slider (0-10, NPS, etc.) | `<number>` or custom |
| Date input | Date picker | `<text>` with date verify |
| Constant sum | Allocate points across options | `<number>` per row |
| Image choice | Select from images | `<radio>` with image markup |
| Net Promoter Score | 0-10 likelihood scale | `<radio>` or `<number>` |
| True/False | Binary choice | `<radio>` with 2 options |
| Yes/No | Binary choice | `<radio>` with 2 options |
| Paired comparison | A vs B choices | `<radio>` |
| MaxDiff | Best/worst selection | Custom handling |
| Semantic differential | Scale between opposites | `<radio>` with endpoint labels |

---

## Technical Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.10+ |
| .docx parsing | python-docx |
| AI provider | OpenAI API (GPT-4o or GPT-4.1) |
| Web framework | Streamlit (keep for now, easy sharing) |
| XML handling | Built-in xml.etree or lxml for validation |
| Config | .env file for API keys, model settings |
| Testing | pytest |

**Streamlit note:** Streamlit is fine for internal team sharing. It can be deployed to Streamlit Cloud for free, or run locally. If the team grows or needs authentication/collaboration features, we can revisit with something like FastAPI + React later. But for now, Streamlit keeps things simple and shareable.

---

## Development Phases

### Phase 1: Foundation (Refactor + Stage 1)
- Refactor the monolithic script into a modular package structure
- Implement Stage 1 (raw extraction) as a standalone module
- Set up project scaffolding (config, .env, requirements, etc.)
- Create test fixtures from both the original test doc and the AOT FINAL doc

**Folder structure:**
```
survey_xml_generator/
  __init__.py
  config.py           # API keys, model settings, constants
  extractor.py         # Stage 1: .docx extraction
  segmenter.py         # Stage 2: AI document segmentation
  classifier.py        # Stage 3: AI question classification
  xml_builder.py       # Stage 4: deterministic XML generation
  assembler.py         # Stage 5: assembly + validation
  prompts/
    segmentation.py    # System/user prompts for Stage 2
    classification.py  # System/user prompts for Stage 3
  data/
    us_states.py       # State list
    countries.py       # Country list
  streamlit_app.py     # UI entry point
  requirements.txt
  .env.example
  tests/
    test_extractor.py
    test_xml_builder.py
    test_end_to_end.py
    fixtures/          # Test .docx files and expected outputs
```

### Phase 2: AI Pipeline (Stages 2 + 3)
- Implement Stage 2 (segmentation) with OpenAI API
- Implement Stage 3 (classification) with OpenAI API
- Design and test prompts with both test documents
- Handle chunking for long documents
- Add retry/error handling for API calls

### Phase 3: XML Generation (Stages 4 + 5)
- Refactor existing finalize_question() into clean template functions
- Add new question type handlers (matrix from non-table, conditions, etc.)
- Implement assembly and validation
- End-to-end testing with the AOT FINAL doc

### Phase 4: UI + Polish
- Update the Streamlit app to use the new pipeline
- Add a progress indicator (Stage 1... Stage 2... etc.)
- Add a "review" step where the user can see and edit the AI's classifications before XML generation
- Add an XML diff/preview panel
- Error reporting and logging
- Deploy instructions

### Phase 5: Hardening
- Prompt tuning based on real survey results
- Edge case handling
- Cost optimization (caching, token management)
- Documentation update

---

## Key Design Decisions

**1. Why separate segmentation and classification into two AI passes?**
Combining them into one pass seems simpler, but in practice: (a) segmentation of a 600-paragraph document is a different cognitive task than classifying individual questions, (b) two smaller, focused prompts are more reliable than one large complex prompt, (c) it's easier to debug - if the XML is wrong, you can check whether segmentation or classification was the issue.

**2. Why keep XML generation deterministic?**
The AI is great at understanding messy human-written documents. It's not great at producing syntactically perfect XML with exactly the right attributes every time. By using the AI only for understanding and a deterministic builder for output, we get the best of both worlds: flexible input handling with consistent, correct output.

**3. Why OpenAI?**
Per your preference. GPT-4o offers a good balance of quality and cost for structured extraction tasks. The architecture is provider-agnostic though - swapping to Claude or another provider would only require changing the API calls in `segmenter.py` and `classifier.py`.

**4. Why Streamlit for sharing?**
Lowest friction for team sharing. `streamlit run app.py` and anyone on the team can use it locally. Streamlit Cloud gives you a shareable URL with zero infrastructure. If you need auth or multi-user features later, we can migrate to a proper web app.

---

## Estimated Effort

| Phase | Estimated Time |
|-------|---------------|
| Phase 1: Foundation | 1 session |
| Phase 2: AI Pipeline | 1-2 sessions |
| Phase 3: XML Generation | 1 session |
| Phase 4: UI + Polish | 1 session |
| Phase 5: Hardening | Ongoing |

---

## What I Need From You

1. **OpenAI API key** - needed for Stage 2 and 3. I'll set up .env support so you never have to hardcode it.
2. **Forsta XML examples** - If you have any actual Forsta-accepted XML from previous surveys (the output that was uploaded to Forsta and worked), that would be extremely valuable for validating our output. The closer I can match known-good output, the better.
3. **Feedback loop** - After Phase 3, I'll run the full pipeline on the AOT FINAL doc and we can compare the output against what a human programmer would produce. That comparison is the real test.
