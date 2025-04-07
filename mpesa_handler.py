import time
import base64
import requests
import random
import string
from datetime import datetime
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv
import os

class MpesaHandler:
    def __init__(self):
        load_dotenv()
        self.now = datetime.now()
        
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
            print(f"\nFinal headers: {self.headers}")  # Debug print
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
            print(f"Token response headers: {dict(res.headers)}")
            print(f"Token response content: {res.text}")
            
            if res.status_code != 200:
                print(f"Error getting access token. Status code: {res.status_code}")
                print(f"Response: {res.text}")
                return None
                
            try:
                json_response = res.json()
            except Exception as e:
                print(f"Error parsing JSON response: {str(e)}")
                print(f"Raw response: {res.text}")
                return None
                
            if 'access_token' not in json_response:
                print(f"No access token in response: {json_response}")
                return None
                
            token = json_response['access_token']
            if not token or len(token) < 10:  # Basic validation
                print(f"Token appears invalid (too short): {token}")
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

    # Keep original method signature but use fixed account number internally
    def initiate_stk_push(self, phone_number, amount, transaction_desc="Document Generation Payment", account_reference=None):
        """
        Initiate STK push using paybill model
        
        Parameters:
        - phone_number: Customer's phone number (format: 254XXXXXXXXX)
        - amount: Amount to be paid
        - transaction_desc: (Optional) Description of the transaction
        - account_reference: (Optional) Custom account reference. If None, will generate one
        """
        try:
            # Generate account reference if not provided
            if account_reference is None:
                account_reference = self.generate_account_reference()
            
            # Ensure account reference is 12 characters or less
            account_reference = account_reference[:12]
            
            # Remove grant_type from URL if present
            base_url = self.stk_push_url.split('?')[0]
            print(f"Making STK push request to: {base_url}")
            
            # Enhanced headers for M-Pesa API
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Cache-Control": "no-cache"
            }
            print(f"Using headers: {headers}")
            
            payload = {
                "BusinessShortCode": self.business_shortcode,
                "Password": self.password,
                "Timestamp": self.timestamp,
                "TransactionType": "CustomerBuyGoodsOnline",
                "Amount": amount,
                "PartyA": phone_number,
                "PartyB": self.till_number,
                "PhoneNumber": phone_number,
                "CallBackURL": self.my_callback_url,
                "TransactionDesc": transaction_desc,
                "AccountReference": account_reference  # New field added here
            }
            print(f"Request payload: {payload}")
            
            response = requests.post(
                base_url,
                headers=headers,
                json=payload
            )
            print(f"Response status code: {response.status_code}")
            print(f"Response content: {response.text}")
            
            return response.json()
        except Exception as e:
            print(str(e), "error initiating stk push")
            raise e

    def query_stk_push(self, checkout_request_id):
        try:
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
            return response.json()
        except Exception as e:
            print(str(e), "error querying stk push")
            raise e

def validate_phone_number(phone):
    """Validate phone number format"""
    if not phone.isdigit():
        return False
    if not phone.startswith('254'):
        return False
    if len(phone) != 12:
        return False
    return True

if __name__ == "__main__":
    try:
        mpesa = MpesaHandler()
        print("\n=== M-PESA Payment System ===")
        
        # Get phone number input
        while True:
            phone = input("\nEnter phone number (format: 254XXXXXXXXX): ").strip()
            if validate_phone_number(phone):
                break
            print("Invalid phone number! Use format: 254XXXXXXXXX")
        
        # Initiate payment
        amount = 1  # Set your amount
        desc = "Document Generation Payment"
        
        print("\nInitiating payment...")
        
        # Initiate STK push
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