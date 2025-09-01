# bot.py

import streamlit as st
import logging
import re
from typing import Dict, List, Optional, Tuple
from openai import OpenAI
import os

# Configure logging for the chatbot
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
- If a current document exists, prefer document-grounded answers that cite or quote the relevant clause/line (briefly), then summarize implications in plain language.
"""

LEGAL_DISCLAIMER = (
    "Important: I’m not a lawyer. This guidance (and any generated document) does not constitute legal advice. "
    "Please have a qualified Kenyan lawyer review before use."
)

def setup_chatbot_model():
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


# ---------- Document utilities (lightweight parsing to surface key facts) ----------

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

def _first_match(patterns: List[str], text: str, flags= re.IGNORECASE | re.MULTILINE) -> Optional[re.Match]:
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
        # join non-None groups, keep the prettiest token
        candidate = next((g for g in m.groups() if g and re.search(r"[A-Za-z]|\d{4}-\d{2}-\d{2}", g)), None)
        if candidate:
            facts["date_example"] = candidate

    return facts


# ---------- Chatbot implementation ----------

class SidebarChatbot:
    """
    Sidebar-only chatbot that improves prompts and can review generated documents.
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

    # ---- Intent analysis (kept lightweight; core intelligence is in GPT) ----
    def analyze_prompt_intent(self, user_input: str) -> Dict:
        user_input_lower = user_input.lower()
        detected_type = "general"
        for doc_type, patterns in self.document_patterns.items():
            if any(k in user_input_lower for k in patterns["keywords"]):
                detected_type = doc_type
                break
        return {"document_type": detected_type}

    # ---- Core LLM calls ----
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

    def generate_ai_response(self, user_message: str) -> str:
        """General helper guidance when not explicitly reviewing the document."""
        analysis = self.analyze_prompt_intent(user_message)

        current_doc = st.session_state.get("current_document")
        has_doc = bool(current_doc)
        doc_summary = ""
        facts = {}

        if has_doc:
            facts = extract_key_facts(current_doc)
            # Keep summary short to avoid prompt bloat
            snippet = current_doc[:2000]
            doc_summary = (
                f"\n[CurrentDocumentPresent: YES]\n"
                f"[DocumentSnippetStart]\n{snippet}\n[DocumentSnippetEnd]\n"
                f"[ExtractedFacts] {facts}\n"
            )
        else:
            doc_summary = "[CurrentDocumentPresent: NO]\n"

        system_msg = {
            "role": "system",
            "content": APP_KNOWLEDGE.strip()
        }
        user_msg = {
            "role": "user",
            "content": (
                f"User message: {user_message}\n"
                f"{doc_summary}\n"
                "Task: Provide a brief, encouraging response (2–3 sentences). "
                "If a current document is present, ground your tips in that content. "
                "Offer 1–2 concrete, Kenyan-law-aware suggestions to improve their request or next steps.\n"
                f"End with: {LEGAL_DISCLAIMER}"
            )
        }

        return self._chat([system_msg, user_msg], max_tokens=650, temperature=0.6)

    def review_current_document(self, user_question: str) -> str:
        """
        Review the current document in relation to the user's question.
        Answers should reference what's actually in the doc.
        """
        current_doc = st.session_state.get("current_document")
        if not current_doc:
            return "No document is currently available for review."

        facts = extract_key_facts(current_doc)
        snippet = current_doc[:6000]  # give the model plenty of context without being huge

        system_msg = {
            "role": "system",
            "content": APP_KNOWLEDGE.strip()
        }
        user_msg = {
            "role": "user",
            "content": (
                "You are reviewing a user-generated legal document. "
                "Quote short, relevant lines where helpful and then explain.\n\n"
                f"[DocumentStart]\n{snippet}\n[DocumentEnd]\n"
                f"[ExtractedFacts] {facts}\n\n"
                f"User question: {user_question}\n"
                "Answer clearly, citing the relevant section(s) in brief. "
                "Point out any obvious gaps or risks (Kenyan law context) and suggest precise edits or clauses."
                f"\n\nEnd with: {LEGAL_DISCLAIMER}"
            )
        }

        return self._chat([system_msg, user_msg], max_tokens=900, temperature=0.4)

    # ---- UI ----
    def show_sidebar_chatbot(self):
        """Display the streamlined sidebar chatbot (no Quick Help buttons)."""
        with st.sidebar:
            with st.expander("💬 Assistant", expanded=st.session_state.get("chatbot_expanded", False)):
                st.caption(f"*You’re chatting with the {APP_NAME} assistant.*")

                # Show recent conversation (max 6 messages)
                recent_messages = (
                    st.session_state.sidebar_chat_messages[-6:]
                    if st.session_state.sidebar_chat_messages
                    else []
                )
                if recent_messages:
                    for msg in recent_messages:
                        if msg["role"] == "user":
                            st.markdown(f"**You:** {msg['content']}")
                        else:
                            st.markdown(f"**Assistant:** {msg['content']}")
                    st.markdown("---")

                with st.form("sidebar_chatbot_form", clear_on_submit=True):
                    placeholder = "Ask anything about generating or reviewing your document. (If a document exists, I can read it.)"
                    user_question = st.text_area(
                        "Your question:",
                        placeholder=placeholder,
                        height=70,
                        max_chars=1000,
                    )
                    col1, col2 = st.columns([2, 1])
                    with col1:
                        ask_button = st.form_submit_button("Ask")
                    with col2:
                        if st.form_submit_button("Clear") and st.session_state.sidebar_chat_messages:
                            st.session_state.sidebar_chat_messages = []
                            st.rerun()

                if ask_button and user_question.strip():
                    st.session_state.sidebar_chat_messages.append(
                        {"role": "user", "content": user_question.strip()}
                    )
                    with st.spinner("Thinking..."):
                        # If user asks to check/confirm/does it include/etc, prefer review path
                        triggers = ["review", "check", "include", "mention", "does it", "where is", "clause", "party", "name", "amount", "id", "jurisdiction", "confidentiality", "data protection"]
                        current_doc = st.session_state.get("current_document")
                        should_review = any(t in user_question.lower() for t in triggers) and bool(current_doc)
                        if should_review:
                            ai_response = self.review_current_document(user_question.strip())
                        else:
                            ai_response = self.generate_ai_response(user_question.strip())

                    st.session_state.sidebar_chat_messages.append(
                        {"role": "assistant", "content": ai_response}
                    )
                    st.session_state.chatbot_expanded = True
                    st.rerun()


# ---- Public helpers used by app.py ----

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
