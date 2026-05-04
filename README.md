# Survey XML Generator v2.0

AI-powered tool that converts survey questionnaire Word documents (.docx) into Forsta/Decipher-compatible XML. Uses OpenAI GPT-4o to dynamically parse any questionnaire format -- no rigid formatting rules required.

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env        # add your OpenAI API key
streamlit run app.py
```

Then upload a .docx survey file and click "Generate XML." See [Running Locally](#running-locally) and [Deploy to Streamlit Cloud](#deploy-to-streamlit-cloud) below for full details.

## How It Works

The pipeline has 5 stages. Stages 2 and 3 use AI (OpenAI GPT-4o) to understand the document. Stages 1, 4, and 5 are deterministic -- no AI involved, no hallucination risk in the actual XML output.

### Stage 1: Extraction (`extractor.py`)
Reads the .docx file and extracts every paragraph, table, and page break into a flat list of blocks. Each block includes the raw text plus metadata like bold/italic/underline, style name, list indent level, and table cell contents. This is pure python-docx parsing -- no AI.

### Stage 2: Segmentation (`segmenter.py`)
Sends the extracted blocks to GPT-4o in chunks (150 blocks per chunk, 25-block overlap at boundaries). The AI identifies logical survey boundaries and classifies each group of blocks as one of: question, pagebreak, text_screen, condition, block_marker, term, metadata, or note.

For long documents, chunks are processed sequentially and then deduplicated to handle the overlap regions.

### Stage 3: Classification (`classifier.py`)
Takes the segmented blocks and sends them to GPT-4o for detailed classification. For each question, the AI determines:

- Forsta question type (radio, checkbox, select, text, textarea, number, html, term)
- Label, title, and comment text
- Answer choices with values, attributes (exclusive, anchor, open-end)
- Matrix structure (rows, columns, scale points)
- Conditional visibility (references to condition definitions)
- Special handling (state dropdowns, country lists, year ranges)
- Shuffle, randomize, atleast/atmost, verify rules

The AI also generates `<condition>` definitions for any branching, skip logic, or termination logic it finds in the document. Previously identified conditions are passed as context to subsequent chunks so the AI can reference them without creating duplicates.

### Stage 4: XML Building (`xml_builder.py`)
Deterministic template functions that take the classified question dicts and produce Forsta XML strings. One function per question type:

- `build_radio()` -- single-select, including matrix support
- `build_checkbox()` -- multi-select with exclusive/anchor/open-end
- `build_select()` -- dropdowns, with auto-population for US states, countries, year ranges, numeric ranges
- `build_text()` -- open-end text
- `build_textarea()` -- long text
- `build_number()` -- numeric with verify/range
- `build_html_block()` -- informational/instructional text screens
- `build_term()` -- termination
- `build_condition()` -- condition definitions
- `build_suspend()` -- page breaks

All builders support the `cond` attribute for conditional visibility.

### Stage 5: Assembly (`assembler.py`)
Wraps all the generated XML in a `<survey>` root element with proper Forsta namespaces and default attributes. Interleaves page breaks and comments back into document order. Runs validation checks for duplicate labels, undefined condition references, and basic XML well-formedness (tag balance, unescaped ampersands).

## Project Structure

```
survey-xml-generator/
  app.py                          # Streamlit UI
  requirements.txt                # Python dependencies
  .env                            # API key + model config (not committed)
  .env.example                    # Template for .env

  survey_xml_generator/
    __init__.py                   # Package init, version
    config.py                     # Loads .env, pipeline settings, Forsta XML defaults
    extractor.py                  # Stage 1: .docx extraction
    segmenter.py                  # Stage 2: AI document segmentation
    classifier.py                 # Stage 3: AI question classification + conditions
    xml_builder.py                # Stage 4: Deterministic XML template builders
    assembler.py                  # Stage 5: Assembly, validation, full pipeline entry points

    ai_client.py                  # Shared OpenAI client wrapper (retry, JSON parsing)

    prompts/
      __init__.py
      segmentation.py             # System + user prompts for Stage 2
      classification.py           # System + user prompts for Stage 3

    data/
      __init__.py
      us_states.py                # 50 states + DC for dropdown auto-population
      countries.py                # Country list matching Forsta standard library

  tests/
    __init__.py
```

## Supported Forsta XML Features

- Question types: `<radio>`, `<checkbox>`, `<select>`, `<text>`, `<textarea>`, `<number>`, `<html>`, `<term>`
- Conditional logic: `<condition>` definitions with `cond` attribute on any element
- Matrix/grid questions with shared column scales
- Answer attributes: `shuffle`, `exclusive`, `anchor`, `open`, `openSize`, `randomize`
- Special dropdowns: US states, countries, year ranges, numeric ranges
- Page breaks (`<suspend/>`)
- Validation: `verify`, `atleast`, `atmost`, range checking
- Proper Forsta namespaces (`builder`, `ss`, `html`)

## Configuration

All config lives in `.env`:

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | (required) | Your OpenAI API key |
| `OPENAI_MODEL` | `gpt-4o` | Primary model for AI stages |
| `OPENAI_MODEL_MINI` | `gpt-4o-mini` | Faster/cheaper alternative |

Pipeline settings are in `config.py`:

| Setting | Default | Description |
|---|---|---|
| `SEGMENTATION_CHUNK_SIZE` | 150 | Max blocks per AI chunk |
| `SEGMENTATION_CHUNK_OVERLAP` | 25 | Overlap between chunks |
| `AI_TEMPERATURE` | 0.1 | Low = more deterministic AI output |

## Usage Without Streamlit

You can also run the pipeline from Python directly:

```python
from survey_xml_generator.assembler import process_file

xml, warnings, debug = process_file(
    "path/to/questionnaire.docx",
    survey_name="MySurvey",
)

with open("output.xml", "w") as f:
    f.write(xml)
```

Or from the command line:

```bash
python -m survey_xml_generator.segmenter path/to/questionnaire.docx   # Stage 2 only
python -m survey_xml_generator.classifier path/to/questionnaire.docx  # Stages 1-3
```

## Deploy to Streamlit Cloud

1. Push your repo to GitHub (ensure `.env` and `.streamlit/secrets.toml` are **not** committed).
2. Go to [share.streamlit.io](https://share.streamlit.io) and connect your GitHub repo.
3. Set the main file path to `app.py`.
4. In the app's **Secrets** section (Settings → Secrets), add:
   ```toml
   OPENAI_API_KEY = "sk-your-key-here"
   ```
5. Deploy. The app will read the key from Streamlit secrets automatically.

If you don't configure a secret, users will be prompted to enter their own OpenAI API key in the sidebar before they can generate XML.

## Running Locally

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up your API key
cp .env.example .env
# Edit .env and add your OpenAI API key

# 3. Run the app
streamlit run app.py
```

Alternatively, you can create `.streamlit/secrets.toml` (from the provided `.streamlit/secrets.toml.example`) instead of using `.env` — either method works locally.

## Current Status

This is the initial build of v2.0. The full pipeline is wired up and ready for end-to-end testing. The extraction stage has been tested successfully against the AOT High-Value Traveler survey (627 blocks extracted). The AI stages (segmentation + classification) and Streamlit UI are built and import-tested but need a live OpenAI API connection to run end-to-end.

Next steps after initial testing: prompt tuning based on output quality, edge case handling, and potentially a review/edit step in the Streamlit UI before final XML download.
