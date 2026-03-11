import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import time
from docx import Document
from concurrent.futures import ThreadPoolExecutor

# --- Performance: Caching results ---
@st.cache_data(ttl=3600)
def get_cambridge_data_fast(word):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/121.0.0.0"}
    url = f"https://dictionary.cambridge.org/dictionary/english/{word}"
    results = []
    seen_fingerprints = set()
    
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code != 200: return []
        soup = BeautifulSoup(resp.content, 'html.parser')
        senses = soup.select(".def-block, .entry-body__el, .pr.dsense")
        
        for sense in senses:
            lvl_tag = sense.find("span", class_=re.compile(r"dxref\s+[A-C][1-2]"))
            if not lvl_tag:
                lvl_tag = sense.select_one(".ecl-badge, .dxst, .label-cefr")

            level = lvl_tag.get_text().upper().strip() if lvl_tag else "NOT LISTED"
            pos_tag = sense.find_previous(class_="pos dpos")
            pos = pos_tag.get_text().strip() if pos_tag else "word"
            def_tag = sense.select_one(".def.ddef_d.db")
            definition = def_tag.get_text().strip() if def_tag else "No definition."
            
            fingerprint = f"{pos}|{level}|{definition[:60]}"
            if fingerprint not in seen_fingerprints:
                results.append({"pos": pos, "level": level, "definition": definition})
                seen_fingerprints.add(fingerprint)
        return results
    except:
        return []

def find_all_instances(word, sources):
    pattern = rf"([^.!?\n]*?\b{re.escape(word)}\b[^.!?\n]*[.!?]?)"
    all_matches = []
    for source_name, content in sources.items():
        hits = re.findall(pattern, content, flags=re.IGNORECASE)
        for hit in hits:
            all_matches.append({"source": source_name, "text": hit.strip()})
    return all_matches

# --- UI Setup ---
st.set_page_config(page_title="GitHub Deployed Validator", layout="wide")
st.title("⚡ High-Performance Curriculum Validator")

with st.sidebar:
    st.header("1. Target Levels")
    target_levels = st.multiselect("Select Target Levels:", ["A1", "A2", "B1", "B2", "C1", "C2"], default=["A1", "A2"])
    
    st.divider()
    st.header("2. Result Filters")
    view_filter = st.multiselect("Display only:", 
                                 ["Matching Levels", "Non-Matching Levels", "Not Listed Levels"],
                                 default=["Matching Levels", "Non-Matching Levels", "Not Listed Levels"])
    
    st.divider()
    st.header("3. Cloud Document Links")
    st.info("🚨 Ensure Docs are set to 'Anyone with the link can view'")
    
    # Check if we have links stored in session, if not, start empty
    if 'cloud_docs' not in st.session_state:
        st.session_state.cloud_docs = [{"name": "Master List", "url": ""}]
    
    for idx, doc in enumerate(st.session_state.cloud_docs):
        col_a, col_b = st.columns([1, 2])
        doc['name'] = col_a.text_input(f"Nickname", value=doc['name'], key=f"name_{idx}")
        doc['url'] = col_b.text_input(f"URL", value=doc['url'], key=f"url_{idx}")

    if st.button("➕ Add Another Link"):
        st.session_state.cloud_docs.append({"name": "New Source", "url": ""})
        st.rerun()

    st.divider()
    st.header("4. Local File Uploads")
    uploaded_files = st.file_uploader("Upload .docx files", accept_multiple_files=True)

raw_input = st.text_area("Paste word list:", height=100)

if st.button("Validate Now"):
    if raw_input:
        start_time = time.time()
        # Clean input: Handle commas, tabs, newlines
        words = list(dict.fromkeys(re.findall(r'\b\w+\b', raw_input.lower())))
        
        # Parallel Sync for Docs
        all_content = {}
        for doc in st.session_state.cloud_docs:
            if doc['url']:
                d_id_match = re.search(r'/d/([a-zA-Z0-9-_]+)', doc['url'])
                if d_id_match:
                    d_id = d_id_match.group(1)
                    try:
                        resp = requests.get(f"https://docs.google.com/document/d/{d_id}/export?format=txt", timeout=5)
                        if resp.status_code == 200:
                            all_content[doc['name']] = resp.text
                    except: pass

        for f in uploaded_files:
            all_content[f.name] = "\n".join([p.text for p in Document(f).paragraphs])

        # Parallel Scraping
        with st.status(f"Scraping {len(words)} words from Cambridge...", expanded=True) as status:
            with ThreadPoolExecutor(max_workers=10) as executor:
                scrape_results = list(executor.map(get_cambridge_data_fast, words))
            status.update(label="Scraping complete!", state="complete", expanded=False)
        
        word_data_map = dict(zip(words, scrape_results))

        # Output Generation
        for word in words:
            cambridge_data = word_data_map.get(word, [])
            
            # Sort & Filter per definition
            filtered_defs = []
            for item in cambridge_data:
                is_match = item['level'] in target_levels
                is_unlisted = item['level'] == "NOT LISTED"
                item['priority'] = 0 if is_match else (2 if is_unlisted else 1)

                if (is_match and "Matching Levels" in view_filter) or \
                   (not is_match and not is_unlisted and "Non-Matching Levels" in view_filter) or \
                   (is_unlisted and "Not Listed Levels" in view_filter):
                    filtered_defs.append(item)

            filtered_defs = sorted(filtered_defs, key=lambda x: x['priority'])
            instances = find_all_instances(word, all_content)
            
            if filtered_defs or instances:
                with st.container():
                    st.markdown(f"### `{word.upper()}`")
                    c1, c2 = st.columns(2)
                    with c1:
                        if instances:
                            for i in instances: st.warning(f"**{i['source']}**: {i['text']}")
                        else: st.success("No duplicates.")
                    with c2:
                        for l in filtered_defs:
                            safe = l['level'] in target_levels
                            unknown = l['level'] == "NOT LISTED"
                            icon = "✅" if safe else ("❓" if unknown else "❌")
                            with st.expander(f"{icon} {l['level']} ({l['pos']})"):
                                st.write(l['definition'])
                st.divider()
        
        st.toast(f"Total time: {round(time.time() - start_time, 2)}s")