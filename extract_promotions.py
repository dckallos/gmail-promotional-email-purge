import os
import pandas as pd
from email.utils import parseaddr
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Load environment variables
load_dotenv()

# Configuration
CREDENTIALS_FILE = os.getenv('GMAIL_CREDENTIALS_FILE')
TOKEN_FILE = os.getenv('GMAIL_READONLY_TOKEN_FILE')
OUTPUT_FILE = os.getenv('RAW_CSV_OUTPUT', 'promotions_to_review.csv')
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']


def authenticate_gmail():
    """
    Handles OAuth 2.0 authentication for reading emails.

    Validates environment variables, checks for existing tokens,
    and initiates the OAuth flow if valid credentials are not found.
    Returns an authenticated Gmail API service resource.
    """
    if not CREDENTIALS_FILE:
        raise ValueError("GMAIL_CREDENTIALS_FILE is not set in the .env file.")

    creds = None
    if TOKEN_FILE and os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        if TOKEN_FILE:
            with open(TOKEN_FILE, 'w') as token:
                token.write(creds.to_json())

    return build('gmail', 'v1', credentials=creds)


def get_header(headers: list, name: str) -> str:
    """
    Extracts a specific header's value from the Gmail payload.

    Iterates through the list of header dictionaries and returns the
    value matching the requested key (case-insensitive).
    """
    for header in headers:
        if header.get('name', '').lower() == name.lower():
            return header.get('value', 'Unknown')
    return "Unknown"


def extract_domain(sender_string: str) -> str:
    """
    Extracts the domain from a standard email sender string.

    Parses strings like '"Company Name" <hello@company.com>'
    and returns just 'company.com'.
    """
    # parseaddr returns a tuple: ('Name', 'email@domain.com')
    _, email_address = parseaddr(sender_string)
    if '@' in email_address:
        return email_address.split('@')[-1]
    return "Unknown"


def extract_promotions(service) -> list[dict]:
    """
    Fetches promotional emails and returns a list of dictionaries.

    Queries the Gmail API for messages in the promotions category,
    handles pagination automatically, and extracts relevant metadata
    including the sender, domain, date, and subject.
    """
    emails_data = []
    print("Fetching promotional emails (this may take a minute)...")

    try:
        results = service.users().messages().list(userId='me', q='category:promotions', maxResults=500).execute()
        messages = results.get('messages', [])

        # Handle pagination defensively
        while 'nextPageToken' in results:
            page_token = results['nextPageToken']
            results = service.users().messages().list(userId='me', q='category:promotions', maxResults=500,
                                                      pageToken=page_token).execute()
            messages.extend(results.get('messages', []))

        if not messages:
            return []

        print(f"Found {len(messages)} emails. Fetching metadata...")

        for msg in messages:
            msg_id = msg.get('id')
            if not msg_id:
                continue

            message_detail = service.users().messages().get(
                userId='me',
                id=msg_id,
                format='metadata',
                metadataHeaders=['From', 'Subject', 'Date']
            ).execute()

            headers = message_detail.get('payload', {}).get('headers', [])
            sender = get_header(headers, 'From')

            emails_data.append({
                'Message_ID': msg_id,
                'Date': get_header(headers, 'Date'),
                'Sender': sender,
                'Domain': extract_domain(sender),
                'Subject': get_header(headers, 'Subject')
            })

        return emails_data

    except HttpError as error:
        print(f'An API error occurred during extraction: {error}')
        return []


if __name__ == '__main__':
    try:
        service = authenticate_gmail()
        data = extract_promotions(service)

        if data:
            df = pd.DataFrame(data)
            df.to_csv(OUTPUT_FILE, index=False)
            print(f"Extraction complete! Saved to '{OUTPUT_FILE}'.")
            print("Please review the CSV, delete the rows you want to KEEP, and save as the approved input file.")
        else:
            print("No promotional emails found.")
    except Exception as e:
        print(f"A critical error occurred: {e}")