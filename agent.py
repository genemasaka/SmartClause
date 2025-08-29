import re
import logging
from pathlib import Path
from typing import Dict, Tuple, Optional, Any
from dataclasses import dataclass
import json
from functools import lru_cache
import os
import time
import queue
import threading
from dotenv import load_dotenv

# OpenAI SDK
from openai import OpenAI
try:
    from openai import RateLimitError
except ImportError:
    class RateLimitError(Exception):
        pass

# Set up logging with a more detailed format
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@dataclass
class DocumentContext:
    """Structured container for document generation context"""
    draft_type: str
    context_data: Dict[str, str]

class TemplateManager:
    """Handles loading and caching of legal templates"""
    
    def __init__(self, template_path: Path):
        self.template_path = template_path
        self._templates: Dict = {}
        
    @lru_cache(maxsize=32)
    def get_template(self, template_type: str) -> Optional[str]:
        """Retrieve a cached template by type"""
        if not self._templates:
            self._load_templates()
        return self._templates.get(template_type, {}).get('template')
    
    def _load_templates(self) -> None:
        """Load templates from JSON file with error handling"""
        try:
            self._templates = json.loads(self.template_path.read_text(encoding='utf-8'))
            logger.info(f"Successfully loaded templates from {self.template_path}")
        except FileNotFoundError:
            logger.error(f"Template file not found: {self.template_path}")
            self._templates = {}
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON in template file: {self.template_path}")
            self._templates = {}

class RequestQueue:
    """
    Background queue for processing requests
    """
    def __init__(self, client: Optional[OpenAI] = None, model_name: Optional[str] = None):
        self.queue = queue.Queue()
        self.results = {}
        self.client = client
        self.model_name = model_name
        self.thread = threading.Thread(target=self._process_queue, daemon=True)
        self.thread.start()

    def _process_queue(self):
        while True:
            request_id, messages = self.queue.get()
            try:
                # Process with delay between requests
                time.sleep(2)  # Minimum 2 second delay between requests
                if not self.client or not self.model_name:
                    raise RuntimeError("OpenAI client/model not configured for RequestQueue")
                
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    max_tokens=7000,
                    temperature=0.25
                )
                self.results[request_id] = response
            except Exception as e:
                self.results[request_id] = e
            finally:
                self.queue.task_done()

    def add_request(self, request_id: str, messages: list):
        self.queue.put((request_id, messages))

    def get_result(self, request_id: str) -> Optional[Any]:
        return self.results.get(request_id)
    
# Consolidated document type detection: All affidavit variants fall under "affidavit"
DOCUMENT_TYPES = {
    "affidavit": ["affidavit", "sworn statement", "declaration", "oath", "ntsa", "name change", "name confirmation", "force transfer", "loss of number plate", "custody", "vehicle"],
    "contract": ["contract", "agreement", "deal", "partnership"],
    "other": ["letter", "notice", "memo", "document"]
}

@dataclass
class OpenAIModel:
    """Light adapter to carry OpenAI client + model name"""
    client: OpenAI
    model_name: str

class DocumentGenerator:
    """Handles document generation logic with enhanced rate limit handling and natural language detection"""
    
    def __init__(self, template_manager, model: OpenAIModel):
        self.template_manager = template_manager
        self.model = model  # OpenAIModel(client=<OpenAI>, model_name=str)
        self.logger = logging.getLogger(__name__)
        self.rate_limit_config = {
            'max_retries': 3,
            'initial_delay': 2,
            'max_delay': 32,
        }

    def detect_document_type(self, query: str) -> str:
        """Detect document type from natural language input, consolidating all affidavits"""
        query_lower = query.lower()
        
        # Check each document type's keywords
        for doc_type, keywords in DOCUMENT_TYPES.items():
            if any(keyword in query_lower for keyword in keywords):
                self.logger.info(f"Detected document type: {doc_type}")
                return doc_type
                
        # Default to 'other' if no specific type detected
        self.logger.info("No specific document type detected, defaulting to 'other'")
        return "other"

    def parse_query(self, query: str) -> DocumentContext:
        """Parse user query into structured context without requiring brackets"""
        draft_type = self.detect_document_type(query)
        
        # Extract context data using key-value patterns (e.g., "name: John")
        context_data = {
            key.strip().lower(): value.strip()
            for key, value in re.findall(r"(\w+):\s*(.*?)(?:\s|$)", query)
        }
        
        # If no explicit key-value pairs, use the whole query as context
        if not context_data:
            context_data["description"] = query.strip()
            
        self.logger.info(f"Parsed query - Type: {draft_type}, Context: {context_data}")
        return DocumentContext(draft_type=draft_type, context_data=context_data)

    def _exponential_backoff(self, attempt: int) -> float:
        """Calculate delay with exponential backoff."""
        delay = min(
            self.rate_limit_config['initial_delay'] * (2 ** attempt),
            self.rate_limit_config['max_delay']
        )
        return delay

    def _generate_ai_content(self, filled_template: str, query: str) -> Any:
        """
        Generate content using OpenAI Chat Completions API with retry logic.
        """
        messages = [
            {
                "role": "user",
                "content": self._create_prompt(filled_template, query)
            }
        ]

        # Allow env override; sensible defaults for long legal prose
        max_tokens = int(os.getenv("OPENAI_MAX_TOKENS", "4000"))
        temperature = float(os.getenv("OPENAI_TEMPERATURE", "0.25"))
        top_p = float(os.getenv("OPENAI_TOP_P", "0.9"))

        last_err: Optional[Exception] = None

        for attempt in range(self.rate_limit_config['max_retries']):
            try:
                response = self.model.client.chat.completions.create(
                    model=self.model.model_name,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                )
                if self._validate_response(response):
                    return response
                self.logger.warning(f"Invalid response format on attempt {attempt + 1}")
            except RateLimitError as e:
                last_err = e
                delay = self._exponential_backoff(attempt)
                self.logger.warning(
                    f"Rate limit hit on attempt {attempt + 1}. Retrying in {delay} seconds... Error: {str(e)}"
                )
                if attempt < self.rate_limit_config['max_retries'] - 1:
                    time.sleep(delay)
                    continue
                self.logger.error("Rate limit exceeded after all retries.")
            except Exception as e:
                last_err = e
                self.logger.error(f"Unexpected error during generation: {str(e)}")
                if attempt < self.rate_limit_config['max_retries'] - 1:
                    delay = self._exponential_backoff(attempt)
                    time.sleep(delay)
                    continue

        # If we got here, all attempts failed
        if last_err:
            raise ValueError("Failed to generate valid content after multiple attempts") from last_err
        raise ValueError("Failed to generate valid content after multiple attempts")

    def _validate_response(self, response: Any) -> bool:
        """Validate that the response has the expected structure for OpenAI Chat Completions API"""
        try:
            return (
                hasattr(response, "choices") and 
                len(response.choices) > 0 and
                hasattr(response.choices[0], "message") and
                hasattr(response.choices[0].message, "content") and
                response.choices[0].message.content and
                len(response.choices[0].message.content.strip()) > 0
            )
        except Exception as e:
            self.logger.error(f"Response validation failed: {e}")
            return False

    def _fill_template(self, template: str, context_data: Dict[str, str]) -> str:
        """Fill template with context data"""
        try:
            placeholders = re.findall(r'\{(.*?)\}', template)
            filled = template.format(**{
                k: context_data.get(k, f'[{k}]') 
                for k in placeholders
            })
            self.logger.debug("Template filled successfully")
            return filled
        except Exception as e:
            self.logger.error(f"Template filling failed: {e}")
            raise

    @staticmethod
    def _extract_response_text(response: Any) -> Optional[str]:
        """Extract text from OpenAI Chat Completions API result"""
        try:
            if hasattr(response, "choices") and len(response.choices) > 0:
                return response.choices[0].message.content
            return None
        except Exception as e:
            logging.error(f"Error extracting response text: {e}")
            return None

    @staticmethod
    def _create_prompt(filled_template: str, query: str) -> str:
        """Create AI prompt with template and query, enhanced for comprehensive document generation"""
        
        # Detect document type from query
        query_lower = query.lower()
        is_contract = any(keyword in query_lower for keyword in ["contract", "agreement", "deal", "partnership", "service", "supply"])
        is_affidavit = any(keyword in query_lower for keyword in ["affidavit", "sworn", "declaration", "oath", "ntsa", "name change"])
        
        if is_contract:
            return f"""
            You are Kenya's Leading Professional Legal Document Drafting AI Agent specializing in COMPREHENSIVE CONTRACTS.
            
            🎯 PRIMARY OBJECTIVE: Generate a detailed, legally compliant Kenyan contract of MINIMUM 3000 WORDS.
            
            📏 CRITICAL LENGTH REQUIREMENT: 
            - MINIMUM 3000 WORDS of substantive legal content
            - This is MANDATORY and NON-NEGOTIABLE
            - Write extensively with [[sensible]] detailed explanations
            
            🏗️ MANDATORY COMPREHENSIVE STRUCTURE (Write extensively for each):
            
            **1. PARTIES AND CAPACITY** (250+ words)
            - Full legal names, registration numbers, physical addresses
            - Corporate authority and capacity to contract
            - Representative authority and signing powers
            - Contact details and communication procedures
            
            **2. RECITALS AND BACKGROUND** (350+ words)  
            - Detailed business context and relationship history
            - Commercial objectives and strategic goals
            - Market conditions and industry context
            - Regulatory environment and compliance framework
            
            **3. DEFINITIONS AND INTERPRETATION** (500+ words)
            Include 30+ comprehensive definitions covering:
            - Technical and industry-specific terms
            - Commercial concepts and performance metrics
            - Legal procedures and compliance requirements
            - Communication and notification terms
            - Financial and payment-related concepts
            
            **4. SCOPE OF WORK/SERVICES/SUPPLY** (600+ words)
            - Detailed specifications and requirements
            - Deliverables with comprehensive acceptance criteria
            - Performance standards and quality metrics
            - Timeline requirements and milestone procedures
            - Change management and variation procedures
            - Resource allocation and responsibility matrix
            
            **5. CONSIDERATION AND PAYMENT TERMS** (400+ words)
            - Detailed payment schedules and methods
            - Comprehensive invoicing procedures
            - Late payment penalties and interest calculations
            - Price adjustment mechanisms and procedures
            - Currency provisions and exchange rate handling
            - Tax responsibilities and VAT procedures
            
            **6. TERM AND COMMENCEMENT** (250+ words)
            - Effective date and commencement procedures
            - Contract duration and milestone schedules
            - Renewal options and extension procedures
            - Early termination scenarios and procedures
            
            **7. PERFORMANCE OBLIGATIONS** (450+ words)
            - Detailed responsibilities of each party
            - Performance monitoring and reporting procedures
            - Key performance indicators and measurement
            - Quality assurance and control procedures
            - Remediation procedures for non-performance
            - Continuous improvement requirements
            
            **8. WARRANTIES AND REPRESENTATIONS** (300+ words)
            - Express warranties and performance guarantees
            - Fitness for purpose declarations
            - Compliance representations and ongoing obligations
            - Warranty period and remedy procedures
            
            **9. LIMITATION OF LIABILITY** (300+ words)
            - Comprehensive liability caps and exclusions
            - Consequential damage limitations
            - Carve-outs for gross negligence and fraud
            - Insurance coordination and coverage requirements
            
            **10. INDEMNIFICATION** (250+ words)
            - Mutual indemnification obligations
            - Defense and settlement procedures
            - Notice requirements and cooperation obligations
            - Cost allocation and expense procedures
            
            **11. INSURANCE AND RISK MANAGEMENT** (300+ words)
            - Required insurance coverage and limits
            - Certificate requirements and renewal procedures
            - Additional insured obligations
            - Risk assessment and mitigation procedures
            
            **12. INTELLECTUAL PROPERTY RIGHTS** (350+ words)
            - Pre-existing IP ownership and protection
            - Work product ownership and licensing
            - Infringement procedures and remedies
            - IP development and collaboration procedures
            
            **13. CONFIDENTIALITY AND DATA PROTECTION** (400+ words)
            - Comprehensive confidentiality obligations
            - Data protection compliance (Kenya DPA 2019)
            - Security measures and breach procedures
            - Cross-border data transfer restrictions
            
            **14. FORCE MAJEURE** (200+ words)
            - Comprehensive definition of force majeure events
            - Notification and documentation procedures
            - Mitigation and resumption protocols
            - Cost allocation during force majeure periods
            
            **15. COMPLIANCE AND REGULATORY** (300+ words)
            - Legal and regulatory compliance obligations
            - Audit rights and inspection procedures
            - Regulatory change adaptation procedures
            - Ethics and anti-corruption compliance
            
            **16. TERMINATION** (350+ words)
            - Comprehensive termination grounds (cause and convenience)
            - Notice periods and termination procedures
            - Effect of termination on ongoing obligations
            - Post-termination cooperation and transition
            
            **17. DISPUTE RESOLUTION** (350+ words)
            - Multi-tier dispute resolution procedures
            - Negotiation requirements and timeframes
            - Mediation procedures and institution selection
            - Arbitration rules and enforcement procedures
            
            **18. GOVERNING LAW AND JURISDICTION** (200+ words)
            - Choice of law provisions and rationale
            - Jurisdiction and venue selection
            - Service of process procedures
            - Judgment enforcement mechanisms
            
            **Plus Additional Clauses**: Amendment, Assignment, Notices, Entire Agreement, Severability, Counterparts
            
            📝 CONTENT EXPANSION STRATEGIES:
            1. Write detailed sub-clauses (a), (b), (c) under each main clause
            2. Include specific examples: "For example, in the event that..."
            3. Add step-by-step procedures for all obligations
            4. Explain commercial rationale for each provision
            5. Include comprehensive risk scenarios and management
            6. Add operational workflows and implementation details
            
            🇰🇪 KENYAN LEGAL COMPLIANCE:
            - Constitution of Kenya, 2010
            - Law of Contract Act (Cap. 23)
            - Companies Act, 2015
            - Data Protection Act, 2019
            - Consumer Protection Act, 2012
            - Employment Act, 2007
            
            🔧 INTELLIGENT DEFAULTS (when context is minimal):
            - Industry-standard commercial terms
            - Professional liability requirements
            - Standard dispute resolution procedures
            - Comprehensive compliance frameworks
            - Risk management provisions

            Important Instructions:
        1. Adhere exactly to template structure, but adapt content for query specifics.
        2. Ensure accurate spelling and grammar throughout the document.
        3. Output only the document content and disclaimer.
        4. Avoid any additional explanations, commentary, or sections.
        5. End immediately after the disclaimer.
        6. Always append this disclaimer:

        **IMPORTANT DISCLAIMER:** This document is a template and may not be suitable for all situations. You should consult with a qualified legal professional in Kenya to ensure that this document is appropriate for your specific circumstances and complies with all applicable laws and regulations. This document does not constitute legal advice.
            
            Base Template: {filled_template}
            User Query: {query}
            
            
            """
            
        elif is_affidavit:
            return f"""
            You are Kenya's Leading Affidavit Specialist with expert knowledge of sworn document requirements.
            
            🎯 OBJECTIVE: Generate a comprehensive Kenyan affidavit of MINIMUM 800 WORDS.
            
            📏 LENGTH REQUIREMENT: 800+ words of substantive sworn statements and legal content.
            
            🏛️ PROPER AFFIDAVIT STRUCTURE:
            
            **STANDARD HEADER**:
            REPUBLIC OF KENYA
            IN THE MATTER OF THE OATHS AND STATUTORY DECLARATIONS ACT
            (CAP 15 OF THE LAWS OF KENYA)
            IN THE MATTER OF [CUSTOMIZE BASED ON PURPOSE]
            AFFIDAVIT
            
            **DEPONENT INFORMATION** (100+ words):
            Complete personal details, addresses, capacity
            
            **COMPREHENSIVE SWORN STATEMENTS** (600+ words):
            8-15 detailed "THAT" statements customized for:
            
            - **Name Change**: Birth details, reasons, gazette requirements, non-fraudulent declarations
            - **NTSA/Vehicle**: Vehicle details, ownership proof, transfer reasons, compliance declarations  
            - **Loss Declaration**: Circumstances, police reports, recovery efforts, non-involvement declarations
            - **Custody**: Child welfare, relationship details, best interest factors, compliance obligations
            - **General**: Comprehensive factual statements relevant to the matter
            
            **PROPER CLOSING** (100+ words):
            Standard sworn format with commissioner details
            
            🇰🇪 LEGAL COMPLIANCE:
            - Oaths and Statutory Declarations Act (Cap. 15)
            - Relevant sectoral legislation
            - Proper legal format and procedures

        [[[[[Important Instructions:]]]]]
        1. Adhere exactly to template structure, but adapt content for query specifics.
        2. Ensure accurate spelling and grammar throughout the document.
        3. Output only the document content and disclaimer.
        4. Avoid any additional explanations, commentary, or sections.
        5. End immediately after the disclaimer.
        6. Always append this disclaimer:

        **IMPORTANT DISCLAIMER:** This document is a template and may not be suitable for all situations. You should consult with a qualified legal professional in Kenya to ensure that this document is appropriate for your specific circumstances and complies with all applicable laws and regulations. This document does not constitute legal advice.
            
            Base Template: {filled_template}
            User Query: {query}
            
            GENERATE: A comprehensive 800+ word affidavit with detailed sworn statements.
            """
            
        

    def generate_document(self, query: str) -> Optional[str]:
        """Generate document from query with natural language handling"""
        try:
            context = self.parse_query(query)
            template = self.template_manager.get_template(context.draft_type)
            
            if not template:
                self.logger.warning(f"No template found for type: {context.draft_type}, using default")
                template = self.template_manager.get_template("other")
                if not template:
                    raise ValueError(f"No default template available")
                    
            filled_template = self._fill_template(template, context.context_data)
            
            response = self._generate_ai_content(filled_template, query)
            if not response:
                self.logger.error("No response received from AI model")
                return None
                
            result = self._extract_response_text(response)
            if not result:
                self.logger.error("Failed to extract text from response")
                return None
                
            return result
            
        except Exception as e:
            self.logger.error(f"Document generation failed: {e}", exc_info=True)
            return None

def _validate_contract_length(self, content: str, min_words: int = 3000) -> tuple[bool, int]:
    """Validate contract meets minimum word count requirement"""
    # Remove common legal formatting to get accurate word count
    clean_content = content.replace("WHEREAS", "").replace("NOW THEREFORE", "")
    clean_content = re.sub(r'\n+', ' ', clean_content)
    clean_content = re.sub(r'\s+', ' ', clean_content.strip())
    
    words = clean_content.split()
    word_count = len(words)
    
    is_valid = word_count >= min_words
    self.logger.info(f"Contract validation - Word count: {word_count}, Required: {min_words}, Valid: {is_valid}")
    return is_valid, word_count

def setup_environment() -> Tuple[TemplateManager, OpenAIModel]:
    """Setup environment and dependencies (OpenAI version)"""
    # Load environment variables
    env_path = Path("./.env")
    if env_path.exists():
        load_dotenv(env_path)
    else:
        logger.warning(".env file not found, attempting to use existing environment variables")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY not found in environment variables")
        
    # Initialize OpenAI client
    client = OpenAI(api_key=api_key)
    
    # Initialize template manager and set model (pick a strong default)
    template_path = Path("./legal_templates.json")
    template_manager = TemplateManager(template_path)
    model = OpenAIModel(client=client, model_name=os.getenv("OPENAI_MODEL", "gpt-4"))
    
    logger.info("Environment setup completed successfully (OpenAI)")
    return template_manager, model

def main():
    """Main application entry point with improved error handling and user feedback"""
    try:
        print("\nKenyan Legal Document Generator")
        print("==============================\n")
        
        template_manager, model = setup_environment()
        generator = DocumentGenerator(template_manager, model)
        
        while True:
            try:
                print("\nEnter your query (or 'quit' to exit)")
                print("Format: Draft a [document_type] for... (e.g., Draft a [contract] for...)")
                query = input("\nQuery: ").strip()
                
                if query.lower() in ('quit', 'exit', 'q'):
                    print("\nThank you for using the Legal Document Generator!")
                    break
                    
                if not query:
                    print("\nError: Please enter a valid query")
                    continue
                
                print("\nGenerating document...")
                result = generator.generate_document(query)
                
                if result:
                    print("\nGenerated Document:")
                    print("==================\n")
                    print(result)
                else:
                    print("\nError: Unable to generate document. This could be due to:")
                    print("1. Invalid query format")
                    print("2. Unsupported document type")
                    print("3. AI model response error")
                    print("\nPlease check the logs for detailed information and try again.")
                
                print("\n" + "="*50 + "\n")
                
            except KeyboardInterrupt:
                print("\nOperation cancelled by user")
                break
                
    except Exception as e:
        logger.error("Application failed", exc_info=True)
        print(f"\nFatal Error: {str(e)}")
        print("Please check the logs for detailed information.")

if __name__ == "__main__":
    main()