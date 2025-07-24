import time
import base64
import requests
import random
import string
import hashlib
import os
import json
import logging
from datetime import datetime
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Configure structured logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter(
    '{"timestamp": "%(asctime)s", "name": "%(name)s", "level": "%(levelname)s", "message": %(message)s}'
))
logger.handlers = [handler]

class DataEncryption:
    """Handles encryption and decryption of sensitive data"""
    
    def __init__(self, password: str = None):
        """
        Initialize encryption with a password-derived key
        
        Args:
            password: Custom password for encryption. If None, uses environment variable or generates one.
        """
        self.password = password or os.getenv("ENCRYPTION_PASSWORD")
        
        if not self.password:
            self.password = base64.urlsafe_b64encode(os.urandom(32)).decode()
            logger.warning('{"event": "no_encryption_password", "status": "generated"}')
        
        self.key = self._derive_key(self.password)
        self.cipher_suite = Fernet(self.key)
    
    def _derive_key(self, password: str) -> bytes:
        """Derive encryption key from password using PBKDF2"""
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
            logger.error('{"event": "encryption_failed", "error": "%s"}', str(e), exc_info=True)
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
            logger.error('{"event": "decryption_failed", "error": "%s"}', str(e), exc_info=True)
            raise
    
    def hash_data(self, data: str) -> str:
        """
        Create a hash of sensitive data for logging/tracking purposes
        
        Args:
            data: Data to hash
            
        Returns:
            SHA256 hash of the data
        """
        return hashlib.sha256(data.encode()).hexdigest()[:8]

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
        logger.info('{"event": "phone_encrypted", "phone_hash": "%s"}', self.phone_hash)
    
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
        
        self.encryptor = DataEncryption(encryption_password)
        logger.info('{"event": "data_encryption_initialized", "status": "success"}')
        
        self.business_shortcode = os.getenv("SAF_SHORTCODE") 
        self.till_number = os.getenv("SAF_TILL_NUMBER") 
        self.consumer_key = os.getenv("SAF_CONSUMER_KEY")
        self.consumer_secret = os.getenv("SAF_CONSUMER_SECRET")
        self.access_token_url = os.getenv("SAF_ACCESS_TOKEN_API")
        self.passkey = os.getenv("SAF_PASS_KEY")
        self.stk_push_url = os.getenv("SAF_STK_PUSH_API")
        self.query_status_url = os.getenv("SAF_STK_PUSH_QUERY_API")
        self.my_callback_url = os.getenv("CALLBACK_URL")
        
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
            logger.error('{"event": "missing_env_vars", "vars": "%s"}', ', '.join(missing_vars))
            raise ValueError("Missing required environment variables.")
        
        self.password = self.generate_password()
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        self.access_token = None

        try:
            token = self.get_mpesa_access_token()
            if not token:
                logger.error('{"event": "access_token_failed", "reason": "no_token"}')
                raise Exception("Failed to get access token")
            
            self.access_token = token
            self.headers.update({
                "Authorization": f"Bearer {token}"
            })
            self.access_token_expiration = time.time() + 3599
            logger.info('{"event": "access_token_obtained", "status": "success"}')
        except Exception as e:
            logger.error('{"event": "initialization_failed", "error": "%s"}', str(e), exc_info=True)
            raise

    def get_mpesa_access_token(self):
        try:
            base_url = self.access_token_url.split('?')[0]
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json"
            }
            params = {'grant_type': 'client_credentials'}
            
            res = requests.get(
                base_url,
                headers=headers,
                params=params,
                auth=HTTPBasicAuth(self.consumer_key, self.consumer_secret)
            )
            
            if res.status_code != 200:
                logger.error('{"event": "access_token_request_failed", "status_code": %d}', res.status_code)
                return None
                
            json_response = res.json()
            if 'access_token' not in json_response:
                logger.error('{"event": "access_token_missing", "response": "%s"}', json.dumps(json_response))
                return None
                
            token = json_response['access_token']
            if not token or len(token) < 10:
                logger.error('{"event": "invalid_access_token", "token_length": %d}', len(token))
                return None
                
            logger.info('{"event": "access_token_received", "status": "success"}')
            return token
        except Exception as e:
            logger.error('{"event": "access_token_error", "error": "%s"}', str(e), exc_info=True)
            raise

    def generate_password(self):
        self.timestamp = self.now.strftime("%Y%m%d%H%M%S")
        password_str = self.business_shortcode + self.passkey + self.timestamp
        password_bytes = password_str.encode()
        return base64.b64encode(password_bytes).decode("utf-8")
    
    def generate_account_reference(self, length=12):
        """
        Generate a unique account reference for M-Pesa transactions.
        """
        timestamp_part = str(int(time.time()))[-4:]
        random_chars_length = length - len(timestamp_part)
        random_chars = ''.join(
            random.choices(string.ascii_uppercase + string.digits, k=random_chars_length)
        )
        account_reference = (timestamp_part + random_chars)[:length]
        logger.info('{"event": "account_reference_generated", "account_ref": "%s"}', account_reference)
        return account_reference

    def _sanitize_phone_number(self, phone_number: str) -> str:
        """
        Sanitize and validate phone number format
        """
        try:
            phone_digits = ''.join(filter(str.isdigit, phone_number))
            if phone_digits.startswith('0'):
                phone_digits = '254' + phone_digits[1:]
            elif phone_digits.startswith('7') and len(phone_digits) == 9:
                phone_digits = '254' + phone_digits
            elif not phone_digits.startswith('254'):
                if len(phone_digits) == 9:
                    phone_digits = '254' + phone_digits
            
            if not (phone_digits.startswith('254') and len(phone_digits) == 12):
                logger.warning('{"event": "invalid_phone_number", "phone_length": %d}', len(phone_digits))
                raise ValueError("Invalid phone number format.")
            
            return phone_digits
        except Exception as e:
            logger.error('{"event": "phone_sanitization_failed", "error": "%s"}', str(e), exc_info=True)
            raise

    def initiate_stk_push(self, phone_number, amount, transaction_desc="Document Generation Payment", account_reference=None):
        """
        Initiate STK push using paybill model with encrypted data handling
        """
        try:
            sanitized_phone = self._sanitize_phone_number(phone_number)
            payment_data = SecurePaymentData(self.encryptor)
            payment_data.set_phone_number(sanitized_phone)
            payment_data.amount = amount
            payment_data.timestamp = datetime.now()
            
            if account_reference is None:
                account_reference = self.generate_account_reference()
            
            account_reference = account_reference[:12]
            payment_data.set_account_reference(account_reference)
            
            base_url = self.stk_push_url.split('?')[0]
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Cache-Control": "no-cache"
            }
            
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
            
            safe_payload = payload.copy()
            safe_payload["PartyA"] = f"***{decrypted_phone[-4:]}"
            safe_payload["PhoneNumber"] = f"***{decrypted_phone[-4:]}"
            logger.info('{"event": "stk_push_request", "payload": %s}', json.dumps(safe_payload))
            
            response = requests.post(
                base_url,
                headers=headers,
                json=payload
            )
            
            decrypted_phone = None
            decrypted_account_ref = None
            payload = None
            
            response_data = response.json()
            safe_response = response_data.copy()
            if 'CustomerMessage' in safe_response:
                safe_response['CustomerMessage'] = "[REDACTED]"
            logger.info('{"event": "stk_push_response", "status_code": %d, "response": %s}', 
                        response.status_code, json.dumps(safe_response))
            
            return response_data
        except ValueError as ve:
            logger.error('{"event": "stk_push_validation_error", "error": "%s"}', str(ve))
            return {"ResponseCode": "1", "errorMessage": "Invalid input. Please check your phone number and try again. If this issue persists, please report it using the Feedback tab in the Help Guide."}
        except Exception as e:
            logger.error('{"event": "stk_push_error", "error": "%s"}', str(e), exc_info=True)
            return {"ResponseCode": "1", "errorMessage": "An error occurred during payment initiation. Please try again later. If this issue persists, please report it using the Feedback tab in the Help Guide."}

    def query_stk_push(self, checkout_request_id):
        """
        Query STK push status
        """
        try:
            request_id_hash = self.encryptor.hash_data(checkout_request_id)
            logger.info('{"event": "query_stk_push", "request_id_hash": "%s"}', request_id_hash)
            
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
            logger.info('{"event": "query_stk_push_response", "request_id_hash": "%s", "response_code": "%s"}', 
                        request_id_hash, response_data.get('ResponseCode', 'Unknown'))
            
            return response_data
        except Exception as e:
            logger.error('{"event": "query_stk_push_error", "request_id_hash": "%s", "error": "%s"}', 
                         self.encryptor.hash_data(checkout_request_id), str(e), exc_info=True)
            raise

    def encrypt_sensitive_data(self, data: str) -> str:
        """
        Public method to encrypt sensitive data
        """
        return self.encryptor.encrypt(data)

    def decrypt_sensitive_data(self, encrypted_data: str) -> str:
        """
        Public method to decrypt sensitive data
        """
        return self.encryptor.decrypt(encrypted_data)

def validate_phone_number(phone):
    """Validate phone number format"""
    try:
        handler = MpesaHandler()
        sanitized = handler._sanitize_phone_number(phone)
        return True
    except ValueError:
        return False

if __name__ == "__main__":
    try:
        mpesa = MpesaHandler()
        logger.info('{"event": "mpesa_handler_main", "status": "started"}')
        
        while True:
            phone = input("Enter phone number (format: 254XXXXXXXXX or 07XXXXXXXX): ").strip()
            if validate_phone_number(phone):
                break
            logger.warning('{"event": "invalid_phone_input", "phone_length": %d}', len(phone))
            print("Invalid phone number! Use format: 254XXXXXXXXX or 07XXXXXXXX. If this issue persists, please report it using the Feedback tab in the Help Guide.")
        
        amount = 1
        desc = "Document Generation Payment"
        
        logger.info('{"event": "payment_initiation", "amount": %d}', amount)
        response = mpesa.initiate_stk_push(phone, amount, desc)
        
        if 'ResponseCode' in response and response['ResponseCode'] == '0':
            logger.info('{"event": "stk_push_success", "checkout_request_id": "%s"}', 
                        response.get('CheckoutRequestID', 'N/A'))
            print("\n✓ STK push sent successfully!")
            print("Please check your phone for the payment prompt")
            print(f"Checkout Request ID: {response.get('CheckoutRequestID', 'N/A')}")
        else:
            logger.error('{"event": "stk_push_failed", "error": "%s"}', response.get('errorMessage', 'Unknown error'))
            print("\n✗ Failed to initiate payment. If this issue persists, please report it using the Feedback tab in the Help Guide.")
            print("Error: Payment initiation failed. Please try again.")
    except Exception as e:
        logger.error('{"event": "main_error", "error": "%s"}', str(e), exc_info=True)
        print("\n✗ An error occurred. Please try again later. If this issue persists, please report it using the Feedback tab in the Help Guide.")