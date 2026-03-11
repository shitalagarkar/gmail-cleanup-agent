import os
import pickle
import json
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from datetime import date, datetime, timedelta
from collections import defaultdict
from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from flask_session import Session
from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables from .env file
load_dotenv()

# Flask app
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')

# Global Gmail service for local use
gmail_service = None

if not os.getenv('RENDER'):
    # Local — use filesystem sessions
    app.config['SESSION_TYPE'] = 'filesystem'
    app.config['SESSION_PERMANENT'] = False
    app.config['SESSION_FILE_DIR'] = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'flask_session'
    )
    Session(app)
# On Render — Flask default cookie sessions (no disk needed)

# Gmail service global variable
gmail_service = None

# OpenAI is optional — initialized per request
OPENAI_API_KEY_ENV = os.getenv('OPENAI_API_KEY')

# SCOPES
SCOPES = ['https://www.googleapis.com/auth/gmail.modify',
          'https://mail.google.com/']


# ─────────────────────────────────────────
# OpenAI Helper
# ─────────────────────────────────────────

def get_openai_client():
    """
    Returns OpenAI client if API key is available.
    Checks session first, then environment variable.
    Returns None if no key found.
    """
    api_key = session.get('openai_api_key') or OPENAI_API_KEY_ENV
    if api_key:
        return OpenAI(api_key=api_key)
    return None


def has_openai_key():
    """Check if OpenAI key is available."""
    return bool(session.get('openai_api_key') or OPENAI_API_KEY_ENV)

# ─────────────────────────────────────────
# Gmail Authentication
# ─────────────────────────────────────────

def get_gmail_service():
    """
    Returns Gmail API service.
    Local: uses token.pickle
    Render: uses session token
    """
    SCOPES = [
        'https://www.googleapis.com/auth/gmail.modify',
        'https://mail.google.com/'
    ]

    creds = None

    # On Render — use session token
    if os.getenv('RENDER'):
        token_data = session.get('gmail_token')
        if not token_data:
            return None
        try:
            credentials_json = os.getenv('GOOGLE_CREDENTIALS')
            creds_data = json.loads(credentials_json)
            client_id = creds_data['web']['client_id']
            client_secret = creds_data['web']['client_secret']

            creds = Credentials(
                token=token_data['access_token'],
                refresh_token=token_data.get('refresh_token'),
                token_uri='https://oauth2.googleapis.com/token',
                client_id=client_id,
                client_secret=client_secret
            )
        except Exception as e:
            print(f"Error loading session credentials: {e}")
            return None

        # Refresh if expired
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                session['gmail_token'] = json.loads(creds.to_json())
            except Exception as e:
                print(f"Error refreshing credentials: {e}")
                return None

    else:
        # Local — use token.pickle
        if os.path.exists('token.pickle'):
            with open('token.pickle', 'rb') as token:
                creds = pickle.load(token)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                credentials_json = os.getenv('GOOGLE_CREDENTIALS')
                if credentials_json:
                    import tempfile
                    with tempfile.NamedTemporaryFile(
                        mode='w', suffix='.json', delete=False
                    ) as f:
                        f.write(credentials_json)
                        temp_path = f.name
                    flow = InstalledAppFlow.from_client_secrets_file(
                        temp_path, SCOPES
                    )
                    os.unlink(temp_path)
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        'credentials.json', SCOPES
                    )
                creds = flow.run_local_server(port=0)

            with open('token.pickle', 'wb') as token:
                pickle.dump(creds, token)

    return build('gmail', 'v1', credentials=creds)

# ─────────────────────────────────────────
# Date Filter
# ─────────────────────────────────────────

def get_date_filter(months):
    """
    Converts number of months into a Gmail search filter.
    Uses start of day to match Gmail's behavior exactly.
    """
    # Minimum 1 day to avoid zero
    days = max(1, round(months * 30))
    date = datetime.now() - timedelta(days=days)
    
    # Use START of that day to match Gmail exactly
    # Gmail's after:YYYY/MM/DD means from 00:00:00 of that day
    date_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
    
    filter_str = date_start.strftime('after:%Y/%m/%d')
    print(f"Filtering emails from: {date_start.strftime('%Y-%m-%d 00:00:00')}")
    print(f"Using Gmail query filter: {filter_str}")
    return filter_str


# ─────────────────────────────────────────
# Email Scanner
# ─────────────────────────────────────────

def scan_emails(service, months=6):
    """
    Scans Gmail inbox and groups emails by sender.
    Also collects last 3 email subjects per sender
    for AI categorization.
    """
    print(f"Scanning emails from last {months} months...")

    date_filter = get_date_filter(months)
    query = f'in:inbox {date_filter}'
    print(f"Using Gmail query: {query}")
    sender_counts = defaultdict(int)
    sender_names = {}
    sender_subjects = defaultdict(list)
    sender_unread = defaultdict(int)
    sender_read = defaultdict(int)

    page_token = None
    total_fetched = 0

    while True:
        if page_token:
            results = service.users().messages().list(
                userId='me',
                q=query,
                maxResults=500,
                pageToken=page_token
            ).execute()
        else:
            results = service.users().messages().list(
                userId='me',
                q=query,
                maxResults=500
            ).execute()

        messages = results.get('messages', [])

        if not messages:
            break

        print(f"Processing {len(messages)} emails...")

        for message in messages:
            msg = service.users().messages().get(
                userId='me',
                id=message['id'],
                format='metadata',
                metadataHeaders=['From', 'Subject']
            ).execute()

            headers = msg['payload']['headers']
            from_value = ''
            subject_value = ''

            for header in headers:
                if header['name'] == 'From':
                    from_value = header['value']
                if header['name'] == 'Subject':
                    subject_value = header['value']

            if from_value:
                if '<' in from_value:
                    name = from_value.split('<')[0].strip().strip('"')
                    email = from_value.split('<')[1].strip('>')
                else:
                    name = from_value
                    email = from_value

                sender_counts[email] += 1
                sender_names[email] = name

                # Collect up to 5 subjects per sender for AI
                if len(sender_subjects[email]) < 5:
                    if subject_value:
                        sender_subjects[email].append(subject_value)

                # Track read vs unread
                labels = msg.get('labelIds', [])
                if 'UNREAD' in labels:
                    sender_unread[email] += 1
                else:
                    sender_read[email] += 1

            total_fetched += 1

        print(f"Total emails processed so far: {total_fetched}")

        page_token = results.get('nextPageToken')
        print(f"Next page token exists: {bool(page_token)}")
        if not page_token:
            print("No more pages — moving to next query!")
            break

    # Build final senders list
    senders = []
    for email, count in sender_counts.items():
        total = count
        unread = sender_unread.get(email, 0)
        read = sender_read.get(email, 0)
        never_read = (read == 0 and unread > 0)

        senders.append({
            'email': email,
            'name': sender_names.get(email, email),
            'count': total,
            'unread': unread,
            'read': read,
            'never_read': never_read,
            'subjects': sender_subjects.get(email, []),
            'category': None,
            'safety': None
        })

    senders.sort(key=lambda x: x['count'], reverse=True)
    print(f"Scan complete! Found {len(senders)} unique senders.")
    return senders


# ─────────────────────────────────────────
# AI Categorization
# ─────────────────────────────────────────

def categorize_senders_with_ai(senders):
    """
    Sends sender info to Claude AI in batches.
    Claude categorizes each sender and gives
    a safety score for deletion.
    """
    print("Starting AI categorization...")

    # Process in batches of 10 to save API costs
    batch_size = 10
    categorized = []

    for i in range(0, len(senders), batch_size):
        batch = senders[i:i + batch_size]

        # Build prompt for this batch
        sender_list = ""
        for j, sender in enumerate(batch):
            subjects = ', '.join(sender['subjects']) if sender['subjects'] else 'No subjects available'
            sender_list += f"""
Sender {j+1}:
- Name: {sender['name']}
- Email: {sender['email']}
- Total emails: {sender['count']}
- Never read: {sender['never_read']}
- Sample subjects: {subjects}
"""

        prompt = f"""You are an expert email categorization assistant.
    Analyze these email senders and categorize each one accurately.

    {sender_list}

    CATEGORIZATION RULES:
    ━━━━━━━━━━━━━━━━━━━━

    Newsletter (mark as SAFE):
    - Regular digest emails (weekly/monthly/daily updates)
    - Blog updates, Medium, Substack
    - News sites (Times of India, Economic Times, NDTV, BBC)
    - Product updates from SaaS tools
    - Community digests
    - ANY email with "unsubscribe" in typical footer
    - Indian platforms: Zerodha, Groww, ET Markets, Moneycontrol

    Promotional (mark as SAFE):
    - Sales, offers, discounts, deals
    - E-commerce: Amazon, Flipkart, Myntra, Meesho, Nykaa
    - Food delivery: Swiggy, Zomato
    - Travel: MakeMyTrip, Goibibo, IRCTC offers
    - Any "% off", "limited time", "sale" type emails
    - Reward points, cashback notifications

    Notification (mark as SAFE):
    - Automated system notifications
    - App activity alerts
    - Social media notifications (LinkedIn, Twitter, Instagram)
    - GitHub, Jira, Slack notifications
    - OTP and transaction alerts from apps (NOT banks)

    Social (mark as SAFE):
    - Facebook, Instagram, Twitter, LinkedIn updates
    - YouTube channel updates
    - Community forum notifications

    Spam (mark as SAFE):
    - Unknown senders
    - Suspicious looking emails
    - Unsolicited bulk emails

    Personal (mark as KEEP):
    - Emails from real people (firstname.lastname@ or name@)
    - Friends, family, colleagues
    - Gmail, Yahoo, Hotmail personal addresses
    - Any human sounding name as sender

    Work (mark as KEEP):
    - Your company domain emails
    - HR, payroll, IT notifications
    - Professional services you pay for
    - Invoice and receipt emails
    - B2B software you use

    Important (mark as KEEP):
    - Banks: HDFC, ICICI, SBI, Axis, Kotak and ALL banks
    - Government: IT dept, UIDAI, DigiLocker, GST
    - Insurance: LIC, health insurance providers
    - Investments: verified stock brokers, mutual funds
    - Utilities: electricity, water, gas bills
    - Healthcare: hospitals, doctors, pharmacies

    SAFETY DECISION GUIDE:
    ━━━━━━━━━━━━━━━━━━━━
    "safe"   → Newsletter, Promotional, Notification, Social, Spam
            These are bulk/automated emails — safe to delete
            When in doubt between safe and review → choose SAFE
            
    "review" → ONLY use this when genuinely unsure if personal or automated
            Use this SPARINGLY — less than 20% of emails should be review
            
    "keep"   → Personal, Work, Important financial/govt emails
            These should NEVER be deleted automatically

    NEVER READ RULE:
    ━━━━━━━━━━━━━━━
    If never_read is true AND category is Newsletter/Promotional
    → Almost certainly safe to delete

    For each sender respond with EXACTLY this JSON format:
    [
    {{
        "index": 1,
        "category": "Newsletter|Promotional|Notification|Personal|Work|Social|Spam|Important|Other",
        "safety": "safe|review|keep",
        "reason": "one line reason max 8 words"
    }}
    ]

    Respond with ONLY the JSON array, nothing else."""       
        try:
              
            client = get_openai_client()
            if not client:
              return
            message = client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=1000,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )

            # Parse OpenAI's response
            response_text = message.choices[0].message.content.strip()

            # Clean up response if needed
            if response_text.startswith('```'):
                response_text = response_text.split('```')[1]
                if response_text.startswith('json'):
                    response_text = response_text[4:]

            import json
            ai_results = json.loads(response_text)

            # Map results back to senders
            for result in ai_results:
                idx = result['index'] - 1
                if idx < len(batch):
                    batch[idx]['category'] = result['category']
                    batch[idx]['safety'] = result['safety']
                    batch[idx]['reason'] = result.get('reason', '')

            print(f"AI categorized batch {i//batch_size + 1} of {len(senders)//batch_size + 1}")

        except Exception as e:
            print(f"AI categorization error for batch: {e}")
            # If AI fails, set defaults
            for sender in batch:
                sender['category'] = 'Other'
                sender['safety'] = 'review'
                sender['reason'] = 'Could not categorize'

        categorized.extend(batch)

    print("AI categorization complete!")
    return categorized


# ─────────────────────────────────────────
# Delete Emails
# ─────────────────────────────────────────

def delete_emails_from_sender(service, sender_email, months,
                               delete_all_time=False, keep_recent_months=None):
    """
    Deletes emails from a specific sender with 3 modes:
    1. delete_all_time=True  → Delete ALL emails ever
    2. keep_recent_months    → Delete emails OLDER than X months
    3. Default               → Delete within selected time period
    """
    if delete_all_time:
        # Mode 1 — Delete everything!
        query = f'from:{sender_email} -in:trash -in:spam'
        print(f"Mode: Delete ALL emails ever from {sender_email}")

    elif keep_recent_months is not None:
        # Mode 2 — Delete old emails, keep recent ones
        # We need emails BEFORE the retention date
        keep_days = max(1, round(keep_recent_months * 30))
        cutoff_date = datetime.now() - timedelta(days=keep_days)

        # Gmail 'before:' filter deletes emails older than cutoff
        before_filter = cutoff_date.strftime('before:%Y/%m/%d')
        query = f'from:{sender_email} {before_filter} -in:trash -in:spam'
        print(f"Mode: Keep last {keep_recent_months} months")
        print(f"Deleting emails before: {cutoff_date.strftime('%Y-%m-%d')}")
        print(f"Query: {query}")

    else:
        # Mode 3 — Delete within selected time period
        date_filter = get_date_filter(months)
        query = f'in:anywhere from:{sender_email} {date_filter} -in:trash -in:spam'
        print(f"Mode: Delete within {months} months from {sender_email}")

    deleted_count = 0
    page_token = None

    while True:
        if page_token:
            results = service.users().messages().list(
                userId='me',
                q=query,
                maxResults=500,
                pageToken=page_token
            ).execute()
        else:
            results = service.users().messages().list(
                userId='me',
                q=query,
                maxResults=500
            ).execute()

        messages = results.get('messages', [])

        if not messages:
            break

        message_ids = [msg['id'] for msg in messages]

        service.users().messages().batchDelete(
            userId='me',
            body={'ids': message_ids}
        ).execute()

        deleted_count += len(message_ids)
        print(f"Deleted {deleted_count} emails from {sender_email}")

        page_token = results.get('nextPageToken')
        if not page_token:
            break

    return deleted_count


# ─────────────────────────────────────────
# Unsubscribe Finder
# ─────────────────────────────────────────

def find_unsubscribe_info(service, sender_email):
    """
    Finds unsubscribe link and mailto from
    the latest email of a sender.
    Checks both email headers and body.
    """
    import re
    import base64

    try:
        # Get latest email from this sender
        results = service.users().messages().list(
            userId='me',
            q=f'from:{sender_email}',
            maxResults=1
        ).execute()

        messages = results.get('messages', [])
        if not messages:
            return None

        # Get full email content
        msg = service.users().messages().get(
            userId='me',
            id=messages[0]['id'],
            format='full'
        ).execute()

        unsubscribe_url = None
        unsubscribe_email = None

        # Check 1 — List-Unsubscribe header (most reliable!)
        headers = msg['payload'].get('headers', [])
        for header in headers:
            if header['name'].lower() == 'list-unsubscribe':
                value = header['value']
                print(f"Found List-Unsubscribe header: {value}")

                # Extract URL
                url_match = re.search(r'<(https?://[^>]+)>', value)
                if url_match:
                    unsubscribe_url = url_match.group(1)

                # Extract mailto
                email_match = re.search(r'<mailto:([^>]+)>', value)
                if email_match:
                    unsubscribe_email = email_match.group(1)

        # Check 2 — Search email body for unsubscribe link
        if not unsubscribe_url:
            body = get_email_body(msg)
            if body:
                # Find unsubscribe links in body
                unsubscribe_patterns = [
                    r'href=["\']([^"\']*unsubscribe[^"\']*)["\']',
                    r'href=["\']([^"\']*opt.out[^"\']*)["\']',
                    r'href=["\']([^"\']*optout[^"\']*)["\']',
                    r'(https?://[^\s<>"]*unsubscribe[^\s<>"]*)',
                ]

                for pattern in unsubscribe_patterns:
                    match = re.search(pattern, body, re.IGNORECASE)
                    if match:
                        unsubscribe_url = match.group(1)
                        print(f"Found unsubscribe URL in body: {unsubscribe_url}")
                        break

        print(f"=== FINAL RESULT ===")
        print(f"URL found: {unsubscribe_url}")
        print(f"Email found: {unsubscribe_email}")

        if unsubscribe_url or unsubscribe_email:
            result = {
                'url': unsubscribe_url,
                'email': unsubscribe_email,
                'found': True
            }
            print(f"Returning: {result}")
            return result

        print("Returning: not found")
        return {'found': False}

    except Exception as e:
        print(f"Error finding unsubscribe for {sender_email}: {e}")
        return {'found': False}


def get_email_body(msg):
    """
    Extracts plain text or HTML body from email.
    """
    import base64

    try:
        payload = msg['payload']

        # Simple email — body directly in payload
        if 'body' in payload and payload['body'].get('data'):
            data = payload['body']['data']
            return base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')

        # Multipart email — body in parts
        if 'parts' in payload:
            for part in payload['parts']:
                if part['mimeType'] in ['text/plain', 'text/html']:
                    if part['body'].get('data'):
                        data = part['body']['data']
                        return base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')

                # Nested parts
                if 'parts' in part:
                    for subpart in part['parts']:
                        if subpart['mimeType'] in ['text/plain', 'text/html']:
                            if subpart['body'].get('data'):
                                data = subpart['body']['data']
                                return base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')

        return None

    except Exception as e:
        print(f"Error extracting body: {e}")
        return None

# ─────────────────────────────────────────
# Flask Routes
# ─────────────────────────────────────────

@app.route('/')
def index():
    """Main page"""
    # If running on Render and not authenticated, show login
    if os.getenv('RENDER') and not session.get('gmail_authenticated'):
        return render_template('login.html')
    return render_template('index.html')


@app.route('/scan', methods=['POST'])
def scan():
    """
    Scans Gmail and returns sender list as JSON.
    """
    global gmail_service
    # On Render, get service from session token
    if os.getenv('RENDER'):
        if not session.get('gmail_authenticated'):
            return jsonify({
                'status': 'error',
                'message': 'Not authenticated. Please login first.'
            })
        gmail_service = get_gmail_service()
    months = float(request.json.get('months', 6))
    use_ai = request.json.get('use_ai', True)

    print(f"DEBUG: Scanning for {months} months, AI: {use_ai}")

    try:
        senders = scan_emails(gmail_service, months)

        if use_ai and senders and has_openai_key():
               senders = categorize_senders_with_ai(senders)

        return jsonify({
            'status': 'success',
            'senders': senders,
            'total': len(senders)
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        })


@app.route('/delete', methods=['POST'])
def delete():
    """
    Deletes emails from selected senders.
    """
    global gmail_service
    # On Render, get service from session token
    if os.getenv('RENDER'):
        if not session.get('gmail_authenticated'):
            return jsonify({
                'status': 'error',
                'message': 'Not authenticated. Please login first.'
            })
        gmail_service = get_gmail_service()

    selected_emails = request.json.get('emails', [])
    months = float(request.json.get('months', 6))
    delete_all_time = request.json.get('delete_all_time', False)
    keep_recent_months = request.json.get('keep_recent_months', None)

    if not selected_emails:
        return jsonify({
            'status': 'error',
            'message': 'No senders selected!'
        })

    try:
        total_deleted = 0
        results = []

        for sender_email in selected_emails:
            count = delete_emails_from_sender(
                gmail_service,
                sender_email,
                months,
                delete_all_time,
                keep_recent_months
            )
            total_deleted += count
            results.append({
                'email': sender_email,
                'deleted': count
            })

        return jsonify({
            'status': 'success',
            'total_deleted': total_deleted,
            'results': results
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        })


@app.route('/find_unsubscribe', methods=['POST'])
def find_unsubscribe():
    """
    Finds unsubscribe link for a specific sender.
    Called when user clicks Find Link button.
    """
    global gmail_service
    # On Render, get service from session token
    if os.getenv('RENDER'):
        if not session.get('gmail_authenticated'):
            return jsonify({
                'status': 'error',
                'message': 'Not authenticated. Please login first.'
            })
        gmail_service = get_gmail_service()

    sender_email = request.json.get('email')

    if not sender_email:
        return jsonify({
            'status': 'error',
            'message': 'No email provided'
        })

    try:
        result = find_unsubscribe_info(gmail_service, sender_email)

        # Handle case where no emails found (e.g. already deleted)
        if result is None:
            return jsonify({
                'status': 'success',
                'email': sender_email,
                'unsubscribe': {
                    'found': False,
                    'url': None,
                    'email': None
                }
            })

        return jsonify({
            'status': 'success',
            'email': sender_email,
            'unsubscribe': result
        })
    except Exception as e:
        print(f"Unsubscribe error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        })

# ─────────────────────────────────────────
# AI Inbox Summary
# ─────────────────────────────────────────

@app.route('/summary', methods=['POST'])
def summary():
    """
    Generates AI powered inbox summary
    with insights and recommendations.
    """
    senders = request.json.get('senders', [])
    privacy_mode = request.json.get('privacy_mode', False)

    if not senders:
        return jsonify({
            'status': 'error',
            'message': 'No senders data provided'
        })

    try:
        # Build stats
        total_emails = sum(s['count'] for s in senders)
        total_senders = len(senders)
        never_read = [s for s in senders if s.get('never_read')]
        safe_senders = [s for s in senders if s.get('safety') == 'safe']
        keep_senders = [s for s in senders if s.get('safety') == 'keep']
        safe_emails = sum(s['count'] for s in safe_senders)

        # Category breakdown
        categories = {}
        for s in senders:
            cat = s.get('category', 'Other')
            if cat not in categories:
                categories[cat] = {'senders': 0, 'emails': 0}
            categories[cat]['senders'] += 1
            categories[cat]['emails'] += s['count']

        # Top senders
        top_senders = sorted(senders, key=lambda x: x['count'], reverse=True)[:5]

        # Build sender list for AI
        sender_summary = ""
        for s in senders:
            if privacy_mode:
                # Privacy mode — no subjects
                sender_summary += f"- {s['name']} ({s['email']}): {s['count']} emails, category={s.get('category', 'Unknown')}, safety={s.get('safety', 'unknown')}, never_read={s.get('never_read', False)}\n"
            else:
                # Full mode — include subjects
                subjects = ', '.join(s.get('subjects', [])[:3])
                sender_summary += f"- {s['name']} ({s['email']}): {s['count']} emails, category={s.get('category', 'Unknown')}, safety={s.get('safety', 'unknown')}, never_read={s.get('never_read', False)}, subjects=[{subjects}]\n"

        prompt = f"""You are an intelligent email inbox analyst.
Analyze this inbox data and provide a helpful summary.

INBOX STATS:
- Total emails: {total_emails}
- Total senders: {total_senders}
- Never read senders: {len(never_read)}
- Safe to delete: {safe_emails} emails from {len(safe_senders)} senders
- Important to keep: {len(keep_senders)} senders

CATEGORY BREAKDOWN:
{chr(10).join([f"- {cat}: {data['senders']} senders, {data['emails']} emails" for cat, data in categories.items()])}

TOP 5 SENDERS:
{chr(10).join([f"- {s['name']}: {s['count']} emails" for s in top_senders])}

ALL SENDERS:
{sender_summary}

Provide a response in this EXACT JSON format:
{{
    "headline": "One engaging sentence summarizing inbox health",
    "stats": {{
        "cleanup_percentage": <number 0-100>,
        "storage_estimate": "<e.g. 2.3 GB>",
        "never_read_percentage": <number 0-100>
    }},
    "insights": [
        "Insight 1 — specific and actionable",
        "Insight 2 — specific and actionable", 
        "Insight 3 — specific and actionable"
    ],
    "recommendations": [
        {{
            "action": "Short action title",
            "description": "One line description",
            "impact": "high|medium|low",
            "category": "delete|unsubscribe|keep|review"
        }}
    ],
    "warnings": [
        "Warning about important emails to keep"
    ]
}}

Respond with ONLY the JSON, nothing else."""

        client = get_openai_client()
        if not client:
            return jsonify({
                'status': 'error',
                'message': 'No OpenAI key. Please add your API key to use this feature.'
            })
        message = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )

        response_text = message.choices[0].message.content.strip()
        if response_text.startswith('```'):
            response_text = response_text.split('```')[1]
            if response_text.startswith('json'):
                response_text = response_text[4:]

        import json
        ai_summary = json.loads(response_text)

        return jsonify({
            'status': 'success',
            'summary': ai_summary,
            'stats': {
                'total_emails': total_emails,
                'total_senders': total_senders,
                'never_read': len(never_read),
                'safe_emails': safe_emails,
                'safe_senders': len(safe_senders)
            }
        })

    except Exception as e:
        print(f"Summary error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        })



@app.route('/send_unsubscribe', methods=['POST'])
def send_unsubscribe():
    """
    Sends unsubscribe email on behalf of user.
    Called when user approves email unsubscribe.
    """
    global gmail_service
    # On Render, get service from session token
    if os.getenv('RENDER'):
        if not session.get('gmail_authenticated'):
            return jsonify({
                'status': 'error',
                'message': 'Not authenticated. Please login first.'
            })
        gmail_service = get_gmail_service()

    unsubscribe_email = request.json.get('unsubscribe_email')
    sender_name = request.json.get('sender_name', 'this sender')

    if not unsubscribe_email:
        return jsonify({
            'status': 'error',
            'message': 'No unsubscribe email provided'
        })

    try:
        import base64
        from email.mime.text import MIMEText

        # Parse email and subject from mailto
        # Format can be: email@domain.com or
        # email@domain.com?subject=Unsubscribe
        email_parts = unsubscribe_email.split('?')
        to_email = email_parts[0]

        # Extract subject if present
        subject = 'Unsubscribe'
        if len(email_parts) > 1:
            import urllib.parse
            params = urllib.parse.parse_qs(email_parts[1])
            if 'subject' in params:
                subject = params['subject'][0]

        # Create unsubscribe email
        message = MIMEText(
            f"Please unsubscribe me from all mailing lists.\n\n"
            f"Thank you."
        )
        message['to'] = to_email
        message['subject'] = subject

        # Encode and send
        raw = base64.urlsafe_b64encode(
            message.as_bytes()
        ).decode('utf-8')

        gmail_service.users().messages().send(
            userId='me',
            body={'raw': raw}
        ).execute()

        print(f"Unsubscribe email sent to: {to_email}")

        return jsonify({
            'status': 'success',
            'message': f'Unsubscribe email sent to {to_email}'
        })

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        })


# ─────────────────────────────────────────
# Gmail OAuth Web Flow (for Render hosting)
# ─────────────────────────────────────────

@app.route('/auth/login')
def auth_login():
    """Starts Gmail OAuth flow for hosted version."""
    SCOPES = [
        'https://www.googleapis.com/auth/gmail.modify',
        'https://mail.google.com/'
    ]

    credentials_json = os.getenv('GOOGLE_CREDENTIALS')
    if not credentials_json:
        return "Error: Google credentials not configured", 500

    try:
        creds_data = json.loads(credentials_json)
        client_id = creds_data['web']['client_id']
        client_secret = creds_data['web']['client_secret']

        redirect_uri = 'https://gmail-cleanup-agent.onrender.com/auth/callback'

        from requests_oauthlib import OAuth2Session
        oauth = OAuth2Session(
            client_id,
            scope=SCOPES,
            redirect_uri=redirect_uri
        )

        auth_url, state = oauth.authorization_url(
            'https://accounts.google.com/o/oauth2/auth',
            access_type='offline',
            prompt='consent'
        )

        session['oauth_state'] = state
        return redirect(auth_url)

    except Exception as e:
        print(f"Auth login error: {e}")
        return f"Auth error: {str(e)}", 500


@app.route('/auth/callback')
def auth_callback():
    """Handles Gmail OAuth callback."""
    print(f"=== AUTH CALLBACK TRIGGERED ===")
    print(f"Request URL: {request.url}")

    credentials_json = os.getenv('GOOGLE_CREDENTIALS')

    try:
        creds_data = json.loads(credentials_json)
        client_id = creds_data['web']['client_id']
        client_secret = creds_data['web']['client_secret']

        redirect_uri = 'https://gmail-cleanup-agent.onrender.com/auth/callback'

        from requests_oauthlib import OAuth2Session
        os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

        oauth = OAuth2Session(
            client_id,
            state=session.get('oauth_state'),
            redirect_uri=redirect_uri
        )

        # Fix URL scheme for Render (https)
        callback_url = request.url.replace('http://', 'https://')

        token = oauth.fetch_token(
            'https://oauth2.googleapis.com/token',
            authorization_response=callback_url,
            client_secret=client_secret
        )

        # Save token to session
        session['gmail_token'] = token
        session['gmail_authenticated'] = True
        session.modified = True

        # Get user email
        try:
            from google.oauth2.credentials import Credentials
            creds = Credentials(
                token=token['access_token'],
                refresh_token=token.get('refresh_token'),
                token_uri='https://oauth2.googleapis.com/token',
                client_id=client_id,
                client_secret=client_secret
            )
            service = build('gmail', 'v1', credentials=creds)
            profile = service.users().getProfile(userId='me').execute()
            session['gmail_email'] = profile.get('emailAddress', '')
            session.modified = True
            print(f"Authenticated as: {session['gmail_email']}")
        except Exception as e:
            print(f"Error getting email: {e}")
            session['gmail_email'] = ''

        print(f"=== AUTH COMPLETE ===")
        return redirect('/')

    except Exception as e:
        print(f"Auth callback error: {e}")
        return redirect('/?error=auth_failed')

@app.route('/auth/status')
def auth_status():
    """Check if user is authenticated with Gmail."""
    return jsonify({
        'authenticated': session.get('gmail_authenticated', False),
        'email': session.get('gmail_email', '')
    })


@app.route('/auth/logout')
def auth_logout():
    """Log out — clear Gmail session."""
    session.pop('gmail_token', None)
    session.pop('gmail_authenticated', None)
    session.pop('gmail_email', None)
    return redirect('/')

# ─────────────────────────────────────────
# API Key Management
# ─────────────────────────────────────────

@app.route('/set_api_key', methods=['POST'])
def set_api_key():
    """Save OpenAI API key to session."""
    api_key = request.json.get('api_key', '').strip()

    if not api_key:
        return jsonify({
            'status': 'error',
            'message': 'No API key provided'
        })

    if not api_key.startswith('sk-'):
        return jsonify({
            'status': 'error',
            'message': 'Invalid API key format'
        })

    # Test the key before saving
    try:
        test_client = OpenAI(api_key=api_key)
        test_client.models.list()
        session['openai_api_key'] = api_key
        return jsonify({
            'status': 'success',
            'message': 'API key saved successfully!'
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': 'Invalid API key — could not connect to OpenAI'
        })


@app.route('/remove_api_key', methods=['POST'])
def remove_api_key():
    """Remove OpenAI API key from session."""
    session.pop('openai_api_key', None)
    return jsonify({'status': 'success'})


@app.route('/check_config', methods=['GET'])
def check_config():
    """Check current configuration status."""
    return jsonify({
        'has_openai_key': has_openai_key(),
        'ai_mode': has_openai_key()
    })

# ─────────────────────────────────────────
# AI Inbox Chat
# ─────────────────────────────────────────


@app.route('/chat', methods=['POST'])
def chat():
    """
    Answers natural language questions
    about the user's inbox.
    """
    question = request.json.get('question', '')
    senders = request.json.get('senders', [])
    privacy_mode = request.json.get('privacy_mode', False)
    chat_history = request.json.get('history', [])

    print(f"=== CHAT REQUEST ===")
    print(f"Question: {question}")
    print(f"Senders received: {len(senders)}")
    print(f"===================")

    if not question:
        return jsonify({
            'status': 'error',
            'message': 'No question provided'
        })

    if not senders:
        return jsonify({
            'status': 'error',
            'message': 'No inbox data found. Please scan first!'
        })

    try:
        # Build sender list for AI
        sender_list = ""
        for i, s in enumerate(senders):
            if privacy_mode:
                sender_list += f"{i+1}. {s['name']} ({s['email']}): {s['count']} emails, category={s.get('category', 'Unknown')}, safety={s.get('safety', 'unknown')}, never_read={s.get('never_read', False)}\n"
            else:
                subjects = ', '.join(s.get('subjects', [])[:3])
                sender_list += f"{i+1}. {s['name']} ({s['email']}): {s['count']} emails, category={s.get('category', 'Unknown')}, safety={s.get('safety', 'unknown')}, never_read={s.get('never_read', False)}, subjects=[{subjects}]\n"

        system_prompt = f"""You are an intelligent email inbox assistant.
You help users understand and manage their inbox.

The user has scanned their inbox. Here are ALL their senders:
{sender_list}

IMPORTANT RULES:
1. ALWAYS search through the complete sender list above
2. Be specific and reference actual sender names and emails
3. For show/find requests return ALL matching senders
4. Never return empty matched_senders if relevant senders exist
5. For technical newsletters look for: dev, tech, engineering,
   code, programming, software, AI, startup related senders

Respond in this EXACT JSON format:
{{
    "answer": "Your conversational answer here",
    "matched_senders": [
        {{
            "email": "sender@example.com",
            "name": "Sender Name",
            "count": 45,
            "reason": "Why this matches the query"
        }}
    ],
    "suggestion": "Optional follow-up suggestion"
}}

Respond with ONLY the JSON, nothing else."""

        # Build messages with history
        messages = [{"role": "system", "content": system_prompt}]

        # Add chat history for context
        for h in chat_history[-6:]:
            messages.append({
                "role": h['role'],
                "content": h['content']
            })

        # Add current question
        messages.append({"role": "user", "content": question})

        client = get_openai_client()
        if not client:
            return jsonify({
                'status': 'error',
                'message': 'No OpenAI key. Please add your API key to use this feature.'
            })
        message = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=1500,
            messages=messages
        )

        response_text = message.choices[0].message.content.strip()

        print(f"=== AI RAW RESPONSE ===")
        print(response_text[:500])
        print(f"======================")

        # Clean response
        if '```' in response_text:
            response_text = response_text.split('```')[1]
            if response_text.startswith('json'):
                response_text = response_text[4:]

        import json
        ai_response = json.loads(response_text)

        print(f"Matched senders count: {len(ai_response.get('matched_senders', []))}")

        return jsonify({
            'status': 'success',
            'response': ai_response
        })

    except Exception as e:
        print(f"Chat error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        })

# ─────────────────────────────────────────
# Start the app
# ─────────────────────────────────────────
if __name__ == '__main__':
    print("Starting Gmail Cleanup Agent...")
    if not os.getenv('RENDER'):
        # Local — pre-authenticate Gmail
        gmail_service = get_gmail_service()
        print("Successfully connected to Gmail! ✅")
    print("Starting web server...")
    print("Open your browser and go to: http://127.0.0.1:5000")
    app.run(host='127.0.0.1', port=5000, debug=True, use_reloader=False)