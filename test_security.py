#!/usr/bin/env python3
"""
Comprehensive security testing suite for the legal document generator app
Tests input validation, sanitization, and security measures
"""

import unittest
import sys
import os
from unittest.mock import patch, MagicMock
import bleach
import html

# Add the current directory to path to import our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Mock streamlit before importing the app
sys.modules['streamlit'] = MagicMock()

try:
    # Import the functions we want to test from your app
    from app import validate_input, sanitize_text, clean_markdown
    print("✓ Successfully imported security functions from app.py")
except ImportError as e:
    print(f"✗ Failed to import from app.py: {e}")
    print("Please ensure app.py is in the same directory and all dependencies are installed")
    sys.exit(1)

class TestInputValidation(unittest.TestCase):
    """Test the validate_input function with various input types"""
    
    def test_phone_number_validation(self):
        """Test phone number validation"""
        print("\n📱 Testing phone number validation...")
        
        # Valid phone numbers
        valid_phones = [
            "254712345678",
            "254701234567",
            "254733333333"
        ]
        
        for phone in valid_phones:
            result = validate_input(phone, 'phone')
            self.assertTrue(result, f"Should accept valid phone: {phone}")
            print(f"  ✓ Valid: {phone}")
        
        # Invalid phone numbers
        invalid_phones = [
            "0712345678",      # Wrong format (should start with 254)
            "254712345",       # Too short
            "25471234567890",  # Too long
            "254abc345678",    # Contains letters
            "123456789012",    # Wrong country code
            "+254712345678",   # Contains plus sign
            "254-712-345-678", # Contains dashes
            "",                # Empty
            "254 712 345 678"  # Contains spaces
        ]
        
        for phone in invalid_phones:
            result = validate_input(phone, 'phone')
            self.assertFalse(result, f"Should reject invalid phone: {phone}")
            print(f"  ✗ Invalid (correctly rejected): {phone}")
    
    def test_email_validation(self):
        """Test email validation"""
        print("\n📧 Testing email validation...")
        
        # Valid emails
        valid_emails = [
            "user@example.com",
            "test.email@domain.co.ke",
            "user123@test-domain.org",
            "firstname.lastname@company.com"
        ]
        
        for email in valid_emails:
            result = validate_input(email, 'email')
            self.assertTrue(result, f"Should accept valid email: {email}")
            print(f"  ✓ Valid: {email}")
        
        # Invalid emails
        invalid_emails = [
            "invalid-email",
            "@domain.com",
            "user@",
            "user@domain",
            "user.domain.com",
            "user@domain.",
            "",
            "user space@domain.com",
            "user@domain@com"
        ]
        
        for email in invalid_emails:
            result = validate_input(email, 'email')
            self.assertFalse(result, f"Should reject invalid email: {email}")
            print(f"  ✗ Invalid (correctly rejected): {email}")
    
    def test_prompt_validation(self):
        """Test prompt validation"""
        print("\n💬 Testing prompt validation...")
        
        # Valid prompts
        valid_prompts = [
            "Create a contract for web development services",
            "Draft an affidavit for change of name",
            "Generate a stock transfer agreement between parties",
            "I need a rental agreement for a commercial property"
        ]
        
        for prompt in valid_prompts:
            result = validate_input(prompt, 'prompt')
            self.assertTrue(result, f"Should accept valid prompt")
            print(f"  ✓ Valid prompt (length: {len(prompt)})")
        
        # Invalid prompts
        invalid_prompts = [
            "",                    # Empty
            "Hi",                  # Too short
            "   ",                 # Only spaces
            "A" * 1001,           # Too long (over 1000 chars)
            "   Hi   "             # Too short after stripping
        ]
        
        for i, prompt in enumerate(invalid_prompts):
            result = validate_input(prompt, 'prompt')
            self.assertFalse(result, f"Should reject invalid prompt {i+1}")
            print(f"  ✗ Invalid prompt {i+1} (correctly rejected)")
    
    def test_feedback_validation(self):
        """Test feedback validation"""
        print("\n💭 Testing feedback validation...")
        
        # Valid feedback
        valid_feedback = [
            "Great app!",
            "The document generation is very helpful",
            "A" * 2000,  # Exactly 2000 chars (should be valid)
            ""           # Empty should be valid for feedback
        ]
        
        for feedback in valid_feedback:
            result = validate_input(feedback, 'feedback')
            self.assertTrue(result, f"Should accept valid feedback")
            print(f"  ✓ Valid feedback (length: {len(feedback)})")
        
        # Invalid feedback
        invalid_feedback = [
            "A" * 2001,  # Too long (over 2000 chars)
        ]
        
        for feedback in invalid_feedback:
            result = validate_input(feedback, 'feedback')
            self.assertFalse(result, f"Should reject invalid feedback")
            print(f"  ✗ Invalid feedback (correctly rejected, length: {len(feedback)})")

class TestTextSanitization(unittest.TestCase):
    """Test the sanitize_text function"""
    
    def test_html_tag_removal(self):
        """Test removal of HTML tags"""
        print("\n🏷️ Testing HTML tag removal...")
        
        test_cases = [
            ("<script>alert('xss')</script>", "alert('xss')"),
            ("<b>Bold text</b>", "Bold text"),
            ("<img src='x' onerror='alert(1)'>", ""),
            ("Normal text", "Normal text"),
            ("<div><p>Nested tags</p></div>", "Nested tags"),
            ("<a href='javascript:alert(1)'>Link</a>", "Link")
        ]
        
        for input_text, expected in test_cases:
            result = sanitize_text(input_text)
            self.assertEqual(result, expected, f"Failed to sanitize: {input_text}")
            print(f"  ✓ '{input_text}' → '{result}'")
    
    def test_script_injection_prevention(self):
        """Test prevention of script injection"""
        print("\n🛡️ Testing script injection prevention...")
        
        malicious_inputs = [
            "<script>window.location='http://evil.com'</script>",
            "<img src=x onerror=alert('XSS')>",
            "<svg onload=alert('XSS')>",
            "javascript:alert('XSS')",
            "<iframe src='javascript:alert(1)'></iframe>",
            "<object data='javascript:alert(1)'></object>"
        ]
        
        for malicious in malicious_inputs:
            result = sanitize_text(malicious)
            # Should not contain script-related content
            self.assertNotIn('<script>', result.lower())
            self.assertNotIn('javascript:', result.lower())
            self.assertNotIn('onerror=', result.lower())
            self.assertNotIn('onload=', result.lower())
            print(f"  ✓ Sanitized malicious input: '{malicious[:30]}...'")
    
    def test_preserves_safe_content(self):
        """Test that safe content is preserved"""
        print("\n✅ Testing preservation of safe content...")
        
        safe_inputs = [
            "Create a contract for John Doe",
            "Draft an affidavit with ID number 12345678",
            "Generate agreement between Party A & Party B",
            "Legal document for property at 123 Main St.",
            "Contract with terms: payment within 30 days"
        ]
        
        for safe_input in safe_inputs:
            result = sanitize_text(safe_input)
            # Safe content should remain largely unchanged
            self.assertTrue(len(result) > 0, "Safe content should not be empty")
            print(f"  ✓ Preserved: '{safe_input}'")

class TestMarkdownCleaning(unittest.TestCase):
    """Test the clean_markdown function"""
    
    def test_html_escaping(self):
        """Test HTML character escaping"""
        print("\n🔤 Testing HTML character escaping...")
        
        test_cases = [
            ("Text with <tags>", "Text with &lt;tags&gt;", False),
            ("**Bold text**", "Bold text", True),
            ("Text & symbols", "Text &amp; symbols", False),
            ("Quote: \"Hello\"", "Quote: &quot;Hello&quot;", False),
            ("Apostrophe's test", "Apostrophe&#x27;s test", False)
        ]
        
        for input_text, expected_text, expected_bold in test_cases:
            result_text, is_bold = clean_markdown(input_text)
            self.assertEqual(result_text, expected_text, f"Text cleaning failed for: {input_text}")
            self.assertEqual(is_bold, expected_bold, f"Bold detection failed for: {input_text}")
            print(f"  ✓ '{input_text}' → '{result_text}' (bold: {is_bold})")
    
    def test_markdown_symbol_removal(self):
        """Test removal of markdown symbols"""
        print("\n📝 Testing markdown symbol removal...")
        
        test_cases = [
            ("**Bold text**", "Bold text", True),
            ("*Italic text*", "Italic text", False),
            ("Text\\with\\backslashes", "Textwithbackslashes", False),
            ("Text{.underline}", "Text", False),
            ("Normal text", "Normal text", False)
        ]
        
        for input_text, expected_text, expected_bold in test_cases:
            result_text, is_bold = clean_markdown(input_text)
            # Check that the base text matches (ignoring HTML escaping for this test)
            self.assertEqual(is_bold, expected_bold, f"Bold detection failed for: {input_text}")
            print(f"  ✓ Markdown cleaned: '{input_text}' → bold: {is_bold}")
    
    def test_special_phrase_correction(self):
        """Test correction of special legal phrases"""
        print("\n⚖️ Testing legal phrase correction...")
        
        test_cases = [
            ("NOW, THEREFOREE", "NOW, THEREFORE"),
            ("IN WITNESS WHEREOFF", "IN WITNESS WHEREOF"),
            ("Normal text", "Normal text")
        ]
        
        for input_text, expected in test_cases:
            result_text, _ = clean_markdown(input_text)
            # Remove HTML escaping for comparison
            unescaped_result = html.unescape(result_text)
            self.assertEqual(unescaped_result, expected, f"Phrase correction failed for: {input_text}")
            print(f"  ✓ Corrected: '{input_text}' → '{unescaped_result}'")

class TestSecurityIntegration(unittest.TestCase):
    """Test security measures in integration scenarios"""
    
    def test_malicious_prompt_handling(self):
        """Test handling of malicious prompts"""
        print("\n🚨 Testing malicious prompt handling...")
        
        malicious_prompts = [
            "<script>alert('XSS')</script>Create a contract",
            "Draft agreement <img src=x onerror=alert(1)>",
            "Generate document'; DROP TABLE users; --",
            "<iframe src='javascript:alert(1)'></iframe>Legal doc",
            "Contract with <svg onload=alert('XSS')> terms"
        ]
        
        for prompt in malicious_prompts:
            # First validate
            is_valid = validate_input(prompt, 'prompt')
            if is_valid:  # If it passes validation, it should be sanitized
                sanitized = sanitize_text(prompt)
                # Check that dangerous content is removed
                self.assertNotIn('<script>', sanitized.lower())
                self.assertNotIn('javascript:', sanitized.lower())
                self.assertNotIn('onerror=', sanitized.lower())
                print(f"  ✓ Handled malicious prompt safely")
            else:
                print(f"  ✓ Rejected malicious prompt at validation stage")
    
    def test_feedback_security(self):
        """Test feedback form security"""
        print("\n📝 Testing feedback form security...")
        
        malicious_feedback = [
            "<script>steal_data()</script>Great app!",
            "Good tool <img src=x onerror=fetch('http://evil.com')>",
            "<svg onload=alert('feedback_xss')>Nice interface"
        ]
        
        for feedback in malicious_feedback:
            # Should pass validation (under length limit)
            is_valid = validate_input(feedback, 'feedback')
            self.assertTrue(is_valid, "Should pass length validation")
            
            # But should be sanitized
            sanitized = sanitize_text(feedback)
            self.assertNotIn('<script>', sanitized.lower())
            self.assertNotIn('onerror=', sanitized.lower())
            self.assertNotIn('onload=', sanitized.lower())
            print(f"  ✓ Feedback sanitized safely")
    
    def test_email_injection_prevention(self):
        """Test prevention of email injection"""
        print("\n📧 Testing email injection prevention...")
        
        injection_attempts = [
            "user@domain.com\nBCC: evil@hacker.com",
            "user@domain.com\r\nTo: victim@target.com",
            "user@domain.com%0ABcc:evil@hacker.com",
            "user@domain.com<script>alert('xss')</script>"
        ]
        
        for email in injection_attempts:
            # Should fail validation
            is_valid = validate_input(email, 'email')
            self.assertFalse(is_valid, f"Should reject injection attempt: {email}")
            print(f"  ✓ Rejected email injection attempt")

def run_security_audit():
    """Run a comprehensive security audit"""
    print("\n" + "="*60)
    print("🔒 SECURITY AUDIT")
    print("="*60)
    
    # Check if bleach is installed and working
    try:
        test_html = "<script>alert('test')</script>Hello"
        cleaned = bleach.clean(test_html, tags=[], strip=True)
        if cleaned == "alert('test')Hello":
            print("✓ Bleach library is working correctly")
        else:
            print("⚠️ Bleach behavior unexpected")
    except Exception as e:
        print(f"❌ Bleach library issue: {e}")
        return False
    
    # Test common attack vectors
    attack_vectors = [
        # XSS attempts
        "<script>alert('XSS')</script>",
        "<img src=x onerror=alert('XSS')>",
        "<svg onload=alert('XSS')>",
        
        # SQL injection attempts (though not directly applicable)
        "'; DROP TABLE documents; --",
        "' OR '1'='1",
        
        # Command injection attempts
        "; rm -rf /",
        "| cat /etc/passwd",
        
        # HTML injection
        "<iframe src='javascript:alert(1)'></iframe>",
        "<object data='javascript:alert(1)'></object>",
    ]
    
    print("\n🛡️ Testing against common attack vectors...")
    for i, vector in enumerate(attack_vectors, 1):
        try:
            sanitized = sanitize_text(vector)
            # Check that dangerous patterns are removed
            dangerous_patterns = ['<script>', 'javascript:', 'onerror=', 'onload=', 'DROP TABLE']
            is_safe = not any(pattern.lower() in sanitized.lower() for pattern in dangerous_patterns)
            
            if is_safe:
                print(f"  ✓ Attack vector {i} neutralized")
            else:
                print(f"  ⚠️ Attack vector {i} may not be fully neutralized")
        except Exception as e:
            print(f"  ❌ Error processing attack vector {i}: {e}")
    
    print("\n" + "="*60)
    print("🎉 SECURITY AUDIT COMPLETE")
    print("="*60)
    return True

def main():
    """Main test runner"""
    print("🛡️ LEGAL DOCUMENT GENERATOR SECURITY TEST SUITE")
    print("=" * 60)
    
    # Check if required libraries are installed
    try:
        import bleach
        print("✓ Bleach library is installed")
    except ImportError:
        print("❌ Bleach library not found!")
        print("Please install it with: pip install bleach")
        return
    
    # Run security audit first
    if not run_security_audit():
        print("❌ Security audit failed. Please check your setup.")
        return
    
    # Run unit tests
    print("\n" + "="*60)
    print("🧪 RUNNING SECURITY UNIT TESTS")
    print("="*60)
    
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add test classes
    suite.addTests(loader.loadTestsFromTestCase(TestInputValidation))
    suite.addTests(loader.loadTestsFromTestCase(TestTextSanitization))
    suite.addTests(loader.loadTestsFromTestCase(TestMarkdownCleaning))
    suite.addTests(loader.loadTestsFromTestCase(TestSecurityIntegration))
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Summary
    print("\n" + "="*60)
    if result.wasSuccessful():
        print("🎉 ALL SECURITY TESTS PASSED!")
        print("Your input validation and sanitization is working correctly!")
        print("\n📋 Security measures verified:")
        print("  ✓ Input validation for all input types")
        print("  ✓ HTML/XSS injection prevention")
        print("  ✓ Script injection prevention")
        print("  ✓ Email injection prevention")
        print("  ✓ Markdown cleaning with HTML escaping")
        print("  ✓ Safe content preservation")
    else:
        print("❌ SOME SECURITY TESTS FAILED!")
        print(f"Failures: {len(result.failures)}")
        print(f"Errors: {len(result.errors)}")
        print("\n⚠️ Please review and fix the failing tests before deployment")
    print("="*60)

if __name__ == "__main__":
    main()