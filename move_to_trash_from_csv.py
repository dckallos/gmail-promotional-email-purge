"""
Reads approved email IDs from a CSV and moves them to the Gmail Trash.
Processes in concurrent batches with state tracking to resume after failures.
"""
import os
import time
import random
import logging
import threading
import pandas as pd
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
load_dotenv()

# Configuration
CREDENTIALS_FILE = os.getenv('GMAIL_CREDENTIALS_FILE')
TOKEN_FILE = os.getenv('GMAIL_MODIFY_TOKEN_FILE')
INPUT_FILE = os.getenv('APPROVED_CSV_INPUT', 'approved_to_trash.csv')
SUCCESS_LOG_FILE = os.getenv('SUCCESS_LOG_FILE', 'trashed_success.log')
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

thread_local = threading.local()

class StateTracker:
    """
    Thread-safe tracker that logs progress and writes successful IDs
    to a checkpoint file to ensure resumability.
    """
    def __init__(self, total: int, log_file: str):
        self.total = total
        self.count = 0
        self.log_file = log_file
        self.lock = threading.Lock()

    def mark_success(self, chunk: list[str]):
        """Records the successful batch to disk and increments the progress."""
        with self.lock:
            # Append successful IDs to the checkpoint file safely
            with open(self.log_file, 'a') as f:
                for msg_id in chunk:
                    f.write(f"{msg_id}\n")

            self.count += len(chunk)
            logging.info(f"Progress: Completed {self.count} out of {self.total} pending emails trashed.")

def get_credentials():
    """Handles OAuth 2.0 authentication for modifying emails."""
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
    """Retrieves or builds a thread-specific Gmail API service."""
    if not hasattr(thread_local, "service"):
        thread_local.service = build('gmail', 'v1', credentials=creds)
    return thread_local.service

def trash_batch(chunk: list[str], creds, tracker: StateTracker) -> None:
    """Worker function to trash a batch of up to 1000 emails with backoff."""
    service = get_thread_local_service(creds)
    max_retries = 5
    base_delay = 2

    for attempt in range(max_retries):
        try:
            time.sleep(1)  # Soft throttle to ensure batch API stability
            service.users().messages().batchModify(
                userId='me',
                body={
                    'ids': chunk,
                    'addLabelIds': ['TRASH'],
                    'removeLabelIds': []
                }
            ).execute()

            # If successful, permanently record these IDs as done
            tracker.mark_success(chunk)
            break  # Exit retry loop

        except HttpError as error:
            if error.resp.status in [403, 429, 500, 502, 503] and attempt < max_retries - 1:
                sleep_time = (base_delay * (2 ** attempt)) + random.uniform(0, 1)
                logging.warning(f"Rate limit hit on batch. Backing off for {sleep_time:.2f}s...")
                time.sleep(sleep_time)
                continue
            logging.error(f"Failed to trash batch after retries: {error}")
            break

def trash_emails(creds, message_ids: list[str]) -> None:
    """Moves a list of message IDs to the trash using concurrent optimized batches."""
    if not message_ids:
        return

    total_emails = len(message_ids)
    logging.info(f"Starting concurrent batch trash process for {total_emails} pending emails...")

    chunk_size = 1000
    tracker = StateTracker(total_emails, SUCCESS_LOG_FILE)
    chunks = [message_ids[i:i + chunk_size] for i in range(0, total_emails, chunk_size)]

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(trash_batch, chunk, creds, tracker) for chunk in chunks]

        for future in as_completed(futures):
            future.result()

if __name__ == '__main__':
    try:
        if not INPUT_FILE or not os.path.exists(INPUT_FILE):
            logging.error(f"Could not find '{INPUT_FILE}'. Please ensure you have reviewed the data.")
        else:
            # 1. Load the original CSV of approved emails
            df = pd.read_csv(INPUT_FILE)

            if df.empty or 'Message_ID' not in df.columns:
                logging.warning("The provided CSV is empty or missing the 'Message_ID' column.")
            else:
                all_ids = df['Message_ID'].dropna().astype(str).tolist()

                # 2. Load the previously trashed IDs (if any exist)
                processed_ids = set()
                if os.path.exists(SUCCESS_LOG_FILE):
                    with open(SUCCESS_LOG_FILE, 'r') as f:
                        # Load all lines into a high-speed lookup set
                        processed_ids = set(line.strip() for line in f)

                # 3. Filter out IDs that have already been trashed
                ids_to_trash = [msg_id for msg_id in all_ids if msg_id not in processed_ids]

                if not ids_to_trash:
                    logging.info("All emails in the CSV have already been successfully trashed. Nothing left to do!")
                else:
                    logging.info(f"Found {len(all_ids)} total emails. {len(processed_ids)} already trashed. {len(ids_to_trash)} remaining.")
                    credentials = get_credentials()
                    trash_emails(credentials, ids_to_trash)
                    logging.info("Cleanup complete!")

    except Exception as e:
        logging.critical(f"A critical error occurred: {e}")