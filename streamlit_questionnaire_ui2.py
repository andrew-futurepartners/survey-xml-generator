import streamlit as st
from docx import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.text.paragraph import Paragraph
from docx.table import Table
import re
from io import BytesIO
import os
import json
from openai import OpenAI

st.set_page_config(page_title="Survey XML Generator", layout="wide")
st.title("📄 Survey Questionnaire to XML")

# === CONFIGURATION ===
from dotenv import load_dotenv
load_dotenv()
client = OpenAI()

# === CODE BLOCK: Utility Functions (from local script) ===
def iter_block_items(parent):
    for child in parent.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)

def detect_atmost(instruction):
    word_to_num = {
        "one": 1, "two": 2, "three": 3, "four": 4,
        "five": 5, "six": 6, "seven": 7, "eight": 8,
        "nine": 9, "ten": 10
    }
    lowered = instruction.lower()
    match_digit = re.search(r'(up to|at most)\s+(\d+)', lowered)
    if match_digit:
        return int(match_digit.group(2))
    match_word = re.search(r'(up to|at most)\s+(one|two|three|four|five|six|seven|eight|nine|ten)', lowered)
    if match_word:
        return word_to_num.get(match_word.group(2))
    return None

def clean_label_and_title(text):
    match = re.match(r'^\[(.+?)\]\s*(.*)', text.strip())
    return (match.group(1), match.group(2)) if match else (None, text.strip())

def classify_question_type(title, instruction, answers):
    prompt = f"""
You are a survey logic analyst. Classify the type of question and its key attributes based on the following information:

Title: {title}
Instruction: {instruction}
Answers:
{chr(10).join(answers)}

Return a JSON object with this structure:

// Question-level attributes:
{{
  "type": "radio",        // One of: text, number, radio, checkbox, select
  "randomize": true,       // Shuffle response rows
  "atmost": null,          // Max number of responses allowed (for multiselects)

  // Response-level attributes:
  "anchor_rows": [],       // List of row labels or values that should not be shuffled
  "exclusive_rows": [],    // List of row labels that should be exclusive
  "other_specify_rows": [] // List of rows that should trigger an open text input
}}

Only return valid JSON. Do not explain or add any extra text.
"""
    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a survey logic classifier."},
                {"role": "user", "content": prompt.strip()}
            ],
            temperature=0
        )
        content = response.choices[0].message.content
        return json.loads(content)
    except Exception as e:
        return {"type": "text", "error": str(e)}

# === CODE BLOCK: Streamlit App UI and Execution ===
uploaded_file = st.file_uploader("Upload your .docx questionnaire file", type="docx")

if uploaded_file:
    document = Document(uploaded_file)
    st.subheader("AI Classification Results (Debugging)")
    xml_blocks = []
    label, title, instruction = None, None, None
    current_answers = []
    mode = None
    awaiting_instruction = False
    modifiers = []
    matrix_mode = False
    block_iter = iter_block_items(document)

    for block in block_iter:
        if isinstance(block, Paragraph):
            text = block.text.strip()

            if text.startswith("[") and text[1:8].lower() == "comment":
                lbl, content = clean_label_and_title(text)
                xml_blocks.append(f'<html label="{lbl}" where="survey">{content}</html>')
                continue

            if re.fullmatch(r'<<PAGE BREAK>>', text):
                if label:
                    classification = classify_question_type(title, instruction, current_answers)
                    st.json({"label": label, **classification})
                xml_blocks.append("<suspend/>")
                label = title = instruction = None
                current_answers = []
                mode = None
                awaiting_instruction = False
                modifiers = []
                matrix_mode = False
                continue

            if re.match(r'^\[(.+?)\]', text) and not text[1:2].isupper():
                if label:
                    classification = classify_question_type(title, instruction, current_answers)
                    st.json({"label": label, **classification})
                label, title = clean_label_and_title(text)
                instruction = ""
                current_answers = []
                awaiting_instruction = True
                mode = None
                modifiers = []
                matrix_mode = False
                continue

            if awaiting_instruction and text:
                instruction = text
                awaiting_instruction = False
                mode = "collect"
                continue

            if mode == "collect" and text:
                if re.fullmatch(r'\[[^\]]+\]', text.strip()) and text.strip()[1:-1].isupper():
                    modifiers.append(text.strip())
                else:
                    current_answers.append(text)

        elif isinstance(block, Table):
            if label and title and instruction:
                classification = classify_question_type(title, instruction, [])
                st.json({"label": label, **classification})
                label = title = instruction = None
                current_answers = []
                mode = None
                modifiers = []
                matrix_mode = True

    if label and not matrix_mode:
        classification = classify_question_type(title, instruction, current_answers)
        st.json({"label": label, **classification})

    st.subheader("(WIP) XML Output Will Be Added After AI Classification Integration")