# payment_verification.py
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
import streamlit as st

@dataclass
class PaymentStatus:
    document_id: str  # Added to track which document the payment is for
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
            return False
            
        attempts = 0
        while attempts < max_attempts:
            try:
                response = self.mpesa_handler.query_stk_push(checkout_request_id)
                
                # Check if payment was successful
                if response.get('ResultCode') == '0':
                    return True
                    
                # Check for specific failure codes
                elif response.get('ResultCode') in ['1032', '1037']:  # Transaction canceled/timeout
                    return False
                    
                # Wait before retrying
                time.sleep(delay)
                attempts += 1
                
            except Exception as e:
                print(f"Error verifying payment: {str(e)}")
                attempts += 1
                
        return False

def init_payment_state():
    """Initialize payment-related session state variables"""
    if 'payment_status' not in st.session_state:
        st.session_state.payment_status = None
    if 'payment_verified' not in st.session_state:
        st.session_state.payment_verified = False
    if 'current_document_id' not in st.session_state:
        st.session_state.current_document_id = None

def generate_document_id():
    """Generate a unique ID for a document"""
    return f"doc_{datetime.now().strftime('%Y%m%d%H%M%S')}_{hash(str(datetime.now()))}"

def reset_payment_state():
    """Reset payment state for new document"""
    st.session_state.payment_status = None
    st.session_state.payment_verified = False
    st.session_state.current_document_id = generate_document_id()

def update_payment_status(checkout_request_id: str, amount: float):
    """Update payment status in session state"""
    st.session_state.payment_status = PaymentStatus(
        document_id=st.session_state.current_document_id,
        checkout_request_id=checkout_request_id,
        amount=amount,
        timestamp=datetime.now()
    )
    st.session_state.payment_verified = False

def handle_download_request(payment_verifier: PaymentVerification) -> bool:
    """
    Handle document download request with payment verification
    Returns True if download should be allowed, False otherwise
    """
    if not st.session_state.current_document_id:
        st.error("Document session expired. Please regenerate the document")
        return False
        
    if st.session_state.payment_verified and st.session_state.payment_status and \
       st.session_state.payment_status.document_id == st.session_state.current_document_id:
        return True
        
    if not st.session_state.payment_status:
        st.error("Please complete payment before downloading")
        return False
        
    # Check if payment is too old (e.g., 30 minutes)
    payment_age = datetime.now() - st.session_state.payment_status.timestamp
    if payment_age > timedelta(minutes=30):
        st.error("Payment session expired. Please make a new payment")
        st.session_state.payment_status = None
        return False
    
    # Verify payment
    with st.spinner("Verifying payment..."):
        if payment_verifier.verify_payment(
            st.session_state.payment_status.checkout_request_id,
            st.session_state.current_document_id
        ):
            st.session_state.payment_verified = True
            st.success("Payment verified successfully!")
            return True
        else:
            st.error("Payment verification failed. Please ensure you have completed the payment")
            return False