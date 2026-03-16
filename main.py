import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import time
from docx import Document
from concurrent.futures import ThreadPoolExecutor

# --- Scraper & Core Logic ---

def lemmatize(word):
    """Strip common English inflections so we always hit the base-form Cambridge page."""
    w = word.lower()
    # Order matters: longer suffixes first
    rules = [
        (r"n't$",    "not"),    # don't -> do not (edge case, skip)
        (r"ies$",    "y"),      # carries -> carry
        (r"ied$",    "y"),      # carried -> carry
        (r"ves$",    "f"),      # leaves -> leaf
        (r"sses$",   "ss"),     # classes -> class
        (r"xes$",    "x"),      # boxes -> box
        (r"ches$",   "ch"),     # watches -> watch
        (r"shes$",   "sh"),     # wishes -> wish
        (r"oes$",    "o"),      # goes -> go
        (r"ing$",    ""),       # looking -> look (will re-check)
        (r"ing$",    "e"),      # taking -> take
        (r"ed$",     ""),       # looked -> look
        (r"ed$",     "e"),      # liked -> like
        (r"er$",     ""),       # faster -> fast (adj)
        (r"est$",    ""),       # fastest -> fast
        (r"ly$",     ""),       # quickly -> quick
        (r"s$",      ""),       # looks -> look, cats -> cat
    ]
    # Try each rule; return first one that shortens the word meaningfully
    for pattern, replacement in rules:
        candidate = re.sub(pattern, replacement, w)
        if candidate and candidate != w and len(candidate) >= 2:
            return candidate
    return w

@st.cache_data(ttl=3600)
def _scrape(query):
    """Fetch and parse Cambridge page for query. Returns list of def dicts."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0"}
    results = []
    seen_fingerprints = set()
    try:
        resp = requests.get(
            f"https://dictionary.cambridge.org/dictionary/english/{query}",
            headers=headers, timeout=7)
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.content, "html.parser")
        for sense in soup.select(".def-block, .entry-body__el, .pr.dsense"):
            lvl_tag = sense.find("span", class_=re.compile(r"dxref\s+[A-C][1-2]"))
            if not lvl_tag:
                lvl_tag = sense.select_one(".ecl-badge, .dxst, .label-cefr")
            level   = lvl_tag.get_text().upper().strip() if lvl_tag else "NOT LISTED"
            pos_tag = sense.find_previous(class_="pos dpos")
            pos     = pos_tag.get_text().strip() if pos_tag else "word"
            def_tag = sense.select_one(".def.ddef_d.db")
            definition = def_tag.get_text().strip()[:-1] if def_tag else "No definition."
            if pos == "word":
                continue
            fp = f"{pos}|{level}|{definition[:60]}"
            if fp not in seen_fingerprints:
                results.append({"pos": pos, "level": level, "definition": definition})
                seen_fingerprints.add(fp)
    except:
        pass
    return results

def get_cambridge_data_fast(word):
    """Try the word as-is first; if nothing found, try the lemmatized form.
    Returns (results, word_used) — word_used differs from word when lemma fallback fired."""
    results = _scrape(word)
    if results:
        return results, word
    base = lemmatize(word)
    if base != word:
        results = _scrape(base)
        if results:
            return results, base
    return [], None

def find_all_instances(word, sources):
    pattern = rf"([^.!?\n]*?\b{re.escape(word)}\b[^.!?\n]*[.!?]?)"
    all_matches = []
    for source_name, content in sources.items():
        hits = re.findall(pattern, content, flags=re.IGNORECASE)
        for hit in hits:
            all_matches.append({"source": source_name, "text": hit.strip()})
    return all_matches

# --- UI Setup ---

st.set_page_config(page_title="Curriculum Validator", layout="wide")
st.title("⚡ LP's High-Performance Vocab Validator")

with st.sidebar:
    st.header("1. Target Levels")
    target_levels = st.multiselect("Select Target Levels:", ["A1", "A2", "B1", "B2", "C1", "C2"], default=["A1", "A2"])
    
    st.divider()
    st.header("2. Result Filters")
    view_filter = st.multiselect("Display definitions:", 
                                 ["Matching Levels", "Non-Matching Levels", "Not Listed Levels"],
                                 default=["Matching Levels"])
    
    st.divider()
    st.header("3. Cloud Document Links")
    st.info("🚨 Ensure Docs are set to 'Anyone with the link can view'")
    if 'cloud_docs' not in st.session_state:
        st.session_state.cloud_docs = [
            {"name": "Week 1-2", "url": "https://docs.google.com/document/d/1SrzKyDz3CWELZtsfWqRSsS5SWheTB9ff/edit"},
            {"name": "Week 3-4", "url": "https://docs.google.com/document/d/1olOkpmw6rh4HVpjonrOBNlJ_3mFwRpR0/edit"}
                                       ]
    
    for idx, doc in enumerate(st.session_state.cloud_docs):
        col_a, col_b, col_c = st.columns([1, 2, 0.3], vertical_alignment="bottom")
        doc['name'] = col_a.text_input("Nickname", value=doc['name'], key=f"n_{idx}")
        doc['url']  = col_b.text_input("URL",      value=doc['url'],  key=f"u_{idx}")
        if col_c.button("🗑️", key=f"del_{idx}", help="Remove this link"):
            st.session_state.cloud_docs.pop(idx)
            st.rerun()

    if st.button("➕ Add Another Link"):
        st.session_state.cloud_docs.append({"name": "New Source", "url": ""})
        st.rerun()

    st.divider()
    st.header("4. Local File Uploads")
    uploaded_files = st.file_uploader("Upload .docx files", accept_multiple_files=True)

# --- Main Logic ---

raw_input = st.text_area("Paste word list:", height=100)

# ── Always-visible clipboard component ──────────────────────────────────────

def build_html_table(word_results):
    rows_html = ""
    for entry in word_results:
        word = entry["word"]
        defs = entry["defs"]
        if defs:
            d = defs[0]
            cell_word = f"<b>{word.capitalize()}</b><br>({d['pos']})"
            cell_def  = d['definition']
        else:
            cell_word = f"<b>{word.capitalize()}</b>"
            cell_def  = "<i>No matching definition found.</i>"
        rows_html += (
            '<tr>'
            '<td style="background:#60cbf3;border:1px solid #000000;padding:8px 12px;'
            'font-family:Arial,sans-serif;font-size:11pt;vertical-align:top;width:30%">'
            + cell_word +
            '</td>'
            '<td style="background:#c8eaf8;border:1px solid #000000;padding:8px 12px;'
            'font-family:Arial,sans-serif;font-size:11pt;vertical-align:top;font-style:italic">'
            + cell_def +
            '</td></tr>'
        )
    if not rows_html:
        return ""
    return '<table style="border-collapse:collapse;width:100%">' + rows_html + '</table>'

word_list_payload = raw_input
table_html        = build_html_table(st.session_state.get("word_results", []))

CLIP_COMPONENT = """
<style>
  .cp-btn {
    background:#262730; color:#fff; border:1px solid #3a3b45;
    border-radius:6px; padding:6px 16px; font-size:14px; font-weight:600;
    cursor:pointer; margin-right:8px; font-family:sans-serif;
  }
  .cp-btn:hover { background:#3a3b45; }
  #cp-msg { font-family:sans-serif; font-size:13px; color:#21c55d;
             margin-top:6px; min-height:18px; }
</style>
<textarea id="clip_wordlist" style="display:none">__WORDLIST__</textarea>
<div id="clip_table_html" style="display:none">__TABLEHTML__</div>
<button class="cp-btn" onclick="copyWordList()">&#x1F4CB; Copy Word List</button>
<button class="cp-btn" onclick="copyTable()">&#x1F4CA; Copy Table</button>
<div id="cp-msg"></div>
<script>
function copyWordList() {
  var text = document.getElementById('clip_wordlist').value;
  if (!text.trim()) { showMsg('Nothing to copy yet.', '#e55'); return; }
  navigator.clipboard.writeText(text).then(function() { showMsg('Word list copied!', '#21c55d'); });
}
function copyTable() {
  var html = document.getElementById('clip_table_html').innerHTML;
  if (!html.trim()) { showMsg('Run a validation first.', '#e55'); return; }
  try {
    var item = new ClipboardItem({
      'text/html':  new Blob([html], {type: 'text/html'}),
      'text/plain': new Blob([html.replace(/<[^>]+>/g,' ')], {type: 'text/plain'})
    });
    navigator.clipboard.write([item]).then(function() {
      showMsg('Styled table copied! Paste into Word or Google Docs.', '#21c55d');
    });
  } catch(e) {
    navigator.clipboard.writeText(html.replace(/<[/]tr>/gi,'\\n').replace(/<[^>]+>/g,'\\t')).then(function() {
      showMsg('Copied as text (upgrade browser for styled paste).', '#f0a500');
    });
  }
}
function showMsg(msg, color) {
  var el = document.getElementById('cp-msg');
  el.style.color = color; el.innerText = '\u2705 ' + msg;
  setTimeout(function(){ el.innerText = ''; }, 3500);
}
</script>
"""

component_html = CLIP_COMPONENT.replace("__WORDLIST__", word_list_payload).replace("__TABLEHTML__", table_html)
st.components.v1.html(component_html, height=70)

# ── Validate button ──────────────────────────────────────────────────────────
if st.button("Validate Now"):
    if raw_input:
        start_time = time.time()
        words = list(dict.fromkeys(re.findall(r'\b\w+\b', raw_input.lower())))

        # Fast Content Sync
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
        with st.status(f"Scanning {len(words)} words...", expanded=True) as status:
            with ThreadPoolExecutor(max_workers=5) as executor:
                scrape_results = list(executor.map(get_cambridge_data_fast, words))

            # Collect log entries BEFORE calling status.update (closing it hides content added after)
            lemma_log  = []
            no_results = []
            for orig, (res, used) in zip(words, scrape_results):
                if used and used != orig:
                    lemma_log.append((orig, used))
                elif not used:
                    no_results.append(orig)

            # Write all log lines into the still-open dropdown
            if lemma_log:
                st.markdown("**⚠️ Lemmatized words:**")
                for orig, used in lemma_log:
                    st.markdown(f"- **{orig}** — no results, searched **{used}** instead")
            if no_results:
                st.markdown("**❌ No results found:**")
                for orig in no_results:
                    st.markdown(f"- **{orig}**")

            # Now close/complete the status — content above is already rendered inside it
            label = f"✅ Done — {len(words)} words scanned"
            if lemma_log:
                label += f" · {len(lemma_log)} lemmatized"
            if no_results:
                label += f" · {len(no_results)} not found"
            status.update(label=label, state="complete", expanded=True)

        word_data_map = {word: res for word, (res, _) in zip(words, scrape_results)}
        word_used_map = {orig: (used or orig) for orig, (res, used) in zip(words, scrape_results)}

        # Collect all results into session_state so Copy Table survives the rerun
        word_results = []
        for word in words:
            cambridge_data = word_data_map.get(word, [])
            filtered_defs = []
            for item in cambridge_data:
                is_match   = item['level'] in target_levels
                is_unlisted = item['level'] == "NOT LISTED"
                item['priority'] = 0 if is_match else (2 if is_unlisted else 1)
                if (is_match     and "Matching Levels"     in view_filter) or \
                   (not is_match and not is_unlisted and "Non-Matching Levels" in view_filter) or \
                   (is_unlisted  and "Not Listed Levels"   in view_filter):
                    filtered_defs.append(item)
            filtered_defs = sorted(filtered_defs, key=lambda x: x['priority'])
            instances     = find_all_instances(word, all_content)
            word_results.append({"word": word, "defs": filtered_defs,
                                  "instances": instances, "has_data": bool(cambridge_data),
                                  "word_used": word_used_map.get(word, word)})

        st.session_state["word_results"] = word_results
        st.toast(f"Finished in {round(time.time() - start_time, 2)}s")

# ── Render results from session_state (persists across copy-button clicks) ───
for entry in st.session_state.get("word_results", []):
    word          = entry["word"]
    filtered_defs = entry["defs"]
    instances     = entry["instances"]

    if not filtered_defs and not instances and not entry["has_data"]:
        continue

    st.markdown(f"### `{word.upper()}`")
    c1, c2 = st.columns(2)
    with c1:
        if instances:
            for i in instances: st.warning(f"**{i['source']}**: {i['text']}")
        else: st.success("No duplicates found.")
    with c2:
        if not filtered_defs:
            cambridge_url = f"https://dictionary.cambridge.org/dictionary/english/{entry.get('word_used', word)}"
            st.error(f"⚠️ No definitions match your selected levels — consider choosing a different word. [View on Cambridge]({cambridge_url})")
        for l in filtered_defs:
            safe    = l['level'] in target_levels
            unknown = l['level'] == "NOT LISTED"
            icon    = "✅" if safe else ("❓" if unknown else "❌")
            with st.expander(f"{icon} {l['level']} ({l['pos']})"):
                st.write(f"**Definition:** {l['definition']}")
                copy_string = f"{word.capitalize()} ({l['pos']})\t{l['definition']}"
                st.code(copy_string, language=None)
    st.divider()