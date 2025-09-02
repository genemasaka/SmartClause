# bot.py

import os
import re
import json
import difflib
import logging
from typing import Dict, List, Optional

import streamlit as st
from openai import OpenAI

# --------------------------------------------------------------------------------------
# App constants & knowledge
# --------------------------------------------------------------------------------------

logger = logging.getLogger(__name__)

APP_NAME = "SmartClause"
APP_KNOWLEDGE = f"""
You are an in-app assistant for {APP_NAME}, a Kenyan-focused AI legal document generator.
Capabilities:
- Drafts detailed contracts and affidavits tailored to Kenyan law.
- Lets users review, pay via M-PESA, and download documents as DOCX/PDF.
- Can read the user's currently generated document from session memory and answer questions about its content.

Important Policy:
- You are not a lawyer.
- Your responses and all generated documents do NOT constitute legal advice.
- Encourage users to seek review by a qualified Kenyan lawyer.

Tone & Style:
- Be concise, precise, and practical.
- If a current document exists, prefer document-grounded answers that cite or quote the relevant clause/line (briefly),
  then summarize implications in plain language.
"""

LEGAL_DISCLAIMER = (
    "Important: This guidance (and any generated document) does not constitute legal advice. "
    "Please have a qualified Kenyan lawyer review before use."
)

# --------------------------------------------------------------------------------------
# Model setup
# --------------------------------------------------------------------------------------

def inject_chatbot_styles():
    st.markdown("""
    <style>
      /* ====== Global responsive container ====== */
      .sc-chat-wrap { display:flex; flex-direction:column; gap:10px; }

      /* Base card */
      .sc-card {
        background: var(--sc-surface, #ffffff);
        border: 1px solid rgba(0,0,0,0.06);
        border-radius: 16px;
        padding: 12px 14px;
        box-shadow: 0 6px 18px rgba(0,0,0,0.06);
      }

      /* Message bubbles */
      .sc-msg { border-radius:14px; padding:10px 12px; line-height:1.5; border:1px solid rgba(0,0,0,0.05); margin-bottom: 16px;}
      .sc-msg.user   { background:#f8fafc; }
      .sc-msg.assist { background:#fff; }

      .sc-chip { display:inline-block; padding:4px 10px; border-radius:999px; background:rgba(0,0,0,0.06); font-size:12px; }
      .sc-row { display:flex; gap:10px; align-items:center; }

      /* Header (used inside the expander body only) */
      .sc-title { display:flex; align-items:center; gap:10px; font-weight:700; font-size:14px; margin-bottom:6px; }
      .sc-title .sc-badge {
        width:28px; height:28px; flex:0 0 28px; border-radius:8px;
        display:grid; place-items:center; background:#ea4a3c; color:#fff;
        box-shadow: inset 0 -3px 8px rgba(0,0,0,0.18);
      }
      .sc-sub { color:#6b7280; font-size:12px; margin-top:-2px; }

      /* Composer */
      .sc-composer-card { padding: 14px; }
      .sc-actions  { display:flex; gap:10px; }
      .sc-btn {
        width:100%; border:none; border-radius:12px; padding:10px 12px; font-weight:600;
        background:#111827; color:#fff; cursor:pointer;
      }
      .sc-btn.secondary { background:#eef2ff; color:#111827; }

      /* Streamlit textarea aesthetics */
      div[data-testid="stTextArea"] textarea {
        border-radius: 12px !important;
        border: 1px solid rgba(0,0,0,0.08) !important;
        box-shadow: inset 0 1px 2px rgba(0,0,0,0.06) !important;
        min-height: 84px;
      }

      /* Remove accidental “empty card” gaps above composer */
      .sc-empty { display:none !important; height:0 !important; margin:0 !important; padding:0 !important; border:none !important; box-shadow:none !important; }

      /* Mobile */
      @media (max-width: 640px) {
        .sc-card { border-radius:14px; padding:10px 12px; }
        .sc-title { font-size:13px; }
        .sc-sub   { font-size:11px; }
        div[data-testid="stTextArea"] textarea { min-height: 96px; font-size: 15px; }
        .sc-btn { padding:12px 14px; font-size:15px; }
      }

      /* Dark-friendly */
      @media (prefers-color-scheme: dark) {
        :root { --sc-surface: #111418; }
        .sc-card { border-color: rgba(255,255,255,0.08); }
        .sc-msg.user { background:#0f172a; border-color:rgba(255,255,255,0.05); }
        .sc-msg.assist { background:#0b0f14; border-color:rgba(255,255,255,0.05); }
        .sc-sub { color:#9aa3af; }
        .sc-chip { background: rgba(255,255,255,0.06); color:#e5e7eb; }
        .sc-btn.secondary { background:#1f2937; color:#e5e7eb; }
      }
    </style>
    """, unsafe_allow_html=True)


def setup_chatbot_model() -> OpenAI:
    """
    Set up and configure the AI model for the chatbot (OpenAI GPT-4o).
    """
    try:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable not found")

        client = OpenAI(api_key=api_key)
        logger.info('{"event": "chatbot_model_initialized", "status": "success"}')
        return client
    except Exception as e:
        logger.error('{"event": "chatbot_model_init_failed", "error": "%s"}', str(e))
        raise e

# --------------------------------------------------------------------------------------
# Lightweight document parsing helpers (surface key facts)
# --------------------------------------------------------------------------------------

PARTY_HINTS = [
    r"between\s+(?P<p1>.+?)\s+and\s+(?P<p2>.+?)\b",
    r"this\s+agreement\s+is\s+made\s+between\s+(?P<p1>.+?)\s+and\s+(?P<p2>.+?)\b",
    r"by\s+and\s+between\s+(?P<p1>.+?)\s+and\s+(?P<p2>.+?)\b",
]
DEPOSER_HINTS = [
    r"\bI,\s*(?P<name>[A-Z][A-Za-z\.\-\s']+?),\s*(?:ID|I\.D\.|Identification)\s*(?:No\.|Number)?\s*(?P<id>\d{5,})",
    r"\bI,\s*(?P<name>[A-Z][A-Za-z\.\-\s']+?)\s*of\s*ID\s*(?P<id>\d{5,})",
]
MONEY_HINTS = [
    r"(KES|KSh|KSHS?\.?)\s?([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)",
    r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)\s*(Kenyan Shillings|KES|KSh)",
]
DATE_HINTS = [
    r"\b(\d{1,2}\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b",
    r"\b(on|dated)\s+(\d{4}-\d{2}-\d{2})\b",
]

def _first_match(patterns: List[str], text: str, flags=re.IGNORECASE | re.MULTILINE) -> Optional[re.Match]:
    for pat in patterns:
        m = re.search(pat, text, flags)
        if m:
            return m
    return None

def extract_key_facts(doc_text: str) -> Dict[str, str]:
    """
    Very lightweight extraction to enrich answers.
    Attempts to pull parties, a deponent (affidavit), money amounts, and dates.
    """
    facts: Dict[str, str] = {}

    # Parties (contracts)
    m = _first_match(PARTY_HINTS, doc_text)
    if m:
        p1 = m.groupdict().get("p1", "").strip(" ,.;:")
        p2 = m.groupdict().get("p2", "").strip(" ,.;:")
        if p1 and p2:
            facts["party_1"] = p1
            facts["party_2"] = p2

    # Deponent (affidavits)
    m = _first_match(DEPOSER_HINTS, doc_text)
    if m:
        name = m.groupdict().get("name", "").strip(" ,.;:")
        idno = m.groupdict().get("id", "").strip(" ,.;:")
        if name:
            facts["deponent_name"] = name
        if idno:
            facts["deponent_id"] = idno

    # Money
    m = _first_match(MONEY_HINTS, doc_text)
    if m:
        facts["amount_example"] = " ".join(x for x in m.groups() if x)

    # Date
    m = _first_match(DATE_HINTS, doc_text)
    if m:
        candidate = next((g for g in m.groups() if g and re.search(r"[A-Za-z]|\d{4}-\d{2}-\d{2}", g)), None)
        if candidate:
            facts["date_example"] = candidate

    return facts

# --------------------------------------------------------------------------------------
# Edit helpers
# --------------------------------------------------------------------------------------

def _extract_json_block(text: str) -> Optional[dict]:
    """
    Extract and parse the first JSON block from model output.
    Accepts ```json ... ``` or raw JSON. Returns dict or None.
    """
    if not text:
        return None
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    raw = m.group(1) if m else text.strip()
    try:
        return json.loads(raw)
    except Exception:
        return None

EDIT_TRIGGERS = [
    "edit", "revise", "replace", "change", "amend", "update",
    "add clause", "insert clause", "remove clause", "delete clause",
    "tighten", "shorten", "expand", "make stronger", "make clearer", "fix", "modify", "rewrite"
]

def _is_edit_intent(user_text: str) -> bool:
    t = user_text.lower()
    return any(kw in t for kw in EDIT_TRIGGERS)

# --------------------------------------------------------------------------------------
# Sidebar chatbot
# --------------------------------------------------------------------------------------

class SidebarChatbot:
    """
    Sidebar-only chatbot that improves prompts, can review generated documents, and can edit them.
    - Uses OpenAI GPT-4o
    - Aware of SmartClause app context & disclaimers
    - Reads st.session_state.current_document when present
    """

    def __init__(self, document_generator=None):
        self.generator = document_generator
        try:
            self.client = setup_chatbot_model()
            self.model_name = "gpt-4o"
            logger.info('{"event": "chatbot_ai_model_ready", "status": "success"}')
        except Exception as e:
            logger.error('{"event": "chatbot_ai_model_failed", "error": "%s"}', str(e))
            self.client = None
            self.model_name = None

        self.document_patterns = {
            "contract": {
                "keywords": ["contract", "agreement", "deal", "service", "hire", "employment", "sale"],
            },
            "affidavit": {
                "keywords": ["affidavit", "sworn", "declare", "oath", "statement", "witness"],
            },
        }

    def initialize_sidebar_chatbot(self):
        """Initialize minimal chatbot state for sidebar use"""
        if "sidebar_chat_messages" not in st.session_state:
            st.session_state.sidebar_chat_messages = []
        if "chatbot_expanded" not in st.session_state:
            st.session_state.chatbot_expanded = False
        # simple history for undo
        if "document_history" not in st.session_state:
            st.session_state.document_history = []

    # ---- Intent analysis (kept lightweight; core intelligence is in GPT) ----
    def analyze_prompt_intent(self, user_input: str) -> Dict:
        user_input_lower = user_input.lower()
        detected_type = "general"
        for doc_type, patterns in self.document_patterns.items():
            if any(k in user_input_lower for k in patterns["keywords"]):
                detected_type = doc_type
                break
        return {"document_type": detected_type}

    # ---- Core LLM call wrapper ----
    def _chat(self, messages: List[Dict], max_tokens=700, temperature=0.6) -> str:
        if not (self.client and self.model_name):
            return "AI model not available. Please try again later."
        resp = self.client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return resp.choices[0].message.content.strip()

    # ---- General helper answer ----
    def generate_ai_response(self, user_message: str) -> str:
        """General helper guidance when not explicitly reviewing or editing the document."""
        analysis = self.analyze_prompt_intent(user_message)

        current_doc = st.session_state.get("current_document")
        has_doc = bool(current_doc)
        doc_summary = ""
        facts = {}

        if has_doc:
            facts = extract_key_facts(current_doc)
            snippet = current_doc[:2000]  # compact snippet to reduce prompt size
            doc_summary = (
                f"\n[CurrentDocumentPresent: YES]\n"
                f"[DocumentSnippetStart]\n{snippet}\n[DocumentSnippetEnd]\n"
                f"[ExtractedFacts] {facts}\n"
            )
        else:
            doc_summary = "[CurrentDocumentPresent: NO]\n"

        system_msg = {"role": "system", "content": APP_KNOWLEDGE.strip()}
        user_msg = {
            "role": "user",
            "content": (
                f"User message: {user_message}\n"
                f"{doc_summary}\n"
                "Task: Provide a brief, encouraging response (2–3 sentences). "
                "If a current document is present, ground your tips in that content. "
                "Offer 1–2 concrete, Kenyan-law-aware suggestions to improve their request or next steps.\n"
                f"End with: {LEGAL_DISCLAIMER}"
            ),
        }

        return self._chat([system_msg, user_msg], max_tokens=650, temperature=0.6)

    # ---- Document review ----
    def review_current_document(self, user_question: str) -> str:
        """
        Review the current document in relation to the user's question.
        Answers should reference what's actually in the doc.
        """
        current_doc = st.session_state.get("current_document")
        if not current_doc:
            return "No document is currently available for review."

        facts = extract_key_facts(current_doc)
        snippet = current_doc[:6000]  # provide a sizeable context

        system_msg = {"role": "system", "content": APP_KNOWLEDGE.strip()}
        user_msg = {
            "role": "user",
            "content": (
                "You are reviewing a user-generated legal document. "
                "Quote short, relevant lines where helpful and then explain.\n\n"
                f"[DocumentStart]\n{snippet}\n[DocumentEnd]\n"
                f"[ExtractedFacts] {facts}\n\n"
                f"User question: {user_question}\n"
                "Answer clearly, citing the relevant section(s) briefly. "
                "Point out any obvious gaps or risks (Kenyan law context) and suggest precise edits or clauses."
                f"\n\nEnd with: {LEGAL_DISCLAIMER}"
            ),
        }

        return self._chat([system_msg, user_msg], max_tokens=900, temperature=0.4)

    # ---- Document edit ----
    def edit_current_document(self, user_instruction: str) -> str:
        """
        Edits the current document per user's instruction.
        - Produces a revised document + change summary via GPT-4o
        - Saves previous version to st.session_state.document_history
        - Updates st.session_state.current_document
        - Returns a short confirmation message (with diff preview)
        """
        current_doc = st.session_state.get("current_document")
        if not current_doc:
            return "There’s no document to edit yet. Please generate one first."

        sys_msg = {
            "role": "system",
            "content": (
                APP_KNOWLEDGE.strip()
                + "\n\nYou will revise the provided document EXACTLY per the user's instruction and return STRICT JSON:\n"
                  '{\n'
                  '  "revised_document": "<full updated document text>",\n'
                  '  "summary": "<2-6 bullet points describing the changes>",\n'
                  '  "changed_sections": ["<short descriptions of key sections touched>"]\n'
                  '}\n'
                  "No commentary outside JSON."
            ),
        }
        user_msg = {
            "role": "user",
            "content": (
                "Current Document:\n"
                f"<<<DOC_START>>>\n{current_doc}\n<<<DOC_END>>>\n\n"
                f"Edit Instruction: {user_instruction}\n\n"
                "Return ONLY JSON. If an instruction is unsafe or would invalidate Kenyan-law basics, "
                "adjust minimally and note it in the summary."
            ),
        }

        try:
            raw = self._chat([sys_msg, user_msg], max_tokens=3500, temperature=0.3)
            data = _extract_json_block(raw)
            if not data or "revised_document" not in data:
                return "I couldn’t safely apply that change. Please rephrase the edit request."

            revised = data["revised_document"].strip()
            if not revised:
                return "The edit produced an empty result. Please try a clearer instruction."

            # save history for undo
            st.session_state.document_history.append(current_doc)

            # update current document
            st.session_state.current_document = revised
            st.session_state.show_download = True
            st.session_state.document_generated_successfully = True
            st.session_state.show_payment = True  # keep normal flow

            # short diff preview (first 20 changed lines)
            diff_lines = list(
                difflib.unified_diff(
                    current_doc.splitlines(),
                    revised.splitlines(),
                    fromfile="before.txt",
                    tofile="after.txt",
                    lineterm="",
                )
            )
            preview = "\n".join(diff_lines[:20]) if diff_lines else "No visible line-level changes (formatting-only)."

            summary = data.get("summary", "Edits applied.")
            return (
                "✅ Edits applied.\n\n"
                f"**Summary:**\n{summary}\n\n"
                "**Diff preview (first 20 lines):**\n"
                f"```\n{preview}\n```\n"
                f"{LEGAL_DISCLAIMER}"
            )
        except Exception as e:
            logger.error('{"event":"edit_failed","error":"%s"}', str(e), exc_info=True)
            return "Sorry, I wasn’t able to apply that edit. Please try again with a clearer instruction."

    # ---- Undo ----
    def undo_last_edit(self) -> str:
        if not st.session_state.get("document_history"):
            return "No previous version found."
        prev = st.session_state.document_history.pop()
        st.session_state.current_document = prev
        st.session_state.show_download = True
        return "↩️ Reverted to the previous version."

    # ---- UI ----
    def show_sidebar_chatbot(self):
        """Display a collapsible, mobile-responsive sidebar chatbot without stray empty cards."""
        with st.sidebar:
            inject_chatbot_styles()

            # Collapsible assistant
            expanded_default = st.session_state.get("assistant_expanded", False)
            with st.expander("SmartClause Assistant", expanded=expanded_default):
                # Save expanded state (best-effort)
                st.session_state.assistant_expanded = True

                # Header inside expander
                st.markdown(
                    """
                    <div class="sc-title">
                    <div class="sc-badge">⚖️</div>
                    <div>
                        Ask • Review • Edit
                    </div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

                # Conversation
                recent_messages = (
                    st.session_state.sidebar_chat_messages[-8:]
                    if st.session_state.sidebar_chat_messages else []
                )

                if recent_messages:
                    st.markdown('<div class="sc-chat-wrap">', unsafe_allow_html=True)
                    for msg in recent_messages:
                        role_cls = "user" if msg["role"] == "user" else "assist"
                        icon = "🧑" if role_cls == "user" else "⚖️"
                        st.markdown(
                            f"""
                            <div class="sc-card sc-msg {role_cls}">
                            <div class="sc-row">
                                <span class="sc-chip">{icon}</span>
                                <div style="font-size:13px">{msg['content']}</div>
                            </div>
                            </div>
                            """,
                            unsafe_allow_html=True
                        )
                    st.markdown('</div>', unsafe_allow_html=True)
                else:
                    # Render nothing if no messages (prevents a blank/empty card)
                    st.markdown('<div class="sc-empty"></div>', unsafe_allow_html=True)

                # Composer (single card, no wrapper above it)
                # st.markdown('<div class="sc-card sc-composer-card">', unsafe_allow_html=True)
                with st.form("sidebar_chatbot_form", clear_on_submit=True):
                    user_question = st.text_area(
                        "Your message",
                        placeholder="Ask to review, or edit your document.",
                        height=90,
                        max_chars=1200,
                        label_visibility="collapsed",
                    )
                    c1, c2 = st.columns([2, 1])
                    with c1:
                        ask_button = st.form_submit_button("Send", use_container_width=True)
                    with c2:
                        clear_now = st.form_submit_button("Clear", use_container_width=True)

                if clear_now and st.session_state.sidebar_chat_messages:
                    st.session_state.sidebar_chat_messages = []
                    st.rerun()

                if ask_button and user_question and user_question.strip():
                    text = user_question.strip()
                    st.session_state.sidebar_chat_messages.append({"role": "user", "content": text})

                    with st.spinner("Thinking..."):
                        current_doc = st.session_state.get("current_document")
                        lower = text.lower()

                        is_edit = _is_edit_intent(lower) and bool(current_doc)
                        review_triggers = [
                            "review", "check", "include", "mention", "does it", "where is",
                            "clause", "party", "name", "amount", "id", "jurisdiction",
                            "confidentiality", "data protection"
                        ]
                        is_review = (not is_edit) and any(t in lower for t in review_triggers) and bool(current_doc)

                        if is_edit:
                            ai_response = self.edit_current_document(text)
                        elif is_review:
                            ai_response = self.review_current_document(text)
                        else:
                            ai_response = self.generate_ai_response(text)

                    st.session_state.sidebar_chat_messages.append({"role": "assistant", "content": ai_response})
                    st.session_state.chatbot_expanded = True
                    st.rerun()

                # Footer actions (Undo + disclaimer)
                f1, f2 = st.columns([1, 1])
                with f1:
                    if st.session_state.get("document_history"):
                        if st.button("↩️ Undo last edit", use_container_width=True):
                            msg = self.undo_last_edit()
                            st.session_state.sidebar_chat_messages.append({"role": "assistant", "content": msg})
                            st.session_state.chatbot_expanded = True
                            st.rerun()
                with f2:
                    st.markdown('<div style="text-align:right;"><span class="sc-chip"></span></div>', unsafe_allow_html=True)

            # When user collapses the expander, try to remember it (best-effort UX)
            # (Streamlit doesn't give direct collapse/expand events; we just default to collapsed next run)
            if "assistant_expanded" in st.session_state:
                st.session_state.assistant_expanded = False



# --------------------------------------------------------------------------------------
# Public helpers used by app.py
# --------------------------------------------------------------------------------------

def init_sidebar_chatbot(generator=None):
    """
    Initialize the sidebar chatbot with its own AI model.
    """
    if "sidebar_chatbot" not in st.session_state:
        try:
            st.session_state.sidebar_chatbot = SidebarChatbot(generator)
            st.session_state.sidebar_chatbot.initialize_sidebar_chatbot()
            if st.session_state.sidebar_chatbot.client:
                logger.info('{"event": "sidebar_chatbot_initialized", "status": "success", "model": "gpt-4o"}')
            else:
                logger.warning('{"event": "sidebar_chatbot_initialized", "status": "fallback_mode"}')
        except Exception as e:
            logger.error('{"event": "sidebar_chatbot_init_failed", "error": "%s"}', str(e))
            st.session_state.sidebar_chatbot = SidebarChatbot(generator)
            st.session_state.sidebar_chatbot.initialize_sidebar_chatbot()
    # ensure history exists even if already created earlier
    if "document_history" not in st.session_state:
        st.session_state.document_history = []
    return st.session_state.sidebar_chatbot


def show_chatbot_in_sidebar():
    """Show the chatbot in sidebar - call from your enhance_sidebar()."""
    if "sidebar_chatbot" in st.session_state:
        st.session_state.sidebar_chatbot.show_sidebar_chatbot()


def test_chatbot_model():
    """Test function to verify the AI model is working properly."""
    try:
        client = setup_chatbot_model()
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Hello, can you help me with legal documents?"}],
            max_tokens=100,
        )
        print(f"✅ Chatbot AI Model Test Successful: {response.choices[0].message.content[:100]}...")
        return True
    except Exception as e:
        print(f"❌ Chatbot AI Model Test Failed: {e}")
        return False
