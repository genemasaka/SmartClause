import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
import streamlit as st
import logging
import json
import os

# Configure structured logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter(
    '{"timestamp": "%(asctime)s", "name": "%(name)s", "level": "%(levelname)s", "message": %(message)s}'
))
logger.handlers = [handler]

@dataclass
class PaymentStatus:
    document_id: str
    checkout_request_id: str
    amount: float
    timestamp: datetime
    verified: bool = False
    attempts: int = 0

class PaymentVerification:
    def __init__(self, mpesa_handler):
        self.mpesa_handler = mpesa_handler
        
    def verify_payment(self, checkout_request_id: str, document_id: str, max_attempts: int = 5, delay: int = 5) -> bool:
        """
        Verify payment status with retries
        Returns True if payment is successful and matches current document, False otherwise
        """
        if not st.session_state.payment_status or st.session_state.payment_status.document_id != document_id:
            logger.warning('{"event": "verify_payment_invalid_session", "document_id": "%s"}', document_id)
            return False
            
        attempts = 0
        while attempts < max_attempts:
            try:
                response = self.mpesa_handler.query_stk_push(checkout_request_id)
                
                if response.get('ResultCode') == '0':
                    logger.info('{"event": "payment_verified", "document_id": "%s", "checkout_request_id": "%s"}', 
                                document_id, checkout_request_id)
                    return True
                    
                elif response.get('ResultCode') in ['1032', '1037']:
                    logger.info('{"event": "payment_failed", "document_id": "%s", "checkout_request_id": "%s", "result_code": "%s"}', 
                                document_id, checkout_request_id, response.get('ResultCode'))
                    return False
                    
                time.sleep(delay)
                attempts += 1
                
            except Exception as e:
                logger.error('{"event": "verify_payment_error", "document_id": "%s", "checkout_request_id": "%s", "attempt": %d, "error": "%s"}', 
                             document_id, checkout_request_id, attempts, str(e), exc_info=True)
                attempts += 1
                
        logger.info('{"event": "verify_payment_max_attempts", "document_id": "%s", "checkout_request_id": "%s"}', 
                    document_id, checkout_request_id)
        return False

def init_payment_state():
    """Initialize payment-related session state variables"""
    logger.info('{"event": "init_payment_state", "status": "success"}')
    if 'payment_status' not in st.session_state:
        st.session_state.payment_status = None
    if 'payment_verified' not in st.session_state:
        st.session_state.payment_verified = False
    if 'current_document_id' not in st.session_state:
        st.session_state.current_document_id = None

def generate_document_id():
    """Generate a unique and secure ID for a document"""
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    random_component = os.urandom(8).hex()
    doc_id = f"doc_{timestamp}_{random_component}"
    logger.info('{"event": "document_id_generated", "document_id": "%s"}', doc_id)
    return doc_id

def reset_payment_state():
    """Reset payment state for new document"""
    logger.info('{"event": "reset_payment_state", "status": "success"}')
    st.session_state.payment_status = None
    st.session_state.payment_verified = False
    st.session_state.current_document_id = generate_document_id()

def update_payment_status(checkout_request_id: str, amount: float):
    """Update payment status in session state"""
    document_id = st.session_state.current_document_id
    st.session_state.payment_status = PaymentStatus(
        document_id=document_id,
        checkout_request_id=checkout_request_id,
        amount=amount,
        timestamp=datetime.now()
    )
    st.session_state.payment_verified = False
    logger.info('{"event": "payment_status_updated", "document_id": "%s", "checkout_request_id": "%s", "amount": %f}', 
                document_id, checkout_request_id, amount)

def handle_download_request(payment_verifier: PaymentVerification) -> bool:
    """
    Handle document download request with payment verification
    Returns True if download should be allowed, False otherwise
    """
    document_id = st.session_state.current_document_id
    if not document_id:
        logger.warning('{"event": "download_request_no_document", "status": "failed"}')
        st.error("Document session expired. Please regenerate the document. If this issue persists, please report it using the Feedback tab in the Help Guide.")
        return False
        
    if st.session_state.payment_verified and st.session_state.payment_status and \
       st.session_state.payment_status.document_id == document_id:
        logger.info('{"event": "download_request_verified", "document_id": "%s"}', document_id)
        return True
        
    if not st.session_state.payment_status:
        logger.warning('{"event": "download_request_no_payment", "document_id": "%s"}', document_id)
        st.error("Please complete payment before downloading. If this issue persists, please report it using the Feedback tab in the Help Guide.")
        return False
        
    payment_age = datetime.now() - st.session_state.payment_status.timestamp
    if payment_age > timedelta(minutes=30):
        logger.warning('{"event": "download_request_expired", "document_id": "%s", "payment_age_minutes": %f}', 
                       document_id, payment_age.total_seconds() / 60)
        st.error("Payment session expired. Please make a new payment. If this issue persists, please report it using the Feedback tab in the Help Guide.")
        st.session_state.payment_status = None
        return False
    
    with st.spinner("Verifying payment..."):
        try:
            if payment_verifier.verify_payment(
                st.session_state.payment_status.checkout_request_id,
                document_id
            ):
                st.session_state.payment_verified = True
                st.success("Payment verified successfully!")
                logger.info('{"event": "download_request_success", "document_id": "%s"}', document_id)
                return True
            else:
                logger.warning('{"event": "download_request_verification_failed", "document_id": "%s"}', document_id)
                st.error("Payment verification failed. Please ensure you have completed the payment. If this issue persists, please report it using the Feedback tab in the Help Guide.")
                return False
        except Exception as e:
            logger.error('{"event": "download_request_error", "document_id": "%s", "error": "%s"}', 
                         document_id, str(e), exc_info=True)
            st.error("An error occurred during payment verification. Please try again later. If this issue persists, please report it using the Feedback tab in the Help Guide.")
            return False