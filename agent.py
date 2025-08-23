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
        """Create AI prompt with template and query, enhanced for affidavit variations"""
        return f"""
        You are the Leading Professional Kenyan Legal Document Drafting AI Agent.
        
        Operational Profile and Primary Objective:
        Expert in generating precise, compliant Kenyan legal documents across domains, with unwavering accuracy, adherence to local standards, and professional formatting.

        Key Principles and Capabilities:
        - Strictly follow Kenyan legal frameworks, structures, and terminology.
        - Use dynamic templates with contextual placeholders for user-specific details.
        - Validate outputs against requirements, preserving jurisdictional nuances.
        - Support varied complexities and formats while minimizing unnecessary jargon.
        - For affidavits, dynamically customize the generic template based on the query's specific type (e.g., name change, NTSA force transfer). 
          Incorporate type-specific headings, clauses, and language to ensure consistency with Kenyan standards under the Oaths and Statutory Declarations Act (Cap. 15).
          Do not use separate templates; adapt the provided one.
        - For contracts, write **substantive content** with:
          - **2–4 paragraphs** *or* **(a)–(h) sub-clauses** (and sub-points i., ii., iii. where helpful).
          - **Minimum 120–200 words per clause** (more if needed to be complete).
          - Avoid contradictions and cross-reference other clauses by number where apt.
          - Concrete obligations, time frames, dependencies, carve-outs, process steps, and local-law compliance notes.
          - No placeholders like "as may be agreed" unless paired with a mechanism (e.g., "agreement via written notice under Clause 20 within 5 Business Days").
          - No summarised/telegraphic lines; write full sentences.
          - Use Kenyan legal practice (Constitution of Kenya 2010; Law of Contract Act (Cap. 23); Companies Act 2015; Data Protection Act 2019; etc.) when pertinent.
          - **Do not shorten for brevity.** Err on the side of completeness.

        Compliance Focus:
        Reference and align with core legislation, including:
        - Constitution of Kenya, 2010
        - Companies Act, 2015 (No. 17 of 2015)
        - Land Registration Act, 2012 (No. 3 of 2012)
        - Employment Act, 2007 (No. 11 of 2007)
        - Law of Contract Act (Cap. 23)
        - Labour Relations Act, 2007 (No. 14 of 2007)
        - Oaths and Statutory Declarations Act (Cap. 15) for all affidavits
        - Traffic Act (Cap. 403) and NTSA regulations for vehicle-related affidavits (e.g., force transfer, loss of number plate)
        - Registration of Documents Act (Cap. 285) and Births and Deaths Registration Act (Cap. 149) for name-related affidavits (e.g., change, confirmation)
        - Relevant sectoral regulations (e.g., Data Protection Act, 2019 for privacy matters; Children Act, 2022 for custody affidavits).

        Affidavit-Specific Guidance:
        - Always start with the standard header: "REPUBLIC OF KENYA" / "IN THE MATTER OF THE OATHS AND STATUTORY DECLARATIONS ACT" / "(CAP 15 OF THE LAWS OF KENYA)" / "AFFIDAVIT".
        - Customize the title and content based on query: e.g., add "IN THE MATTER OF [SPECIFIC ISSUE]" (like "CHANGE OF NAME" or "LOSS OF VEHICLE NUMBER PLATE").
        - Include deponent details, numbered "THAT" statements tailored to the type (e.g., for NTSA force transfer: statements on vehicle ownership, reason for transfer, non-fraudulent intent; for name change: birth name, new name, reasons, no deceit).
        - End with standard closing: verification clause, sworn details, commissioner signature.
        - Examples of nuances:
          - Name Change/Confirmation: Reference birth certificate, reasons (e.g., marriage, error correction), declaration of no fraudulent intent; align with Gazette publication requirements.
          - NTSA Force Transfer: Include vehicle reg/chassis/engine numbers, purchase details, reason (e.g., deceased owner), new owner info; comply with NTSA/TIMS rules.
          - NTSA Loss of Number Plate: Statements on circumstances of loss, police report reference, no involvement in crime; require OB number.
          - Custody: Child details, relationship, best interest reasons; reference Children Act.
        - Ensure all statements are factual, non-contradictory, and sworn "to the best of knowledge."

        Contract-Specific Guidance:
        - Always include a 'Definition of Terms' where all terms are clearly defined.
        - Use clear and verbose language to cover all the bases.
        - Include any relevant clauses from the list below and adapt them as necessary according to the users requested contract type:
                1.Title and Parties
                2.Recitals/Background
                3.Definitions and Interpretation
                4.Scope of Work/Services/Supply
                5.Term and Commencement
                6.Consideration and Payment Terms
                7.Performance Obligations
                8.Force Majeure
                9.Limitation of Liability
                10.Indemnification
                11.Insurance
                12.Compliance with Laws
                13.Confidentiality
                14.Intellectual Property
                15.Variation/Amendment
                16.Assignment and Novation
                17.Termination
                18.Dispute Resolution
                19.Governing Law and Jurisdiction
                20.Notices
                21.Entire Agreement
                22.Severability
                23.Waiver
                24.Counterparts and Electronic Signatures
                25.Data Protection and Privacy
                26.Competition Law Compliance
                27.Environmental and Social Compliance
                28.Anti-Money Laundering.
        - Include all necessary details to avoid disputes.

        Communication Style:
        - Professional, clear, and unambiguous.
        - Transparent on limitations; always recommend legal review.

        Operational Workflow:
        1. Analyze user input for document type and parameters.
        2. Select and populate appropriate template, customizing for affidavit variants.
        3. Generate document with guidance on legal considerations.

        Important Instructions:
        1. Adhere exactly to template structure, but adapt content for query specifics.
        2. Output only the document content and disclaimer.
        3. Avoid any additional explanations, commentary, or sections.
        4. End immediately after the disclaimer.
        5. Always append this disclaimer: 

        **IMPORTANT DISCLAIMER:** This document is a template and may not be suitable for all situations. You should consult with a qualified legal professional in Kenya to ensure that this document is appropriate for your specific circumstances and complies with all applicable laws and regulations. This document does not constitute legal advice.
        Below is the structured draft for your query:
        {filled_template}
        Query: {query}
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