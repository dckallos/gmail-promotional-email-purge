import os
import threading
import pandas as pd
from email.utils import parseaddr
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# Thread-local storage to hold a separate API service object for each thread
thread_local = threading.local()


class ProgressCounter:
    """
    A thread-safe counter to handle incremental logging.
    """

    def __init__(self, total: int):
        self.total = total
        self.count = 0
        self.lock = threading.Lock()

    def increment(self):
        with self.lock:
            self.count += 1
            if self.count % 1000 == 0 or self.count == self.total:
                print(f"Completed {self.count} out of {self.total} total emails retrieved...")


def get_credentials():
    """
    Handles OAuth 2.0 authentication and returns the credentials object.
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

    return creds


def get_thread_local_service(creds):
    """
    Retrieves or builds a thread-specific Gmail API service.
    """
    if not hasattr(thread_local, "service"):
        thread_local.service = build('gmail', 'v1', credentials=creds)
    return thread_local.service


def get_header(headers: list, name: str) -> str:
    """
    Extracts a specific header's value from the Gmail payload.
    """
    for header in headers:
        if header.get('name', '').lower() == name.lower():
            return header.get('value', 'Unknown')
    return "Unknown"


def extract_domain(sender_string: str) -> str:
    """
    Extracts the domain from a standard email sender string.
    """
    _, email_address = parseaddr(sender_string)
    if '@' in email_address:
        return email_address.split('@')[-1]
    return "Unknown"


def fetch_single_metadata(msg_id: str, creds, counter: ProgressCounter) -> dict:
    """
    Worker function to fetch metadata for a single email.

    Uses thread-local service objects to remain thread-safe.
    Updates the shared counter upon completion.
    """
    service = get_thread_local_service(creds)
    try:
        message_detail = service.users().messages().get(
            userId='me',
            id=msg_id,
            format='metadata',
            metadataHeaders=['From', 'Subject', 'Date']
        ).execute()

        headers = message_detail.get('payload', {}).get('headers', [])
        sender = get_header(headers, 'From')

        result = {
            'Message_ID': msg_id,
            'Date': get_header(headers, 'Date'),
            'Sender': sender,
            'Domain': extract_domain(sender),
            'Subject': get_header(headers, 'Subject')
        }
    except Exception as e:
        # If one API call fails, log it but don't crash the thread
        print(f"\nError fetching {msg_id}: {e}")
        result = None
    finally:
        counter.increment()

    return result


def extract_promotions(creds) -> list[dict]:
    """
    Fetches promotional emails concurrently and returns a list of dictionaries.

    Phase 1: Sequentially fetches all message IDs (500 per page).
    Phase 2: Concurrently fetches the metadata for all IDs using a ThreadPoolExecutor.
    """
    service = get_thread_local_service(creds)
    message_ids = []

    print("Phase 1: Fetching list of all promotional email IDs...")
    try:
        results = service.users().messages().list(userId='me', q='category:promotions', maxResults=500).execute()
        messages = results.get('messages', [])

        while 'nextPageToken' in results:
            page_token = results['nextPageToken']
            results = service.users().messages().list(userId='me', q='category:promotions', maxResults=500,
                                                      pageToken=page_token).execute()
            messages.extend(results.get('messages', []))

        if not messages:
            return []

        message_ids = [msg.get('id') for msg in messages if msg.get('id')]
        total_emails = len(message_ids)
        print(f"Found {total_emails} emails. Moving to Phase 2...")

    except HttpError as error:
        print(f'An API error occurred during ID extraction: {error}')
        return []

    print("\nPhase 2: Concurrently fetching metadata...")
    emails_data = []
    counter = ProgressCounter(total_emails)

    # max_workers=10 is a safe threshold to avoid hitting the Gmail API 250 requests/sec limit
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_single_metadata, msg_id, creds, counter): msg_id for msg_id in message_ids}

        for future in as_completed(futures):
            result = future.result()
            if result:
                emails_data.append(result)

    return emails_data


if __name__ == '__main__':
    try:
        credentials = get_credentials()
        data = extract_promotions(credentials)

        if data:
            df = pd.DataFrame(data)
            df.to_csv(OUTPUT_FILE, index=False)
            print(f"\nExtraction complete! Saved {len(data)} rows to '{OUTPUT_FILE}'.")
            print("Please review the CSV, delete the rows you want to KEEP, and save as the approved input file.")
        else:
            print("No promotional emails found.")
    except Exception as e:
        print(f"A critical error occurred: {e}")