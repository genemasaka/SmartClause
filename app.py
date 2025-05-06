import streamlit as st
import logging
from pathlib import Path
from typing import Optional
import os
from agent import setup_environment, DocumentGenerator
from mpesa_handler import MpesaHandler
from font_setup import setup_fonts
from docx import Document
from docx.shared import Pt
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from fpdf import FPDF
import io
import re
import time
from payment_verification import PaymentVerification, init_payment_state, update_payment_status, handle_download_request, reset_payment_state


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def init_session_state():
    """Initialize session state variables"""
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "generator" not in st.session_state:
        try:
            template_manager, model = setup_environment()
            st.session_state.generator = DocumentGenerator(template_manager, model)
            logger.info("Document generator initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize document generator: {e}")
            st.error("Failed to initialize the application. Please check your configuration.")
    if "mpesa" not in st.session_state:
        st.session_state.mpesa = MpesaHandler()
        logger.info("Mpesa handler initialized successfully")
    if "document_generated_successfully" not in st.session_state:
        st.session_state.document_generated_successfully = False
    if "show_welcome" not in st.session_state:
        st.session_state.show_welcome = True
    if "current_document" not in st.session_state:
        st.session_state.current_document = None
    if "generation_in_progress" not in st.session_state:
        st.session_state.generation_in_progress = False
    if "document_price" not in st.session_state:
        st.session_state.document_price = 0
    if "show_payment" not in st.session_state:
        st.session_state.show_payment = False
    if "show_download" not in st.session_state:
        st.session_state.show_download = False
    if "force_sidebar_open" not in st.session_state: 
        st.session_state.force_sidebar_open = False
    if not setup_fonts():
        st.warning("Some fonts are missing. Documents may not display correctly.")
    init_payment_state()


class UnicodeAwarePDF(FPDF):
    def __init__(self):
        super().__init__()
        self.add_font('DejaVu', '', 'DejaVuSansCondensed.ttf', uni=True)
        self.add_font('DejaVu', 'B', 'DejaVuSansCondensed-Bold.ttf', uni=True)
        self.set_font('DejaVu', size=11)
        self.set_auto_page_break(auto=True, margin=15)
    
    def multi_cell(self, w, h, txt, border=0, align='L', fill=False):
        # Calculate text width and handle wrapping
        if w == 0:
            w = self.w - self.l_margin - self.r_margin
        wmax = (w - 2 * self.c_margin) * 1000 / self.font_size
        text = txt.replace('\r', '')
        super().multi_cell(w, h, text, border, align, fill)

class KenyanLegalDocument:
    def clean_text_for_pdf(text: str) -> str:
        """Clean and prepare text for PDF conversion by replacing problematic Unicode characters."""
        replacements = {
            '\u2013': '-',  # en dash
            '\u2014': '--', # em dash
            '\u2018': "'",  # left single quote
            '\u2019': "'",  # right single quote
            '\u201C': '"',  # left double quote
            '\u201D': '"',  # right double quote
            '\u2026': '...', # ellipsis
            '\u2022': '*',  # bullet point
            # Add more replacements as needed
        }
        
        # Replace known special characters
        for old, new in replacements.items():
            text = text.replace(old, new)
        
        # Replace any remaining non-Latin1 characters with their closest ASCII equivalent
        cleaned_text = ''
        for char in text:
            try:
                char.encode('latin-1')
                cleaned_text += char
            except UnicodeEncodeError:
                cleaned_text += ' '  # Replace unsupported chars with space
                
        return cleaned_text



class LegalDocumentPDF(FPDF):
    def __init__(self):
        super().__init__()
        self.add_page()
        self.set_margins(25, 25, 25)
        self.set_auto_page_break(auto=True, margin=25)
    
    def chapter_title(self, text):
        """Add a chapter title with specific formatting"""
        self.set_font('Helvetica', 'BU', 13)
        self.cell(0, 10, text, ln=True, align='C')
        self.ln(10)
    
    def body_text(self, text):
        """Add body text with standard formatting"""
        self.set_font('Helvetica', '', 11)
        self.multi_cell(0, 10, text)
        self.ln(5)

def clean_markdown(text: str) -> tuple[str, bool]:
    """
    Remove markdown symbols except square brackets and return the clean text and whether it was bold
    
    Args:
        text (str): Input text with potential markdown formatting
    
    Returns:
        tuple[str, bool]: Cleaned text and a boolean indicating if it was bold
    """
    is_bold = False
    cleaned_text = text
    
    # Remove markdown bold indicators
    if '**' in text:
        cleaned_text = text.replace('**', '')
        is_bold = True
    
    # Remove other markdown formatting, excluding square brackets
    cleaned_text = re.sub(r'\\|\*', '', cleaned_text)
    cleaned_text = cleaned_text.replace('{.underline}', '')
    
    # Specifically handle common problematic phrases
    special_phrases = {
        "NOW, THEREFOREE": "NOW, THEREFORE",
        "IN WITNESS WHEREOFF": "IN WITNESS WHEREOF"
    }
    
    for incorrect, correct in special_phrases.items():
        cleaned_text = cleaned_text.replace(incorrect, correct)
    
    return cleaned_text.strip(), is_bold

def identify_document_type(content: str) -> str:
    """Identify if the document is an affidavit or contract"""
    if "OATHS AND STATUTORY DECLARATIONS ACT" in content:
        return "affidavit"
    elif "CONTRACT" in content.upper() or "AGREEMENT" in content.upper():
        return "contract"
    return "other"


def format_affidavit_docx(doc, content: str):
    """Apply specific formatting for affidavit documents in DOCX"""
    # Define title sections in correct order
    title_sections = [
        "REPUBLIC OF KENYA",
        "IN THE MATTER OF THE OATHS AND STATUTORY DECLARATIONS ACT",
        "(CAP 15 OF THE LAWS OF KENYA)",
        "AFFIDAVIT"
    ]
    
    # Add title sections in specified order
    for title in title_sections:
        para = doc.add_paragraph()
        run = para.add_run(title)
        run.bold = True
        run.font.underline = True
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    # Split content into paragraphs and process each
    paragraphs = content.split('\n\n')
    
    for para_text in paragraphs:
        if not para_text.strip():
            continue
            
        # Skip title sections as they're already processed
        if any(title in para_text for title in title_sections):
            continue
        
        para = doc.add_paragraph()
        
        # Handle "THAT" statements
        if "THAT" in para_text:
            match = re.match(r'(\d+\.\s+)?(\*\*)?THAT(\*\*)?(.+)', para_text)
            if match:
                number = match.group(1) or ''
                remainder = match.group(4)
                
                if number:
                    para.add_run(number)
                
                that_run = para.add_run("THAT ")
                that_run.bold = True
                that_run.font.underline = True
                
                clean_remainder, is_bold = clean_markdown(remainder)
                remainder_run = para.add_run(clean_remainder)
                if is_bold:
                    remainder_run.bold = True
        
        # Handle sworn section
        elif "SWORN" in para_text:
            lines = para_text.split('\n')
            for line in lines:
                clean_line, is_bold = clean_markdown(line)
                if clean_line:
                    sworn_para = doc.add_paragraph()
                    run = sworn_para.add_run(clean_line)
                    run.bold = True
                    sworn_para.alignment = WD_ALIGN_PARAGRAPH.LEFT
        
        # Handle regular paragraphs
        else:
            clean_text, is_bold = clean_markdown(para_text)
            run = para.add_run(clean_text)
            if is_bold:
                run.bold = True

def format_contract_docx(doc, content: str):
    """Apply specific formatting for contract documents in DOCX"""
    paragraphs = content.split('\n\n')
    title_added = False
    
    for para_text in paragraphs:
        if not para_text.strip():
            continue
            
        para = doc.add_paragraph()
        
        # Handle main title (only add once)
        if not title_added and ("CONTRACT" in para_text.upper() or "AGREEMENT" in para_text.upper()):
            title_para = doc.add_paragraph()
            title_run = title_para.add_run("CONTRACT AGREEMENT")
            title_run.bold = True
            title_run.font.underline = True
            title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            title_added = True
            continue
        
        # Handle article titles and section headings
        if re.match(r'^ARTICLE\s+\d+:', para_text, re.IGNORECASE):
            # Split the line into title and content
            parts = para_text.split(':', 1)
            if len(parts) == 2:
                title, content = parts
                run = para.add_run(title + ':')
                run.bold = True
                para.add_run(content)
            else:
                run = para.add_run(para_text)
                run.bold = True
        elif "WHEREAS:" in para_text:
            para.add_run("WHEREAS:").bold = True
            para.add_run(para_text[8:])  # Add rest of text without bold
        elif "NOW, THEREFORE" in para_text:
            para.add_run("NOW, THEREFORE").bold = True
            para.add_run(para_text[13:])  # Add rest of text without bold
        elif "IN WITNESS WHEREOF" in para_text:
            para.add_run("IN WITNESS WHEREOF").bold = True
            para.add_run(para_text[17:])  # Add rest of text without bold
        else:
            clean_text, is_bold = clean_markdown(para_text)
            run = para.add_run(clean_text)
            if is_bold:
                run.bold = True

def convert_to_docx(content: str) -> bytes:
    """Convert text content to DOCX format with appropriate formatting"""
    doc = Document()
    
    # Set default font
    style = doc.styles['Normal']
    style.font.name = 'Times New Roman'
    style.font.size = Pt(12)
    
    # Identify document type and apply appropriate formatting
    doc_type = identify_document_type(content)
    
    if doc_type == "affidavit":
        format_affidavit_docx(doc, content)
    elif doc_type == "contract":
        format_contract_docx(doc, content)
    else:
        # Default formatting for other document types
        for para in content.split('\n\n'):
            if para.strip():
                doc.add_paragraph(para.strip())
    
    # Save to bytes
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()

class LegalDocumentPDF(FPDF):
    def init(self):
        super().init()
        self.add_page()

def format_affidavit_pdf(pdf: FPDF, content: str):
    """Apply specific formatting for affidavit documents in PDF"""
    # Define title sections in correct order
    title_sections = [
        "REPUBLIC OF KENYA",
        "IN THE MATTER OF THE OATHS AND STATUTORY DECLARATIONS ACT",
        "(CAP 15 OF THE LAWS OF KENYA)",
        "AFFIDAVIT"
    ]
    
    # Add title sections
    for title in title_sections:
        pdf.set_font('Helvetica', 'BU', 13)
        pdf.cell(0, 10, title, ln=True, align='C')
    
    pdf.ln(10)
    
    # Split content into paragraphs
    paragraphs = content.split('\n\n')
    
    for para_text in paragraphs:
        if not para_text.strip():
            continue
            
        # Skip title sections as they're already processed
        if any(title in para_text for title in title_sections):
            continue
        
        # Handle "THAT" statements
        if "THAT" in para_text:
            match = re.match(r'(\d+\.\s+)?(\*\*)?THAT(\*\*)?(.+)', para_text)
            if match:
                number = match.group(1) or ''
                remainder = match.group(4)
                
                pdf.set_font('Helvetica', '', 12)
                if number:
                    pdf.write(10, number)
                
                pdf.set_font('Helvetica', 'BU', 12)
                pdf.write(10, "THAT ")
                
                clean_remainder, is_bold = clean_markdown(remainder)
                pdf.set_font('Helvetica', 'B' if is_bold else '', 12)
                pdf.multi_cell(0, 10, clean_remainder)
        
        # Handle sworn section
        elif "SWORN" in para_text:
            lines = para_text.split('\n')
            for line in lines:
                clean_line, _ = clean_markdown(line)
                if clean_line:
                    pdf.set_font('Helvetica', 'B', 12)
                    pdf.cell(0, 10, clean_line, ln=True)
        
        # Handle regular paragraphs
        else:
            clean_text, is_bold = clean_markdown(para_text)
            pdf.set_font('Helvetica', 'B' if is_bold else '', 12)
            pdf.multi_cell(0, 10, clean_text)
        
        pdf.ln(5)

def format_contract_pdf(pdf: FPDF, content: str):
    """Apply specific formatting for contract documents in PDF"""
    paragraphs = content.split('\n\n')
    title_added = False
    
    for para_text in paragraphs:
        if not para_text.strip():
            continue
            
        # Handle main title (only add once)
        if not title_added and ("CONTRACT" in para_text.upper() or "AGREEMENT" in para_text.upper()):
            pdf.set_font('Helvetica', 'BU', 14)
            pdf.cell(0, 10, "CONTRACT AGREEMENT", ln=True, align='C')
            pdf.ln(10)
            title_added = True
            continue
        
        # Handle article titles and section headings
        if re.match(r'^ARTICLE\s+\d+:', para_text, re.IGNORECASE):
            parts = para_text.split(':', 1)
            if len(parts) == 2:
                title, content = parts
                # Print title in bold
                pdf.set_font('Helvetica', 'B', 12)
                pdf.write(10, title + ':')
                # Print content in normal font
                pdf.set_font('Helvetica', '', 12)
                # Use write for inline content
                pdf.multi_cell(0, 10, content)
            else:
                pdf.set_font('Helvetica', 'B', 12)
                pdf.multi_cell(0, 10, para_text)
        elif "WHEREAS:" in para_text:
            pdf.set_font('Helvetica', 'B', 12)
            pdf.write(10, "WHEREAS:")
            pdf.set_font('Helvetica', '', 12)
            pdf.multi_cell(0, 10, para_text[8:])
        elif "NOW, THEREFORE" in para_text:
            pdf.set_font('Helvetica', 'B', 12)
            pdf.write(10, "NOW, THEREFORE")
            pdf.set_font('Helvetica', '', 12)
            pdf.multi_cell(0, 10, para_text[13:])
        elif "IN WITNESS WHEREOF" in para_text:
            pdf.set_font('Helvetica', 'B', 12)
            pdf.write(10, "IN WITNESS WHEREOF")
            pdf.set_font('Helvetica', '', 12)
            pdf.multi_cell(0, 10, para_text[17:])
        else:
            clean_text, is_bold = clean_markdown(para_text)
            pdf.set_font('Helvetica', 'B' if is_bold else '', 12)
            pdf.multi_cell(0, 10, clean_text, align='L')
        
        pdf.ln(5)

def convert_to_pdf(content: str) -> bytes:
    """Convert document content to PDF with appropriate formatting"""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_margins(25, 25, 25)
    pdf.set_auto_page_break(auto=True, margin=25)
    
    # Identify document type and apply appropriate formatting
    doc_type = identify_document_type(content)
    
    try:
        if doc_type == "affidavit":
            format_affidavit_pdf(pdf, content)
        elif doc_type == "contract":
            format_contract_pdf(pdf, content)
        else:
            # Default formatting for other document types
            pdf.set_font('Helvetica', '', 12)
            for para in content.split('\n\n'):
                if para.strip():
                    pdf.multi_cell(0, 10, para.strip())
                    pdf.ln(5)
        
        return pdf.output(dest='S').encode('latin-1')
    except Exception as e:
        logging.error(f"PDF generation error: {str(e)}")
        raise RuntimeError(f"Failed to generate PDF: {str(e)}")
    
def show_download_buttons():
    """Display persistent download buttons for the document"""
    if st.session_state.show_download and st.session_state.current_document:
        # Create payment verifier
        payment_verifier = PaymentVerification(st.session_state.mpesa)
        
        try:
            # First row for payment status
            if not st.session_state.payment_verified:
                st.warning("⚠️ Payment required before download")
            else:
                st.success("✅ Payment verified")
            
            # Second row for download buttons
            st.markdown("### Download Options")
            col1, col2 = st.columns(2)
            
            with col1:
                if st.button("Download as DOCX", key="download_docx_button"):
                    if handle_download_request(payment_verifier):
                        docx_data = convert_to_docx(st.session_state.current_document)
                        st.download_button(
                            label="Click to download DOCX",
                            data=docx_data,
                            file_name="document.docx",
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            key="docx_download"
                        )
            
            with col2:
                if st.button("Download as PDF", key="download_pdf_button"):
                    if handle_download_request(payment_verifier):
                        pdf_data = convert_to_pdf(st.session_state.current_document)
                        st.download_button(
                            label="Click to download PDF",
                            data=pdf_data,
                            file_name="document.pdf",
                            mime="application/pdf",
                            key="pdf_download"
                        )
                
        except Exception as e:
            st.error(f"Error preparing download: {str(e)}")
            logging.error(f"Download preparation error: {str(e)}", exc_info=True)

def generate_document(generator: DocumentGenerator, prompt: str) -> tuple[Optional[str], bool]:
    try:
        response = generator.generate_document(prompt)
        if response:
            st.session_state.current_document = response
            st.session_state.document_price = DocumentPricing.get_document_price(response)
            reset_payment_state()
            return response, True
        else:
            logger.error("No response received from the backend.")
            return "An error occurred while generating the document. Please check your input and try again.", False
    except Exception as e:
        logger.error(f"Error during document generation: {e}")
        return "An unexpected error occurred. Please check your input and try again.", False


def validate_phone_number(phone: str) -> bool:
    """Validate the phone number format"""
    return phone.startswith("254") and len(phone) == 12 and phone.isdigit()
class DocumentPricing:
    """Handles pricing logic for different document types"""
    
    PRICES = {
        "affidavit": 3000,
        "contract": 5000,
        "other": 5000  # Default price for unrecognized document types
    }
    
    @staticmethod
    def get_document_price(content: str) -> int:
        """
        Determine the price based on document type
        
        Args:
            content (str): The document content to analyze
            
        Returns:
            int: Price in KES
        """
        if "OATHS AND STATUTORY DECLARATIONS ACT" in content:
            return DocumentPricing.PRICES["affidavit"]
        elif "CONTRACT" in content.upper() or "AGREEMENT" in content.upper():
            return DocumentPricing.PRICES["contract"]
        return DocumentPricing.PRICES["other"]
    
def show_welcome_modal():
    """Shows a welcome modal with updated quick start guide"""
    if st.session_state.show_welcome:
        with st.container():
            st.markdown(""" 
            # Welcome!
            
            Let's help you get started with generating your legal documents.
            """)
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.markdown(""" 
                ### Quick Start
                1. Type your request naturally
                2. Wait for document generation
                3. Review the document
                4. Make payment via M-PESA
                5. Download your document
                
                ### Example Prompts
                - "Draft a contract for web development services"
                - "Create an affidavit for change of name"
                - "Generate a stock transfer agreement"
                """)
                
            with col2:
                st.markdown(""" 
                ### Pro Tips
                - Be specific about parties involved
                - Include key terms and conditions
                - Mention important dates
                - Include any special requirements
                
                ### ⚠️ Important Notes
                - All documents should be reviewed by a legal professional
                - Sensitive information is encrypted
                - Documents are compliant with Kenyan law
                """)

            # Only show the button if no generation is in progress
            if not st.session_state.get("generation_in_progress", False):
                if st.button("Got it!", key="welcome_close"):
                    st.session_state.show_welcome = False
                    # Only rerun if no document generation is in progress
                    if not st.session_state.get("generation_in_progress", False):
                        st.rerun()
            else:
                # Show disabled button during generation
                st.button("Got it!", key="welcome_close", disabled=True)

def show_help_section():
    """Shows the comprehensive help section"""
    with st.expander("Help Guide", expanded=False):
        tabs = st.tabs(["Document Types", "How to Use", "Best Practices", "FAQ"])
        
        with tabs[0]:
            st.markdown("""
            ### Available Document Types
            
            1. **Contract** - For business agreements and services
            2. **Affidavit** - Sworn statements for legal proceedings
            
            Each document type follows specific Kenyan legal requirements and formatting.
            """)
            
        with tabs[1]:
            st.markdown("""
            ### Step-by-Step Guide
            
            1. **Formulate Your Request**
               - Simply describe what you need
               - Example: "Create a contract for a house sale"
               - Include all relevant details
               - Be specific about requirements
            
            2. **Review Generated Document**
               - Check all details are correct
               - Verify names and dates
               - Ensure terms match your needs
            
            3. **Make Payment**
               - Enter your M-PESA number
               - Confirm payment prompt on your phone
               - Complete the transaction
            
            4. **Download and Use**
               - Download the final document
               - Review with legal counsel
               - Make any necessary modifications
            """)
            
        with tabs[2]:
            st.markdown("""
            ### Best Practices for Document Generation
            
            1. **Be Specific in Your Request**
               - Include full names of all parties
               - Specify dates and deadlines
               - Detail all terms and conditions
               - Mention specific requirements
            
            2. **Include Essential Details**
               - Business/Company names
               - Registration numbers
               - Physical addresses
               - Contact information
               - Monetary values
               - Time periods
            
            3. **Consider Legal Requirements**
               - Jurisdiction specifications
               - Regulatory compliance needs
               - Required certifications
               - Witness requirements
            
            4. **Document Review**
               - Always read thoroughly
               - Check for accuracy
               - Verify all parties' details
               - Confirm terms and conditions
            """)
            
        with tabs[3]:
            st.markdown("""
            ### Frequently Asked Questions
            
            **Q: Are these documents legally binding?**  
            A: While generated documents follow legal formats, they should be reviewed by a qualified legal professional before use.
            
            **Q: How secure is my information?**  
            A: Your information is held temporarily in memory during your active session and is not stored persistently by the app after the session ends. 
               Data transmitted to external services (e.g., M-PESA for payments and the AI model for document generation) is sent over secure connections, 
               but storage by these services is subject to their privacy policies. We recommend reviewing M-PESA and Google’s (Gemini AI) privacy terms for details on how they handle your data.
            
            **Q: Can I modify the generated document?**  
            A: Yes, you can request modifications by providing specific changes needed in your prompt.
            
            **Q: What if I need a document type not listed?**  
            A: Contact support for custom document types or use the closest matching document type.
            
            **Q: How long does generation take?**  
            A: Document generation typically takes 30-60 seconds depending on complexity.
            
            **Q: What payment methods are accepted?**  
            A: Currently, we accept M-PESA payments only.
                        
            📱 Need Help?
                Email: support@smartclause.co.ke
                Call: +254 XXX XXX XXX
            """)


def enhance_sidebar():
    """Enhanced sidebar with additional information"""
    with st.sidebar:
        st.header("Document Quality Tips")
        st.info("""
        To get the best results:
        - Use clear, specific language
        - Include all relevant parties
        - Include all important details(eg. ID numbers, Postal addresses, vehicle registration numbers, title deed numbers, etc)
        - Specify important dates
        - Detail key terms
        """)

        st.header("Controls")
        if not st.session_state.get("generation_in_progress", False):
            if st.button("Clear Chat History", key="clear_chat_button"):
                st.session_state.messages = []
                st.session_state.show_payment = False
                st.session_state.document_generated_successfully = False
                st.session_state.show_download = False
                st.session_state.current_document = None
                st.session_state.force_sidebar_open = False
                st.rerun()
        else:
            st.button("Clear Chat History", key="clear_chat_button", disabled=True)

        if st.session_state.get("show_payment", False) and st.session_state.get("document_generated_successfully", False):
            st.markdown('<div class="payment-card">', unsafe_allow_html=True)
            with st.form("sidebar_payment_form"):
                st.subheader("💳 Pay using M-PESA STK")
                st.write(f"Amount to pay: KES {st.session_state.document_price:,}")
                phone = st.text_input("Phone number (254XXXXXXXXX):")
                submitted = st.form_submit_button("Pay Now")
                
                if submitted and phone:
                    if validate_phone_number(phone):
                        amount = st.session_state.document_price
                        account_ref = "Smart Clause"
                        desc = "Payment for legal document generation"
                        try:
                            response = st.session_state.mpesa.initiate_stk_push(phone, amount, desc)
                            if 'ResponseCode' in response and response['ResponseCode'] == '0':
                                update_payment_status(response['CheckoutRequestID'], amount)
                                st.success("✓ Check your phone for payment prompt")
                            else:
                                st.error("✗ Payment failed: " + response.get('errorMessage', 'Unknown error'))
                        except Exception as e:
                            st.error(f"✗ Error: {str(e)}")
            st.markdown('</div>', unsafe_allow_html=True)

def format_document_html(content: str) -> str:
    """Convert document content to formatted HTML for display in the web interface with theme awareness"""
    # Add debug logging
    logger.info(f"Full document content length: {len(content)}")
    logger.info(f"First 200 characters: {content[:200]}")

    doc_type = identify_document_type(content)
    
    # Modify the base HTML to ensure full visibility
    html = f"""
    <div class="legal-document" style="
        font-family: 'Times New Roman', serif; 
        line-height: 1.5; 
        padding: 25px; 
        max-width: 800px; 
        margin: 0 auto; 
        background-color: var(--document-bg-color, #ffffff); 
        color: var(--document-text-color, #000000); 
        border: 1px solid rgba(128, 128, 128, 0.2); 
        overflow: visible !important; 
        min-height: 100px;
        display: block;
    ">
    <style>
        .legal-document * {{
            max-height: none !important;
            overflow: visible !important;
        }}
    </style>
    """
    
    # Rest of your document formatting code with class-based styling
    if doc_type == "affidavit":
        # Title sections
        title_sections = [
            "REPUBLIC OF KENYA",
            "IN THE MATTER OF THE OATHS AND STATUTORY DECLARATIONS ACT",
            "(CAP 15 OF THE LAWS OF KENYA)",
            "AFFIDAVIT",
        ]
        
        # Add title sections with class-based styling
        for title in title_sections:
            html += f'<h6 class="doc-title" style="text-align: center; font-weight: bold; text-decoration: underline; color: var(--document-text-color, #000000); opacity: 1;">{title}</h6>'
        
        html += '<div style="margin-top: 14px; "></div>'
        
        # Process paragraphs
        paragraphs = content.split('\n\n')
        
        for para_text in paragraphs:
            para_text = para_text.strip()
            if not para_text:
                continue
                
            # Skip title sections as they're already processed
            if any(title in para_text for title in title_sections):
                continue
            
            # Handle "THAT" statements
            if "THAT" in para_text:
                match = re.match(r'(\d+\.\s+)?(\*\*)?THAT(\*\*)?(.+)', para_text)
                if match:
                    number = match.group(1) or ''
                    remainder = match.group(4)
                    
                    clean_remainder, is_bold = clean_markdown(remainder)
                    
                    html += '<p class="doc-paragraph">'
                    if number:
                        html += f'{number}'
                    
                    # Add space after "THAT" to ensure separation
                    html += '<span style="font-weight: bold; text-decoration: underline;">THAT</span> '
                    
                    if is_bold:
                        html += f'<span style="font-weight: bold;">{clean_remainder}</span>'
                    else:
                        html += clean_remainder
                    
                    html += '</p>'
            
            # Handle sworn section
            elif "SWORN" in para_text:
                lines = para_text.split('\n')
                for line in lines:
                    clean_line, _ = clean_markdown(line)
                    if clean_line:
                        html += f'<p class="doc-paragraph" style="font-weight: bold;">{clean_line}</p>'
            
            # Handle regular paragraphs
            else:
                clean_text, is_bold = clean_markdown(para_text)
                if is_bold:
                    html += f'<p class="doc-paragraph" style="font-weight: bold;">{clean_text}</p>'
                else:
                    html += f'<p class="doc-paragraph">{clean_text}</p>'
    
    elif doc_type == "contract":
        # Contract formatting with class-based styling
        paragraphs = content.split('\n\n')
        title_added = False
        
        for para_text in paragraphs:
            para_text = para_text.strip()
            if not para_text:
                continue
                
            # Handle main title (only add once)
            if not title_added and ("CONTRACT" in para_text.upper() or "AGREEMENT" in para_text.upper()):
                html += '<h6 class="doc-title" style="text-align: center; font-weight: bold; text-decoration: underline; color: var(--document-text-color, #000000); opacity: 1;">CONTRACT AGREEMENT</h6>'
                title_added = True
                continue
            
            # Handle article titles and section headings
            if re.match(r'^ARTICLE\s+\d+:', para_text, re.IGNORECASE):
                parts = para_text.split(':', 1)
                if len(parts) == 2:
                    title, content = parts
                    html += f'<p class="doc-paragraph"><span style="font-weight: bold;">{title}:</span>{content}</p>'
                else:
                    html += f'<p class="doc-paragraph" style="font-weight: bold;">{para_text}</p>'
            elif "WHEREAS:" in para_text:
                html += f'<p class="doc-paragraph"><span style="font-weight: bold;">WHEREAS:</span>{para_text[8:]}</p>'
            elif "NOW, THEREFORE" in para_text:
                html += f'<p class="doc-paragraph"><span style="font-weight: bold;">NOW, THEREFORE</span>{para_text[13:]}</p>'
            elif "IN WITNESS WHEREOF" in para_text:
                html += f'<p class="doc-paragraph"><span style="font-weight: bold;">IN WITNESS WHEREOF</span>{para_text[17:]}</p>'
            else:
                clean_text, is_bold = clean_markdown(para_text)
                if is_bold:
                    html += f'<p class="doc-paragraph" style="font-weight: bold;">{clean_text}</p>'
                else:
                    html += f'<p class="doc-paragraph">{clean_text}</p>'
    
    else:
        # Default formatting for other document types
        for para in content.split('\n\n'):
            if para.strip():
                html += f'<p class="doc-paragraph">{para.strip()}</p>'
    
    html += '</div>'
    return html


    
def show_main_content():
     # Get current theme
    is_dark_mode = True if st.get_option("theme.base") == "dark" else False

    # Choose logo based on theme
    if is_dark_mode:
        logo_path = "assets/smartclause_logo_light.png"
    else:
        logo_path = "assets/smartclause_logo_dark.png"

    st.image(logo_path, width=300)
    st.caption("Generate professional legal documents compliant with Kenyan law")

    if st.session_state.show_welcome:
        show_welcome_modal()
    show_help_section()
    
    # Display chat interface
    chat_container = st.container()
    
    with chat_container:
        for message in st.session_state.messages:
            if message["role"] == "user" or (message["role"] == "assistant" and not st.session_state.document_generated_successfully):
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])
    
    # If document is generated, display it separately
    if st.session_state.document_generated_successfully and st.session_state.current_document:
        st.markdown('<div class="document-preview">', unsafe_allow_html=True)
        st.markdown(format_document_html(st.session_state.current_document), unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
    
    # Display download buttons if applicable
    if st.session_state.show_download and st.session_state.current_document:
        show_download_buttons()

    # Chat input
    if prompt := st.chat_input("Describe your legal document needs, e.g., 'Draft a contract for...'"):
        if st.session_state.show_welcome:
            st.session_state.show_welcome = False
            
        with st.chat_message("user"):
            st.markdown(prompt)

        st.session_state.messages.append({"role": "user", "content": prompt})

        with st.chat_message("assistant"):
            response_placeholder = st.empty()
            
            # Set the flag before generation
            st.session_state.generation_in_progress = True
            
            try:
                with st.spinner("Generating your document..."):
                    preview, success = generate_document(st.session_state.generator, prompt)
                
                if success:
                    formatted_html = format_document_html(preview)
                    response_placeholder.markdown(formatted_html, unsafe_allow_html=True)
                else:
                    response_placeholder.markdown(preview)
            finally:
                # Reset the flag after generation, even if it fails
                st.session_state.generation_in_progress = False

            st.session_state.messages.append({"role": "assistant", "content": preview})
            st.session_state.document_generated_successfully = success
            st.session_state.show_payment = success
            if success:
                st.session_state.current_document = preview
                st.session_state.show_download = True
                st.session_state.force_sidebar_open = True
                show_download_buttons()
                st.rerun()  # Re-render to ensure sidebar is visible
def main():
    st.set_page_config(
        page_title="SmartClause",
        page_icon="assets/smartclause_badge.png",
        layout="wide"
    )
    
    init_session_state()
    

    st.markdown("""
    <style>
     .title-container {
        text-align: center;
    }
    
    /* Scoped payment card styling */
    div[data-testid="stSidebar"] .payment-card {
        position: relative !important;
        bottom: auto;
        left: auto;
        background-color: rgb(240, 240, 240);
        padding: 20px;
        border-radius: 10px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        width: 100%;
        margin-bottom: 20px;
    }
    
    /* Text protection styles */
    .stMarkdown, 
    .element-container div,
    .stChatMessage {
        -webkit-user-select: none !important;
        -moz-user-select: none !important;
        -ms-user-select: none !important;
        user-select: none !important;
    }
    
    .stMarkdown *, 
    .element-container div *,
    .stChatMessage * {
        -webkit-user-drag: none !important;
        -khtml-user-drag: none !important;
        -moz-user-drag: none !important;
        -o-user-drag: none !important;
        user-drag: none !important;
    }
    
    .stChatMessage div[data-testid="stMarkdownContainer"] {
        -webkit-user-select: none !important;
        -moz-user-select: none !important;
        -ms-user-select: none !important;
        user-select: none !important;
    }
    
    button[data-testid="StyledFullScreenButton"],
    .stMarkdown button {
        display: none !important;
    }
    
    /* Legal document styling */
    .legal-document {
        background-color: white;
        border: 1px solid #ddd;
        box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        padding: 30px !important;
        margin: 15px 0;
        border-radius: 8px;
                
    }
    
    .legal-document h1, .legal-document h2 {
        margin-bottom: 15px;
    }
    
    .legal-document p {
        text-align: justify;
    }
    
    .stChatMessage,
    .stChatMessageContent, 
    [data-testid="stChatMessageContent"],
    [data-testid="stMarkdownContainer"],
    .legal-document,
    .document-preview,
    .element-container,
    div[data-testid="stVerticalBlock"] > div {
        max-height: none !important;
        overflow: visible !important;
        height: auto !important;
    }

    /* Ensure full document visibility */
    .stMarkdown,
    .stChatMessage div[data-testid="stMarkdownContainer"] {
        max-height: none !important;
        overflow: visible !important;
    }

    /* Additional global overrides */
    * {
        max-height: none !important;
    }

    /* Specific document container styling */
    .legal-document {
        max-height: none !important;
        height: auto !important;
        overflow: visible !important;
        display: block;
        width: 100%;
    }
 
    </style>
    """, unsafe_allow_html=True)

    enhance_sidebar()
    show_main_content()

if __name__ == "__main__":
    main()
