# bot.py - OPTIMIZED VERSION FOR FAST DOCUMENT EDITING

import os
import re
import json
import difflib
import logging
from typing import Dict, List, Optional, Tuple
import difflib
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
    "Important: This guidance does not constitute legal advice. "
    
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
      .sc-msg {
        border-radius: 14px;
        padding: 10px 12px;
        line-height: 1.5;
        border: 1px solid rgba(0,0,0,0.05);
        margin-bottom: 16px;
        max-width: 100%;
        box-sizing: border-box;
        word-wrap: break-word;        /* legacy */
        overflow-wrap: anywhere;       /* modern wrapping */
      }
      .sc-msg.user   { background:#f8fafc; }
      .sc-msg.assist { background:#fff; }

      /* Make all bubble children respect width & wrap */
      .sc-msg * {
        max-width: 100%;
        box-sizing: border-box;
        overflow-wrap: anywhere;
      }

      /* Target Streamlit's Markdown container inside the bubble */
      .sc-msg [data-testid="stMarkdownContainer"] {
        max-width: 100%;
        overflow: hidden;
      }

      /* Wrap long code/diff blocks rendered by Markdown */
      .sc-msg [data-testid="stMarkdownContainer"] pre,
      .sc-msg [data-testid="stMarkdownContainer"] code,
      .sc-msg pre,
      .sc-msg code {
        white-space: pre-wrap !important;  /* keep formatting, allow wrap */
        word-break: break-word !important; /* break long tokens */
        overflow-x: auto;                  /* scroll if still too wide */
        display: block;
        background: #f1f5f9;
        padding: 8px 10px;
        border-radius: 8px;
        font-size: 0.9em;
        font-family: "Courier New", monospace;
      }

      .sc-chip { display:inline-block; padding:4px 10px; border-radius:999px; background:rgba(0,0,0,0.06); font-size:12px; }
      .sc-row  { display:flex; gap:10px; align-items:center; }

      /* Header (inside expander) */
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
# ULTRA-FAST Edit helpers - OPTIMIZED FOR SPEED
# --------------------------------------------------------------------------------------

# Comprehensive replacement patterns for instant edits
import re
from typing import Optional, Tuple

# Comprehensive replacement patterns for instant edits (updated with greedy quantifiers)
# Comprehensive replacement patterns for instant edits (updated with greedy quantifiers)
FAST_REPLACE_PATTERNS = [
    # Direct replacement patterns
    r'(?:replace|change|update)\s+["\']?([^"\']+)["\']?\s+(?:with|to)\s+["\']?([^"\']+)["\']?',
    r'(?:change|update)\s+(?:the\s+)?([^"\']+)\s+(?:to|into)\s+([^"\']+)(?:\s|$)',
    r'(?:substitute|swap)\s+([^"\']+)\s+(?:with|for)\s+([^"\']+)(?:\s|$)',
    # Name/party specific
    r'(?:change|update|replace)\s+(?:party|name)\s+([^"\']+)\s+(?:to|with)\s+([^"\']+)(?:\s|$)',
    # Amount specific
    r'(?:change|update|replace)\s+(?:amount|sum|price)\s+([^"\']+)\s+(?:to|with)\s+([^"\']+)(?:\s|$)',
    # Date specific
    r'(?:change|update|replace)\s+(?:date|time)\s+([^"\']+)\s+(?:to|with)\s+([^"\']+)(?:\s|$)',
]

def try_instant_replacement(doc: str, instruction: str) -> Optional[Tuple[str, str, str]]:
    """
    ULTRA-FAST: Try multiple patterns for instant text replacement.
    Returns (new_doc, old_text, new_text) or None.
    """
    instruction = instruction.strip()
    
    for pattern in FAST_REPLACE_PATTERNS:
        match = re.search(pattern, instruction, re.IGNORECASE)
        if match:
            old_text = match.group(1).strip(' "\'.,;:')
            new_text = match.group(2).strip(' "\'.,;:')
            
            if old_text and new_text and old_text != new_text:
                # Try exact match first
                if old_text in doc:
                    new_doc = doc.replace(old_text, new_text)
                    return (new_doc, old_text, new_text)
                
                # Try case-insensitive match
                for line in doc.split('\n'):
                    if old_text.lower() in line.lower():
                        new_line = re.sub(re.escape(old_text), new_text, line, flags=re.IGNORECASE)
                        if new_line != line:
                            new_doc = doc.replace(line, new_line)
                            return (new_doc, old_text, new_text)
    
    return None
def get_document_summary(doc: str, max_chars: int = 6000) -> str:
    """
    Get a smart summary of the document for editing context.
    Prioritizes beginning and key sections.
    """
    if len(doc) <= max_chars:
        return doc
    
    # Take first part and try to include key sections
    first_part = doc[:max_chars // 2]
    
    # Look for important sections in the rest
    remaining = doc[max_chars // 2:]
    important_sections = []
    
    # Find clauses/sections that might be relevant
    for line in remaining.split('\n')[:50]:  # Only check first 50 lines of remainder
        line_lower = line.lower()
        if any(keyword in line_lower for keyword in [
            'termination', 'payment', 'liability', 'confidentiality', 
            'governing law', 'jurisdiction', 'indemnity', 'force majeure',
            'whereas', 'therefore', 'party', 'agreement'
        ]):
            important_sections.append(line)
            if len('\n'.join(important_sections)) > max_chars // 2:
                break
    
    if important_sections:
        return first_part + '\n...\n' + '\n'.join(important_sections)
    else:
        return first_part + '\n...'

EDIT_KEYWORDS = [
    "add", "edit", "revise", "replace", "change", "amend", "update", "modify",
    "insert", "remove", "delete", "fix", "correct", "adjust", "alter",
    "include", "exclude", "strengthen", "weaken", "clarify", "rewrite"
]

def is_edit_request(text: str) -> bool:
    """Ultra-fast edit detection"""
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in EDIT_KEYWORDS)

# --------------------------------------------------------------------------------------
# Sidebar chatbot - OPTIMIZED FOR SPEED
# --------------------------------------------------------------------------------------

class SidebarChatbot:
    """
    SPEED-OPTIMIZED chatbot that prioritizes fast, reliable edits.
    """

    def __init__(self, document_generator=None):
        self.generator = document_generator
        try:
            self.client = setup_chatbot_model()
            self.model_name = "gpt-4o-mini"
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
        if "document_history" not in st.session_state:
            st.session_state.document_history = []

    def analyze_prompt_intent(self, user_input: str) -> Dict:
        """Lightweight intent analysis"""
        user_input_lower = user_input.lower()
        detected_type = "general"
        for doc_type, patterns in self.document_patterns.items():
            if any(k in user_input_lower for k in patterns["keywords"]):
                detected_type = doc_type
                break
        return {"document_type": detected_type}

    def _fast_chat(self, messages: List[Dict], max_tokens: int = 800, temperature: float = 0.3) -> str:
        """
        OPTIMIZED: Faster API calls with lower token limits and temperature
        """
        if not (self.client and self.model_name):
            return "AI model not available. Please try again later."
        
        try:
            resp = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=False  # No streaming for faster response
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Fast chat completion error: {e}")
            return f"Error processing request: {str(e)}"

    def generate_ai_response(self, user_message: str) -> str:
        """FAST general helper guidance"""
        analysis = self.analyze_prompt_intent(user_message)
        
        current_doc = st.session_state.get("current_document")
        has_doc = bool(current_doc)
        doc_summary = ""
        facts = {}

        if has_doc:
            facts = extract_key_facts(current_doc)
            # Much smaller snippet for speed
            snippet = current_doc[:1000]
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
                f"User message: {user_message}\n{doc_summary}\n"
                "Task: Provide a brief, helpful response (1-2 sentences). "
                "If a current document exists, give specific advice. "
                f"End with: {LEGAL_DISCLAIMER}"
            )
        }

        return self._fast_chat([system_msg, user_msg], max_tokens=400, temperature=0.4)

    def review_current_document(self, user_question: str) -> str:
        """FAST document review with smart summarization"""
        current_doc = st.session_state.get("current_document")
        if not current_doc:
            return "No document is currently available for review."

        facts = extract_key_facts(current_doc)
        # Use smart summarization instead of truncation
        doc_summary = get_document_summary(current_doc, max_chars=8000)

        system_msg = {"role": "system", "content": APP_KNOWLEDGE.strip()}
        user_msg = {
            "role": "user",
            "content": (
                "Review this legal document and answer the user's question concisely.\n\n"
                f"[DocumentStart]\n{doc_summary}\n[DocumentEnd]\n"
                f"[ExtractedFacts] {facts}\n\n"
                f"User question: {user_question}\n"
                "Answer directly, citing relevant sections briefly. "
                "Highlight any obvious gaps or risks for Kenyan law. "
                f"End with: {LEGAL_DISCLAIMER}"
            )
        }

        return self._fast_chat([system_msg, user_msg], max_tokens=600, temperature=0.2)

    def edit_current_document(self, user_instruction: str) -> str:
        """
        ULTRA-FAST document editing optimized for speed and reliability.
        Always attempts to make the requested changes.
        """
        current_doc = st.session_state.get("current_document")
        if not current_doc:
            return "There's no document to edit yet. Please generate one first."

        # Save current state for undo
        if "document_history" not in st.session_state:
            st.session_state.document_history = []
        st.session_state.document_history.append(current_doc)

        # STEP 1: Try instant replacement patterns (fastest)
        instant_result = try_instant_replacement(current_doc, user_instruction)
        if instant_result:
            new_doc, old_text, new_text = instant_result
            st.session_state.current_document = new_doc
            self._update_document_state()
            return f"✅ **INSTANT EDIT:** Successfully replaced '{old_text}' with '{new_text}'"

        # STEP 2: Fast AI edit for everything else
        return self._fast_ai_edit(current_doc, user_instruction)

    def _update_document_state(self):
        """Update all necessary session state after document edit"""
        st.session_state.show_download = True
        st.session_state.document_generated_successfully = True
        st.session_state.show_payment = True

    def _fast_ai_edit(self, doc: str, instruction: str) -> str:
        """
        OPTIMIZED AI editing - always tries to make changes, handles large docs smartly
        """
        # Remove summarization; send full doc (model can handle it)
        doc_for_editing = doc  # Previously: get_document_summary(doc, max_chars=10000) if len(doc) > 10000 else doc
        
        # Ultra-simple prompt that focuses on making the change
        system_prompt = (
            "You are a document editor. Make the requested changes to this document. "
            "ALWAYS attempt to make the requested change, even if it requires reasonable assumptions. "
            "Return the COMPLETE edited document, maintaining all original formatting.\n"
            "Be decisive and make the changes the user wants."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"INSTRUCTION: {instruction}\n\nDOCUMENT:\n{doc_for_editing}"}
        ]

        try:
            # Fast edit with reasonable token limit
            edited_response = self._fast_chat(messages, max_tokens=16384, temperature=0.1)
            
            # Add truncation check and continuation
            if len(edited_response) < len(doc_for_editing) * 0.8 or not edited_response.strip().endswith(('.', '!', '?')):
                continuation_prompt = f"Continue the edited document from: {edited_response[-500:]}"
                messages.append({"role": "user", "content": continuation_prompt})
                continuation = self._fast_chat(messages, max_tokens=16384, temperature=0.1)
                edited_response += continuation
            
            # Simple validation - if it looks like a document, use it
            if len(edited_response.strip()) > 200:
                # For large original docs, we need to be smarter about applying edits
                if len(doc) > len(doc_for_editing):
                    final_doc = self._apply_edit_to_large_doc(doc, doc_for_editing, edited_response, instruction)
                else:
                    final_doc = edited_response.strip()
                
                st.session_state.current_document = final_doc
                self._update_document_state()
                return f"✅ **EDIT APPLIED:** {instruction}\n\n{LEGAL_DISCLAIMER}"
            else:
                # If AI response is too short, try a simple text operation
                return self._fallback_edit(doc, instruction)
                
        except Exception as e:
            logger.error(f"Fast AI edit failed: {e}")
            return self._fallback_edit(doc, instruction)

    def _apply_edit_to_large_doc(self, original_doc: str, summarized_doc: str, edited_summary: str, instruction: str) -> str:
        """
        Smart application of edits from summarized version back to full document
        """
        # For simple replacements, apply to full document
        instruction_lower = instruction.lower()
        if any(word in instruction_lower for word in ['replace', 'change', 'update']):
            # Try to find what was changed in the summary and apply to full doc
            simple_replacement = try_instant_replacement(original_doc, instruction)
            if simple_replacement:
                return simple_replacement[0]
        
        # Compute unified diff between summary and edited summary
        summary_lines = summarized_doc.splitlines()
        edited_lines = edited_summary.splitlines()
        diff = list(difflib.unified_diff(summary_lines, edited_lines, lineterm=''))
        
        # Apply diff to original (simple patch applicator)
        original_lines = original_doc.splitlines()
        patched_lines = []
        i = 0
        for line in diff:
            if line.startswith('@'):
                # Parse hunk header, e.g., @@ -1,3 +1,4 @@
                hunk = re.match(r'@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@', line)
                if hunk:
                    start = int(hunk.group(1)) - 1
                    i = start  # Align to original (assuming summary structure matches start/end)
            elif line.startswith('-'):
                continue  # Skip removals (or handle as needed)
            elif line.startswith('+'):
                patched_lines.append(line[1:])
            else:
                if i < len(original_lines):
                    patched_lines.append(original_lines[i])
                    i += 1
        
        # Fallback to edited_summary if patch fails (e.g., empty patched_lines)
        return '\n'.join(patched_lines) if patched_lines else edited_summary

    def _fallback_edit(self, doc: str, instruction: str) -> str:
        """
        Fallback editing attempts when AI edit fails
        """
        # Try one more instant replacement attempt with more flexible patterns
        flexible_patterns = [
            r'([A-Za-z][A-Za-z\s]+)\s+(?:to|with)\s+([A-Za-z][A-Za-z\s]+)',
            r'(\d+[\d,\.]+)\s+(?:to|with)\s+(\d+[\d,\.]+)',
            r'(\d{1,2}/\d{1,2}/\d{4})\s+(?:to|with)\s+(\d{1,2}/\d{1,2}/\d{4})',
        ]
        
        for pattern in flexible_patterns:
            match = re.search(pattern, instruction, re.IGNORECASE)
            if match:
                old_text = match.group(1).strip()
                new_text = match.group(2).strip()
                if old_text in doc:
                    new_doc = doc.replace(old_text, new_text)
                    st.session_state.current_document = new_doc
                    self._update_document_state()
                    return f"✅ **FALLBACK EDIT:** Successfully replaced '{old_text}' with '{new_text}'"
        
        return (
            f"⚠️ **EDIT ATTEMPTED:** I tried to make the requested change but encountered some difficulty. "
            f"For fastest results, try specific instructions like:\n"
            f"- 'Replace [exact text] with [new text]'\n"
            f"- 'Change John Doe to Jane Smith'\n"
            f"- 'Update the amount to KES 50,000'\n\n"
            f"{LEGAL_DISCLAIMER}"
        )

    def undo_last_edit(self) -> str:
        """Fast undo operation"""
        if not st.session_state.get("document_history"):
            return "No previous version found."
        
        prev = st.session_state.document_history.pop()
        st.session_state.current_document = prev
        self._update_document_state()
        return "↩️ **REVERTED:** Document restored to previous version."

    def show_sidebar_chatbot(self):
        """Display the optimized sidebar chatbot interface"""
        with st.sidebar:
            inject_chatbot_styles()

            # Collapsible assistant
            expanded_default = st.session_state.get("assistant_expanded", False)
            with st.expander("SmartClause Assistant", expanded=expanded_default):
                st.session_state.assistant_expanded = True

                # Header
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

                # Show recent messages
                recent_messages = (
                    st.session_state.sidebar_chat_messages[-6:]  # Reduced for speed
                    if st.session_state.sidebar_chat_messages else []
                )

                if recent_messages:
                    st.markdown('<div class="sc-chat-wrap">', unsafe_allow_html=True)
                    for msg in recent_messages:
                        role_cls = "user" if msg["role"] == "user" else "assist"
                        icon = "🧑" if role_cls == "user" else "⚡"

                        st.markdown(
                            f"""
                            <div class="sc-card sc-msg {role_cls}">
                            <div class="sc-row" style="margin-bottom:8px;">
                                <span class="sc-chip">{icon}</span>
                                <div style="font-size:13px; opacity:0.85;">{"You" if role_cls=="user" else "Assistant"}</div>
                            </div>
                            """,
                            unsafe_allow_html=True
                        )

                        st.markdown(msg["content"])
                        st.markdown("</div>", unsafe_allow_html=True)
                    st.markdown("</div>", unsafe_allow_html=True)
                else:
                    st.markdown('<div class="sc-empty"></div>', unsafe_allow_html=True)

                # Fast input form
                with st.form("fast_chatbot_form", clear_on_submit=True):
                    user_question = st.text_area(
                        "Your message",
                        placeholder="Quick edits: 'Replace John with Jane' or 'Change amount to KES 50,000'",
                        height=80,
                        max_chars=800,  # Reduced for speed
                        label_visibility="collapsed",
                    )
                    
                    col1, col2 = st.columns([2, 1])
                    with col1:
                        send_button = st.form_submit_button("Send", use_container_width=True)
                    with col2:
                        clear_button = st.form_submit_button("Clear", use_container_width=True)

                # Handle form submissions
                if clear_button and st.session_state.sidebar_chat_messages:
                    st.session_state.sidebar_chat_messages = []
                    st.rerun()

                if send_button and user_question and user_question.strip():
                    user_text = user_question.strip()
                    st.session_state.sidebar_chat_messages.append({"role": "user", "content": user_text})

                    # OPTIMIZED: Faster intent detection
                    current_doc = st.session_state.get("current_document")
                    user_text_lower = user_text.lower()
                    
                    # Quick processing with minimal spinner time
                    with st.spinner("Processing..."):
                        if is_edit_request(user_text) and current_doc:
                            # EDIT REQUEST - Fastest path
                            ai_response = self.edit_current_document(user_text)
                        elif any(word in user_text_lower for word in ['review', 'check', 'what', 'where', 'how', 'does']) and current_doc:
                            # REVIEW REQUEST - Fast document analysis
                            ai_response = self.review_current_document(user_text)
                        else:
                            # GENERAL REQUEST - Quick guidance
                            ai_response = self.generate_ai_response(user_text)

                    st.session_state.sidebar_chat_messages.append({"role": "assistant", "content": ai_response})
                    st.session_state.chatbot_expanded = True
                    st.rerun()

                # Quick action buttons
                col1, col2 = st.columns([1, 1])
                with col1:
                    if st.session_state.get("document_history"):
                        if st.button("↩️ Undo", use_container_width=True, help="Undo last edit"):
                            undo_msg = self.undo_last_edit()
                            st.session_state.sidebar_chat_messages.append({"role": "assistant", "content": undo_msg})
                            st.rerun()
                
                with col2:
                    if st.button("💡 Tips", use_container_width=True, help="Quick editing tips"):
                        tips_msg = (
                            "**FAST EDITING TIPS:**\n\n"
                            "**Instant edits:** 'Replace [old] with [new]'\n\n"
                            "**Names:** 'Change John Doe to Jane Smith' or 'Change [first_party] to John Doe'\n\n" 
                            "**Amounts:** 'Update amount to KES 100,000'\n\n"
                            "**Dates:** 'Change date to 15 March 2024'\n\n"
                            "*Type what you want changed exactly as it is and provide the new information!*"
                        )
                        st.session_state.sidebar_chat_messages.append({"role": "assistant", "content": tips_msg})
                        st.rerun()

            # Save collapsed state
            if "assistant_expanded" in st.session_state:
                st.session_state.assistant_expanded = False

# --------------------------------------------------------------------------------------
# Public helpers - OPTIMIZED
# --------------------------------------------------------------------------------------

def init_sidebar_chatbot(generator=None):
    """
    Initialize the speed-optimized sidebar chatbot.
    """
    if "sidebar_chatbot" not in st.session_state:
        try:
            st.session_state.sidebar_chatbot = SidebarChatbot(generator)
            st.session_state.sidebar_chatbot.initialize_sidebar_chatbot()
            if st.session_state.sidebar_chatbot.client:
                logger.info('{"event": "fast_chatbot_initialized", "status": "success", "model": "gpt-4o-mini"}')
            else:
                logger.warning('{"event": "fast_chatbot_initialized", "status": "fallback_mode"}')
        except Exception as e:
            logger.error('{"event": "fast_chatbot_init_failed", "error": "%s"}', str(e))
            st.session_state.sidebar_chatbot = SidebarChatbot(generator)
            st.session_state.sidebar_chatbot.initialize_sidebar_chatbot()
    
    # Ensure history exists
    if "document_history" not in st.session_state:
        st.session_state.document_history = []
    
    return st.session_state.sidebar_chatbot

def show_chatbot_in_sidebar():
    """Show the speed-optimized chatbot in sidebar"""
    if "sidebar_chatbot" in st.session_state:
        st.session_state.sidebar_chatbot.show_sidebar_chatbot()

def test_fast_chatbot_model():
    """Test the optimized chatbot model for speed and functionality"""
    try:
        import time
        start_time = time.time()
        
        client = setup_chatbot_model()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Test: Replace 'John Doe' with 'Jane Smith' in a contract."}],
            max_tokens=100,
            temperature=0.1
        )
        
        end_time = time.time()
        duration = end_time - start_time
        
        print(f"✅ **FAST CHATBOT TEST SUCCESSFUL**")
        print(f"📊 **Response Time:** {duration:.2f} seconds")
        print(f"🤖 **Model:** gpt-4o-mini")
        print(f"💬 **Sample Response:** {response.choices[0].message.content[:100]}...")
        print(f"⚡ **Optimization Status:** ACTIVE")
        
        return True
    except Exception as e:
        print(f"❌ **FAST CHATBOT TEST FAILED:** {e}")
        return False

# --------------------------------------------------------------------------------------
# ADDITIONAL SPEED OPTIMIZATIONS
# --------------------------------------------------------------------------------------

class DocumentEditCache:
    """
    Simple cache for common edits to avoid repeated API calls
    """
    def __init__(self, max_size: int = 50):
        self.cache = {}
        self.max_size = max_size
    
    def get_cache_key(self, doc_snippet: str, instruction: str) -> str:
        """Generate a cache key from document snippet and instruction"""
        import hashlib
        content = f"{doc_snippet[:500]}{instruction}"
        return hashlib.md5(content.encode()).hexdigest()
    
    def get(self, doc_snippet: str, instruction: str) -> Optional[str]:
        """Get cached edit result if available"""
        key = self.get_cache_key(doc_snippet, instruction)
        return self.cache.get(key)
    
    def set(self, doc_snippet: str, instruction: str, result: str):
        """Cache an edit result"""
        if len(self.cache) >= self.max_size:
            # Remove oldest entry
            oldest_key = next(iter(self.cache))
            del self.cache[oldest_key]
        
        key = self.get_cache_key(doc_snippet, instruction)
        self.cache[key] = result

# Global cache instance
_edit_cache = DocumentEditCache()

def get_edit_cache() -> DocumentEditCache:
    """Get the global edit cache instance"""
    return _edit_cache

# --------------------------------------------------------------------------------------
# BATCH EDITING SUPPORT
# --------------------------------------------------------------------------------------

def apply_batch_edits(doc: str, edit_instructions: List[str]) -> Tuple[str, List[str]]:
    """
    Apply multiple edits in sequence for efficiency.
    Returns (final_document, list_of_change_summaries)
    """
    current_doc = doc
    changes = []
    
    for instruction in edit_instructions:
        # Try instant replacement first
        result = try_instant_replacement(current_doc, instruction)
        if result:
            current_doc, old_text, new_text = result
            changes.append(f"Replaced '{old_text}' with '{new_text}'")
        else:
            changes.append(f"Attempted: {instruction}")
    
    return current_doc, changes

# --------------------------------------------------------------------------------------
# PERFORMANCE MONITORING
# --------------------------------------------------------------------------------------

class EditPerformanceMonitor:
    """Monitor edit performance for optimization"""
    
    def __init__(self):
        self.edit_times = []
        self.edit_methods = []
        self.success_rates = {}
    
    def record_edit(self, method: str, duration: float, success: bool):
        """Record an edit operation"""
        self.edit_times.append(duration)
        self.edit_methods.append(method)
        
        if method not in self.success_rates:
            self.success_rates[method] = {"attempts": 0, "successes": 0}
        
        self.success_rates[method]["attempts"] += 1
        if success:
            self.success_rates[method]["successes"] += 1
    
    def get_stats(self) -> Dict:
        """Get performance statistics"""
        if not self.edit_times:
            return {}
        
        return {
            "avg_edit_time": sum(self.edit_times) / len(self.edit_times),
            "fastest_edit": min(self.edit_times),
            "total_edits": len(self.edit_times),
            "success_rates": {
                method: data["successes"] / data["attempts"]
                for method, data in self.success_rates.items()
            }
        }

# Global performance monitor
_perf_monitor = EditPerformanceMonitor()

def get_performance_monitor() -> EditPerformanceMonitor:
    """Get the global performance monitor"""
    return _perf_monitor