from docx import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.text.paragraph import Paragraph
from docx.table import Table
import re
from pathlib import Path

# Load document
doc_path = "Survey Programming Question Examples.docx"
output_path = "survey_output.txt"
doc = Document(doc_path)

# Utilities
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

def finalize_question(label, title, instruction, answers, modifiers):
    if not label or not title:
        return None

    filtered_answers = []
    for a in answers:
        if re.fullmatch(r'\[[^\]]+\]', a.strip()) and a.strip()[1:-1].isupper():
            continue
        if re.fullmatch(r'_+', a.strip()) or "leave blank" in a.lower():
            continue
        filtered_answers.append(a)

    drop_down_row = next((a for a in filtered_answers if "<drop down>" in a.lower()), None)
    if drop_down_row:
        range_match = re.search(r'\[([^\[\]\u2013-]+)[\u2013-]([^\[\]\u2013-]+)\]', drop_down_row)
        if range_match:
            left = range_match.group(1).strip()
            right = range_match.group(2).strip()
            left_num = re.match(r'(\d+)(.*)', left)
            right_num = re.match(r'(\d+)(.*)', right)
            if left_num and right_num:
                start = int(left_num.group(1))
                end = int(right_num.group(1))
                left_text = left_num.group(2).strip()
                right_text = right_num.group(2).strip()
                choices = [f"{start} {left_text}".strip()] + [str(n) for n in range(start + 1, end)] + [f"{end} {right_text}".strip()]
            else:
                choices = ["PASTE CHOICE OPTIONS"]
        else:
            choices = ["PASTE CHOICE OPTIONS"]

        xml_lines = [f'<select label="{label}">']
        xml_lines.append(f'  <title>{title}</title>')
        xml_lines.append(f'  <comment>{instruction}</comment>')
        for idx, choice in enumerate(choices, 1):
            xml_lines.append(f'  <choice label="ch{idx}">{choice}</choice>')
        xml_lines.append('</select>')
        return "\n".join(xml_lines)

    if not filtered_answers:
        if "number" in instruction.lower():
            xml_lines = [
                f'<number label="{label}" optional="0" size="10">',
                f'  <title>{title}</title>',
                f'  <comment>{instruction}</comment>',
                f'</number>'
            ]
        else:
            xml_lines = [
                f'<text label="{label}" optional="0" size="25">',
                f'  <title>{title}</title>',
                f'  <comment>{instruction}</comment>',
                f'</text>'
            ]
        return "\n".join(xml_lines)

    has_modifiers = any("exclusive" in a.lower() or "anchor" in a.lower() for a in filtered_answers)
    anchor_present = any("anchor" in a.lower() for a in filtered_answers)
    randomize_flag = any("random" in a.lower() for a in answers + [instruction] + modifiers)
    max_limit = detect_atmost(instruction)
    is_multiselect = ("all that apply" in instruction.lower()) or max_limit or has_modifiers

    if is_multiselect:
        checkbox_attrs = [f'label="{label}"', 'atleast="1"']
        if randomize_flag or anchor_present:
            checkbox_attrs.append('shuffle="rows"')
        if max_limit:
            checkbox_attrs.append(f'atmost="{max_limit}"')

        xml_lines = [f'<checkbox {" ".join(checkbox_attrs)}>']
        xml_lines.append(f'  <title>{title}</title>')
        xml_lines.append(f'  <comment><span>{instruction}</span></comment>')
        for idx, ans in enumerate(filtered_answers, 1):
            text = re.sub(r'\[.*?\]', '', ans).strip()
            modifiers = re.findall(r'\[(.*?)\]', ans)
            flags = []
            if any("exclusive" in m.lower() for m in modifiers):
                flags.append('exclusive="1"')
            if any("anchor" in m.lower() for m in modifiers):
                flags.append('randomize="0"')
            attr = " ".join(flags)
            xml_lines.append(f'  <row label="r{idx}" {attr}>{text}</row>' if attr else f'  <row label="r{idx}">{text}</row>')
        xml_lines.append('</checkbox>')
        return "\n".join(xml_lines)

    radio_attrs = [f'label="{label}"']
    if randomize_flag:
        radio_attrs.append('shuffle="rows"')
    xml_lines = [f'<radio {" ".join(radio_attrs)}>']
    xml_lines.append(f'  <title>{title}</title>')
    xml_lines.append(f'  <comment>{instruction}</comment>')
    for idx, ans in enumerate(filtered_answers, 1):
        xml_lines.append(f'  <row label="r{idx}">{ans}</row>')
    xml_lines.append('</radio>')

    return "\n".join(xml_lines)

if __name__ == "__main__":
    xml_blocks = []
    label, title, instruction = None, None, None
    current_answers = []
    mode = None
    awaiting_instruction = False
    modifiers = []
    matrix_mode = False
    block_iter = iter_block_items(doc)

    for block in block_iter:
        if isinstance(block, Paragraph):
            text = block.text.strip()

            if text.startswith("[") and text[1:8].lower() == "comment":
                lbl, content = clean_label_and_title(text)
                xml_blocks.append(f'<html label="{lbl}" where="survey">{content}</html>')
                continue

            if re.fullmatch(r'<<PAGE BREAK>>', text):
                if label:
                    finalized = finalize_question(label, title, instruction, current_answers, modifiers)
                    if finalized:
                        xml_blocks.append(finalized)
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
                    finalized = finalize_question(label, title, instruction, current_answers, modifiers)
                    if finalized:
                        xml_blocks.append(finalized)
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
            rows = block.rows
            if label and title and instruction:
                col_headers = [c.text.strip() for c in rows[0].cells[1:] if c.text.strip()]
                row_labels = [r.cells[0].text.strip() for r in rows[1:] if r.cells[0].text.strip()]
                randomize_flag = any("random" in m.lower() for m in modifiers + [instruction] + current_answers)
                anchor_flag = any("anchor" in m.lower() for m in modifiers + row_labels)

                attrs = [f'label="{label}"']
                if randomize_flag or anchor_flag:
                    attrs.append('shuffle="rows"')

                xml_lines = [f'<radio {" ".join(attrs)}>']
                xml_lines.append(f'  <title>{title}</title>')
                xml_lines.append(f'  <comment>{instruction}</comment>')
                for c_idx, col in enumerate(col_headers, 1):
                    xml_lines.append(f'  <col label="c{c_idx}">{col}</col>')
                for r_idx, row_text in enumerate(row_labels, 1):
                    clean_row = re.sub(r'\[.*?\]', '', row_text).strip()
                    row_mods = re.findall(r'\[(.*?)\]', row_text)
                    row_attrs = []
                    if any("anchor" in m.lower() for m in row_mods):
                        row_attrs.append('randomize="0"')
                    attr_str = f' {row_attrs[0]}' if row_attrs else ''
                    xml_lines.append(f'  <row label="r{r_idx}"{attr_str}>{clean_row}</row>')
                xml_lines.append('</radio>')
                xml_blocks.append("\n".join(xml_lines))
                label = title = instruction = None
                current_answers = []
                mode = None
                modifiers = []
                matrix_mode = True

    if label and not matrix_mode:
        finalized = finalize_question(label, title, instruction, current_answers, modifiers)
        if finalized:
            xml_blocks.append(finalized)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(xml_blocks))
