# Survey XML Generator

A Python tool that parses survey questionnaire Word documents (`.docx`) and automatically generates the standard XML formatting used by the survey software platform. It detects question types from the document structure and content, then emits the correct XML element for each one.

Two entry points are provided:

| File | Interface | Description |
|------|-----------|-------------|
| `streamlit_questionnaire_ui.py` | Streamlit web app | Upload a `.docx` file through the browser, view the generated XML, and download it. |
| `surveytoXML.py` | CLI script | Reads a hard-coded `.docx` path and writes the XML to `survey_output.txt`. |

---

## Prerequisites

- Python 3.8+
- Dependencies listed in `requirements.txt`:
  - **streamlit** -- web UI framework
  - **python-docx** -- reads `.docx` files

Install with:

```bash
pip install -r requirements.txt
```

---

## Running the App

### Streamlit (recommended)

```bash
streamlit run streamlit_questionnaire_ui.py
```

This opens a browser window where you can upload a `.docx` questionnaire file. The generated XML is displayed on-screen and can be downloaded as a `.txt` file.

### CLI

Edit the `doc_path` variable at the top of `surveytoXML.py` to point at your questionnaire file, then run:

```bash
python surveytoXML.py
```

Output is written to `survey_output.txt`.

---

## Expected Document Format

The `.docx` questionnaire must follow a consistent structure so the parser can identify questions, instructions, answer choices, and modifiers.

### Question block

```
[label] Question title text
Instruction text (e.g. "Please select all that apply.")
Answer choice 1
Answer choice 2
...
```

- **Label + title** -- A line starting with `[label]` begins a new question. The text inside the brackets becomes the XML label; the remaining text becomes the title.
- **Instruction** -- The first non-empty line after the label/title line is captured as the instruction (rendered as `<comment>` in XML).
- **Answer choices** -- Subsequent lines are collected as answer options until the next question label or page break.

### Special markers

| Marker | Effect |
|--------|--------|
| `<<PAGE BREAK>>` | Emits a `<suspend/>` tag and resets the parser state. |
| `[comment...] Text` | Emits an `<html label="..." where="survey">` block (informational text, not a question). |
| `[term...] Text` | Emits a `<term label="..." cond="1">` termination block. |
| `>Programming Note:` | Line is skipped entirely (Streamlit version). |

### Modifiers (inline or standalone)

Modifiers are bracketed tags that appear either on their own line or appended to an answer choice.

| Modifier | Where it appears | Effect |
|----------|-----------------|--------|
| `[RANDOMIZE]` | Standalone line or in instruction | Adds `shuffle="rows"` to the question element. |
| `[EXCLUSIVE]` | Appended to an answer choice | Adds `exclusive="1"` to that row (checkbox questions). |
| `[ANCHOR]` | Appended to an answer choice or table row | Adds `randomize="0"` to that row and enables `shuffle="rows"` on the parent element. |
| `[VERIFY: type]` | Standalone line | Adds `verify="type"` to text/number inputs. Recognized types: `zipcode`, `email`, `number`, `digits`. |
| `[RANGE: min-max]` | Standalone line | Adds `verify="range(min,max)"` to number inputs. |

---

## Question Type Detection

The parser determines the XML element type using these rules (evaluated in order):

### 1. Known labels

| Label (case-insensitive) | XML element | Behavior |
|--------------------------|-------------|----------|
| `qState` | `<select>` | Auto-populates with all 50 US states + DC. |
| `qCountry` | `<select>` | Auto-populates with a built-in list of countries. |
| `qZipCode` | `<text>` | Forces a text input with `size="10"`. |

### 2. Drop-down detection

If any answer choice contains the phrase "drop down", the question becomes a `<select>`. A numeric range pattern (e.g., `2007-1910` or `0-100 or more`) is parsed to auto-generate the `<choice>` list; otherwise a placeholder `PASTE CHOICE OPTIONS` is emitted.

### 3. No answer choices

When no valid answer choices are present after filtering:

| Condition | XML element |
|-----------|-------------|
| Instruction contains the word "number" | `<number>` (numeric input) |
| Otherwise | `<text>` (open-ended text input) |

Both support `[VERIFY]` and `[RANGE]` modifiers.

### 4. Multi-select (checkbox)

A question is treated as multi-select if **any** of the following are true:

- The instruction contains "all that apply".
- The instruction contains "up to N" or "at most N" (sets `atmost="N"`).
- Any answer choice carries an `[EXCLUSIVE]` or `[ANCHOR]` modifier.

Emitted as `<checkbox>` with `atleast="1"`.

### 5. Single-select (radio)

The default for questions that have answer choices but don't match multi-select criteria. Emitted as `<radio>`.

### 6. Matrix / grid (table)

When a Word **table** follows a question label and instruction, it is treated as a matrix question. Column headers become `<col>` elements and row labels become `<row>` elements, all wrapped in a `<radio>` tag. Anchor modifiers on individual rows are respected.

---

## XML Output Examples

**Single-select (radio):**
```xml
<radio label="q1">
  <title>What is your favorite color?</title>
  <comment>Please select one.</comment>
  <row label="r1">Red</row>
  <row label="r2">Blue</row>
  <row label="r3">Green</row>
</radio>
```

**Multi-select (checkbox) with shuffle and exclusive:**
```xml
<checkbox label="q2" atleast="1" shuffle="rows">
  <title>Which brands do you recognize?</title>
  <comment><span>Please select all that apply.</span></comment>
  <row label="r1">Brand A</row>
  <row label="r2">Brand B</row>
  <row label="r3">None of the above <exclusive="1"></row>
</checkbox>
```

**Open-ended text:**
```xml
<text label="q3" optional="0" size="25">
  <title>Please describe your experience.</title>
  <comment>Type your answer below.</comment>
</text>
```

**Drop-down select:**
```xml
<select label="q4">
  <title>In what year were you born?</title>
  <comment>Please select from the list.</comment>
  <choice label="ch1">2007</choice>
  <choice label="ch2">2006</choice>
  ...
  <choice label="ch98">1910</choice>
</select>
```

**Matrix (table-based radio):**
```xml
<radio label="q5" shuffle="rows">
  <title>Rate each item.</title>
  <comment>Select one per row.</comment>
  <col label="c1">Excellent</col>
  <col label="c2">Good</col>
  <col label="c3">Poor</col>
  <row label="r1">Service</row>
  <row label="r2">Quality</row>
  <row label="r3" randomize="0">Overall</row>
</radio>
```

---

## Architecture Overview

```
.docx upload
     │
     ▼
iter_block_items()        Yields Paragraph and Table objects in document order
     │
     ▼
Main parsing loop         State machine that tracks:
  ├─ label                  - current question label
  ├─ title                  - current question title
  ├─ instruction            - instruction/comment text
  ├─ current_answers[]      - collected answer choices
  ├─ modifiers[]            - bracketed modifier tags
  └─ mode                   - parser state (None → awaiting_instruction → collect)
     │
     ▼
finalize_question()       Applies detection rules and returns the XML string
     │
     ▼
xml_blocks[]              Accumulated XML fragments joined with blank lines
     │
     ▼
Output                    Displayed in Streamlit / written to file
```

The parser is a single-pass state machine. Each paragraph is evaluated in order: question labels reset the state, the next non-empty line becomes the instruction, and all subsequent lines are collected as answers or modifiers until the next question or page break triggers finalization.
