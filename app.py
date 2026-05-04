"""Streamlit UI for the AI Survey XML Generator.

Upload a .docx survey questionnaire, run the 5-stage pipeline,
and download clean Forsta XML.
"""

import streamlit as st
import time
import os
from pathlib import Path

# Ensure the project root is on the path so the package resolves
import sys

_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from survey_xml_generator.config import OPENAI_MODEL
from survey_xml_generator.ai_client import reset_client
from survey_xml_generator.assembler import process_bytes

# ---------------------------------------------------------------------------
# API key resolution: st.secrets -> .env -> user input
# ---------------------------------------------------------------------------

def _resolve_api_key() -> str:
    """Return the active OpenAI API key from the best available source."""
    # 1. Streamlit secrets (set in Cloud dashboard or .streamlit/secrets.toml)
    try:
        secret_key = st.secrets.get("OPENAI_API_KEY", "")
        if secret_key:
            os.environ["OPENAI_API_KEY"] = secret_key
            return secret_key
    except FileNotFoundError:
        pass

    # 2. Already in environment (loaded from .env by config.py or set by user)
    env_key = os.environ.get("OPENAI_API_KEY", "")
    if env_key:
        return env_key

    return ""

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Survey Programming Script",
    page_icon="📋",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("📋 Survey Programming Script")
    st.caption("AI-powered .docx to Forsta XML")

    st.divider()

    # API key status
    active_key = _resolve_api_key()
    if active_key:
        st.success("OpenAI API key active")
    else:
        st.error("No OpenAI API key found!")
        api_key_input = st.text_input("Enter OpenAI API Key:", type="password")
        if api_key_input:
            os.environ["OPENAI_API_KEY"] = api_key_input
            reset_client()
            st.rerun()

    st.divider()

    # Survey name
    survey_name = st.text_input(
        "Survey Name",
        value="Survey",
        help="Used as the name attribute on the <survey> root element.",
    )

# ---------------------------------------------------------------------------
# Main area
# ---------------------------------------------------------------------------

st.header("Upload Survey Questionnaire")
st.write(
    "Upload a .docx survey questionnaire and the AI pipeline will extract, "
    "segment, classify, and generate Forsta-compatible XML."
)

uploaded_file = st.file_uploader(
    "Choose a .docx file",
    type=["docx"],
    help="Word document containing the survey questionnaire.",
)

if uploaded_file is not None:
    st.info(f"**{uploaded_file.name}** ({uploaded_file.size / 1024:.1f} KB)")

    if st.button("🚀 Generate XML", type="primary", use_container_width=True):
        progress_bar = st.progress(0)
        status_text = st.empty()

        stage_messages = []
        stage_count = {"current": 0}

        def progress_callback(msg: str):
            stage_messages.append(msg)
            if "Stage 1" in msg:
                stage_count["current"] = 10
            elif "Stage 2" in msg:
                stage_count["current"] = 30
            elif "Stage 3" in msg or "Classif" in msg:
                stage_count["current"] = 55
            elif "Stage 4" in msg or "assembl" in msg.lower():
                stage_count["current"] = 80
            elif "validation" in msg.lower():
                stage_count["current"] = 90
            elif "complete" in msg.lower() or "Pipeline" in msg:
                stage_count["current"] = 100

            progress_bar.progress(min(stage_count["current"], 100))
            status_text.text(msg)

        try:
            start_time = time.time()

            xml_output, warnings, debug_info = process_bytes(
                uploaded_file.getvalue(),
                survey_name=survey_name,
                model=OPENAI_MODEL,
                progress_callback=progress_callback,
            )

            elapsed = time.time() - start_time
            progress_bar.progress(100)
            status_text.empty()

            st.session_state["xml_output"] = xml_output
            st.session_state["xml_warnings"] = warnings
            st.session_state["xml_debug_info"] = debug_info
            st.session_state["xml_elapsed"] = elapsed
            st.session_state["xml_filename"] = uploaded_file.name.replace(".docx", ".xml")
            st.session_state["xml_stage_messages"] = stage_messages

        except ValueError as e:
            st.error(f"Configuration error: {e}")
        except Exception as e:
            st.error(f"Pipeline error: {e}")

    # --- Display results from session state (persists across reruns) ---
    if "xml_output" in st.session_state:
        xml_output = st.session_state["xml_output"]
        warnings = st.session_state["xml_warnings"]
        debug_info = st.session_state["xml_debug_info"]
        elapsed = st.session_state["xml_elapsed"]
        xml_filename = st.session_state["xml_filename"]

        q_count = debug_info.get('classified_questions', 0)
        c_count = debug_info.get('conditions', 0)
        seg_count = debug_info.get('segments', 0)
        st.success(
            f"XML generated in {elapsed:.1f}s — "
            f"{debug_info.get('xml_lines', 0)} lines, "
            f"{q_count} questions, "
            f"{c_count} conditions"
        )

        if q_count == 0 and seg_count == 0:
            st.error(
                "No segments were extracted from the AI response. "
                "This usually means the AI response structure was unexpected."
            )
        elif q_count == 0:
            st.warning(
                f"{seg_count} segments were found but 0 questions were classified."
            )

        if warnings:
            with st.expander(f"⚠️ {len(warnings)} Warning(s)", expanded=True):
                for w in warnings:
                    st.warning(w)

        st.download_button(
            label="📥 Download XML",
            data=xml_output,
            file_name=xml_filename,
            mime="application/xml",
            use_container_width=True,
        )

        with st.expander("Preview XML", expanded=True):
            st.markdown(
                """<style>
                .stExpander [data-testid="stCodeBlock"] {
                    max-width: 800px;
                    max-height: 600px;
                    overflow: auto;
                }
                </style>""",
                unsafe_allow_html=True,
            )
            col_copy, _ = st.columns([1, 3])
            with col_copy:
                st.components.v1.html(
                    f"""<button onclick="navigator.clipboard.writeText(document.getElementById('xml-src').textContent).then(()=>this.textContent='Copied!')"
                    style="padding:6px 18px;border:1px solid #ccc;border-radius:6px;background:#f0f2f6;cursor:pointer;font-size:14px;">
                    📋 Copy XML</button>
                    <textarea id="xml-src" style="display:none">{xml_output.replace("<","&lt;").replace(">","&gt;")}</textarea>""",
                    height=42,
                )
            st.code(xml_output, language="xml", line_numbers=True)

else:
    # Show instructions when no file is uploaded
    st.divider()

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("How it works")
        st.write(
            "1. **Extract** — Reads every paragraph, table, and page break from the .docx\n\n"
            "2. **Segment** — AI identifies logical boundaries (questions, conditions, text screens)\n\n"
            "3. **Classify** — AI determines Forsta question types, attributes, and conditions\n\n"
            "4. **Build** — Deterministic XML templates produce valid Forsta markup\n\n"
            "5. **Assemble** — Wraps everything in `<survey>`, validates, and outputs"
        )

    with col2:
        st.subheader("Supported features")
        st.write(
            "- Radio, checkbox, select, text, textarea, number questions\n\n"
            "- Matrix/grid questions (auto-detected)\n\n"
            "- Conditional visibility and branching (`<condition>`)\n\n"
            "- Termination logic (`<term>`)\n\n"
            "- Dropdowns for states, countries, years, numeric ranges\n\n"
            "- Answer attributes: shuffle, exclusive, anchor, open-end\n\n"
            "- Page breaks, text screens, programming notes"
        )
