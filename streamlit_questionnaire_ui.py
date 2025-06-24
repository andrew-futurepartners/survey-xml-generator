import streamlit as st
from docx import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.text.paragraph import Paragraph
from docx.table import Table
import re
from io import StringIO

st.set_page_config(page_title="Survey XML Generator", layout="wide")
st.title("📄 Survey Questionnaire to XML")

# Utilities
def iter_block_items(parent):
    for child in parent.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)

def detect_atmost(instruction):
    word_to_num = {"one": 1, "two": 2, "three": 3, "four": 4,
                   "five": 5, "six": 6, "seven": 7, "eight": 8,
                   "nine": 9, "ten": 10}
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

# Main function
uploaded_file = st.file_uploader("Upload your .docx questionnaire file", type="docx")

if uploaded_file:
    document = Document(uploaded_file)
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
                label = title = instruction = None
                current_answers = []
                mode = None
                awaiting_instruction = False
                modifiers = []
                matrix_mode = False
                xml_blocks.append("<suspend/>")
                continue

            if re.match(r'^\[(.+?)\]', text) and not text[1:2].isupper():
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

    # Display results
    st.subheader("Generated XML Preview")
    xml_output = "\n\n".join(xml_blocks)
    st.code(xml_output, language="xml")

    st.download_button(
        label="📥 Download XML Output",
        data=xml_output,
        file_name="survey_output.txt",
        mime="text/plain"
    )
