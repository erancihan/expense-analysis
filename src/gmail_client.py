import os.path
import base64
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import logging

# If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

logger = logging.getLogger(__name__)

class GmailClient:
    def __init__(self, credentials_path='secrets/credentials.json', token_path='secrets/token.json'):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.service = None
        self.authenticate()

    def authenticate(self):
        creds = None
        # The file token.json stores the user's access and refresh tokens, and is
        # created automatically when the authorization flow completes for the first
        # time.
        if os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)
        # If there are no (valid) credentials available, let the user log in.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(self.credentials_path):
                    logger.error(f"Credentials file not found at {self.credentials_path}")
                    raise FileNotFoundError(f"Please place your Google Cloud credentials file at {self.credentials_path}")
                
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path, SCOPES)
                creds = flow.run_local_server(port=0)
            # Save the credentials for the next run
            with open(self.token_path, 'w') as token:
                token.write(creds.to_json())

        self.service = build('gmail', 'v1', credentials=creds)
        logger.info("Gmail service authenticated successfully.")

    def fetch_emails(self, query):
        """
        Search for emails matching the query.
        Returns a list of message objects (containing 'id' and 'threadId').
        """
        try:
            results = self.service.users().messages().list(userId='me', q=query).execute()
            messages = results.get('messages', [])
            
            while 'nextPageToken' in results:
                page_token = results['nextPageToken']
                results = self.service.users().messages().list(userId='me', q=query, pageToken=page_token).execute()
                messages.extend(results.get('messages', []))
            
            return messages
        except Exception as e:
            logger.error(f"An error occurred while fetching emails: {e}")
            return []

    def get_message_detail(self, message_id):
        """
        Get full details of a specific message.
        """
        try:
            message = self.service.users().messages().get(userId='me', id=message_id).execute()
            return message
        except Exception as e:
            logger.error(f"An error occurred while getting message details: {e}")
            return None

    def get_attachment(self, message_id, attachment_id):
        """
        Get a specific attachment.
        """
        try:
            attachment = self.service.users().messages().attachments().get(
                userId='me', messageId=message_id, id=attachment_id).execute()
            file_data = base64.urlsafe_b64decode(attachment['data'].encode('UTF-8'))
            return file_data
        except Exception as e:
            logger.error(f"An error occurred while getting attachment: {e}")
            return None

    def save_attachment(self, message_id, attachment_id, filename, save_dir='temp'):
        """
        Downloads and saves an attachment to the specified directory.
        """
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)
            
        file_data = self.get_attachment(message_id, attachment_id)
        if file_data:
            path = os.path.join(save_dir, filename)
            with open(path, 'wb') as f:
                f.write(file_data)
            return path
        return None
