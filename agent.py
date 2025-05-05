import re
import logging
from pathlib import Path
from typing import Dict, Tuple, Optional
from dataclasses import dataclass
import json
from functools import lru_cache
import os
import time
import queue
import threading
from google.api_core.exceptions import ResourceExhausted
import google.generativeai as genai
from dotenv import load_dotenv

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
    def __init__(self):
        self.queue = queue.Queue()
        self.results = {}
        self.thread = threading.Thread(target=self._process_queue, daemon=True)
        self.thread.start()

    def _process_queue(self):
        while True:
            request_id, prompt = self.queue.get()
            try:
                # Process with delay between requests
                time.sleep(2)  # Minimum 2 second delay between requests
                response = self.model.generate_content(prompt)
                self.results[request_id] = response
            except Exception as e:
                self.results[request_id] = e
            finally:
                self.queue.task_done()

    def add_request(self, request_id: str, prompt: str):
        self.queue.put((request_id, prompt))

    def get_result(self, request_id: str) -> Optional[str]:
        return self.results.get(request_id)
    
# Add document type detection keywords with specific affidavit types first
DOCUMENT_TYPES = {
    "affidavit_ntsa": ["ntsa affidavit", "vehicle affidavit", "tims affidavit"],
    "affidavit_name_change": ["name change affidavit", "change of name affidavit"],
    "affidavit_custody": ["custody affidavit", "child custody affidavit"],
    "affidavit_force_transfer": ["force transfer affidavit", "vehicle transfer affidavit"],
    "affidavit": ["affidavit", "sworn statement", "declaration", "oath"],
    "contract": ["contract", "agreement", "deal", "partnership"],
    "other": ["letter", "notice", "memo", "document"]
}

class DocumentGenerator:
    """Handles document generation logic with enhanced rate limit handling and natural language detection"""
    
    def __init__(self, template_manager, model: genai.GenerativeModel):
        self.template_manager = template_manager
        self.model = model
        self.logger = logging.getLogger(__name__)
        self.rate_limit_config = {
            'max_retries': 3,
            'initial_delay': 2,
            'max_delay': 32,
        }

    def detect_document_type(self, query: str) -> str:
        """Detect document type from natural language input"""
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

    def _generate_ai_content(self, filled_template: str, query: str) -> Optional[genai.types.GenerateContentResponse]:
        """Generate content using AI model with enhanced retry logic"""
        prompt = self._create_prompt(filled_template, query)
        
        for attempt in range(self.rate_limit_config['max_retries']):
            try:
                response = self.model.generate_content(
                    prompt,
                    generation_config={
                        'temperature': 0.7,
                        'top_p': 0.8,
                        'top_k': 40,
                        'max_output_tokens': 2048,
                    }
                )
                
                if self._validate_response(response):
                    return response
                    
                self.logger.warning(f"Invalid response format on attempt {attempt + 1}")
                
            except ResourceExhausted as e:
                delay = self._exponential_backoff(attempt)
                self.logger.warning(
                    f"Rate limit hit on attempt {attempt + 1}. "
                    f"Retrying in {delay} seconds... Error: {str(e)}"
                )
                
                if attempt < self.rate_limit_config['max_retries'] - 1:
                    time.sleep(delay)
                    continue
                    
                self.logger.error(
                    "Rate limit exceeded after all retries. "
                    "Consider implementing rate limiting or request queuing."
                )
                raise
                
            except Exception as e:
                self.logger.error(f"Unexpected error during generation: {str(e)}")
                if attempt == self.rate_limit_config['max_retries'] - 1:
                    raise
                    
        raise ValueError("Failed to generate valid content after multiple attempts")

    def _validate_response(self, response) -> bool:
        """Validate that the response has the expected structure"""
        try:
            return (
                hasattr(response, 'candidates') and
                response.candidates and
                hasattr(response.candidates[0], 'content') and
                hasattr(response.candidates[0].content, 'parts') and
                response.candidates[0].content.parts and
                hasattr(response.candidates[0].content.parts[0], 'text')
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
    def _extract_response_text(response: genai.types.GenerateContentResponse) -> Optional[str]:
        """Extract text from AI response with improved error handling"""
        try:
            if not response.candidates:
                return None
                
            candidate = response.candidates[0]
            if not hasattr(candidate, 'content') or not candidate.content.parts:
                return None
                
            text = candidate.content.parts[0].text
            if not text:
                return None
                
            return text
            
        except Exception as e:
            logging.error(f"Error extracting response text: {e}")
            return None

    @staticmethod
    def _create_prompt(filled_template: str, query: str) -> str:
        """Create AI prompt with template and query"""
        return f"""
         You are a professional Kenyan Legal Document Drafting AI Agent

        Operational Profile: Kenyan Legal Document Drafting AI Agent

        Primary Objective:
        Comprehensive generation of high-quality, legally precise Kenyan legal documents across multiple domains, ensuring accuracy, compliance with local legal standards, and professional formatting.

        Key Operational Principles:
        - Strict adherence to Kenyan legal frameworks
        - Precise document structure following local legal conventions
        - Incorporation of relevant legal terminology
        - Comprehensive placeholder mechanism for context-specific details
        - Ability to generate documents across various complexity levels

        Technical Capabilities:
        - Dynamic template-based document generation
        - Contextual placeholder replacement
        - Validation against standard legal document requirements
        - Support for multiple document formats
        - Preservation of legal nuance and jurisdictional specificity

        Compliance and Accuracy Focus:
        - Reference key legislation:
        * Companies Act
        * Land Registration Act
        * Employment Act
        * Contract Law principles
        * Kenyan Constitution
        * Specific sectoral regulations

        Communication Style:
        - Professional and precise
        - Clear, unambiguous language
        - Contextually appropriate legal tone
        - Minimum use of unnecessary legal jargon
        - Transparent about document limitations and recommended legal review

        Operational Workflow:
        1. Receive detailed user input
        2. Identify appropriate document type
        3. Extract specific context parameters
        4. Generate document using predefined templates
        5. Provide contextual guidance and potential legal considerations

        
   
            IMPORTANT INSTRUCTIONS:
            1. Follow the exact structure of the template provided
            2. Include ONLY the document content and a brief disclaimer
            3. Do NOT add any explanations, improvements, or commentary after the disclaimer
            4. Do NOT include any sections about "Key Improvements", "Explanations", or similar
            5. End the document immediately after the disclaimer
            6. Always include this exact disclaimer at the end:

            **IMPORTANT DISCLAIMER:** This document is a template and may not be suitable for all situations. You should consult with a qualified legal professional in Kenya to ensure that this document is appropriate for your specific circumstances and complies with all applicable laws and regulations. This document does not constitute legal advice.


        Below is the structured draft for your query::
        
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

def setup_environment() -> Tuple[TemplateManager, genai.GenerativeModel]:
    """Setup environment and dependencies"""
    # Load environment variables
    env_path = Path("./.env")
    if env_path.exists():
        load_dotenv(env_path)
    else:
        logger.warning(".env file not found, attempting to use existing environment variables")

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY not found in environment variables")
        
    # Configure Gemini API
    genai.configure(api_key=api_key)
    
    # Initialize template manager and model
    template_path = Path("./legal_templates.json")
    template_manager = TemplateManager(template_path)
    model = genai.GenerativeModel("gemini-1.5-pro")
    
    logger.info("Environment setup completed successfully")
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