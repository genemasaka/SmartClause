import time
import base64
import requests
import random
import string
import hashlib
import os
from datetime import datetime
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DataEncryption:
    """Handles encryption and decryption of sensitive data"""
    
    def __init__(self, password: str = None):
        """
        Initialize encryption with a password-derived key
        
        Args:
            password: Custom password for encryption. If None, uses environment variable or generates one.
        """
        # Get encryption password from environment or use provided password
        self.password = password or os.getenv("ENCRYPTION_PASSWORD")
        
        if not self.password:
            # Generate a random password if none provided (not recommended for production)
            self.password = base64.urlsafe_b64encode(os.urandom(32)).decode()
            logger.warning("No encryption password provided. Using generated password. "
                         "Consider setting ENCRYPTION_PASSWORD environment variable.")
        
        # Create encryption key from password
        self.key = self._derive_key(self.password)
        self.cipher_suite = Fernet(self.key)
    
    def _derive_key(self, password: str) -> bytes:
        """Derive encryption key from password using PBKDF2"""
        # Use a fixed salt for consistency (in production, you might want to store this securely)
        salt = b'mpesa_encryption_salt_2024'
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        return key
    
    def encrypt(self, data: str) -> str:
        """
        Encrypt sensitive data
        
        Args:
            data: Plain text data to encrypt
            
        Returns:
            Encrypted data as base64 string
        """
        if not data:
            return data
            
        try:
            encrypted_data = self.cipher_suite.encrypt(data.encode())
            return base64.urlsafe_b64encode(encrypted_data).decode()
        except Exception as e:
            logger.error(f"Encryption failed: {str(e)}")
            raise
    
    def decrypt(self, encrypted_data: str) -> str:
        """
        Decrypt sensitive data
        
        Args:
            encrypted_data: Base64 encoded encrypted data
            
        Returns:
            Decrypted plain text data
        """
        if not encrypted_data:
            return encrypted_data
            
        try:
            decoded_data = base64.urlsafe_b64decode(encrypted_data.encode())
            decrypted_data = self.cipher_suite.decrypt(decoded_data)
            return decrypted_data.decode()
        except Exception as e:
            logger.error(f"Decryption failed: {str(e)}")
            raise
    
    def hash_data(self, data: str) -> str:
        """
        Create a hash of sensitive data for logging/tracking purposes
        
        Args:
            data: Data to hash
            
        Returns:
            SHA256 hash of the data
        """
        return hashlib.sha256(data.encode()).hexdigest()[:8]  # First 8 characters for brevity

class SecurePaymentData:
    """Container for encrypted payment data"""
    
    def __init__(self, encryptor: DataEncryption):
        self.encryptor = encryptor
        self.encrypted_phone = None
        self.encrypted_account_ref = None
        self.phone_hash = None
        self.amount = None
        self.timestamp = None
    
    def set_phone_number(self, phone_number: str):
        """Encrypt and store phone number"""
        self.encrypted_phone = self.encryptor.encrypt(phone_number)
        self.phone_hash = self.encryptor.hash_data(phone_number)
        logger.info(f"Phone number encrypted (hash: {self.phone_hash})")
    
    def get_phone_number(self) -> str:
        """Decrypt and return phone number"""
        if not self.encrypted_phone:
            return None
        return self.encryptor.decrypt(self.encrypted_phone)
    
    def set_account_reference(self, account_ref: str):
        """Encrypt and store account reference"""
        self.encrypted_account_ref = self.encryptor.encrypt(account_ref)
    
    def get_account_reference(self) -> str:
        """Decrypt and return account reference"""
        if not self.encrypted_account_ref:
            return None
        return self.encryptor.decrypt(self.encrypted_account_ref)

class MpesaHandler:
    def __init__(self, encryption_password: str = None):
        load_dotenv()
        self.now = datetime.now()
        
        # Initialize encryption
        self.encryptor = DataEncryption(encryption_password)
        logger.info("Data encryption initialized successfully")
        
        # Load and validate environment variables
        self.business_shortcode = os.getenv("SAF_SHORTCODE") 
        self.till_number = os.getenv("SAF_TILL_NUMBER") 
        self.consumer_key = os.getenv("SAF_CONSUMER_KEY")
        self.consumer_secret = os.getenv("SAF_CONSUMER_SECRET")
        self.access_token_url = os.getenv("SAF_ACCESS_TOKEN_API")
        self.passkey = os.getenv("SAF_PASS_KEY")
        self.stk_push_url = os.getenv("SAF_STK_PUSH_API")
        self.query_status_url = os.getenv("SAF_STK_PUSH_QUERY_API")
        self.my_callback_url = os.getenv("CALLBACK_URL")
        
        # Print environment variables for debugging (without exposing secrets)
        print("\nChecking environment variables:")
        print(f"- SAF_SHORTCODE: {'Set' if self.business_shortcode else 'Not set'}")
        print(f"- SAF_TILL_NUMBER: {'Set' if self.till_number else 'Not set'}")
        print(f"- SAF_CONSUMER_KEY: {'Set' if self.consumer_key else 'Not set'}")
        print(f"- SAF_CONSUMER_SECRET: {'Set' if self.consumer_secret else 'Not set'}")
        print(f"- SAF_ACCESS_TOKEN_API: {self.access_token_url}")
        print(f"- SAF_STK_PUSH_API: {self.stk_push_url}")
        print(f"- ENCRYPTION_PASSWORD: {'Set' if os.getenv('ENCRYPTION_PASSWORD') else 'Generated'}")
        
        # Validate required environment variables
        required_vars = [
            ('SAF_SHORTCODE', self.business_shortcode),
            ('SAF_TILL_NUMBER', self.till_number),
            ('SAF_CONSUMER_KEY', self.consumer_key),
            ('SAF_CONSUMER_SECRET', self.consumer_secret),
            ('SAF_ACCESS_TOKEN_API', self.access_token_url),
            ('SAF_PASS_KEY', self.passkey),
            ('SAF_STK_PUSH_API', self.stk_push_url),
            ('CALLBACK_URL', self.my_callback_url)
        ]
        
        missing_vars = [name for name, value in required_vars if not value]
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")
        
        self.password = self.generate_password()
        
        # Initialize headers with a default value
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        self.access_token = None

        try:
            token = self.get_mpesa_access_token()
            if not token:
                raise Exception("Failed to get access token")
            
            self.access_token = token
            self.headers.update({
                "Authorization": f"Bearer {token}"
            })
            print(f"\nFinal headers configured successfully")  # Don't log actual token
            self.access_token_expiration = time.time() + 3599
            print("Successfully obtained access token")
        except Exception as e:
            print(f"Error during initialization: {str(e)}")

    def get_mpesa_access_token(self):
        try:
            print(f"\nMaking token request to: {self.access_token_url}")
            print("Using Basic Auth with consumer key/secret")
            
            # Remove grant_type from URL if present
            base_url = self.access_token_url.split('?')[0]
            
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json"
            }
            
            params = {
                'grant_type': 'client_credentials'
            }
            
            res = requests.get(
                base_url,
                headers=headers,
                params=params,
                auth=HTTPBasicAuth(self.consumer_key, self.consumer_secret)
            )
            
            print(f"Token request status code: {res.status_code}")
            print(f"Token response received")  # Don't log actual response content
            
            if res.status_code != 200:
                print(f"Error getting access token. Status code: {res.status_code}")
                return None
                
            try:
                json_response = res.json()
            except Exception as e:
                print(f"Error parsing JSON response: {str(e)}")
                return None
                
            if 'access_token' not in json_response:
                print(f"No access token in response")
                return None
                
            token = json_response['access_token']
            if not token or len(token) < 10:  # Basic validation
                print(f"Token appears invalid (too short)")
                return None
                
            return token
        except Exception as e:
            print(f"Exception in get_mpesa_access_token: {str(e)}")
            print(f"Exception type: {type(e)}")
            raise e

    def generate_password(self):
        self.timestamp = self.now.strftime("%Y%m%d%H%M%S")
        password_str = self.business_shortcode + self.passkey + self.timestamp
        password_bytes = password_str.encode()
        return base64.b64encode(password_bytes).decode("utf-8")
    
    def generate_account_reference(self, length=12):
        """
        Generate a unique account reference for M-Pesa transactions.
        
        Parameters:
        - length: Length of the account reference (max 12 characters)
        
        Returns:
        - A unique account reference string
        """
        # Use current timestamp (last 4 digits)
        timestamp_part = str(int(time.time()))[-4:]
        
        # Generate random alphanumeric characters to fill remaining length
        random_chars_length = length - len(timestamp_part)
        random_chars = ''.join(
            random.choices(string.ascii_uppercase + string.digits, k=random_chars_length)
        )
        
        # Combine timestamp and random characters
        account_reference = (timestamp_part + random_chars)[:length]
        
        return account_reference

    def _sanitize_phone_number(self, phone_number: str) -> str:
        """
        Sanitize and validate phone number format
        
        Args:
            phone_number: Raw phone number input
            
        Returns:
            Sanitized phone number in correct format
            
        Raises:
            ValueError: If phone number format is invalid
        """
        # Remove any non-digit characters
        phone_digits = ''.join(filter(str.isdigit, phone_number))
        
        # Handle different input formats
        if phone_digits.startswith('0'):
            # Convert 07XXXXXXXX to 2547XXXXXXXX
            phone_digits = '254' + phone_digits[1:]
        elif phone_digits.startswith('7') and len(phone_digits) == 9:
            # Convert 7XXXXXXXX to 2547XXXXXXXX
            phone_digits = '254' + phone_digits
        elif not phone_digits.startswith('254'):
            # Assume it's missing country code
            if len(phone_digits) == 9:
                phone_digits = '254' + phone_digits
        
        # Validate final format
        if not (phone_digits.startswith('254') and len(phone_digits) == 12):
            raise ValueError(f"Invalid phone number format. Expected 254XXXXXXXXX")
        
        return phone_digits

    def initiate_stk_push(self, phone_number, amount, transaction_desc="Document Generation Payment", account_reference=None):
        """
        Initiate STK push using paybill model with encrypted data handling
        
        Parameters:
        - phone_number: Customer's phone number (format: 254XXXXXXXXX)
        - amount: Amount to be paid
        - transaction_desc: (Optional) Description of the transaction
        - account_reference: (Optional) Custom account reference. If None, will generate one
        """
        try:
            # Sanitize and validate phone number
            sanitized_phone = self._sanitize_phone_number(phone_number)
            
            # Create secure payment data container
            payment_data = SecurePaymentData(self.encryptor)
            payment_data.set_phone_number(sanitized_phone)
            payment_data.amount = amount
            payment_data.timestamp = datetime.now()
            
            # Generate account reference if not provided
            if account_reference is None:
                account_reference = self.generate_account_reference()
            
            # Ensure account reference is 12 characters or less
            account_reference = account_reference[:12]
            payment_data.set_account_reference(account_reference)
            
            # Remove grant_type from URL if present
            base_url = self.stk_push_url.split('?')[0]
            logger.info(f"Making STK push request to: {base_url}")
            logger.info(f"Payment request for phone hash: {payment_data.phone_hash}, amount: {amount}")
            
            # Enhanced headers for M-Pesa API
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Cache-Control": "no-cache"
            }
            
            # Decrypt data only when needed for API call
            decrypted_phone = payment_data.get_phone_number()
            decrypted_account_ref = payment_data.get_account_reference()
            
            payload = {
                "BusinessShortCode": self.business_shortcode,
                "Password": self.password,
                "Timestamp": self.timestamp,
                "TransactionType": "CustomerBuyGoodsOnline",
                "Amount": amount,
                "PartyA": decrypted_phone,
                "PartyB": self.till_number,
                "PhoneNumber": decrypted_phone,
                "CallBackURL": self.my_callback_url,
                "TransactionDesc": transaction_desc,
                "AccountReference": decrypted_account_ref
            }
            
            # Log payload without sensitive data
            safe_payload = payload.copy()
            safe_payload["PartyA"] = f"***{decrypted_phone[-4:]}"  # Show only last 4 digits
            safe_payload["PhoneNumber"] = f"***{decrypted_phone[-4:]}"
            logger.info(f"Request payload (sanitized): {safe_payload}")
            
            response = requests.post(
                base_url,
                headers=headers,
                json=payload
            )
            
            logger.info(f"Response status code: {response.status_code}")
            
            # Clear sensitive data from memory
            decrypted_phone = None
            decrypted_account_ref = None
            payload = None
            
            response_data = response.json()
            
            # Log response without sensitive data
            safe_response = response_data.copy()
            if 'CustomerMessage' in safe_response:
                # Don't log customer messages as they might contain phone numbers
                safe_response['CustomerMessage'] = "[REDACTED]"
            logger.info(f"Response data (sanitized): {safe_response}")
            
            return response_data
            
        except ValueError as ve:
            logger.error(f"Validation error: {str(ve)}")
            return {"ResponseCode": "1", "errorMessage": str(ve)}
        except Exception as e:
            logger.error(f"Error initiating stk push: {str(e)}")
            raise e

    def query_stk_push(self, checkout_request_id):
        """
        Query STK push status
        
        Args:
            checkout_request_id: The checkout request ID to query
            
        Returns:
            Query response from M-PESA API
        """
        try:
            # Hash the checkout request ID for logging
            request_id_hash = self.encryptor.hash_data(checkout_request_id)
            logger.info(f"Querying STK push status for request hash: {request_id_hash}")
            
            response = requests.post(
                self.query_status_url,
                headers=self.headers,
                json={
                    "BusinessShortCode": self.business_shortcode,
                    "Password": self.password,
                    "Timestamp": self.timestamp,
                    "CheckoutRequestID": checkout_request_id
                }
            )
            
            response_data = response.json()
            logger.info(f"Query response status: {response_data.get('ResponseCode', 'Unknown')}")
            
            return response_data
        except Exception as e:
            logger.error(f"Error querying stk push: {str(e)}")
            raise e

    def encrypt_sensitive_data(self, data: str) -> str:
        """
        Public method to encrypt sensitive data
        
        Args:
            data: Plain text data to encrypt
            
        Returns:
            Encrypted data as base64 string
        """
        return self.encryptor.encrypt(data)

    def decrypt_sensitive_data(self, encrypted_data: str) -> str:
        """
        Public method to decrypt sensitive data
        
        Args:
            encrypted_data: Base64 encoded encrypted data
            
        Returns:
            Decrypted plain text data
        """
        return self.encryptor.decrypt(encrypted_data)

def validate_phone_number(phone):
    """Validate phone number format"""
    try:
        # Use the sanitization method from MpesaHandler
        handler = MpesaHandler()
        sanitized = handler._sanitize_phone_number(phone)
        return True
    except ValueError:
        return False

if __name__ == "__main__":
    try:
        mpesa = MpesaHandler()
        print("\n=== M-PESA Payment System with Encryption ===")
        
        # Get phone number input
        while True:
            phone = input("\nEnter phone number (format: 254XXXXXXXXX or 07XXXXXXXX): ").strip()
            if validate_phone_number(phone):
                break
            print("Invalid phone number! Use format: 254XXXXXXXXX or 07XXXXXXXX")
        
        # Initiate payment
        amount = 1  # Set your amount
        desc = "Document Generation Payment"
        
        print("\nInitiating encrypted payment...")
        
        # Initiate STK push with encryption
        response = mpesa.initiate_stk_push(phone, amount, desc)
        
        if 'ResponseCode' in response and response['ResponseCode'] == '0':
            print("\n✓ STK push sent successfully!")
            print("Please check your phone for the payment prompt")
            print(f"Checkout Request ID: {response.get('CheckoutRequestID', 'N/A')}")
        else:
            print("\n✗ Failed to initiate payment")
            print("Error:", response.get('errorMessage', 'Unknown error'))

    except Exception as e:
        print(f"\n✗ Error: {str(e)}")