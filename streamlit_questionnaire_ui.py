import streamlit as st
from docx import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.text.paragraph import Paragraph
from docx.table import Table
import re
from io import BytesIO

st.set_page_config(page_title="Survey XML Generator", layout="wide")
st.title("📄 Survey Questionnaire to XML")

# === CODE BLOCK: Utility Functions ===
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

US_STATES = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware",
    "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
    "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana",
    "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina",
    "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina",
    "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington", "West Virginia",
    "Wisconsin", "Wyoming", "District of Columbia"
]

COUNTRIES = [
    "Afghanistan", "Albania", "Algeria", "American Samoa", "Andorra", "Angola", "Anguilla", "Antarctica",
    "Antigua and Barbuda", "Argentina", "Armenia", "Aruba", "Australia", "Austria", "Azerbaijan", "The Bahamas",
    "Bahrain", "Bangladesh", "Barbados", "Belarus", "Belgium", "Belize", "Bermuda", "Bolivia",
    "Bosnia and Herzegovina", "Botswana", "Brazil", "British Indian Ocean Territory", "British Virgin Islands",
    "Brunei", "Bulgaria", "Burkina Faso", "Burma", "Cambodia", "Cameroon", "Canada", "Cape Verde",
    "Cayman Islands", "Central African Republic", "Chad", "Chile", "China", "Colombia",
    "Democratic Republic of the Congo", "Republic of the Congo", "Cook Islands", "Costa Rica", "Cote d'Ivoire",
    "Croatia", "Cuba", "Cyprus", "Czech Republic", "Denmark", "Dominica", "Dominican Republic", "Ecuador",
    "Egypt", "El Salvador", "Eritrea", "Estonia", "Ethiopia", "Fiji", "Finland", "France", "French Guiana",
    "French Polynesia", "French Southern and Antarctic Lands", "The Gambia", "Gaza Strip", "Georgia", "Germany",
    "Ghana", "Gibraltar", "Greece", "Greenland", "Guam", "Guatemala", "Guinea", "Guinea-Bissau", "Guyana",
    "Haiti", "Honduras", "Hong Kong", "Hungary", "Iceland", "India", "Indonesia", "Iran", "Iraq", "Ireland",
    "Israel", "Italy", "Jamaica", "Japan", "Jersey", "Jordan", "Kazakhstan", "Kenya", "North Korea",
    "South Korea", "Kuwait", "Kyrgyzstan", "Laos", "Latvia", "Lebanon", "Liberia", "Libya", "Liechtenstein",
    "Lithuania", "Luxembourg", "Macau", "Macedonia", "Madagascar", "Malaysia", "Maldives", "Mali", "Malta",
    "Mexico", "Moldova", "Monaco", "Mongolia", "Montserrat", "Morocco", "Mozambique", "Namibia", "Nepal",
    "Netherlands", "Netherlands Antilles", "New Zealand", "Nicaragua", "Niger", "Nigeria", "Norway", "Oman",
    "Pakistan", "Palau", "Panama", "Papua New Guinea", "Paraguay", "Peru", "Philippines", "Poland",
    "Portugal", "Puerto Rico", "Qatar", "Romania", "Russia", "Rwanda", "Samoa", "Saint Lucia",
    "Saint Vincent and the Grenadines", "Saudi Arabia", "Senegal", "Serbia and Montenegro", "Sierra Leone",
    "Singapore", "Slovakia", "Slovenia", "Somalia", "South Africa", "Spain", "Sri Lanka", "Sudan", "Suriname",
    "Swaziland", "Sweden", "Switzerland", "Syria", "Taiwan", "Tajikistan", "Tanzania", "Thailand", "Tonga",
    "Trinidad and Tobago", "Tunisia", "Turkey", "Turkmenistan", "Uganda", "Ukraine", "United Arab Emirates",
    "United Kingdom", "United States", "Uruguay", "Uzbekistan", "Vatican City", "Venezuela", "Vietnam",
    "Virgin Islands", "West Bank", "Western Sahara", "Yemen", "Zambia", "Zimbabwe"
]

def finalize_question(label, title, instruction, answers, modifiers):
    if not label or not title:
        return None

    if label.lower() == "qstate":
        answers = US_STATES
        xml_lines = [f'<select label="{label}">']
        xml_lines.append(f'  <title>{title}</title>')
        xml_lines.append(f'  <comment>{instruction}</comment>')
        for idx, choice in enumerate(answers, 1):
            xml_lines.append(f'  <choice label="ch{idx}">{choice}</choice>')
        xml_lines.append('</select>')
        return "\n".join(xml_lines)

    elif label.lower() == "qcountry":
        answers = COUNTRIES
        xml_lines = [f'<select label="{label}">']
        xml_lines.append(f'  <title>{title}</title>')
        xml_lines.append(f'  <comment>{instruction}</comment>')
        for idx, choice in enumerate(answers, 1):
            xml_lines.append(f'  <choice label="ch{idx}">{choice}</choice>')
        xml_lines.append('</select>')
        return "\n".join(xml_lines)

    filtered_answers = []
    for a in answers:
        if re.fullmatch(r'\[[^\]]+\]', a.strip()) and a.strip()[1:-1].isupper():
            continue
        if re.fullmatch(r'_+', a.strip()) or "leave blank" in a.lower():
            continue
        filtered_answers.append(a)

    drop_down_row = next((a for a in filtered_answers if "drop down" in a.lower()), None)
    if drop_down_row:
        # Normalize brackets (handle both <...> and [...] cases)
        drop_down_row = re.sub(r'[<\[]\s*drop down\s*[–-]\s*(.*?)\s*[>\]]', r'\1', drop_down_row, flags=re.IGNORECASE)

        # Match optional prefix and number range (e.g., "After 2007-1910" or "0-100 or more")
        match = re.match(r'(?:([A-Za-z ]+)\s+)?(\d+)\s*[-–]\s*(\d+)(?:\s*([A-Za-z ]+))?', drop_down_row.strip())
        if match:
            prefix = (match.group(1) or "").strip()
            start = int(match.group(2))
            end = int(match.group(3))
            suffix = (match.group(4) or "").strip()

            step = -1 if start > end else 1
            choices = []
            for i in range(start, end + step, step):
                text = str(i)
                if i == start and prefix:
                    text = f"{prefix} {text}"
                if i == end and suffix:
                    text = f"{text} {suffix}"
                choices.append(text)
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
        # --- force qZipCode to always be a text input ---
        if label.lower() == "qzipcode":
            verify_type = None
            for mod in modifiers:
                match = re.match(r'\[VERIFY:\s*(.+?)\s*\]', mod, re.IGNORECASE)
                if match:
                    verify_type = match.group(1).strip().lower().replace(" ", "")
            verify_attr = f' verify="{verify_type}"' if verify_type else ""
            xml_lines = [
                f'<text label="{label}" optional="0" size="10"{verify_attr}>',
                f'  <title>{title}</title>',
                f'  <comment>{instruction}</comment>',
                f'</text>'
            ]
            return "\n".join(xml_lines)

        if "number" in instruction.lower():
            verify_type = None
            for mod in modifiers:
                range_match = re.match(r'\[RANGE:\s*(\d+)\s*[-–]\s*(\d+)\s*\]', mod, re.IGNORECASE)
                if range_match:
                    min_val, max_val = range_match.groups()
                    verify_type = f'range({min_val},{max_val})'
                    break

            verify_attr = f' verify="{verify_type}"' if verify_type else ""
            xml_lines = [
                f'<number label="{label}" optional="0" size="10"{verify_attr}>',
                f'  <title>{title}</title>',
                f'  <comment>{instruction}</comment>',
                f'</number>'
            ]
            return "\n".join(xml_lines)
        else:
            verify_type = None
            for mod in modifiers:
                match = re.match(r'\[VERIFY:\s*(.+?)\s*\]', mod, re.IGNORECASE)
                if match:
                    verify_type = match.group(1).strip().lower().replace(" ", "")
            verify_attr = f' verify="{verify_type}"' if verify_type else ""
            xml_lines = [
                f'<text label="{label}" optional="0" size="25"{verify_attr}>',
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

# === CODE BLOCK: Streamlit App UI and Execution ===
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

            if text.startswith("["):
                lbl, content = clean_label_and_title(text)
                if lbl and lbl.lower().startswith("term"):
                    xml_blocks.append(f'<term label="{lbl}" cond="1">{content}</term>')
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
                if ">programming note:" in text.lower():
                    continue  # Skip any line containing a programming note
                stripped = text.strip()
                if re.fullmatch(r'\[[^\]]+\]', stripped):
                    if stripped[1:-1].lower().startswith("verify:"):
                        modifiers.append(stripped)
                    elif stripped[1:-1].isupper():
                        modifiers.append(stripped)
                    else:
                        current_answers.append(text)
                elif stripped.lower().startswith("<drop down"):
                    current_answers.append(stripped)  # Ensure drop-down definitions get captured
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

    st.subheader("Generated XML Output")
    final_output = "\n\n".join(xml_blocks)
    st.code(final_output, language="xml")
    st.download_button("📅 Download XML Output", final_output, file_name="survey_output.txt", mime="text/plain")