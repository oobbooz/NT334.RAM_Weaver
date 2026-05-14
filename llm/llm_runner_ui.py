import streamlit as st
import os
import sys
import glob
import re
import pandas as pd
from pathlib import Path

# =====================================================================
# 1. SYSTEM CONFIGURATION & INIT
# =====================================================================
st.set_page_config(page_title="RAM-Weaver", layout="wide")

@st.cache_resource
def init_reconstructor():
    """Initialize LLM Pipeline. Hardcoded Temperature = 0.1 for Forensics."""
    try:
        cwd = os.getcwd()
        if cwd not in sys.path:
            sys.path.insert(0, cwd)
        
        llm_dir = str(Path(__file__).resolve().parent)
        if llm_dir not in sys.path:
            sys.path.insert(0, llm_dir)

        from config import LLMConfig          
        from llm_pipeline import LLMReconstructor
        
        # Hardcode Temperature to 0.1 for precise extraction
        config = LLMConfig(temperature=0.1)
        rec = LLMReconstructor(config)
        
        # --- UI-SPECIFIC HOT-SWAP ---
        try:
            from restorer_ui import TextRestorerUI
            import prompts_for_ui as ui_prompts
            
            rec.restorer = TextRestorerUI(rec.llm, rec.cfg)
            if hasattr(rec, 'query_engine'):
                rec.query_engine.system_prompt = ui_prompts.FORENSIC_QUERY_SYSTEM_PROMPT_V2
                rec.query_engine.user_template = ui_prompts.FORENSIC_QUERY_USER_TEMPLATE_V2
                
        except ImportError as e:
            st.error(f"Error: Missing UI support files: {e}")
            return None
            
        return rec
    except Exception as e:
        st.error(f"Initialization Error: {e}")
        return None

def parse_evidence_to_tables(raw_text: str):
    """
    Robust Regex-powered parser. 
    Splits the raw LLM output into Reliable and Reference DataFrames.
    Parses Reliable data into 3 explicit columns (Time, Sender, Message).
    """
    reliable_data = []
    reference_data = []

    # 1. ROBUST SPLIT: Use Regex to catch any variation of the Reference header
    parts = re.split(r'(?i)===\s*(?:🟡)?\s*(?:BẰNG CHỨNG THAM KHẢO|REFERENCE(?: FRAGMENTS)?).*===', raw_text)
    
    reliable_part = parts[0]
    reference_part = parts[1] if len(parts) > 1 else ""

    # Clean the Reliable header using Regex
    reliable_part = re.sub(r'(?i)===\s*(?:🟢)?\s*(?:BẰNG CHỨNG ĐÁNG TIN CẬY|RELIABLE EVIDENCE).*===', '', reliable_part).strip()

    # --- RELIABLE PARSING ---
    raw_msgs = re.split(r'(?=\[\d{2}:\d{2}:\d{2}\])', reliable_part)
    for msg in raw_msgs:
        msg = msg.strip()
        if not msg: 
            continue
        
        time_match = re.search(r'^\[(\d{2}:\d{2}:\d{2})\]', msg)
        if time_match:
            time_str = time_match.group(1)
            remainder = msg[time_match.end():].strip()
            
            if ':' in remainder:
                sender, content = remainder.split(':', 1)
                sender = sender.strip()
                content = content.strip()
                
                reliable_data.append({
                    "Time": time_str, 
                    "Sender": sender, 
                    "Message Content": content
                })
            else:
                reliable_data.append({
                    "Time": time_str, 
                    "Sender": "Unknown",
                    "Message Content": remainder
                })

    # --- REFERENCE PARSING ---
    ref_msgs = re.split(r'- \[Fragment\]:', reference_part)
    for msg in ref_msgs:
        msg = msg.strip()
        if msg and not msg.startswith('(List any'):
            reference_data.append({"Fragment Content (Unverified)": msg})

    # Build DataFrames
    df_rel = pd.DataFrame(reliable_data)
    if not df_rel.empty:
        df_rel = df_rel.sort_values(by="Time").reset_index(drop=True)
    else:
        df_rel = pd.DataFrame(columns=["Time", "Sender", "Message Content"])

    df_ref = pd.DataFrame(reference_data) if reference_data else pd.DataFrame(columns=["Fragment Content (Unverified)"])
    
    return df_rel, df_ref

# =====================================================================
# 2. MAIN INTERFACE & SIDEBAR
# =====================================================================
st.title("RAM-Weaver")
st.markdown("High-Fidelity Memory Forensics & Interactive Query System.")

with st.sidebar:
    st.header("Evidence Source")
    
    # Auto-scan output directories
    txt_files = glob.glob("./output/*.txt") + glob.glob("../output/*.txt")
    txt_files = list(set(txt_files))
    
    if txt_files:
        chunks_file = st.selectbox("Select AMC Dump File:", sorted(txt_files))
    else:
        chunks_file = st.text_input("File Path:", value="./output/amc_output.txt")
        
    if os.path.isfile(chunks_file):
        st.success("System Status: File Ready")
    else:
        st.error("System Status: File not found")

# =====================================================================
# 3. EXECUTION MODES
# =====================================================================
if os.path.isfile(chunks_file):
    rec = init_reconstructor()

    if rec:
        tab_restore, tab_query, tab_interactive = st.tabs([
            "Restore Text", 
            "Quick Query", 
            "Interactive Copilot"
        ])

        # -----------------------------------------------------------
        # TASK A: RESTORE 
        # -----------------------------------------------------------
        with tab_restore:
            st.subheader("High-Fidelity Evidence Restoration")
            
            if st.button("Run Extraction Process"):
                with st.spinner("Extracting and reconstructing memory fragments..."):
                    results = rec.run_restoration(chunks_file)
                    st.session_state.restore_results = "\n".join(results)
                    st.success("Extraction Complete.")

            if 'restore_results' in st.session_state:
                raw_text = st.session_state.restore_results
                df_rel, df_ref = parse_evidence_to_tables(raw_text)
                
                # View Mode Switcher
                view_mode = st.radio(
                    "Select evidence category to display:", 
                    ["Reliable Evidence (Structured)", "Reference Fragments (Unstructured)"],
                    horizontal=True
                )
                
                # Render DataFrames
                if view_mode == "Reliable Evidence (Structured)":
                    st.dataframe(
                        df_rel, 
                        width="stretch",
                        height=400,
                        column_config={
                            "Time": st.column_config.TextColumn(width="small"),
                            "Sender": st.column_config.TextColumn(width="medium"),
                            "Message Content": st.column_config.TextColumn(width="large")
                        }
                    )
                else:
                    st.dataframe(
                        df_ref, 
                        width="stretch",
                        height=400,
                        column_config={
                            "Fragment Content (Unverified)": st.column_config.TextColumn(width="large")
                        }
                    )
                
                # Downloads and Raw Logs
                col1, col2 = st.columns([1, 4])
                with col1:
                    st.download_button("Download Report (.txt)", raw_text, "restored_evidence.txt")
                with col2:
                    with st.expander("View Raw Output Log"):
                        st.text_area(
                            label="Raw Output Log", 
                            value=raw_text, 
                            height=200, 
                            label_visibility="collapsed"
                        )

        # -----------------------------------------------------------
        # TASK B: QUERY & INTERACTIVE
        # -----------------------------------------------------------
        with tab_query:
            st.subheader("Single Shot Forensic Query")
            question = st.text_input("Enter your query:", placeholder="e.g., What food items were mentioned?")
            if st.button("Execute Query"):
                if question:
                    with st.spinner("Analyzing memory context..."):
                        answer = rec.run_forensic_query(chunks_file, question)
                        st.info(answer)

        with tab_interactive:
            col1, col2 = st.columns([4, 1])
            with col1:
                st.subheader("Interactive Investigation Copilot")
            with col2:
                if st.button("Clear History"):
                    st.session_state.chat_history = []
                    st.rerun()

            if "chat_history" not in st.session_state:
                st.session_state.chat_history = []

            for msg in st.session_state.chat_history:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

            if prompt := st.chat_input("Ask about anything..."):
                st.session_state.chat_history.append({"role": "user", "content": prompt})
                with st.chat_message("user"):
                    st.markdown(prompt)

                with st.chat_message("assistant"):
                    with st.spinner("Reasoning over memory artifacts..."):
                        response = rec.run_forensic_query(chunks_file, prompt)
                        st.markdown(response)
                        st.session_state.chat_history.append({"role": "assistant", "content": response})
    else:
        st.error("Error: Failed to initialize system.")
else:
    st.info("Status: Waiting for AMC Dump file to begin.")
