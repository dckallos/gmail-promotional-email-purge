
# Gmail Promotions Cleaner 🧹✉️

## Introduction
I created this project out of necessity. After a decade of using the same Gmail account, my inbox had accumulated hundreds of thousands of promotional emails, forcing me to pay a monthly fee for excess Google One storage. 

I built this tool because I wanted to review the emails I was about to delete rather than simply "delete all" promotional emails indiscriminately. It allows you to bulk-delete promotional emails using the Gmail API, but strictly enforces a **human-in-the-loop** review process. In addition, the "delete all" usually glitches over the Gmail UI, and it only deletes a portion of the total emails when a user requests to "delete all" promo emails. With this utility, you get to see exactly what is being deleted *before* any destructive actions are taken.

## Features
* **Human-in-the-loop Safety:** No emails are deleted without explicit manual review.
* **Highly Concurrent:** Uses multithreading to fetch and process thousands of emails in minutes.
* **Rate-Limit Resilient:** Built-in throttling and exponential backoff prevent Google API bans (429/403 errors).
* **Domain Extraction:** Automatically parses the sender's domain for easy filtering and pivoting in Excel/Pandas.

## How it Works

1. **Extraction:** A Python script connects to your inbox via the Gmail API, searches for all emails tagged under the `Promotions` category, and downloads their metadata (Sender, Domain, Date, Subject, Message ID) into a local CSV file.
2. **Manual Review:** You open the CSV using Excel, Python (Pandas), R, or any spreadsheet tool. You filter and **delete the rows of the emails you want to KEEP**. The remaining rows represent the emails you approve for deletion.
3. **Execution:** A second Python script reads your reviewed CSV file and uses the Gmail API to move all of those specific emails to your Trash folder. (Google retains trashed emails for 30 days before permanent deletion, giving you an extra safety net).

## Prerequisites
* Python 3.8+
* A Google Cloud project with the **Gmail API** enabled.

## Setup & Installation

### 1. Google Cloud Credentials
1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project and enable the **Gmail API**.
3. Configure the OAuth Consent Screen (set it to "Desktop App").
4. Create OAuth 2.0 Client ID credentials.
5. Download the JSON file, rename it to `credentials.json`, and place it in the root directory of this project.

### 2. Local Environment Setup
Clone this repository and install the required dependencies:

```bash
git clone https://github.com/yourusername/gmail-promotions-cleaner.git
cd gmail-promotions-cleaner

# Install required Python packages
pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib python-dotenv pandas

```

Create a `.env` file in the root directory to manage your file paths:

```env
GMAIL_CREDENTIALS_FILE="credentials.json"
GMAIL_READONLY_TOKEN_FILE="token_readonly.json"
GMAIL_MODIFY_TOKEN_FILE="token_modify.json"
RAW_CSV_OUTPUT="promotions_to_review.csv"
APPROVED_CSV_INPUT="approved_to_trash.csv"

```

## Usage Guide

### Phase 1: Extract

Run the extraction script. On the first run, it will open a browser window asking you to authenticate with your Google account.

```bash
python extract_promotions.py

```

*This will generate `promotions_to_review.csv`.*

### Phase 2: Review (Human-in-the-Loop)

1. Open `promotions_to_review.csv` in your preferred data tool.
2. Review the list. **Remove the rows for any emails you do NOT want to delete.**
3. Save the resulting file as `approved_to_trash.csv` in the same directory.

### Phase 3: Trash

Run the deletion script. This will also ask for browser authentication on the first run to grant "Modify" permissions.

```bash
python move_to_trash_from_csv.py

```

*The script will read `approved_to_trash.csv` and move those specific emails to your Gmail Trash in batches of 1,000.*

## Disclaimer

**Use at your own risk.** While this script moves emails to the Trash (allowing a 30-day recovery window) rather than permanently deleting them bypassing the trash, it interacts with your live email data. Always double-check your `approved_to_trash.csv` before running the final script. The author is not responsible for accidentally deleted data.
