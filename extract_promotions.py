"""
Fetches promotional emails concurrently with API rate limiting.
Extracts metadata (Sender, Domain, Date, Subject) and saves to a CSV.
"""
import os
import time
import random
import logging
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

# Configure thread-safe logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load environment variables
load_dotenv()

# Configuration
CREDENTIALS_FILE = os.getenv('GMAIL_CREDENTIALS_FILE')
TOKEN_FILE = os.getenv('GMAIL_READONLY_TOKEN_FILE')
OUTPUT_FILE = os.getenv('RAW_CSV_OUTPUT', 'promotions_to_review.csv')
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

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
                logging.info(f"Progress: Completed {self.count} out of {self.total} total emails retrieved.")


def get_credentials():
    """
    Handles OAuth 2.0 authentication.
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
    Extracts a specific header's value.
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
    Implements a hard rate-limit ceiling and exponential backoff.
    """
    service = get_thread_local_service(creds)
    max_retries = 5
    base_delay = 1

    for attempt in range(max_retries):
        try:
            # HARD CEILING: 10 threads sleeping 0.3s ensures max ~2000 queries/min
            time.sleep(0.3)

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
            counter.increment()
            return result

        except HttpError as error:
            # Catch Rate Limits (403 or 429) and apply exponential backoff
            if error.resp.status in [403, 429] and attempt < max_retries - 1:
                sleep_time = (base_delay * (2 ** attempt)) + random.uniform(0, 1)
                logging.warning(f"Rate limit hit for {msg_id}. Backing off for {sleep_time:.2f}s...")
                time.sleep(sleep_time)
                continue

            logging.error(f"Failed to fetch {msg_id} after retries: {error}")
            counter.increment()
            return None
        except Exception as e:
            logging.error(f"Unexpected error fetching {msg_id}: {e}")
            counter.increment()
            return None

    counter.increment()
    return None


def extract_promotions(creds) -> list[dict]:
    """
    Fetches all promotional email IDs, then concurrently extracts their metadata.
    """
    service = get_thread_local_service(creds)

    logging.info("Phase 1: Fetching list of all promotional email IDs...")
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
        logging.info(f"Found {total_emails} emails. Moving to Phase 2 (Concurrent Extraction)...")

    except HttpError as error:
        logging.error(f"An API error occurred during ID extraction: {error}")
        return []

    emails_data = []
    counter = ProgressCounter(total_emails)

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
            logging.info(f"Extraction complete! Saved {len(data)} rows to '{OUTPUT_FILE}'.")
        else:
            logging.info("No promotional emails found.")
    except Exception as e:
        logging.critical(f"A critical error occurred: {e}")