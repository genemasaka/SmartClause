import pytest
from unittest.mock import patch, MagicMock
from payment_verification import PaymentVerification, PaymentStatus, generate_document_id, reset_payment_state, update_payment_status
from datetime import datetime, timedelta
import streamlit as st

# Mock Streamlit session state
class MockSessionState:
    def __init__(self):
        self.payment_status = None
        self.payment_verified = False
        self.current_document_id = None

st.session_state = MockSessionState()

# Mock MpesaHandler
class MockMpesaHandler:
    def query_stk_push(self, checkout_request_id):
        return {"ResultCode": "0"}  # Default to successful payment

# Test fixtures
@pytest.fixture
def payment_verifier():
    mpesa_handler = MockMpesaHandler()
    return PaymentVerification(mpesa_handler)

@pytest.fixture
def payment_status():
    return PaymentStatus(
        document_id="doc_20250101000000_abcdef123456",
        checkout_request_id="ws_CO_123456789",
        amount=100.0,
        timestamp=datetime.now()
    )

# Custom test reporter for colorful output
def pytest_report_teststatus(report):
    if report.when == "call":
        if report.passed:
            return "passed", "PASSED", "✅ \033[32mPASSED\033[0m"  # Green text with emoji
        elif report.failed:
            return "failed", "FAILED", "❌ \033[31mFAILED\033[0m"  # Red text with emoji
        elif report.skipped:
            return "skipped", "SKIPPED", "⏭️ \033[33mSKIPPED\033[0m"  # Yellow text with emoji
    return None

# Test cases with decorated names
def test_verify_payment_success(payment_verifier, payment_status):
    """✅ Test: Verify Payment Success"""
    st.session_state.payment_status = payment_status
    st.session_state.current_document_id = payment_status.document_id
    assert payment_verifier.verify_payment(payment_status.checkout_request_id, payment_status.document_id) == True

def test_verify_payment_failure(payment_verifier, payment_status):
    """❌ Test: Verify Payment Failure"""
    with patch.object(payment_verifier.mpesa_handler, 'query_stk_push', return_value={"ResultCode": "1032"}):
        st.session_state.payment_status = payment_status
        st.session_state.current_document_id = payment_status.document_id
        assert payment_verifier.verify_payment(payment_status.checkout_request_id, payment_status.document_id) == False

def test_generate_document_id():
    """✅ Test: Generate Document ID"""
    doc_id = generate_document_id()
    assert doc_id.startswith("doc_")
    assert len(doc_id) > 20  # Ensure it's sufficiently unique

def test_reset_payment_state():
    """✅ Test: Reset Payment State"""
    reset_payment_state()
    assert st.session_state.payment_status is None
    assert st.session_state.payment_verified == False
    assert st.session_state.current_document_id is not None

# Custom summary reporter
def pytest_terminal_summary(terminalreporter):
    terminalreporter.write_sep("=", "📊 \033[1mTest Summary\033[0m", green=True)
    terminalreporter.write(f"  🌟 Total Tests: {terminalreporter._session.testscollected}\n")
    terminalreporter.write(f"  ✅ Passed: {len(terminalreporter.stats.get('passed', []))} \033[32mPASSED\033[0m\n")
    terminalreporter.write(f"  ❌ Failed: {len(terminalreporter.stats.get('failed', []))} \033[31mFAILED\033[0m\n")
    terminalreporter.write(f"  ⏭️ Skipped: {len(terminalreporter.stats.get('skipped', []))} \033[33mSKIPPED\033[0m\n")
    terminalreporter.write_sep("=", "🏁 \033[1mTest Run Complete\033[0m", green=True)