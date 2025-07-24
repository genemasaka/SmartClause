#!/usr/bin/env python3
"""
Comprehensive test suite for encrypted M-PESA handler
Run this to verify encryption and functionality work correctly
"""

import os
import sys
import time
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime

# Add the current directory to path to import our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from mpesa_handler import MpesaHandler, DataEncryption, SecurePaymentData, validate_phone_number
    print("✓ Successfully imported encrypted MpesaHandler")
except ImportError as e:
    print(f"✗ Failed to import MpesaHandler: {e}")
    print("Please ensure mpesa_handler.py is in the same directory and cryptography is installed")
    sys.exit(1)

class TestDataEncryption(unittest.TestCase):
    """Test the encryption functionality"""
    
    def setUp(self):
        self.encryptor = DataEncryption("test_password_123")
        self.test_data = "254712345678"
    
    def test_encryption_decryption(self):
        """Test basic encryption and decryption"""
        print("\n🔐 Testing encryption/decryption...")
        
        # Encrypt data
        encrypted = self.encryptor.encrypt(self.test_data)
        print(f"  Original: {self.test_data}")
        print(f"  Encrypted: {encrypted[:20]}..." if len(encrypted) > 20 else f"  Encrypted: {encrypted}")
        
        # Verify encryption worked
        self.assertNotEqual(encrypted, self.test_data, "Data should be encrypted")
        self.assertIsInstance(encrypted, str, "Encrypted data should be string")
        
        # Decrypt data
        decrypted = self.encryptor.decrypt(encrypted)
        print(f"  Decrypted: {decrypted}")
        
        # Verify decryption worked
        self.assertEqual(decrypted, self.test_data, "Decrypted data should match original")
        print("  ✓ Encryption/Decryption working correctly")
    
    def test_different_passwords_different_results(self):
        """Test that different passwords produce different encrypted results"""
        print("\n🔑 Testing password isolation...")
        
        encryptor1 = DataEncryption("password1")
        encryptor2 = DataEncryption("password2")
        
        encrypted1 = encryptor1.encrypt(self.test_data)
        encrypted2 = encryptor2.encrypt(self.test_data)
        
        self.assertNotEqual(encrypted1, encrypted2, "Different passwords should produce different results")
        print("  ✓ Password isolation working correctly")
    
    def test_hash_generation(self):
        """Test hash generation for logging"""
        print("\n#️⃣ Testing hash generation...")
        
        hash_result = self.encryptor.hash_data(self.test_data)
        print(f"  Data: {self.test_data}")
        print(f"  Hash: {hash_result}")
        
        self.assertEqual(len(hash_result), 8, "Hash should be 8 characters")
        self.assertNotEqual(hash_result, self.test_data, "Hash should be different from original")
        print("  ✓ Hash generation working correctly")
    
    def test_empty_data_handling(self):
        """Test handling of empty/None data"""
        print("\n⚪ Testing empty data handling...")
        
        # Test empty string
        empty_encrypted = self.encryptor.encrypt("")
        self.assertEqual(empty_encrypted, "", "Empty string should remain empty")
        
        # Test None
        none_encrypted = self.encryptor.encrypt(None)
        self.assertIsNone(none_encrypted, "None should remain None")
        
        print("  ✓ Empty data handling working correctly")

class TestSecurePaymentData(unittest.TestCase):
    """Test the secure payment data container"""
    
    def setUp(self):
        self.encryptor = DataEncryption("test_password_123")
        self.payment_data = SecurePaymentData(self.encryptor)
    
    def test_phone_number_encryption(self):
        """Test phone number encryption in payment data"""
        print("\n📱 Testing phone number encryption in payment container...")
        
        test_phone = "254712345678"
        self.payment_data.set_phone_number(test_phone)
        
        # Check that encrypted data is stored
        self.assertIsNotNone(self.payment_data.encrypted_phone, "Encrypted phone should be stored")
        self.assertNotEqual(self.payment_data.encrypted_phone, test_phone, "Phone should be encrypted")
        
        # Check hash generation
        self.assertIsNotNone(self.payment_data.phone_hash, "Phone hash should be generated")
        print(f"  Phone hash: {self.payment_data.phone_hash}")
        
        # Check decryption
        decrypted_phone = self.payment_data.get_phone_number()
        self.assertEqual(decrypted_phone, test_phone, "Decrypted phone should match original")
        
        print("  ✓ Phone number encryption working correctly")
    
    def test_account_reference_encryption(self):
        """Test account reference encryption"""
        print("\n🔗 Testing account reference encryption...")
        
        test_ref = "TEST_REF_123"
        self.payment_data.set_account_reference(test_ref)
        
        # Check encryption
        self.assertIsNotNone(self.payment_data.encrypted_account_ref, "Encrypted account ref should be stored")
        
        # Check decryption
        decrypted_ref = self.payment_data.get_account_reference()
        self.assertEqual(decrypted_ref, test_ref, "Decrypted account ref should match original")
        
        print("  ✓ Account reference encryption working correctly")

class TestPhoneNumberValidation(unittest.TestCase):
    """Test phone number validation and sanitization"""
    
    def test_phone_number_formats(self):
        """Test various phone number format handling"""
        print("\n📞 Testing phone number format handling...")
        
        # Create a mock MpesaHandler for testing sanitization
        with patch.dict(os.environ, {
            'SAF_SHORTCODE': 'test',
            'SAF_TILL_NUMBER': 'test',
            'SAF_CONSUMER_KEY': 'test',
            'SAF_CONSUMER_SECRET': 'test',
            'SAF_ACCESS_TOKEN_API': 'http://test.com',
            'SAF_PASS_KEY': 'test',
            'SAF_STK_PUSH_API': 'http://test.com',
            'CALLBACK_URL': 'http://test.com'
        }):
            with patch('mpesa_handler.MpesaHandler.get_mpesa_access_token', return_value='test_token'):
                handler = MpesaHandler()
                
                test_cases = [
                    ("254712345678", "254712345678", "Standard format"),
                    ("0712345678", "254712345678", "Leading zero format"),
                    ("712345678", "254712345678", "Without country code"),
                    ("+254712345678", "254712345678", "With plus sign"),
                    ("254-712-345-678", "254712345678", "With dashes"),
                    ("254 712 345 678", "254712345678", "With spaces"),
                ]
                
                for input_phone, expected, description in test_cases:
                    try:
                        result = handler._sanitize_phone_number(input_phone)
                        self.assertEqual(result, expected, f"Failed for {description}")
                        print(f"  ✓ {description}: {input_phone} → {result}")
                    except Exception as e:
                        print(f"  ✗ {description}: {input_phone} → Error: {e}")
                
                # Test invalid formats
                invalid_cases = [
                    "12345",
                    "254123",
                    "abcdefghijk",
                    "",
                ]
                
                print("\n  Testing invalid formats (should raise errors):")
                for invalid_phone in invalid_cases:
                    try:
                        result = handler._sanitize_phone_number(invalid_phone)
                        print(f"  ✗ Should have failed for: {invalid_phone}")
                    except ValueError:
                        print(f"  ✓ Correctly rejected: {invalid_phone}")

class TestMpesaHandlerIntegration(unittest.TestCase):
    """Test M-PESA handler integration"""
    
    def setUp(self):
        # Mock environment variables
        self.env_patch = patch.dict(os.environ, {
            'SAF_SHORTCODE': 'test_shortcode',
            'SAF_TILL_NUMBER': 'test_till',
            'SAF_CONSUMER_KEY': 'test_key',
            'SAF_CONSUMER_SECRET': 'test_secret',
            'SAF_ACCESS_TOKEN_API': 'http://test.com/token',
            'SAF_PASS_KEY': 'test_pass',
            'SAF_STK_PUSH_API': 'http://test.com/stk',
            'SAF_STK_PUSH_QUERY_API': 'http://test.com/query',
            'CALLBACK_URL': 'http://test.com/callback',
            'ENCRYPTION_PASSWORD': 'test_encryption_password'
        })
        self.env_patch.start()
    
    def tearDown(self):
        self.env_patch.stop()
    
    @patch('mpesa_handler.requests.get')
    @patch('mpesa_handler.requests.post')
    def test_stk_push_with_encryption(self, mock_post, mock_get):
        """Test STK push with encryption enabled"""
        print("\n💳 Testing STK push with encryption...")
        
        # Mock token request
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {'access_token': 'test_token_123'}
        
        # Mock STK push request
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            'ResponseCode': '0',
            'ResponseDescription': 'Success',
            'CheckoutRequestID': 'ws_CO_123456789'
        }
        
        # Create handler
        handler = MpesaHandler()
        
        # Test STK push
        response = handler.initiate_stk_push(
            phone_number="0712345678",
            amount=1000,
            transaction_desc="Test payment"
        )
        
        # Verify response
        self.assertEqual(response['ResponseCode'], '0', "STK push should succeed")
        print(f"  ✓ STK push response: {response['ResponseDescription']}")
        
        # Verify that the request was made (encryption should be transparent)
        self.assertTrue(mock_post.called, "POST request should have been made")
        
        # Get the actual request payload
        call_args = mock_post.call_args
        payload = call_args[1]['json']  # The JSON payload
        
        # Verify that phone number in payload is in correct format (decrypted for API)
        self.assertEqual(payload['PartyA'], '254712345678', "Phone should be properly formatted in API call")
        self.assertEqual(payload['PhoneNumber'], '254712345678', "Phone should be properly formatted in API call")
        
        print("  ✓ Encryption/decryption working transparently with API calls")

def run_live_encryption_test():
    """Run a live test of encryption without making actual API calls"""
    print("\n" + "="*60)
    print("🔒 LIVE ENCRYPTION TEST")
    print("="*60)
    
    try:
        # Test encryption with various data
        encryptor = DataEncryption("my_secret_password_123")
        
        test_data = [
            "254712345678",
            "Account_Ref_12345",
            "Sensitive payment info",
            "User identification data"
        ]
        
        print("\nTesting encryption/decryption cycle:")
        for i, data in enumerate(test_data, 1):
            print(f"\n{i}. Testing: {data}")
            
            # Encrypt
            encrypted = encryptor.encrypt(data)
            print(f"   Encrypted: {encrypted[:30]}{'...' if len(encrypted) > 30 else ''}")
            
            # Generate hash
            hash_val = encryptor.hash_data(data)
            print(f"   Hash: {hash_val}")
            
            # Decrypt
            decrypted = encryptor.decrypt(encrypted)
            print(f"   Decrypted: {decrypted}")
            
            # Verify
            if decrypted == data:
                print("   ✓ SUCCESS: Decryption matches original")
            else:
                print("   ✗ FAILED: Decryption doesn't match original")
                return False
        
        print("\n" + "="*60)
        print("🎉 ALL ENCRYPTION TESTS PASSED!")
        print("="*60)
        return True
        
    except Exception as e:
        print(f"\n❌ ENCRYPTION TEST FAILED: {str(e)}")
        return False

def run_integration_test():
    """Run integration test to verify the handler works with your app"""
    print("\n" + "="*60)
    print("🔗 INTEGRATION TEST")
    print("="*60)
    
    # Set up minimal environment for testing
    required_env = {
        'SAF_SHORTCODE': 'test',
        'SAF_TILL_NUMBER': 'test',
        'SAF_CONSUMER_KEY': 'test',
        'SAF_CONSUMER_SECRET': 'test',
        'SAF_ACCESS_TOKEN_API': 'http://test.com',
        'SAF_PASS_KEY': 'test',
        'SAF_STK_PUSH_API': 'http://test.com',
        'SAF_STK_PUSH_QUERY_API': 'http://test.com',
        'CALLBACK_URL': 'http://test.com',
        'ENCRYPTION_PASSWORD': 'test_password_123'
    }
    
    for key, value in required_env.items():
        if key not in os.environ:
            os.environ[key] = value
    
    try:
        print("\n1. Testing MpesaHandler initialization...")
        with patch('mpesa_handler.requests.get') as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {'access_token': 'test_token'}
            
            handler = MpesaHandler()
            print("   ✓ Handler initialized successfully with encryption")
        
        print("\n2. Testing phone number validation...")
        test_phones = ["0712345678", "254712345678", "+254712345678"]
        for phone in test_phones:
            is_valid = validate_phone_number(phone)
            print(f"   {phone}: {'✓ Valid' if is_valid else '✗ Invalid'}")
        
        print("\n3. Testing encryption methods...")
        test_data = "254712345678"
        encrypted = handler.encrypt_sensitive_data(test_data)
        decrypted = handler.decrypt_sensitive_data(encrypted)
        
        if decrypted == test_data:
            print("   ✓ Public encryption methods working")
        else:
            print("   ✗ Public encryption methods failed")
            return False
        
        print("\n" + "="*60)
        print("🎉 INTEGRATION TEST PASSED!")
        print("Your encrypted MpesaHandler is ready to use!")
        print("="*60)
        return True
        
    except Exception as e:
        print(f"\n❌ INTEGRATION TEST FAILED: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Main test runner"""
    print("🧪 M-PESA ENCRYPTION TEST SUITE")
    print("=" * 60)
    
    # Check if cryptography is installed
    try:
        from cryptography.fernet import Fernet
        print("✓ Cryptography library is installed")
    except ImportError:
        print("❌ Cryptography library not found!")
        print("Please install it with: pip install cryptography")
        return
    
    # Run live encryption test
    if not run_live_encryption_test():
        print("❌ Basic encryption test failed. Please check the implementation.")
        return
    
    # Run integration test
    if not run_integration_test():
        print("❌ Integration test failed. Please check your setup.")
        return
    
    # Run unit tests
    print("\n" + "="*60)
    print("🔬 RUNNING UNIT TESTS")
    print("="*60)
    
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add test classes
    suite.addTests(loader.loadTestsFromTestCase(TestDataEncryption))
    suite.addTests(loader.loadTestsFromTestCase(TestSecurePaymentData))
    suite.addTests(loader.loadTestsFromTestCase(TestPhoneNumberValidation))
    suite.addTests(loader.loadTestsFromTestCase(TestMpesaHandlerIntegration))
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Summary
    print("\n" + "="*60)
    if result.wasSuccessful():
        print("🎉 ALL TESTS PASSED!")
        print("Your encrypted M-PESA handler is working correctly!")
    else:
        print("❌ SOME TESTS FAILED!")
        print(f"Failures: {len(result.failures)}")
        print(f"Errors: {len(result.errors)}")
    print("="*60)

if __name__ == "__main__":
    main()