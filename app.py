import os
import json
import time
import threading
import requests
from datetime import date, datetime, timedelta
from flask import Flask, request, jsonify
from flask_cors import CORS
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client

app = Flask(__name__)
CORS(app)  # אפשר בקשות מכל דומיין

# ─── Config from environment variables ────────────────────────────────────────
TWILIO_ACCOUNT_SID   = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN    = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

ZOHO_CLIENT_ID     = os.environ.get("ZOHO_CLIENT_ID", "1000.7FHQAIE3QWLJNLZ6E8R2T4MK2832GU")
ZOHO_CLIENT_SECRET = os.environ.get("ZOHO_CLIENT_SECRET", "15d90522b0da8cab12989a4245cf90f9bb989aff3a")
ZOHO_REFRESH_TOKEN = os.environ.get("ZOHO_REFRESH_TOKEN", "1000.e9f4151bc029dc53c981b36eef22ddaa.79189d0c343dd6abc6bff1efae6e96e9")
ZOHO_API_DOMAIN    = os.environ.get("ZOHO_API_DOMAIN", "https://www.zohoapis.com")
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# ─── שיפור #1: Token cache עם זמן תפוגה (חוסך 1-3 שניות) ──────────────────
_token_cache = {
    "access_token": os.environ.get("ZOHO_ACCESS_TOKEN", ""),
    "api_domain": ZOHO_API_DOMAIN,
    "expires_at": 0  # timestamp - מתי הטוקן פג תוקף
}

## ─── שיפור #3: Product cache בזיכרון (חוסך 1-2 שניות) + סנכרון אינקרמנטלי ─────
_product_cache = {
    "products": [],            # רשימת כל המוצרים
    "products_by_id": {},      # מילון לפי id לגישה מהירה
    "loaded_at": 0,            # מתי נטען (לאשונה)
    "last_sync_time": None,    # זמן סנכרון אחרון (ISO format)
    "ttl": 3600 * 6            # בדיקת מוצרים חדשים כל 6 שעות
}

# ─── Session memory (per phone number) ────────────────────────────────────────
sessions = {}
cancel_flags = {}  # from_number -> True אם המשתמש ביקש ביטול

# ──# ─── יומן פעולות יומי (קובץ קבוע) ──────────────────────────────────
LOG_DIR = "/tmp/bot_logs"
os.makedirs(LOG_DIR, exist_ok=True)

def _log_file_path(day: str = None) -> str:
    d = day or date.today().isoformat()
    return os.path.join(LOG_DIR, f"daily_{d}.json")

def log_action(action_type: str, description: str):
    today = date.today().isoformat()
    now_str = datetime.now().strftime("%H:%M")
    entry = {"time": now_str, "type": action_type, "desc": description}
    path = _log_file_path(today)
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                entries = json.load(f)
        else:
            entries = []
        entries.append(entry)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False)
    except Exception as e:
        print(f"[LOG ERROR] {e}")
    print(f"[LOG] {now_str} [{action_type}] {description}")

def _load_daily_log(day: str = None) -> list:
    path = _log_file_path(day)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def build_daily_report() -> str:
    today = datetime.now().strftime("%d/%m/%Y")
    day_heb = ["ראשון","שני","שלישי","רביעי","חמישי","שישי","שבת"][datetime.now().weekday() % 7]
    log_entries = _load_daily_log()
    counts = {}
    for entry in log_entries:
        t = entry["type"]
        counts[t] = counts.get(t, 0) + 1
    total = len(log_entries)
    emoji_map = {"חשבונית":"🧾","תשלום":"💰","לקוח חדש":"👤","מחיקה":"🗑️","קווים פעילים":"📡","אחר":"⚙️"}
    lines = [
        f"📊 *סיכום יומי - {today} (יום {day_heb})*",
        f"{'─'*28}",
    ]
    if total == 0:
        lines.append("😴 לא בוצעו פעולות היום")
    else:
        for action_type, count in sorted(counts.items(), key=lambda x: -x[1]):
            em = emoji_map.get(action_type, "▫️")
            lines.append(f"{em} *{action_type}*: {count} פעולות")
        lines.append(f"{'─'*28}")
        lines.append(f"📋 *פירוט ({total} פעולות):*")
        for entry in log_entries:
            lines.append(f"  🕐 {entry['time']} | {entry['desc']}")
    lines.append(f"{'─'*28}")
    lines.append("🤖 _הבוט שלך - עד מחר!_")
    return "\n".join(lines)

MAX_MSG_LEN = 1400  # גבול תווים להודעות WhatsApp

def split_message(text: str, max_len: int = None) -> list:
    """פצל הודעה לחלקים לפי גבול תווים, חיתוך בשורות"""
    if max_len is None:
        max_len = MAX_MSG_LEN
    if len(text) <= max_len:
        return [text]
    parts = []
    while text:
        if len(text) <= max_len:
            parts.append(text.strip())
            break
        # חפש \n קרוב לגבול
        cut = text.rfind('\n', 0, max_len)
        if cut == -1:
            cut = max_len
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    return parts

def send_whatsapp_to_owner(message: str):
    """שולח הודעה לבעלים, מפצל אוטומטית אם ארוך"""
    owner_number = os.environ.get("OWNER_WHATSAPP", "")
    if not owner_number:
        print("[WA] OWNER_WHATSAPP not set")
        return
    parts = split_message(message)
    for i, part in enumerate(parts):
        try:
            twilio_client.messages.create(from_=TWILIO_WHATSAPP_FROM, to=f"whatsapp:{owner_number}", body=part)
            if i < len(parts) - 1:
                time.sleep(0.5)
        except Exception as e:
            print(f"[WA] Error sending part {i+1}: {e}")

def send_daily_report():
    owner_number = os.environ.get("OWNER_WHATSAPP", "")
    if not owner_number:
        print("[DAILY REPORT] OWNER_WHATSAPP not set, skipping")
        return
    report = build_daily_report()
    send_whatsapp_to_owner(report)
    print(f"[DAILY REPORT] Sent to {owner_number}")

def _daily_report_scheduler():
    while True:
        try:
            now = datetime.now()
            target = now.replace(hour=23, minute=30, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            wait_seconds = (target - now).total_seconds()
            print(f"[DAILY REPORT] Next report in {wait_seconds/3600:.1f}h (at 23:30)")
            time.sleep(wait_seconds)
            send_daily_report()
        except Exception as e:
            print(f"[DAILY REPORT SCHEDULER] Error: {e}")
            time.sleep(60)

# ─── Zoho helpers ──────────────────────────────────────────────────────────────
def get_access_token():
    """שיפור: בודק תפוגה לפי זמן במקום לקרוא ל-Zoho כל פעם"""
    token = _token_cache.get("access_token", "")
    domain = _token_cache.get("api_domain", ZOHO_API_DOMAIN)
    expires_at = _token_cache.get("expires_at", 0)
    
    # אם הטוקן עדיין תקף (עם מרווח ביטחון של 5 דקות)
    if token and time.time() < (expires_at - 300):
        return token, domain
    
    # רענון טוקן
    print("Refreshing Zoho token...")
    r = requests.post("https://accounts.zoho.com/oauth/v2/token", params={
        "grant_type": "refresh_token",
        "client_id": ZOHO_CLIENT_ID,
        "client_secret": ZOHO_CLIENT_SECRET,
        "refresh_token": ZOHO_REFRESH_TOKEN
    })
    if r.status_code == 200 and "access_token" in r.json():
        data = r.json()
        _token_cache["access_token"] = data["access_token"]
        # Zoho tokens expire in 3600 seconds (1 hour)
        _token_cache["expires_at"] = time.time() + data.get("expires_in", 3600)
        print(f"Token refreshed successfully, expires in {data.get('expires_in', 3600)}s")
    else:
        print(f"Token refresh failed: {r.status_code} {r.text[:200]}")
    return _token_cache["access_token"], domain

def zoho_get(endpoint, params=None):
    token, domain = get_access_token()
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    r = requests.get(f"{domain}/crm/v5/{endpoint}", headers=headers, params=params)
    print(f"zoho_get {endpoint} status={r.status_code}")
    if r.status_code == 401:
        # טוקן פג - אפס ורענן
        _token_cache["expires_at"] = 0
        token, domain = get_access_token()
        headers = {"Authorization": f"Zoho-oauthtoken {token}"}
        r = requests.get(f"{domain}/crm/v5/{endpoint}", headers=headers, params=params)
        print(f"zoho_get {endpoint} retry status={r.status_code}")
    if r.status_code in [200, 201]:
        return r.json().get("data", [])
    if r.status_code == 204:
        print(f"zoho_get {endpoint} returned 204 (no content)")
    else:
        print(f"zoho_get {endpoint} error: {r.text[:200]}")
    return []

def zoho_post(endpoint, data):
    token, domain = get_access_token()
    headers = {"Authorization": f"Zoho-oauthtoken {token}", "Content-Type": "application/json"}
    r = requests.post(f"{domain}/crm/v5/{endpoint}", headers=headers, json=data)
    print(f"zoho_post {endpoint} status={r.status_code}")
    return r.json()

def zoho_get_full(endpoint, params=None):
    """כמו zoho_get אבל מחזיר גם info (לפגינציה)"""
    token, domain = get_access_token()
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    r = requests.get(f"{domain}/crm/v5/{endpoint}", headers=headers, params=params)
    if r.status_code == 401:
        _token_cache["expires_at"] = 0
        token, domain = get_access_token()
        headers = {"Authorization": f"Zoho-oauthtoken {token}"}
        r = requests.get(f"{domain}/crm/v5/{endpoint}", headers=headers, params=params)
    if r.status_code in [200, 201]:
        return r.json().get("data", []), r.json().get("info", {})
    return [], {}

def zoho_put(endpoint, data):
    token, domain = get_access_token()
    headers = {"Authorization": f"Zoho-oauthtoken {token}", "Content-Type": "application/json"}
    r = requests.put(f"{domain}/crm/v5/{endpoint}", headers=headers, json=data)
    print(f"zoho_put {endpoint} status={r.status_code}")
    return r.json()

def zoho_delete(endpoint):
    """מוחק רשומה ב-Zoho CRM"""
    token, domain = get_access_token()
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    r = requests.delete(f"{domain}/crm/v5/{endpoint}", headers=headers)
    print(f"zoho_delete {endpoint} status={r.status_code}")
    if r.status_code in [200, 201]:
        return r.json()
    if r.status_code == 204:
        return {"data": [{"code": "SUCCESS"}]}
    print(f"zoho_delete error: {r.text[:200]}")
    return {}

# ─── שיפור #3: Product cache functions + סנכרון אינקרמנטלי ─────────────────
def load_all_products():
    """טעינה מלאה ראשונה - מושך את כל המוצרים מ-Zoho"""
    print("Loading ALL products into cache (full load)...")
    all_products = []
    page = 1
    while True:
        results = zoho_get("Products", {"fields": "Product_Name,Unit_Price,id,Modified_Time", "per_page": 200, "page": page})
        if not results:
            break
        all_products.extend(results)
        if len(results) < 200:
            break
        page += 1
    # בנה מילון לפי id לגישה מהירה בעדכונים
    products_by_id = {p["id"]: p for p in all_products}
    _product_cache["products"] = all_products
    _product_cache["products_by_id"] = products_by_id
    _product_cache["loaded_at"] = time.time()
    _product_cache["last_sync_time"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S+00:00")
    print(f"Product cache FULL load: {len(all_products)} products")
    return all_products

def sync_new_products():
    """סנכרון אינקרמנטלי - מושך רק מוצרים שנוצרו/עודכנו מאז הסנכרון האחרון"""
    last_sync = _product_cache.get("last_sync_time")
    if not last_sync or not _product_cache["products"]:
        # אין נתוני סנכרון קודמים - עשה טעינה מלאה
        return load_all_products()
    
    print(f"Syncing products modified since {last_sync}...")
    new_sync_time = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S+00:00")
    
    # חפש מוצרים שעודכנו/נוצרו מאז הסנכרון האחרון
    new_products = []
    page = 1
    while True:
        # Zoho CRM v5 תומך ב-criteria עם Modified_Time
        criteria = f"(Modified_Time:greater_equal:{last_sync})"
        results = zoho_get("Products/search", {
            "criteria": criteria,
            "fields": "Product_Name,Unit_Price,id,Modified_Time",
            "per_page": 200,
            "page": page
        })
        if not results:
            break
        new_products.extend(results)
        if len(results) < 200:
            break
        page += 1
    
    if new_products:
        # מזג לתוך הקאש - עדכן קיימים או הוסף חדשים
        products_by_id = _product_cache["products_by_id"]
        for p in new_products:
            products_by_id[p["id"]] = p
        # בנה מחדש את רשימת המוצרים
        _product_cache["products"] = list(products_by_id.values())
        _product_cache["products_by_id"] = products_by_id
        print(f"Product cache INCREMENTAL sync: {len(new_products)} new/updated products merged. Total: {len(_product_cache['products'])}")
    else:
        print("Product cache INCREMENTAL sync: no new products found.")
    
    _product_cache["loaded_at"] = time.time()
    _product_cache["last_sync_time"] = new_sync_time
    return _product_cache["products"]

def get_cached_products():
    """מחזיר מוצרים מהקאש. אם פג TTL - עושה סנכרון אינקרמנטלי (לא טעינה מלאה!)"""
    if not _product_cache["products"]:
        return load_all_products()  # טעינה ראשונה
    if (time.time() - _product_cache["loaded_at"]) > _product_cache["ttl"]:
        return sync_new_products()  # רק מוצרים חדשים/מעודכנים
    return _product_cache["products"]

# ─── CRM actions ───────────────────────────────────────────────────────────────
def find_contact_by_name_and_account(contact_name, account_name):
    contacts = zoho_get("Contacts/search", {"word": contact_name}) if contact_name else []
    accounts = zoho_get("Accounts/search", {"word": account_name}) if account_name else []
    account_ids = [a["id"] for a in accounts]
    
    # סינון לפי חשבון
    matches = []
    for c in contacts:
        c_acc = c.get("Account_Name")
        if account_name:
            if c_acc and c_acc.get("id") in account_ids:
                matches.append(c)
        else:
            matches.append(c)
    
    # סינון נוסף: רק לקוחות שהשם שלהם באמת מכיל את מילת החיפוש
    if contact_name and matches:
        search_words = contact_name.strip().lower().split()
        filtered = []
        for c in matches:
            full_name = c.get("Full_Name", "").lower()
            # בדוק שלפחות מילה אחת מהחיפוש מופיעה בשם הלקוח
            if any(w in full_name for w in search_words):
                filtered.append(c)
        if filtered:
            matches = filtered
            print(f"find_contact: filtered by name '{contact_name}' → {len(matches)} matches")
    
    print(f"find_contact: '{contact_name}' @ '{account_name}' → {len(matches)} matches, {len(accounts)} accounts")
    return matches, accounts

def best_account_match(accounts, search_name):
    """בוחר את ההתאמה הטובה ביותר מרשימת Accounts.
    מחזיר: account יחיד אם יש התאמה מדויקת, או None אם יש כמה אפשרויות וצריך לבחור."""
    if not accounts:
        return None
    if len(accounts) == 1:
        return accounts[0]
    search_lower = search_name.strip().lower()
    # עדיפות 1: התאמה מדויקת על חלק בעל הבית (אחרי ' - ')
    for a in accounts:
        name = a.get("Account_Name", "")
        if " - " in name:
            landlord_part = name.split(" - ", 1)[1].strip().lower()
            if landlord_part == search_lower:
                return a
    # עדיפות 2: התאמה מדויקת על שם מלא
    for a in accounts:
        if a.get("Account_Name", "").lower() == search_lower:
            return a
    # עדיפות 3: סינון למי שמכיל את החיפוש
    containing = [a for a in accounts if search_lower in a.get("Account_Name", "").lower()]
    if len(containing) == 1:
        return containing[0]  # רק אחד מתאים - בחר אוטומטית
    if len(containing) > 1:
        return None  # כמה אפשרויות - צריך להציג רשימה
    return accounts[0]  # אף אחד לא מכיל - בחר ראשון

def show_account_choice(accounts, search_name, from_number, original_action, extra_context=None):
    """מציג רשימת בעלי בתים לבחירה ושומר session"""
    search_lower = search_name.strip().lower()
    containing = [a for a in accounts if search_lower in a.get("Account_Name", "").lower()]
    options = containing if containing else accounts
    context = {"original_action": original_action}
    if extra_context:
        context.update(extra_context)
    sessions[from_number] = {
        "pending": "account_choice",
        "options": options[:10],
        "context": context
    }
    lines = [f"{i+1}. {a.get('Account_Name', '')}" for i, a in enumerate(options[:10])]
    extra = f"\n... ועוד {len(options) - 10}" if len(options) > 10 else ""
    return f"🏠 מצאתי {len(options)} בעלי בתים עבור '{search_name}':\n" + "\n".join(lines) + extra + "\n\nכתוב מספר לבחירה:"

def find_product(product_name):
    """שיפור: חיפוש מוצר מהקאש בזיכרון במקום API call כל פעם"""
    if not product_name:
        print("find_product: empty product name")
        return []

    print(f"find_product: searching for '{product_name}'")
    product_lower = product_name.strip().lower()
    search_words = product_lower.split()

    # שיפור: חיפוש מהקאש בזיכרון (מיידי!)
    all_products = get_cached_products()
    if all_products:
        # סינון - כל המילים חייבות להופיע
        filtered = [p for p in all_products if all(w in p.get("Product_Name", "").lower() for w in search_words)]
        if filtered:
            print(f"find_product: cache hit! {len(filtered)} results for '{product_name}'")
            return filtered
        # סינון חלקי - לפחות מילה אחת
        partial = [p for p in all_products if any(w in p.get("Product_Name", "").lower() for w in search_words)]
        if partial:
            print(f"find_product: cache partial hit! {len(partial)} results for '{product_name}'")
            return partial

    # Fallback: חיפוש ישיר ב-Zoho API
    print(f"find_product: cache miss, searching Zoho API...")
    results = zoho_get("Products/search", {"word": product_name, "fields": "Product_Name,Unit_Price,id"})
    if results:
        filtered = [p for p in results if all(w in p.get("Product_Name", "").lower() for w in search_words)]
        if filtered:
            return filtered
        partial = [p for p in results if any(w in p.get("Product_Name", "").lower() for w in search_words)]
        if partial:
            return partial
        return results

    # אם לא נמצא - נסה חיפוש עם מילה ראשונה בלבד
    if len(search_words) > 1:
        first_word = search_words[0]
        print(f"find_product: retry with first word '{first_word}'")
        results2 = zoho_get("Products/search", {"word": first_word, "fields": "Product_Name,Unit_Price,id"})
        if results2:
            filtered2 = [p for p in results2 if all(w in p.get("Product_Name", "").lower() for w in search_words)]
            if filtered2:
                return filtered2
            partial2 = [p for p in results2 if any(w in p.get("Product_Name", "").lower() for w in search_words)]
            if partial2:
                return partial2
            for p in results2:
                pname = p.get("Product_Name", "").lower()
                if product_lower in pname or pname in product_lower:
                    return [p]
            return results2

    print(f"find_product: no results for '{product_name}'")
    return []

def find_open_invoices_for_contact(contact_name):
    invoices = zoho_get("Invoices/search", {"word": contact_name,
                                             "fields": "Subject,Status,Grand_Total,Contact_Name,Account_Name"})
    return [i for i in invoices if i.get("Status") in ["לא שולם", None, ""]]

def mark_invoice_paid(invoice_id, amount, method):
    method_label = method if method else "מזומן"
    # Map method label to Zoho picklist actual_value
    method_map = {
        "מזומן": "Option 1",
        "העברה ציאפ": "העברה - ציאפ בנקוק",
        "ציאפ": "העברה - ציאפ בנקוק",
        "העברה": "העברה בנקאית",
        "העברה בנקאית": "העברה בנקאית",
        "אשראי": "Option 2",
        "כרטיס אשראי": "Option 2",
        "המחאה": "המחאה (צ'ק)",
        "צ'ק": "המחאה (צ'ק)",
        "גיהוץ": "גיהוץ 019",
        "gmt": "Gmt",
        "מקס": "מקס - אשראי צליה",
        "דני": "העברה - דני בנקוק",
    }
    payment_kind_value = method_map.get(method_label, method_map.get(method_label.split()[0] if method_label else "מזומן", "Option 1"))
    now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S+00:00")
    result = zoho_put(f"Invoices/{invoice_id}", {"data": [{
        "id": invoice_id,
        "payment_amount": float(amount) if amount else 0,
        "payment_kind": payment_kind_value,
        "payment_time": now_str,
        "payment_desc": f"שולם ₪{amount} ב{method_label}",
        "add_payment": True
    }]})
    print(f"mark_invoice_paid result: {json.dumps(result, ensure_ascii=False)[:300]}")
    return result.get("data", [{}])[0].get("code") == "SUCCESS"

def create_invoice(contact_id, account_id, product_id, price, contact_name, quantity=1):
    today = date.today().strftime("%Y-%m-%d")
    payload = {"data": [{
        "Subject": f"חשבונית - {contact_name} - {today}",
        "Account_Name": {"id": account_id},
        "Contact_Name": {"id": contact_id},
        "Invoiced_Date": today,
        "Status": "לא שולם",
        "Invoiced_Items": [{
            "Product_Name": {"id": product_id},
            "Quantity": quantity,
            "List_Price": price
        }]
    }]}
    result = zoho_post("Invoices", payload)
    print(f"create_invoice result: {json.dumps(result, ensure_ascii=False)[:300]}")
    if result.get("data") and result["data"][0].get("code") == "SUCCESS":
        return result["data"][0]["details"]["id"]
    return None

def get_active_lines_for_account(account_id, account_name):
    """מחזיר את מספר הקווים הפעילים של בעל בית לפי field11 (קווים פעילים) בלקוחות שלו"""
    print(f"get_active_lines: searching for account {account_name} (id={account_id})")
    page = 1
    total_lines = 0
    active_contacts = []
    while True:
        contacts, info = zoho_get_full("Contacts/search", {
            "criteria": f"(Account_Name:equals:{account_id})",
            "fields": "Full_Name,field11,field12",
            "per_page": 200,
            "page": page
        })
        if not contacts:
            break
        for c in contacts:
            lines = c.get("field11", 0) or 0
            if lines > 0:
                total_lines += lines
                active_contacts.append({
                    "name": c.get("Full_Name", ""),
                    "lines": lines,
                    "numbers": c.get("field12", "")
                })
        if not info.get("more_records", False):
            break
        page += 1
    print(f"get_active_lines: {account_name} = {total_lines} active lines from {len(active_contacts)} contacts")
    return total_lines, active_contacts

# מיפוי שם בעל בית (Account) לשם מושב (שדה picklist ב-Zoho)
ACCOUNT_TO_MOSHAV = {
    "אוהד": "אוהד",
    "איציק לוטם": "שטח - עטיה",
    "גבולות": "גבולות",
    "חצבה": "חצבה",
    "ישע": "ישע",
    "יתד": "יתד",
    "מבטחים": "מבטחים",
    "מופ": "מופ",
    "מסלול": "מסלול",
    "עין הבשור": "עין הבשור",
    "עמיעוז": "עמיעוז",
    "פטיש": "פטיש",
    "קיבוץ": "עין הבשור",
    "רנן": "רנן",
    "שדה אברהם": "יתד",
    "שדה ניצן": "שדה ניצן",
    "שרשרת": "שרשרת",
    "תלמי אליהו": "תלמי אליהו",
}

def get_moshav_for_account(account_name):
    """מחזיר את שם המושב לפי שם ה-Account. מחפש גם חלקית."""
    # נסה התאמה מדויקת קודם
    acc_lower = account_name.lower().strip()
    for key, moshav in ACCOUNT_TO_MOSHAV.items():
        if key in acc_lower or acc_lower in key:
            return moshav
    # אם לא מצאנו - נסה לחלץ את שם המושב משם ה-Account (בדרך כלל הפורמט הוא "מושב - שם בעל בית")
    if " - " in account_name:
        return account_name.split(" - ")[0].strip()
    return None

def create_zoho_contact(contact_name, account_id, account_name):
    """יוצר לקוח חדש ב-Zoho CRM עם מושב, טלפון ברירת מחדל, ושיוך לבעל בית"""
    # מצא את המושב לפי ה-Account
    moshav = get_moshav_for_account(account_name)
    
    contact_data = {
        "Last_Name": contact_name,
        "Account_Name": {"id": account_id},
        "Mobile": "0"  # טלפון ברירת מחדל
    }
    
    # הוסף מושב אם מצאנו
    if moshav:
        contact_data["field"] = moshav  # api_name של שדה מושב
    
    payload = {"data": [contact_data]}
    result = zoho_post("Contacts", payload)
    print(f"create_zoho_contact result: {json.dumps(result, ensure_ascii=False)[:300]}")
    if result.get("data") and result["data"][0].get("code") == "SUCCESS":
        return result["data"][0]["details"]["id"]
    return None

def build_invoice_confirmation(contact, product, final_price=None, quantity=1):
    acc_name = contact.get("Account_Name", {}).get("name", "") if isinstance(contact.get("Account_Name"), dict) else ""
    price = final_price if final_price is not None else product.get('Unit_Price', 0)
    qty_text = f" x{quantity}" if quantity > 1 else ""
    total = price * quantity if quantity > 1 else price
    total_line = f"\n📊 סה\"כ: ₪{total}" if quantity > 1 else ""
    log_action("חשבונית", f"נוצרה: {contact['Full_Name']} @ {acc_name} | {product.get('Product_Name')} ₪{price}")
    return (f"✅ חשבונית נוצרה!\n"
            f"👤 {contact['Full_Name']}\n"
            f"🏠 {acc_name}\n"
            f"📦 {product.get('Product_Name')}{qty_text}\n"
            f"💰 ₪{price} | לא שולם{total_line}")

# ─── שיפור #2: AI intent parser - Gemini 2.0 Flash Lite (חוסך 2-5 שניות) ────
SYSTEM_PROMPT = """
אתה עוזר חכם שמנתח פקודות קצרות בעברית ומחזיר JSON בלבד. אסור לך לשאול שאלות - תמיד תחזיר JSON.

הפורמט הנפוץ ביותר הוא: [מוצר] [שם לקוח] [שם בעל בית/מקום]
לדוגמה: "050 פלאפון קונצאי שלום חיים" = מוצר "050 פלאפון", לקוח "קונצאי", בעל בית "שלום חיים".

כלל חשוב מאוד: שדה product חייב לכלול את כל המילים שמתארות את המוצר! אם המשתמש כתב "050 פלאפון" - ה-product הוא "050 פלאפון" (לא רק "050"). אם כתב "בלוטוס JBL" - ה-product הוא "בלוטוס JBL". אם כתב "מכשיר גלקסי" - ה-product הוא "מכשיר גלקסי". תמיד שמור על כל מילות המוצר!

כלל חשוב: אם ההודעה מכילה שם מוצר (כמו 050, סוביט, סוויט, כרטיס, מקל סלפי, בלוטוס, מכשיר, אופניים, טאבלט, רמקול, מזגן, סוללה, מטען, שעון, פלאפון, אוזניות, בידורית, ראוטר, פנס, מאוורר, תיק, כבל, מגן, מעמד, מקלדת, עכבר, משקפיים, גיטרה, מקרן, מקרר, נרתיק) - זו תמיד יצירת חשבונית!

רשימת בעלי הבתים (accounts) במערכת - השתמש בשם המקוצר שהמשתמש כותב כדי לזהות את בעל הבית:
אוהד (אילני, אשר, גאזה, דורון, יוסי שלום, מאור, מוטי, קובי, שלום חיים, שמעון ושניר, שרון, תומר)
איציק לוטם
גבולות (יואב, איציק עטיה, קטן, פוון)
חצבה (איציק, גולן, גיא ורפי, עמית)
ישע (אורי תותים, בית אריזה צביקה, יעקב ארד, משה דנינו, משה ים נמרוד, עופר, עמי תותים, רני, תמי אמנון, איתמר אגו)
יתד (מוטי אור, עופר בוכניק)
מבטחים (אילן, איציק ומשה, איציק אבירם, איתן כהן, אלי, בלול אלבז, ברוך סמי, גבי, דותן, יגאל סולומון, יהודה ואבי אסולין, ישראל, ישראל 2, לוגסי, מיכה, מיקי, מקה אסולין, משה אדם, משה בן אבו, משה דהן, ניסים, סולומו, עידו נחום, עידו נחום 2, עידן, עינהב, צביקה הום, צביקה סנוקר, צביקה פן, שורשים שרוני, שורשים עמודי, שחף, שער דוד, יוסי אלבז)
מופ (מחע, ליאור דורון סאק, צאלים, דוד טיבולי)
מסלול (שלמה)
עין הבשור (אילן סנג, יריב קילפון, צביקה)
עמיעוז (אלון סטורנה, דוד סומבאת, יגאל אמר, רפי 155, איציק עטיה, דני אלפסי)
פטיש (מוטי ועודד)
קיבוץ (בארי, מגן, עלומים)
רנן (אלי, יוסף, מרדכי, משה אליהו)
שדה אברהם (רון)
שדה ניצן (אביחי, אור, אסף, דני 60, מאווין, נועם, נמרוד, נתן, קט, רוברט, רונן, שמעון, דן קיו)
שרשרת (משה טרבלסטי, משה עמר)
תלמי אליהו (אלי יוסף, אלי פארן, אלכס חממות, אסף מגן, בן רומנוב, דודי ועדי, יוני, יוני אליצור, יעקב ספאייה, משה אסולין, ניסים אקוקה, עמית אבוטבול, רואי תורג'מן, רן אבוטבול, שמעון ביטון)

כשהמשתמש כותב שם מקוצר כמו "אילן" - זהה אותו כ-"אילן" (בעל בית מבטחים). כשכותב "שער דוד" - זהה כ-"שער דוד". כשכותב "דורון" - זהה כ-"דורון" (בעל בית אוהד). תמיד החזר רק את החלק הייחודי של שם בעל הבית בשדה account.

הפקודות האפשריות:
1. יצירת חשבונית: {"action": "create_invoice", "product": "...", "contact": "...", "account": "...", "price": 0, "quantity": 1}
2. תשלום חשבונית: {"action": "payment", "contact": "...", "account": "...", "amount": 120, "method": "מזומן"}
3. שאילתת חשבוניות פתוחות: {"action": "query", "type": "open_invoices", "account": "..."}
4. הוספת לקוח חדש: {"action": "create_contact", "contact": "...", "account": "..."}
5. שאילתת קווים פעילים: {"action": "active_lines", "account": "..."}
6. חשבונית קווים פעילים: {"action": "active_lines_invoice", "contact": "...", "account": "..."}
7. לא מובן: {"action": "unknown"}

כללים:
- "שילם", "שולם", "שלם", "תשלום", "מזומן" בלי מוצר = action: payment
- "הוסף לקוח", "לקוח חדש", "פתח לקוח", "צור לקוח" = action: create_contact
- "קווים פעילים" + שם בעל בית בלבד (בלי שם לקוח) = action: active_lines
- "חשבונית קווים פעילים" + שם לקוח + שם בעל בית = action: active_lines_invoice
- אם יש שם מוצר בהודעה (ואין "הוסף לקוח"/"לקוח חדש"/"קווים פעילים") = תמיד action: create_invoice
- contact = שם הלקוח הספציפי (אם לא ברור - שים "")
- account = שם בעל הבית / מקום העבודה / הנכס (השם המקוצר כפי שמופיע ברשימה למעלה)
- price = מחיר מותאם אישית. מספר שמופיע אחרי שם המוצר (שאינו חלק משם המוצר) = מחיר. אם לא ציין מחיר - שים 0
- quantity = כמות יחידות. מספר שמופיע לפני שם המוצר (בין 2 ל-30) = כמות. אם לא ציין כמות - שים 1. המספר חייב להיות בין 1 ל-30.
- אמצעי תשלום: "מזומן", "העברה", "צ'ק", "אשראי" - ברירת מחדל "מזומן"
- חשוב: מספרים כמו 050, 48, 155 שהם חלק משם המוצר - אל תשים ב-price ולא ב-quantity! רק מספרים שמייצגים סכום כסף או כמות
- הבחנה בין quantity ל-price: מספר לפני שם המוצר (2-30) = quantity. מספר אחרי שם המוצר = price.

דוגמאות ליצירת חשבונית:
- "050 סוויט אילן" → {"action": "create_invoice", "product": "050 סוויט", "contact": "", "account": "אילן", "price": 0, "quantity": 1}
- "3 בלוטוס קשת סאק דורון" → {"action": "create_invoice", "product": "בלוטוס קשת", "contact": "סאק", "account": "דורון", "price": 0, "quantity": 3}
- "5 מקל סלפי טונגצאי שער דוד" → {"action": "create_invoice", "product": "מקל סלפי", "contact": "טונגצאי", "account": "שער דוד", "price": 0, "quantity": 5}
- "3 בלוטוס קשת 120 סאק דורון" → {"action": "create_invoice", "product": "בלוטוס קשת", "contact": "סאק", "account": "דורון", "price": 120, "quantity": 3}
- "בלוטוס קשת 120 סאק דורון" → {"action": "create_invoice", "product": "בלוטוס קשת", "contact": "סאק", "account": "דורון", "price": 120, "quantity": 1}
- "050 פלאפון קונצאי שלום חיים" → {"action": "create_invoice", "product": "050 פלאפון", "contact": "קונצאי", "account": "שלום חיים", "price": 0, "quantity": 1}
- "10 אוזניות JBL אילן" → {"action": "create_invoice", "product": "אוזניות JBL", "contact": "", "account": "אילן", "price": 0, "quantity": 10}
- "10 אוזניות JBL 150 אילן" → {"action": "create_invoice", "product": "אוזניות JBL", "contact": "", "account": "אילן", "price": 150, "quantity": 10}
- "סוללה 48 אילן" → {"action": "create_invoice", "product": "סוללה 48", "contact": "", "account": "אילן", "price": 0, "quantity": 1}
- "מזגן נייד 900 דורון" → {"action": "create_invoice", "product": "מזגן נייד", "contact": "", "account": "דורון", "price": 900, "quantity": 1}
- "2 מזגן נייד דורון" → {"action": "create_invoice", "product": "מזגן נייד", "contact": "", "account": "דורון", "price": 0, "quantity": 2}

דוגמאות להוספת לקוח:
- "הוסף לקוח חדש בשם סוויט לבעל הבית אילן" → {"action": "create_contact", "contact": "סוויט", "account": "אילן"}
- "הוסף לקוח סוויט לאילן" → {"action": "create_contact", "contact": "סוויט", "account": "אילן"}
- "לקוח חדש סוויט אילן" → {"action": "create_contact", "contact": "סוויט", "account": "אילן"}
- "פתח לקוח טונגצאי דורון" → {"action": "create_contact", "contact": "טונגצאי", "account": "דורון"}
- "צור לקוח חדש בשם מוחמד לשער דוד" → {"action": "create_contact", "contact": "מוחמד", "account": "שער דוד"}

דוגמאות לקווים פעילים:
- "קווים פעילים אילן" → {"action": "active_lines", "account": "אילן"}
- "כמה קווים פעילים יש לדורון" → {"action": "active_lines", "account": "דורון"}
- "קווים פעילים שער דוד" → {"action": "active_lines", "account": "שער דוד"}

דוגמאות לחשבונית קווים פעילים:
- "חשבונית קווים פעילים סומניק אילן" → {"action": "active_lines_invoice", "contact": "סומניק", "account": "אילן"}
- "פתח חשבונית קווים פעילים טונגצאי דורון" → {"action": "active_lines_invoice", "contact": "טונגצאי", "account": "דורון"}

דוגמאות לתשלום:
- "טונגצאי בוי שער דוד שילם 120 מזומן" → {"action": "payment", "contact": "טונגצאי בוי", "account": "שער דוד", "amount": 120, "method": "מזומן"}
- "סוביט אילן שילם 120 מזומן" → {"action": "payment", "contact": "", "account": "אילן", "amount": 120, "method": "מזומן"}
- "כמה חשבוניות פתוחות לאילן?" → {"action": "query", "type": "open_invoices", "account": "אילן"}

החזר JSON בלבד, ללא טקסט נוסף, ללא הסברים.
"""

def parse_intent(message):
    """שיפור: משתמש ב-gemini-2.5-flash - מהיר ומדויק!"""
    if not GEMINI_API_KEY:
        return {"action": "unknown"}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": SYSTEM_PROMPT + "\n\nהודעת משתמש: " + message}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json"
        }
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        print(f"Gemini status: {r.status_code}")
        if r.status_code == 200:
            resp_json = r.json()
            text = resp_json["candidates"][0]["content"]["parts"][0]["text"].strip()
            print(f"Gemini text: {text}")
            # נקה markdown אם יש
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            parsed = json.loads(text)
            print(f"Gemini parsed: {parsed}")
            return parsed
        else:
            print(f"Gemini error response: {r.text[:300]}")
    except Exception as e:
        print(f"Gemini exception: {e}")
    return {"action": "unknown"}

def pick_best_match(options, user_reply):
    reply_lower = user_reply.strip().lower()
    for opt in options:
        name = opt.get("Full_Name", opt.get("Subject", "")).lower()
        if reply_lower in name:
            return opt
    if reply_lower.isdigit():
        idx = int(reply_lower) - 1
        if 0 <= idx < len(options):
            return options[idx]
    return None

# ─── Payment flow ──────────────────────────────────────────────────────────────
def handle_payment(contact_name, account_name, amount, method, from_number):
    contacts, _ = find_contact_by_name_and_account(contact_name, account_name)
    if not contacts:
        return f"❌ לא מצאתי לקוח '{contact_name}' אצל '{account_name}'"
    if len(contacts) > 1:
        sessions[from_number] = {
            "pending": "payment_contact_choice",
            "options": contacts,
            "context": {"amount": amount, "method": method}
        }
        names = "\n".join([f"{i+1}. {c['Full_Name']}" for i, c in enumerate(contacts)])
        return f"מצאתי כמה לקוחות:\n{names}\n\nכתוב חלק מהשם או מספר לבחירה:"
    return _process_payment_for_contact(contacts[0], amount, method, from_number)

def _process_payment_for_contact(contact, amount, method, from_number):
    open_invoices = find_open_invoices_for_contact(contact["Full_Name"])
    if not open_invoices:
        return f"❌ לא מצאתי חשבוניות פתוחות עבור {contact['Full_Name']}"
    if len(open_invoices) == 1:
        inv = open_invoices[0]
        pay_amount = amount if amount else inv.get("Grand_Total", 0)
        pay_method = method if method else "מזומן"
        success = mark_invoice_paid(inv["id"], pay_amount, pay_method)
        if success:
            acc_name = contact.get("Account_Name", {}).get("name", "") if isinstance(contact.get("Account_Name"), dict) else ""
            return (f"✅ תשלום עודכן!\n"
                    f"👤 {contact['Full_Name']}\n"
                    f"🏠 {acc_name}\n"
                    f"💰 ₪{pay_amount} | {pay_method}\n"
                    f"📄 {inv.get('Subject', '')}")
        return "❌ שגיאה בעדכון התשלום"
    acc_name_pay = contact.get("Account_Name", {}).get("name", "") if isinstance(contact.get("Account_Name"), dict) else str(contact.get("Account_Name", ""))
    sessions[from_number] = {
        "pending": "payment_invoice_choice",
        "options": open_invoices,
        "context": {"contact": contact, "amount": amount, "method": method}
    }
    lines = "\n".join([f"{i+1}. {inv.get('Subject','')} - ₪{inv.get('Grand_Total',0)}"
                       for i, inv in enumerate(open_invoices)])
    return (f"👤 *{contact['Full_Name']}* | 🏠 {acc_name_pay}\n"
            f"מצאתי {len(open_invoices)} חשבוניות פתוחות:\n{lines}\n\n"
            f"איזו לסמן כשולם?\nשלח *ביטול* לביטול | *תפריט* לתפריט ראשי")

# ─── Main handler ──────────────────────────────────────────────────────────────
def _looks_like_new_command(message):
    """בדוק אם ההודעה נראית כמו פקודה חדשה ולא תשובה לשאלה קודמת."""
    msg = message.strip()
    # אם זה רק מספר (1-30) - זו תשובה לבחירה
    if msg.isdigit() and 1 <= int(msg) <= 30:
        return False
    # אם זה מילה אחת קצרה (עד 10 תווים) - כנראה תשובה (שם)
    if len(msg) <= 10 and " " not in msg:
        return False
    # אם זה "כן" או "לא" - תשובה
    if msg in ["כן", "לא", "yes", "no"]:
        return False
    # אם מכיל מילות מפתח של פקודות - זו פקודה חדשה
    command_keywords = ["חשבונית", "הוסף לקוח", "לקוח חדש", "פתח לקוח", "צור לקוח",
                        "קווים פעילים", "שילם", "שולם", "תשלום", "חשבוניות פתוחות",
                        "כרטיס 050", "מקל סלפי", "בלוטוס", "אוזניות", "רמקול", "סוללה",
                        "מחק חשבונית"]
    msg_lower = msg.lower()
    for kw in command_keywords:
        if kw in msg_lower:
            return True
    # אם יש יותר מ-3 מילים - כנראה פקודה חדשה
    if len(msg.split()) > 3:
        return True
    return False

def get_last_invoice():
    """מחזיר את החשבונית האחרונה שנוצרה (לפי Created_Time)"""
    invoices = zoho_get("Invoices", {
        "fields": "Subject,Status,Grand_Total,Contact_Name,Account_Name,Created_Time,Invoiced_Items",
        "sort_by": "Created_Time",
        "sort_order": "desc",
        "per_page": 1
    })
    if invoices:
        return invoices[0]
    return None

def get_payment_records_for_invoice(invoice_id):
    """מחזיר את כל רשומות בקרת התשלום (CustomModule1) שקשורות לחשבונית"""
    results = zoho_get("CustomModule1/search", {
        "criteria": f"(Invoice:equals:{invoice_id})",
        "fields": "Name,payment_amount,payment_kind,Invoice,Contact",
        "per_page": 50
    })
    print(f"Payment records for invoice {invoice_id}: {len(results)} found")
    return results

def delete_invoice_with_payment(invoice_id):
    """מוחק רשומות בקרת תשלום (CustomModule1) ואז את החשבונית עצמה"""
    # שלב 1: מצא ומחק את רשומות בקרת התשלום
    payment_records = get_payment_records_for_invoice(invoice_id)
    for pr in payment_records:
        pr_id = pr["id"]
        del_result = zoho_delete(f"CustomModule1/{pr_id}")
        print(f"Deleted payment record {pr_id}: {json.dumps(del_result, ensure_ascii=False)[:100]}")
    # שלב 2: מחק את החשבונית עצמה
    delete_result = zoho_delete(f"Invoices/{invoice_id}")
    print(f"Delete invoice result: {json.dumps(delete_result, ensure_ascii=False)[:200]}")
    # assume success (Zoho sometimes returns 200 with empty body)
    return True

def _zoho_today_range():
    """מחזיר טווח תאריכים להיום בפורמט Zoho עם אופסט ישראל"""
    today_start = datetime.now().strftime("%Y-%m-%dT00:00:00+03:00")
    today_end   = datetime.now().strftime("%Y-%m-%dT23:59:59+03:00")
    return today_start, today_end

def _fetch_sales_today() -> list:
    """שלוף כל חשבוניות היום עם פרטי לקוח, בעל בית, מוצרים"""
    today_start, today_end = _zoho_today_range()
    invoices = zoho_get("Invoices/search", {
        "criteria": f"(Created_Time:between:{today_start},{today_end})",
        "fields": "Subject,Grand_Total,Contact_Name,Account_Name,Created_Time",
        "per_page": 200
    })
    result = []
    for inv in invoices:
        try:
            full_data = zoho_get(f"Invoices/{inv['id']}")
            full = full_data[0] if full_data else inv
        except:
            full = inv
        contact = full.get("Contact_Name", {})
        cname = contact.get("name", "") if isinstance(contact, dict) else str(contact)
        account = full.get("Account_Name", {})
        aname = account.get("name", "") if isinstance(account, dict) else str(account)
        total = full.get("Grand_Total", 0) or 0
        items = full.get("Invoiced_Items", []) or []
        products = []
        for item in items:
            pn = item.get("Product_Name", {})
            pname = pn.get("name", "") if isinstance(pn, dict) else str(pn)
            qty = item.get("Quantity", 1) or 1
            unit_price = item.get("Unit_Price", 0) or 0
            products.append({"name": pname, "qty": qty, "price": unit_price})
        result.append({"contact": cname, "landlord": aname, "total": total, "products": products})
    return result

def _sales_nav_footer(current: str) -> str:
    """שורת ניווט בתחתית דוח מכירות"""
    opts = []
    if current != "contact":  opts.append("1️⃣ לפי לקוח")
    if current != "landlord": opts.append("2️⃣ לפי בעל בית")
    if current != "product":  opts.append("3️⃣ לפי מוצר")
    return "🔄 עבור ל: " + " | ".join(opts)

def build_sales_report() -> str:
    """דוח מכירות - סיכום + תפריט נוסף"""
    today_str = datetime.now().strftime("%d/%m/%Y")
    SEP = "──────────────"
    try:
        invoices = _fetch_sales_today()
    except Exception as e:
        return f"❌ שגיאה בשליפת דוח מכירות: {e}"
    if not invoices:
        return f"🧾 *דוח מכירות - {today_str}*\n{SEP}\n😴 לא נוצרו חשבוניות היום"
    grand = sum(i["total"] for i in invoices)
    lines = [
        f"🧾 *דוח מכירות - {today_str}*",
        SEP,
        f"📊 סהכ: *₪{grand}* | {len(invoices)} חשבוניות",
        SEP,
        "🔍 *פירוט:*",
        "1️⃣ לפי לקוח",
        "2️⃣ לפי בעל בית",
        "3️⃣ לפי מוצר",
    ]
    return "\n".join(lines)

def build_sales_report_with_cache(invoices: list) -> str:
    """דוח מכירות סיכום עם נתונים שכבר נשלפו (לא שלוף שניית)"""
    today_str = datetime.now().strftime("%d/%m/%Y")
    SEP = "──────────────"
    if not invoices:
        return f"🧾 *דוח מכירות - {today_str}*\n{SEP}\n😴 לא נוצרו חשבוניות היום"
    grand = sum(i["total"] for i in invoices)
    lines = [
        f"🧾 *דוח מכירות - {today_str}*",
        SEP,
        f"📊 סהכ: *₪{grand}* | {len(invoices)} חשבוניות",
        SEP,
        "🔍 *פירוט:*",
        "1️⃣ לפי לקוח",
        "2️⃣ לפי בעל בית",
        "3️⃣ לפי מוצר",
    ]
    return "\n".join(lines)

def build_sales_by_contact(invoices: list) -> str:
    SEP = "──────────────"
    today_str = datetime.now().strftime("%d/%m/%Y")
    by_c = {}
    for inv in invoices:
        c = inv["contact"] or "לא ידוע"
        by_c.setdefault(c, {"total": 0, "landlord": inv["landlord"], "products": []})
        by_c[c]["total"] += inv["total"]
        for p in inv["products"]:
            pname = p["name"]
            if pname and pname not in by_c[c]["products"]:
                by_c[c]["products"].append(pname)
    # מיין מהגבוה לנמוך
    sorted_contacts = sorted(by_c.items(), key=lambda x: x[1]["total"], reverse=True)
    lines = [f"🧾 *מכירות לפי לקוח - {today_str}*", SEP]
    grand = 0
    for cname, data in sorted_contacts:
        grand += data["total"]
        prod_str = ", ".join(data["products"]) if data["products"] else "לא צוין"
        lines.append(f"👤 *{cname}* - ₪{data['total']}")
        lines.append(f"   🏠 {data['landlord']}")
        lines.append(f"   📦 {prod_str}")
        lines.append("")
    lines.append(SEP)
    lines.append(f"📊 סהכ: *₪{grand}*")
    lines.append("")
    lines.append(_sales_nav_footer("contact"))
    return "\n".join(lines)

def build_sales_by_landlord(invoices: list) -> str:
    SEP = "──────────────"
    today_str = datetime.now().strftime("%d/%m/%Y")
    by_a = {}
    for inv in invoices:
        a = inv["landlord"] or "לא ידוע"
        by_a.setdefault(a, {"total": 0, "items": []})
        by_a[a]["total"] += inv["total"]
        prods = ", ".join(p["name"] + (f" x{p['qty']}" if p["qty"] > 1 else "") for p in inv["products"]) or "לא צוין"
        by_a[a]["items"].append({"contact": inv["contact"], "total": inv["total"], "products": prods})
    # מיין מהגבוה לנמוך
    sorted_landlords = sorted(by_a.items(), key=lambda x: x[1]["total"], reverse=True)
    lines = [f"🏠 *מכירות לפי בעל בית - {today_str}*", SEP]
    grand = 0
    for aname, data in sorted_landlords:
        grand += data["total"]
        lines.append(f"🏠 *{aname}*")
        for item in sorted(data["items"], key=lambda x: x["total"], reverse=True):
            lines.append(f"   👤 {item['contact']} | 📦 {item['products']} | ₪{item['total']}")
        lines.append(f"   📊 סיכום: ₪{data['total']}")
        lines.append("")
    lines.append(SEP)
    lines.append(f"📊 סהכ: *₪{grand}*")
    lines.append("")
    lines.append(_sales_nav_footer("landlord"))
    return "\n".join(lines)

def build_sales_by_product(invoices: list) -> str:
    SEP = "──────────────"
    today_str = datetime.now().strftime("%d/%m/%Y")
    by_p = {}
    for inv in invoices:
        for p in inv["products"]:
            pname = p["name"] or "לא צוין"
            by_p.setdefault(pname, {"qty": 0, "total": 0, "contacts": []})
            by_p[pname]["qty"] += p["qty"]
            by_p[pname]["total"] += inv["total"]
            by_p[pname]["contacts"].append(inv["contact"])
    # מיין: סכום גבוה לנמוך, אם שווה - כמות גבוה לנמוך
    sorted_products = sorted(by_p.items(), key=lambda x: (x[1]["total"], x[1]["qty"]), reverse=True)
    lines = [f"📦 *מכירות לפי מוצר - {today_str}*", SEP]
    grand = 0
    for pname, data in sorted_products:
        grand += data["total"]
        lines.append(f"📦 *{pname}* - {data['qty']} יחידות | ₪{data['total']}")
        lines.append(f"   👤 {', '.join(data['contacts'])}")
        lines.append("")
    lines.append(SEP)
    lines.append(f"📊 סהכ כל מכירות: *₪{grand}*")
    lines.append("")
    lines.append(_sales_nav_footer("product"))
    return "\n".join(lines)

def _fetch_deposits_today() -> list:
    """שלוף כל בקרות התשלום של היום עם פרטי החשבונית (לקוח + בעל בית)"""
    today_start, today_end = _zoho_today_range()
    records = zoho_get("CustomModule1/search", {
        "criteria": f"(Created_Time:between:{today_start},{today_end})",
        "fields": "Name,payment_amount,payment_kind,Invoice,Contact",
        "per_page": 200
    })
    enriched = []
    for rec in records:
        amt = rec.get("payment_amount", 0) or 0
        if amt == 0:
            continue
        kind = (rec.get("payment_kind") or "לא ידוע").strip()
        contact_obj = rec.get("Contact", {})
        cname = contact_obj.get("name", "") if isinstance(contact_obj, dict) else str(contact_obj)
        # שלוף חשבונית לקבלת בעל בית
        aname = ""
        inv_obj = rec.get("Invoice", {})
        if isinstance(inv_obj, dict) and inv_obj.get("id"):
            try:
                inv_data = zoho_get(f"Invoices/{inv_obj['id']}")
                if inv_data:
                    acc = inv_data[0].get("Account_Name", {})
                    aname = acc.get("name", "") if isinstance(acc, dict) else str(acc)
            except:
                pass
        enriched.append({"kind": kind, "amount": amt, "contact": cname, "landlord": aname})
    return enriched

def build_deposits_report() -> str:
    """דוח הפקדות - סיכום לפי שיטת תשלום + תפריט לפי לקוח/בעל בית"""
    SEP = "──────────────"
    today_str = datetime.now().strftime("%d/%m/%Y")
    try:
        records = _fetch_deposits_today()
    except Exception as e:
        return f"❌ שגיאה בשליפת דוח הפקדות: {e}"

    if not records:
        return f"💳 *דוח הפקדות - {today_str}*\n{SEP}\n😴 לא נרשמו הפקדות היום"

    # סיכום לפי שיטת תשלום
    by_kind = {}
    for rec in records:
        k = rec["kind"]
        by_kind.setdefault(k, 0)
        by_kind[k] += rec["amount"]

    grand_total = sum(by_kind.values())
    lines = [
        f"💳 *דוח הפקדות - {today_str}*",
        SEP,
        f"📊 *סיכום לפי שיטת תשלום:*",
    ]
    for kind, total in sorted(by_kind.items()):
        lines.append(f"   📌 {kind}: ₪{total}")
    lines.append(f"   → סהכ: *₪{grand_total}*")
    lines.append(SEP)
    lines.append("🔍 *פירוט נוסף:*")
    lines.append("1️⃣ לפי לקוח")
    lines.append("2️⃣ לפי בעל בית")
    return "\n".join(lines)

def build_deposits_report_with_cache(records: list) -> str:
    """דוח הפקדות מסיכום עם נתונים שכבר נשלפו (לא שלוף שניית)"""
    SEP = "──────────────"
    today_str = datetime.now().strftime("%d/%m/%Y")
    if not records:
        return f"💳 *דוח הפקדות - {today_str}*\n{SEP}\n😴 לא נרשמו הפקדות היום"
    by_kind = {}
    for rec in records:
        k = rec["kind"]
        by_kind.setdefault(k, 0)
        by_kind[k] += rec["amount"]
    grand_total = sum(by_kind.values())
    lines = [
        f"💳 *דוח הפקדות - {today_str}*",
        SEP,
        f"📊 *סיכום לפי שיטת תשלום:*",
    ]
    for kind, total in sorted(by_kind.items()):
        lines.append(f"   📌 {kind}: ₪{total}")
    lines.append(f"   → סהכ: *₪{grand_total}*")
    lines.append(SEP)
    lines.append("🔍 *פירוט נוסף:*")
    lines.append("1️⃣ לפי לקוח")
    lines.append("2️⃣ לפי בעל בית")
    return "\n".join(lines)

def build_deposits_by_contact(records: list) -> str:
    """פירוט הפקדות לפי לקוח"""
    SEP = "──────────────"
    today_str = datetime.now().strftime("%d/%m/%Y")
    by_contact = {}
    for rec in records:
        c = rec["contact"] or "לא ידוע"
        by_contact.setdefault(c, {"total": 0, "kinds": {}})
        by_contact[c]["total"] += rec["amount"]
        k = rec["kind"]
        by_contact[c]["kinds"].setdefault(k, 0)
        by_contact[c]["kinds"][k] += rec["amount"]
    lines = [f"💳 *הפקדות לפי לקוח - {today_str}*", SEP]
    grand = 0
    for cname, data in sorted(by_contact.items()):
        grand += data["total"]
        lines.append(f"👤 *{cname}* - ₪{data['total']}")
        for kind, amt in sorted(data["kinds"].items()):
            lines.append(f"   • {kind}: ₪{amt}")
        lines.append("")
    lines.append(SEP)
    lines.append(f"📊 סהכ: *₪{grand}*")
    lines.append("")
    lines.append("🔄 לעבור לפי בעל בית - כתוב *2*")
    return "\n".join(lines)

def build_deposits_by_landlord(records: list) -> str:
    """פירוט הפקדות לפי בעל בית"""
    SEP = "──────────────"
    today_str = datetime.now().strftime("%d/%m/%Y")
    by_landlord = {}
    for rec in records:
        a = rec["landlord"] or "לא ידוע"
        by_landlord.setdefault(a, {"total": 0, "kinds": {}})
        by_landlord[a]["total"] += rec["amount"]
        k = rec["kind"]
        by_landlord[a]["kinds"].setdefault(k, 0)
        by_landlord[a]["kinds"][k] += rec["amount"]
    lines = [f"🏠 *הפקדות לפי בעל בית - {today_str}*", SEP]
    grand = 0
    for aname, data in sorted(by_landlord.items()):
        grand += data["total"]
        lines.append(f"🏠 *{aname}* - ₪{data['total']}")
        for kind, amt in sorted(data["kinds"].items()):
            lines.append(f"   • {kind}: ₪{amt}")
        lines.append("")
    lines.append(SEP)
    lines.append(f"📊 סהכ: *₪{grand}*")
    lines.append("")
    lines.append("🔄 לעבור לפי לקוח - כתוב *1*")
    return "\n".join(lines)

# ─── פקודות חדשות: עזרה, ביטול, חובות, סטטוס, חיפוש, דוח לקוח/בעל בית ──────────
HELP_TEXT = ("""
🤖 *עזרה - כל הפקודות*
────────────────────────────
📋 *ניווט*
  • תפריט → תפריט ממוספר של כל הפקודות
  • עזרה → הסבר מלא על כל פקודה
  • ביטול → ביטול פעולה פעילה
🧧 *חשבוניות*
  • חשבונית [לקוח] [בעל בית] [מוצר] → יצירת חשבונית
  • מחק חשבונית אחרונה → מחיקת חשבונית אחרונה
  • מחק חשבונית אחרונה כפול / משולש → מחיקת 2/3 חשבוניות
💰 *תשלומים*
  • תשלום [לקוח] [סכום] [שיטה] → עדכון תשלום
👤 *לקוחות*
  • סטטוס [שם] → סטטוס מלא של לקוח (פרטים + חובות + מוצרים)
  • סטטוס בית [שם] → כל הלקוחות וחובות של בעל בית
📎 *פספורטים*
  • עדכון פספורט [שם] → חילוץ שם ויזה מפספורט קיים
  • פספורט בית [בעל בית] → עדכון פספורטים לכל לקוחות בבית
  • פספורט כללי → עדכון פספורטים לבעלי בתים לפי בחירה
  • תמונה + "פספורט [שם]" → העלאה + עדכון שם ויזה אוטומטי
📷 *פרופילים*
  • תמונה + "פרופיל [שם]" → העלאה + מיקוד פנים + עדכון בזוהו
  • פרופיל בית [בעל בית] → עדכון פרופיל לכל לקוחות בבית
  • פרופיל כללי → עדכון פרופיל לבעלי בתים לפי בחירה
  • תיקון פרופיל [שם] → בחר ידנית איזה תמונה להעלאה לפרופיל
  • בדוק פרופיל בית [בעל בית] → סריקת פרופילים + בחירת מי לתיקון
📊 *דוחות*
  • דוח יומי → תפריט דוחות
  • כל הדוחות → שליחת כל הדוחות בבת אחת
  • חובות פתוחים → כל החשבוניות שלא שולמו
────────────────────────────
💡 שלח *ביטול* לביטול פעולה פעילה בכל שלב
""")
MAIN_MENU_TEXT = ("""
📋 *תפריט ראשי*
────────────────────────────
🧧 *חשבוניות*
1. חשבונית [לקוח] [בעל בית] [מוצר]
2. מחק חשבונית אחרונה
💰 *תשלומים*
4. תשלום [לקוח] [סכום] [שיטה]
👤 *לקוחות*
5. סטטוס [שם]
6. סטטוס בית [שם]
📎 *פספורטים*
8. עדכון פספורט [שם]
9. פספורט בית [בעל בית]
10. פספורט כללי
📷 *פרופילים*
11. פרופיל בית [בעל בית]
12. פרופיל כללי
13. תיקון פרופיל [שם]
14. בדוק פרופיל בית [בעל בית]
📊 *דוחות*
15. דוח יומי
16. כל הדוחות
17. חובות פתוחים
🔀 *כלים*
18. מיזוג לקוחות
19. חסר פספורט
────────────────────────────
💡 לפרטים נוספים כתוב *עזרה*
""")

PAID_STATUSES = ["שולם מלא", "Paid", "paid"]
UNPAID_STATUSES = ["לא שולם", "Unpaid", "unpaid"]

def _word_search_contacts(name_query: str, per_page: int = 8):
    """Search contacts using word search, then filter by smart exact-word logic.
    If Zoho returns no results, tries fuzzy search with difflib."""
    token, domain = get_access_token()
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    r = requests.get(f"{domain}/crm/v5/Contacts/search", headers=headers,
                     params={"word": name_query, "per_page": per_page})
    if r.status_code == 200:
        all_results = r.json().get("data", [])
        filtered = _smart_filter(all_results, name_query, "Full_Name")
        if filtered:
            return filtered
    # אם זוהו לא החזיר תוצאות - נסה חיפוש חכם עם difflib
    return _fuzzy_search_contacts(name_query, per_page)

def _word_search_accounts(name_query: str, per_page: int = 8):
    """Search accounts using word search, then filter by smart exact-word logic.
    If Zoho returns no results, tries fuzzy search with difflib."""
    token, domain = get_access_token()
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    r = requests.get(f"{domain}/crm/v5/Accounts/search", headers=headers,
                     params={"word": name_query, "per_page": per_page})
    if r.status_code == 200:
        all_results = r.json().get("data", [])
        filtered = _smart_filter(all_results, name_query, "Account_Name")
        if filtered:
            return filtered
    # אם זוהו לא החזיר תוצאות - נסה חיפוש חכם עם difflib
    return _fuzzy_search_accounts(name_query, per_page)

def _smart_filter(results: list, query: str, name_field: str) -> list:
    """
    Hebrew-safe smart filtering:
    - Exact whole-word match ONLY: split name into words by spaces/hyphens,
      check if any word == query exactly (case-insensitive).
    - If exact match found → return only those results.
    - If no exact match → return ALL results (let user choose from menu).
    This prevents 'סולומו' from also returning 'סולומון' and vice versa.
    """
    import re
    q = query.strip().lower()

    def get_words(name: str):
        # split on spaces, hyphens, en-dash, em-dash
        return [w.lower() for w in re.split(r'[\s\-\u2013\u2014]+', name.strip()) if w]

    # Exact whole-word match only
    exact = [r for r in results
             if q in get_words(r.get(name_field, "") or "")]
    if exact:
        return exact

    # No exact match - return all results so user can choose
    return results

def _fuzzy_search_contacts(name_query: str, per_page: int = 8) -> list:
    """
    חיפוש חכם לאנשי קשר עם difflib - מטפל בשגיאות כתיב בשמות עבריים.
    מוריד את כל אנשי הקשר ומדרג לפי דמיון שם.
    """
    from difflib import SequenceMatcher
    try:
        token, domain = get_access_token()
        headers = {"Authorization": f"Zoho-oauthtoken {token}"}
        # שלוף כמה מילים ראשונות מהשם לחיפוש רחב
        words = name_query.strip().split()
        candidates = []
        seen_ids = set()
        for word in words:
            if len(word) < 2:
                continue
            r = requests.get(f"{domain}/crm/v5/Contacts/search", headers=headers,
                             params={"word": word, "per_page": 20})
            if r.status_code == 200:
                for c in r.json().get("data", []):
                    if c["id"] not in seen_ids:
                        seen_ids.add(c["id"])
                        candidates.append(c)
        if not candidates:
            return []
        # דרג לפי דמיון שם
        q = name_query.strip().lower()
        def score(c):
            name = (c.get("Full_Name") or "").lower()
            return SequenceMatcher(None, q, name).ratio()
        ranked = sorted(candidates, key=score, reverse=True)
        # החזר רק אם דמיון >= 0.4
        good = [c for c in ranked if SequenceMatcher(None, q, (c.get("Full_Name") or "").lower()).ratio() >= 0.4]
        return good[:per_page] if good else ranked[:per_page]
    except Exception as e:
        print(f"_fuzzy_search_contacts error: {e}")
        return []

def _fuzzy_search_accounts(name_query: str, per_page: int = 8) -> list:
    """
    חיפוש חכם לבעלי בית עם difflib - מטפל בשגיאות כתיב בשמות עבריים.
    """
    from difflib import SequenceMatcher
    try:
        token, domain = get_access_token()
        headers = {"Authorization": f"Zoho-oauthtoken {token}"}
        words = name_query.strip().split()
        candidates = []
        seen_ids = set()
        for word in words:
            if len(word) < 2:
                continue
            r = requests.get(f"{domain}/crm/v5/Accounts/search", headers=headers,
                             params={"word": word, "per_page": 20})
            if r.status_code == 200:
                for a in r.json().get("data", []):
                    if a["id"] not in seen_ids:
                        seen_ids.add(a["id"])
                        candidates.append(a)
        if not candidates:
            return []
        q = name_query.strip().lower()
        def score(a):
            name = (a.get("Account_Name") or "").lower()
            return SequenceMatcher(None, q, name).ratio()
        ranked = sorted(candidates, key=score, reverse=True)
        good = [a for a in ranked if SequenceMatcher(None, q, (a.get("Account_Name") or "").lower()).ratio() >= 0.4]
        return good[:per_page] if good else ranked[:per_page]
    except Exception as e:
        print(f"_fuzzy_search_accounts error: {e}")
        return []

def _format_contact_choice_menu(contacts, action_label: str) -> str:
    """Format a numbered menu for contact selection"""
    lines = [f"🔍 נמצאו {len(contacts)} לקוחות - בחר מספר ל{action_label}:",
             "─" * 28]
    for i, c in enumerate(contacts, 1):
        cname = c.get("Full_Name", "")
        account = c.get("Account_Name", {})
        aname = account.get("name", "") if isinstance(account, dict) else str(account)
        lines.append(f"{i}. {cname} (🏠 {aname})")
    lines.append("─" * 28)
    lines.append("שלח *ביטול* לביטול | *תפריט* לתפריט ראשי")
    return "\n".join(lines)

def _format_account_choice_menu(accounts, action_label: str) -> str:
    """Format a numbered menu for account selection"""
    lines = [f"🔍 נמצאו {len(accounts)} בעלי בית - בחר מספר ל{action_label}:",
             "─" * 28]
    for i, a in enumerate(accounts, 1):
        aname = a.get("Account_Name", "")
        lines.append(f"{i}. {aname}")
    lines.append("─" * 28)
    lines.append("שלח *ביטול* לביטול | *תפריט* לתפריט ראשי")
    return "\n".join(lines)

def build_open_debts_report() -> str:
    """דוח חובות פתוחים - כל חשבוניות שלא שולמו"""
    SEP = "──────────────"
    today_str = datetime.now().strftime("%d/%m/%Y")
    try:
        invoices = zoho_get("Invoices/search", {
            "criteria": "(Status:equals:לא שולם)",
            "fields": "Subject,Grand_Total,Contact_Name,Account_Name,Created_Time",
            "sort_by": "Created_Time",
            "sort_order": "desc",
            "per_page": 100
        })
    except Exception as e:
        return f"❌ שגיאה: {e}"
    if not invoices:
        return f"✅ אין חובות פתוחים כרגע! ({today_str})"
    lines = [f"🚨 *חובות פתוחים - {today_str}*", SEP]
    grand = 0
    for inv in invoices:
        contact = inv.get("Contact_Name", {})
        cname = contact.get("name", "") if isinstance(contact, dict) else str(contact)
        account = inv.get("Account_Name", {})
        aname = account.get("name", "") if isinstance(account, dict) else str(account)
        total = inv.get("Grand_Total", 0) or 0
        created = inv.get("Created_Time", "")[:10] if inv.get("Created_Time") else ""
        try:
            days_open = (datetime.now() - datetime.strptime(created, "%Y-%m-%d")).days
        except:
            days_open = 0
        grand += total
        lines.append(f"👤 *{cname}*")
        lines.append(f"   🏠 {aname} | 💰 ₪{total} | 📅 {created} ({days_open} ימים)")
        lines.append("")
    lines.append(SEP)
    lines.append(f"🚨 סהכ חובות: *₪{grand}* | {len(invoices)} חשבוניות")
    return "\n".join(lines)

def build_customer_status(name_query: str, contact=None) -> str:
    """סטטוס מלא של לקוח - רק חשבונות פתוחות + פרטי לקוח"""
    SEP = "──────────────"
    if contact is None:
        contacts = _word_search_contacts(name_query)
        if not contacts:
            return f"❓ לא מצאתי לקוח בשם *{name_query}*"
        contact = contacts[0]
    # שלוף פרטים מלאים של איש הקשר כולל שדות מותאמים
    try:
        token, domain = get_access_token()
        import requests as _req
        r_full = _req.get(f"{domain}/crm/v5/Contacts/{contact['id']}",
                          headers={"Authorization": f"Zoho-oauthtoken {token}"},
                          params={"fields": "Full_Name,Account_Name,field8,field11,field12,field6,Mobile"})
        if r_full.status_code == 200:
            contact = r_full.json()["data"][0]
    except:
        pass
    cname = contact.get("Full_Name", name_query)
    account = contact.get("Account_Name", {})
    aname = account.get("name", "") if isinstance(account, dict) else str(account)
    company   = contact.get("field8", "") or ""   # חברה
    active_lines = contact.get("field11", 0) or 0  # קווים פעילים
    line_numbers = contact.get("field12", "") or "" # מספרי קווים
    mobile = contact.get("Mobile", "") or ""
    cid = contact["id"]
    # שלוף חשבוניות פתוחות בלבד
    try:
        invoices = zoho_get("Invoices/search", {
            "criteria": f"(Contact_Name:equals:{cid})",
            "fields": "Subject,Grand_Total,Status,Created_Time,Invoiced_Items",
            "sort_by": "Created_Time",
            "sort_order": "desc",
            "per_page": 100
        })
    except:
        invoices = []
    unpaid = [i for i in invoices if i.get("Status") not in PAID_STATUSES]
    debt_amount = sum(i.get("Grand_Total", 0) or 0 for i in unpaid)
    # סיכום מוצרים שנקנו (כולל סגורות)
    product_counts = {}
    for inv in invoices:
        items = inv.get("Invoiced_Items") or []
        if isinstance(items, list):
            for item in items:
                pname = ""
                if isinstance(item, dict):
                    prod = item.get("product") or item.get("Product_Name") or {}
                    pname = prod.get("name", "") if isinstance(prod, dict) else str(prod)
                if pname:
                    product_counts[pname] = product_counts.get(pname, 0) + 1
    lines = [
        f"👤 *{cname}*",
        SEP,
        f"🏠 בעל בית: {aname}",
    ]
    if company:
        lines.append(f"🏢 חברה: {company}")
    if mobile:
        lines.append(f"📱 טלפון: {mobile}")
    lines.append(f"📞 קווים פעילים: {active_lines}")
    if line_numbers:
        lines.append(f"🔢 מספרי קווים: {line_numbers}")
    lines.append(SEP)
    if unpaid:
        lines.append(f"🚨 *חוב פתוח: ₪{debt_amount} ({len(unpaid)} חשבונות)*")
        for inv in unpaid:
            created = inv.get("Created_Time", "")[:10]
            lines.append(f"   • ₪{inv.get('Grand_Total',0)} | {created}")
    else:
        lines.append("✅ אין חובות פתוחים")
    if product_counts:
        lines.append(SEP)
        lines.append("📦 *מוצרים שנקנו:*")
        for pname, qty in sorted(product_counts.items(), key=lambda x: -x[1]):
            lines.append(f"   • {pname} x{qty}")
    return "\n".join(lines)

def build_landlord_report(name_query: str, account=None) -> tuple:
    """דוח בעל בית - רק חשבונות פתוחות לפי לקוח + קווים פעילים.
    Returns (report_text, ordered_contact_ids) for interactive selection."""
    SEP = "──────────────"
    if account is None:
        accounts = _word_search_accounts(name_query)
        if not accounts:
            return f"❓ לא מצאתי בעל בית בשם *{name_query}*", []
        account = accounts[0]
    aname = account.get("Account_Name", name_query)
    aid = account["id"]
    # שלוף חשבונות פתוחות (לא שולם + שולם חלקית)
    try:
        inv_unpaid = zoho_get("Invoices/search", {
            "criteria": f"(Account_Name:equals:{aid})and(Status:equals:לא שולם)",
            "fields": "Subject,Grand_Total,Status,Contact_Name,Created_Time",
            "per_page": 100
        })
    except:
        inv_unpaid = []
    try:
        inv_partial = zoho_get("Invoices/search", {
            "criteria": f"(Account_Name:equals:{aid})and(Status:equals:שולם חלקית)",
            "fields": "Subject,Grand_Total,Status,Contact_Name,Created_Time",
            "per_page": 100
        })
    except:
        inv_partial = []
    invoices = inv_unpaid + inv_partial
    # קבץ לפי לקוח + שלוף קווים פעילים לכל איש קשר
    by_contact = {}   # cname -> {"debt": total, "invs": [...], "contact_id": id}
    contact_ids = {}
    for inv in invoices:
        contact = inv.get("Contact_Name", {})
        cname = contact.get("name", "") if isinstance(contact, dict) else str(contact)
        cid   = contact.get("id", "")   if isinstance(contact, dict) else ""
        total = inv.get("Grand_Total", 0) or 0
        created = inv.get("Created_Time", "")[:10]
        if cname not in by_contact:
            by_contact[cname] = {"debt": 0, "invs": []}
            contact_ids[cname] = cid
        status = inv.get("Status", "")
        em = "🟡" if status == "שולם חלקית" else "🚨"
        by_contact[cname]["debt"] += total
        by_contact[cname]["invs"].append({"total": total, "date": created, "em": em})
    # שלוף קווים פעילים לכל לקוח (שדה field11)
    active_lines_map = {}
    try:
        token, domain = get_access_token()
        import requests as _req
        for cname, cid in contact_ids.items():
            if cid:
                r = _req.get(f"{domain}/crm/v5/Contacts/{cid}",
                             headers={"Authorization": f"Zoho-oauthtoken {token}"},
                             params={"fields": "field11"})
                if r.status_code == 200:
                    active_lines_map[cname] = r.json()["data"][0].get("field11", 0) or 0
    except:
        pass
    grand_debt = sum(v["debt"] for v in by_contact.values())
    lines = [
        f"🏠 *{aname}*",
        SEP,
        f"🚨 חובות פתוחים: *₪{grand_debt}* | {len(invoices)} חשבונות | {len(by_contact)} לקוחות",
        SEP,
    ]
    ordered_contacts = []  # list of (cname, cid) in display order
    if not by_contact:
        lines.append("✅ אין חובות פתוחים")
        return "\n".join(lines), []
    # מיין לפי שם לקוח (אלפבית)
    for idx, cname in enumerate(sorted(by_contact.keys()), 1):
        data = by_contact[cname]
        active = active_lines_map.get(cname, 0)
        cid = contact_ids.get(cname, "")
        ordered_contacts.append((cname, cid))
        lines.append(f"{idx}. 👤 *{cname}* | 📞 {active} קווים | 🚨 ₪{data['debt']}")
        for i in data["invs"]:
            lines.append(f"   {i['em']} ₪{i['total']} | {i['date']}")
        lines.append("")
    lines.append(SEP)
    lines.append("💡 שלח מספר לסטטוס מלא של אותו לקוח")
    lines.append("שלח *ביטול* לביטול | *תפריט* לתפריט ראשי")
    return "\n".join(lines), ordered_contacts

def update_passport_for_contact(contact: dict) -> str:
    """
    מוריד את קובץ הפספורט מהלקוח, מחלץ שם באנגלית ומעדכן שדה Visa_Name1.
    מחזיר הודעת תוצאה.
    """
    contact_id = contact["id"]
    contact_name = contact.get("Full_Name", "")

    # שלוף קבצים מצורפים
    token, domain = get_access_token()
    headers_z = {"Authorization": f"Zoho-oauthtoken {token}"}
    r = requests.get(f"{domain}/crm/v2/Contacts/{contact_id}/Attachments", headers=headers_z)
    if r.status_code != 200:
        return f"❌ שגיאה בשליפת קבצים עבור {contact_name}"
    attachments = r.json().get("data", [])
    if not attachments:
        return f"❌ לא נמצאו קבצים מצורפים עבור {contact_name}"

    # מיין: פספורט ראשון, אחר כך שאר הקבצים
    # חלק לשתי רשימות: פספורט בשם, ושאר
    def _is_passport_file(att):
        fname = att.get("File_Name", "").lower()
        return ("פספורט" in fname or "passport" in fname or "תעודה" in fname or "id" in fname or "visa" in fname)
    passport_files = [a for a in attachments if _is_passport_file(a)]
    other_files = [a for a in attachments if not _is_passport_file(a)]
    # נסה קודם קבצי פספורט, ורק אם לא מצא - שאר הקבצים
    attachments_sorted = passport_files + other_files

    # נסה לחלץ שם מכל קובץ עד שמצליח
    for att in attachments_sorted:
        fname = att.get("File_Name", "")
        att_id = att["id"]
        # הורד את הקובץ
        r2 = requests.get(f"{domain}/crm/v2/Contacts/{contact_id}/Attachments/{att_id}", headers=headers_z)
        if r2.status_code != 200:
            continue
        img_bytes = r2.content
        # שלח ל-Gemini Vision לחילוץ שם
        import base64
        img_b64 = base64.b64encode(img_bytes).decode("utf-8")
        # זהה סוג תמונה
        content_type = r2.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        if "png" in content_type:
            mime = "image/png"
        elif "webp" in content_type:
            mime = "image/webp"
        else:
            mime = "image/jpeg"

        gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{
                "parts": [
                    {"inline_data": {"mime_type": mime, "data": img_b64}},
                    {"text": "Look at this image. If it is a passport or official ID document, extract ONLY the full English name (given name + surname as printed). Return ONLY the name in UPPERCASE, nothing else. Format: FIRSTNAME LASTNAME. If the image is NOT a passport or official ID document (e.g. it is a photo of a person, a selfie, a document in a different language without Latin name, or any other non-ID image), return exactly the word: NONE"}
                ]
            }],
            "generationConfig": {"temperature": 0}
        }
        try:
            gr = requests.post(gemini_url, json=payload, timeout=30)
            if gr.status_code == 200:
                extracted = gr.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                # ניקוי - רק אותיות לטיניות ורווחים
                import re as _re
                extracted_clean = _re.sub(r'[^A-Za-z\s]', '', extracted).strip().upper()
                _refusal_words = {"SORRY", "CANNOT", "UNABLE", "DOCUMENT", "IMAGE", "PASSPORT", "REQUEST", "EXTRACT", "PROVIDED", "THEREFORE", "FULFILL", "PHOTOGRAPH", "PERSON", "LOCATED", "NONE"}
                word_count = len(extracted_clean.split())
                has_refusal = any(w in extracted_clean.split() for w in _refusal_words)
                if extracted_clean and 1 <= word_count <= 4 and len(extracted_clean) <= 50 and not has_refusal:
                    # עדכן שדה Visa_Name1
                    upd = requests.put(f"{domain}/crm/v2/Contacts",
                                       headers={**headers_z, "Content-Type": "application/json"},
                                       json={"data": [{"id": contact_id, "Visa_Name1": extracted_clean}]})
                    if upd.status_code == 200 and upd.json().get("data", [{}])[0].get("code") == "SUCCESS":
                        return f"✅ שם ויזה עודכן!\n👤 {contact_name}\n🪪 {extracted_clean}"
                    else:
                        return f"❌ שגיאה בעדכון שדה שם ויזה"
        except Exception as e:
            print(f"Gemini vision error: {e}")
            continue

    return f"❌ לא הצלחתי לחלץ שם מהמסמכים של {contact_name}"


def handle_command(message, from_number):
    print(f"handle_command: '{message}' from {from_number}")
    session = sessions.get(from_number, {})
    pending = session.get("pending")

    # === בחירת לקוח לעדכון פספורט ===
    if pending == "choose_contact_passport":
        contacts = session.get("contacts", [])
        choice = message.strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(contacts):
                sessions.pop(from_number, None)
                return update_passport_for_contact(contacts[idx])
        return f"❓ כתוב מספר בין 1 ל-{len(contacts)}"

    # === בחירת לקוח להעלאת פרופיל מתמונה ===
    if pending == "choose_contact_profile_upload":
        contacts = session.get("contacts", [])
        media_url = session.get("media_url", "")
        media_type = session.get("media_type", "image/jpeg")
        choice = message.strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(contacts):
                sessions.pop(from_number, None)
                return _do_profile_upload(contacts[idx], media_url, media_type)
        return f"❓ כתוב מספר בין 1 ל-{len(contacts)}"

    # === בחירת בעל בית לעדכון פרופילים במאסס ===
    if pending == "choose_account_bulk_profile":
        accounts = session.get("accounts", [])
        choice = message.strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(accounts):
                sessions.pop(from_number, None)
                aname = accounts[idx].get("Account_Name", "")
                sessions[from_number] = {"pending": "confirm_bulk_profile", "account": accounts[idx]}
                return (f"🔍 תעדכן פרופילים לכל לקוחות בית *{aname}*\n"
                        f"התהליך מחפש קובץ 'פרופיל' בקבצים ומעדכן תמונת פרופיל.\n"
                        f"האם להתחיל?\n1. כן\n2. לא")
        return f"❓ כתוב מספר בין 1 ל-{len(accounts)}"

    # === אישור עדכון פרופילים במאסס ===
    if pending == "confirm_bulk_profile":
        account = session.get("account", {})
        choice = message.strip()
        if choice in ["1", "כן"]:
            sessions.pop(from_number, None)
            def _run_bulk_profile():
                result, used_att_ids = bulk_profile_update_for_account(account, from_number)
                # שמור את ה-attachment IDs ששימשו כדי שהתיקון ידלג עליהם
                sessions[from_number] = {
                    "pending": "after_bulk_profile",
                    "account": account,
                    "used_att_ids": used_att_ids
                }
                _send_reply(result, from_number)
            threading.Thread(target=_run_bulk_profile, daemon=True).start()
            aname = account.get("Account_Name", "")
            return f"⏳ מתחיל עדכון פרופילים - *{aname}*... תקבל עדכון בסיום."
        elif choice in ["2", "לא"]:
            sessions.pop(from_number, None)
            return "❌ בוטל"
        return "❓ כתוב 1 (כן) או 2 (לא)"

    # === בדיקת פרופילים - בחירת מספרים לתיקון ===
    if pending == "review_profile_beit":
        account = session.get("account", {})
        contacts_data = session.get("contacts_data", [])  # [(contact, has_photo, atts)]
        choice = message.strip()
        if choice in ["0", "סיום", "יציאה", "ביטול"]:
            sessions.pop(from_number, None)
            return "✅ סיום בדיקת פרופילים."
        # Parse comma/space separated numbers
        import re as _re
        nums = [int(x) for x in _re.findall(r"\d+", choice) if 1 <= int(x) <= len(contacts_data)]
        if not nums:
            return f"❓ שלח מספרים בין 1 ל-{len(contacts_data)} מופרדים בפסיקים (או 0 לסיום)"
        sessions.pop(from_number, None)
        used_att_ids = session.get("used_att_ids", {})
        to_fix = [(contacts_data[n-1][0], contacts_data[n-1][2]) for n in nums]
        def _run_fix_profiles():
            result, new_used = _fix_profiles_from_next_attachment(to_fix, account, from_number, used_att_ids)
            # עדכן את ה-used_att_ids עם הנוכחיים
            merged = {**used_att_ids, **new_used}
            sessions[from_number] = {
                "pending": "review_profile_beit",
                "account": account,
                "contacts_data": contacts_data,
                "used_att_ids": merged
            }
            _send_reply(result, from_number)
            _send_reply("שלח מספרים נוספים לתיקון, או 0 לסיום.", from_number)
        threading.Thread(target=_run_fix_profiles, daemon=True).start()
        names = ", ".join(contacts_data[n-1][0].get("Full_Name","") for n in nums)
        return f"⏳ מתחיל תיקון פרופילים: {names}..."


    # === בחירת לקוח להעלאת פספורט מתמונה ===
    if pending == "choose_contact_passport_upload":
        contacts = session.get("contacts", [])
        media_url = session.get("media_url", "")
        media_type = session.get("media_type", "image/jpeg")
        choice = message.strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(contacts):
                sessions.pop(from_number, None)
                return _do_passport_upload_and_update(contacts[idx], media_url, media_type)
        return f"❓ כתוב מספר בין 1 ל-{len(contacts)}"

    # === תיקון פרופיל - בחירת לקוח ===
    if pending == "choose_contact_fix_profile":
        contacts = session.get("contacts", [])
        choice = message.strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(contacts):
                contact = contacts[idx]
                cid = contact["id"]
                cname = contact.get("Full_Name", "")
                sessions.pop(from_number, None)
                # טען קבצים
                def _load_atts_fix():
                    try:
                        token2, domain2 = get_access_token()
                        h2 = {"Authorization": f"Zoho-oauthtoken {token2}"}
                        r = requests.get(f"{domain2}/crm/v2/Contacts/{cid}/Attachments", headers=h2)
                        atts = r.json().get("data", []) if r.status_code == 200 else []
                        img_exts = ('.jpg','.jpeg','.png','.webp','.heic','.heif')
                        image_atts = [a for a in atts if a.get("File_Name","").lower().endswith(img_exts)]
                        if not image_atts:
                            _send_reply(f"❌ אין תמונות בקבצים של {cname}", from_number)
                            return
                        # טען שאר לקוחות מאותו בית
                        acc_info = contact.get("Account_Name", {})
                        acc_id = acc_info.get("id", "") if isinstance(acc_info, dict) else ""
                        acc_name = acc_info.get("name", "") if isinstance(acc_info, dict) else ""
                        account_contacts = []
                        if acc_id:
                            rc = requests.get(f"{domain2}/crm/v2/Contacts/search",
                                headers=h2,
                                params={"criteria": f"(Account_Name:equals:{acc_id})", "fields": "Full_Name,id", "per_page": 200})
                            if rc.status_code == 200:
                                account_contacts = rc.json().get("data", [])
                        lines = [f"📋 *תמונות של {cname}:*"]
                        for j, a in enumerate(image_atts, 1):
                            lines.append(f"{j}. {a.get('File_Name','')}")
                        lines.append("\nשלח מספר לבחירה (0 = ביטול)")
                        sessions[from_number] = {
                            "pending": "pick_attachment_fix_profile",
                            "contact": contact,
                            "image_atts": image_atts,
                            "account_contacts": account_contacts,
                            "account_name": acc_name
                        }
                        _send_reply("\n".join(lines), from_number)
                    except Exception as e:
                        _send_reply(f"❌ שגיאה בטעינת קבצים: {e}", from_number)
                threading.Thread(target=_load_atts_fix, daemon=True).start()
                return f"⏳ טוען קבצים של {cname}..."
        return f"❓ כתוב מספר בין 1 ל-{len(contacts)}"

    # === תיקון פרופיל - בחירת קובץ ===
    if pending == "pick_attachment_fix_profile":
        contact = session.get("contact", {})
        image_atts = session.get("image_atts", [])
        account_contacts = session.get("account_contacts", [])  # שאר לקוחות באותו בית
        account_name = session.get("account_name", "")
        cname = contact.get("Full_Name", "")
        cid = contact["id"]
        choice = message.strip()
        if choice == "0":
            sessions.pop(from_number, None)
            return "❌ בוטל"
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(image_atts):
                att = image_atts[idx]
                sessions.pop(from_number, None)
                def _do_fix_profile():
                    try:
                        token2, domain2 = get_access_token()
                        h2 = {"Authorization": f"Zoho-oauthtoken {token2}"}
                        r2 = requests.get(f"{domain2}/crm/v2/Contacts/{cid}/Attachments/{att['id']}", headers=h2)
                        if r2.status_code != 200:
                            _send_reply(f"❌ לא הצלחתי להוריד {att['File_Name']}", from_number)
                            return
                        face_bytes, face_dbg = _crop_face_center(r2.content)
                        if not face_bytes:
                            _send_reply(f"⚠️ חיתוך פנים נכשל: {face_dbg[:80]}", from_number)
                            return
                        photo_resp = requests.post(
                            f"{domain2}/crm/v2/Contacts/{cid}/photo",
                            headers=h2,
                            files={"file": ("profile.jpg", face_bytes, "image/jpeg")}
                        )
                        if photo_resp.status_code in [200, 201, 202]:
                            _send_reply(f"✅ תמונת פרופיל של {cname} עודכנה מ: {att['File_Name']}", from_number)
                            # אם יש שאר לקוחות באותו בית - הצג תפריט
                            others = [c for c in account_contacts if c["id"] != cid]
                            if others:
                                lines = [f"🏠 *שאר לקוחות של {account_name}:*"]
                                for j, c in enumerate(others, 1):
                                    lines.append(f"{j}. {c.get('Full_Name','')}")
                                lines.append("\nשלח מספר לתיקון פרופיל, או 0 לסיום")
                                sessions[from_number] = {
                                    "pending": "choose_next_fix_profile",
                                    "contacts": others,
                                    "account_contacts": account_contacts,
                                    "account_name": account_name
                                }
                                _send_reply("\n".join(lines), from_number)
                        else:
                            _send_reply(f"❌ שגיאה בעדכון פרופיל ({photo_resp.status_code})", from_number)
                    except Exception as e:
                        _send_reply(f"❌ שגיאה: {str(e)[:80]}", from_number)
                threading.Thread(target=_do_fix_profile, daemon=True).start()
                return f"⏳ מעדכן פרופיל של {cname} מ: {att['File_Name']}..."
        return f"❓ כתוב מספר בין 1 ל-{len(image_atts)}"

    # === תיקון פרופיל - בחירת לקוח הבא מאותו בית ===
    if pending == "choose_next_fix_profile":
        contacts = session.get("contacts", [])
        account_contacts = session.get("account_contacts", [])
        account_name = session.get("account_name", "")
        choice = message.strip()
        if choice == "0":
            sessions.pop(from_number, None)
            return "✅ סיום"
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(contacts):
                contact = contacts[idx]
                cid = contact["id"]
                cname = contact.get("Full_Name", "")
                sessions.pop(from_number, None)
                def _load_atts_next():
                    try:
                        token2, domain2 = get_access_token()
                        h2 = {"Authorization": f"Zoho-oauthtoken {token2}"}
                        r = requests.get(f"{domain2}/crm/v2/Contacts/{cid}/Attachments", headers=h2)
                        atts = r.json().get("data", []) if r.status_code == 200 else []
                        img_exts = ('.jpg','.jpeg','.png','.webp','.heic','.heif')
                        image_atts = [a for a in atts if a.get("File_Name","").lower().endswith(img_exts)]
                        if not image_atts:
                            _send_reply(f"❌ אין תמונות בקבצים של {cname}", from_number)
                            # הצג שאר לקוחות
                            others = [c for c in account_contacts if c["id"] != cid]
                            if others:
                                lines = [f"🏠 *שאר לקוחות של {account_name}:*"]
                                for j, c in enumerate(others, 1):
                                    lines.append(f"{j}. {c.get('Full_Name','')}")
                                lines.append("\nשלח מספר לתיקון פרופיל, או 0 לסיום")
                                sessions[from_number] = {
                                    "pending": "choose_next_fix_profile",
                                    "contacts": others,
                                    "account_contacts": account_contacts,
                                    "account_name": account_name
                                }
                                _send_reply("\n".join(lines), from_number)
                            return
                        lines = [f"📋 *תמונות של {cname}:*"]
                        for j, a in enumerate(image_atts, 1):
                            lines.append(f"{j}. {a.get('File_Name','')}")
                        lines.append("\nשלח מספר לבחירה (0 = ביטול)")
                        sessions[from_number] = {
                            "pending": "pick_attachment_fix_profile",
                            "contact": contact,
                            "image_atts": image_atts,
                            "account_contacts": account_contacts,
                            "account_name": account_name
                        }
                        _send_reply("\n".join(lines), from_number)
                    except Exception as e:
                        _send_reply(f"❌ שגיאה: {e}", from_number)
                threading.Thread(target=_load_atts_next, daemon=True).start()
                return f"⏳ טוען קבצים של {cname}..."
        return f"❓ כתוב מספר בין 1 ל-{len(contacts)}, או 0 לסיום"

    # === בחירת לקוח מתוצאות חיפוש ===
    if pending == "choose_contact_status":
        contacts = session.get("contacts", [])
        name_q = session.get("name_q", "")
        choice = message.strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(contacts):
                sessions.pop(from_number, None)
                return build_customer_status(name_q, contact=contacts[idx])
        return f"❓ כתוב מספר בין 1 ל-{len(contacts)}"

    # === בחירת בעל בית מתוצאות חיפוש ===
    if pending == "choose_account_report":
        accounts = session.get("accounts", [])
        name_q = session.get("name_q", "")
        choice = message.strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(accounts):
                sessions.pop(from_number, None)
                report, ordered_contacts = build_landlord_report(name_q, account=accounts[idx])
                if ordered_contacts:
                    sessions[from_number] = {"pending": "choose_landlord_contact", "contacts": ordered_contacts}
                return report
        return f"❓ כתוב מספר בין 1 ל-{len(accounts)}"

    # === בחירת לקוח מסטטוס בית (לחיצה על ספרה) ===
    if pending == "choose_landlord_contact":
        contacts = session.get("contacts", [])  # list of (cname, cid)
        choice = message.strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(contacts):
                cname, cid = contacts[idx]
                sessions.pop(from_number, None)
                # בנה contact dict מינימלי ל-build_customer_status
                contact_obj = {"id": cid, "Full_Name": cname}
                return build_customer_status(cname, contact=contact_obj)
        return f"❓ כתוב מספר בין 1 ל-{len(contacts)}"

    # === בחירת בעל בית לעדכון פספורטות במאסס ===
    if pending == "choose_account_bulk_passport":
        accounts = session.get("accounts", [])
        choice = message.strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(accounts):
                sessions.pop(from_number, None)
                # שלח אישור ראשון
                aname = accounts[idx].get("Account_Name", "")
                sessions[from_number] = {"pending": "confirm_bulk_passport", "account": accounts[idx]}
                return (f"🔍 תעדכן פספורטים לכל לקוחות בית *{aname}*\n"
                        f"התהליך עובר על כל לקוח שאין לו שם ויזה ומחפש פספורט בקבצים.\n"
                        f"האם להתחיל?\n1. כן\n2. לא")
        return f"❓ כתוב מספר בין 1 ל-{len(accounts)}"

    # === אישור עדכון פספורטות במאסס ===
    if pending == "confirm_bulk_passport":
        account = session.get("account", {})
        choice = message.strip()
        if choice in ["1", "כן"]:
            sessions.pop(from_number, None)
            # הרץ בתהליך רקע ושלח עדכונים
            def _run_bulk():
                result = bulk_passport_update_for_account(account, from_number)
                _send_reply(result, from_number)
            threading.Thread(target=_run_bulk, daemon=True).start()
            aname = account.get("Account_Name", "")
            return f"⏳ מתחיל עדכון פספורטים - *{aname}*... תקבל עדכון בסיום."
        elif choice in ["2", "לא"]:
            sessions.pop(from_number, None)
            return "❌ בוטל"
        return "❓ כתוב 1 (כן) או 2 (לא)"

    # === דוח יומי - תפריט הפקדות משני ===
    if pending == "deposits_detail_menu":
        choice = message.strip()
        records = session.get("deposits_records", [])
        if choice == "1":
            # שמור סשניה פעילה כדי שאפשר מעבר לבעל בית
            sessions[from_number] = {"pending": "deposits_detail_menu", "deposits_records": records}
            return build_deposits_by_contact(records)
        elif choice == "2":
            # שמור סשניה פעילה כדי שאפשר מעבר ללקוח
            sessions[from_number] = {"pending": "deposits_detail_menu", "deposits_records": records}
            return build_deposits_by_landlord(records)
        else:
            return "❓ כתוב *1* לפי לקוח או *2* לפי בעל בית"

    # === דוח מכירות - תפריט משני ===
    if pending == "sales_detail_menu":
        choice = message.strip()
        invoices = session.get("sales_invoices", [])
        if choice == "1":
            sessions[from_number] = {"pending": "sales_detail_menu", "sales_invoices": invoices}
            return build_sales_by_contact(invoices)
        elif choice == "2":
            sessions[from_number] = {"pending": "sales_detail_menu", "sales_invoices": invoices}
            return build_sales_by_landlord(invoices)
        elif choice == "3":
            sessions[from_number] = {"pending": "sales_detail_menu", "sales_invoices": invoices}
            return build_sales_by_product(invoices)
        else:
            return "❓ כתוב 1, 2 או 3"

    # === דוח יומי - תפריט ראשי ===
    if pending == "report_menu":
        choice = message.strip()
        sessions.pop(from_number, None)
        if choice == "1":
            return build_daily_report()
        elif choice == "2":
            # שלוף נתונים ושמור בסשניה לתפריט
            try:
                invoices = _fetch_sales_today()
            except:
                invoices = []
            sessions[from_number] = {"pending": "sales_detail_menu", "sales_invoices": invoices}
            return build_sales_report_with_cache(invoices)
        elif choice == "3":
            # שלוף נתונים ושמור בסשניה לתפריט
            try:
                records = _fetch_deposits_today()
            except:
                records = []
            sessions[from_number] = {"pending": "deposits_detail_menu", "deposits_records": records}
            return build_deposits_report_with_cache(records)
        else:
            return "❓ בחר 1, 2 או 3"

    # === דוח יומי - תפריט ===
    if message.strip() in ["דוח יומי"]:
        sessions[from_number] = {"pending": "report_menu"}
        return (
            "📊 *דוחות יומיים* - בחר סוג:\n"
            "────────────────────────────\n"
            "1️⃣ פעולות יומיות (WhatsApp)\n"
            "2️⃣ דוח מכירות יומי (Zoho)\n"
            "3️⃣ דוח הפקדות יומי (Zoho)\n"
            "────────────────────────────\n"
            "כתוב מספר לבחירה"
        )

    # === כל הדוחות - שליחת כל הדוחות בבת אחת ===
    if message.strip() == "כל הדוחות":
        sessions.pop(from_number, None)
        all_reports = []

        # 1. פעולות יומיות
        all_reports.append(build_daily_report())

        # 2. מכירות - כל 3 תצוגות
        try:
            sales_invoices = _fetch_sales_today()
        except:
            sales_invoices = []
        all_reports.append(build_sales_report_with_cache(sales_invoices))
        if sales_invoices:
            all_reports.append(build_sales_by_contact(sales_invoices))
            all_reports.append(build_sales_by_landlord(sales_invoices))
            all_reports.append(build_sales_by_product(sales_invoices))

        # 3. הפקדות - סיכום + לפי לקוח + לפי בעל בית
        try:
            dep_records = _fetch_deposits_today()
        except:
            dep_records = []
        all_reports.append(build_deposits_report_with_cache(dep_records))
        if dep_records:
            all_reports.append(build_deposits_by_contact(dep_records))
            all_reports.append(build_deposits_by_landlord(dep_records))

        # שלח כל דוח בנפרד (split אוטומטי אם צריך)
        owner_number = from_number
        parts_to_send = []
        for report in all_reports:
            parts_to_send.extend(split_message(report))

        # החזר את הדוח הראשון מייד, ושלח את השאר ברקע
        if not parts_to_send:
            return "😴 אין נתונים להיום"
        first = parts_to_send[0]
        rest = parts_to_send[1:]
        if rest:
            def _send_rest():
                time.sleep(1)
                for i, part in enumerate(rest):
                    try:
                        twilio_client.messages.create(
                            from_=TWILIO_WHATSAPP_FROM,
                            to=f"whatsapp:{owner_number.replace('whatsapp:', '')}",
                            body=part
                        )
                        time.sleep(0.5)
                    except Exception as e:
                        print(f"[ALL REPORTS] Error sending part {i+2}: {e}")
            threading.Thread(target=_send_rest, daemon=True).start()
        return first

    # === ביטול פעולה ===
    if message.strip() in ["ביטול", "ביטול", "cancel"]:
        sessions.pop(from_number, None)
        cancel_flags[from_number] = True
        return "✅ בוטל! פעולה פעילה תעצור בקרוב."

    # === עזרה ===
    if message.strip() in ["עזרה", "עזר", "help"]:
        sessions.pop(from_number, None)
        return HELP_TEXT.strip()

    # === תפריט ===
    if message.strip() in ["תפריט", "תפריט ראשי", "menu"]:
        sessions.pop(from_number, None)
        return MAIN_MENU_TEXT.strip()

    # === חובות פתוחים ===
    if message.strip() in ["חובות פתוחים", "חובות"]:
        sessions.pop(from_number, None)
        report = build_open_debts_report()
        parts = split_message(report)
        if len(parts) == 1:
            return parts[0]
        # שלח חלקים נוספים ברקע
        def _send_debt_rest():
            time.sleep(0.8)
            for p in parts[1:]:
                try:
                    twilio_client.messages.create(from_=TWILIO_WHATSAPP_FROM, to=f"whatsapp:{from_number.replace('whatsapp:', '')}", body=p)
                    time.sleep(0.5)
                except: pass
        threading.Thread(target=_send_debt_rest, daemon=True).start()
        return parts[0]

    # === עדכון פספורט ===
    msg_s = message.strip()
    if msg_s.startswith("עדכון פספורט "):
        name_q = msg_s[len("עדכון פספורט "):].strip()
        if name_q:
            contacts = _word_search_contacts(name_q)
            if not contacts:
                # נסה חיפוש רחב יותר - כל מילה בנפרד
                for word in name_q.split():
                    if len(word) >= 2:
                        contacts = _word_search_contacts(word, per_page=15)
                        if contacts:
                            break
            if not contacts:
                return f"❓ לא מצאתי לקוח בשם *{name_q}* - נסה שם קצר יותר"
            if len(contacts) == 1:
                sessions.pop(from_number, None)
                return update_passport_for_contact(contacts[0])
            # כמה לקוחות - הצג תפריט בחירה
            sessions[from_number] = {"pending": "choose_contact_passport", "contacts": contacts, "name_q": name_q}
            return _format_contact_choice_menu(contacts, "עדכון פספורט")

    # === חסר פספורט - סריקת לקוחות פעילים ללא שם ויזה ===
    if msg_s == "חסר פספורט":
        sessions.pop(from_number, None)
        _send_reply("⏳ סורק לקוחות פעילים ללא פספורט...", from_number)
        def _find_missing_passport():
            try:
                import datetime as _dt
                token, domain = get_access_token()
                headers_z = {"Authorization": f"Zoho-oauthtoken {token}"}
                one_year_ago = (_dt.datetime.utcnow() - _dt.timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
                accounts_with_missing = 0
                total_missing = 0
                account_details = []  # רשימת (שם_בעל_בית, [שמות_לקוחות])
                # טען את כל בעלי הבתים
                all_accounts = []
                page = 1
                while True:
                    r = requests.get(f"{domain}/crm/v5/Accounts",
                        headers=headers_z,
                        params={"fields": "Account_Name,id", "per_page": 200, "page": page})
                    if r.status_code != 200: break
                    batch = r.json().get("data", [])
                    if not batch: break
                    all_accounts.extend(batch)
                    if not r.json().get("info", {}).get("more_records"): break
                    page += 1
                if not all_accounts:
                    _send_reply("❌ לא נמצאו בעלי בתים", from_number)
                    return
                for acc in all_accounts:
                    acc_id = acc.get("id")
                    acc_name = acc.get("Account_Name", "")
                    # טען לקוחות של בעל הבית
                    contacts_page = 1
                    contacts = []
                    while True:
                        rc = requests.get(f"{domain}/crm/v5/Contacts/search",
                            headers=headers_z,
                            params={"criteria": f"(Account_Name.id:equals:{acc_id})",
                                    "fields": "Full_Name,Visa_Name1,id,field11",
                                    "per_page": 200, "page": contacts_page})
                        if rc.status_code != 200: break
                        batch_c = rc.json().get("data", [])
                        if not batch_c: break
                        contacts.extend(batch_c)
                        if not rc.json().get("info", {}).get("more_records"): break
                        contacts_page += 1
                    # סנן: לקוחות ללא שם ויזה עם חשבונית בשנה האחרונה או קו פעיל
                    missing_names = []
                    for c in contacts:
                        visa = c.get("Visa_Name1", "") or ""
                        if visa.strip():
                            continue  # יש שם ויזה - דלג
                        c_id = c.get("id")
                        # בדוק קו פעיל (field11 = 1 או 2)
                        active_lines = c.get("field11", None)
                        has_active_line = active_lines is not None and str(active_lines).strip() not in ["", "0", "None"]
                        # בדוק אם יש חשבונית בשנה האחרונה
                        has_recent = False
                        if not has_active_line:
                            try:
                                ri = requests.get(
                                    f"{domain}/crm/v5/Invoices/search",
                                    headers=headers_z,
                                    params={
                                        "criteria": f"(Contact_Name.id:equals:{c_id})AND(Created_Time:greater_equal:{one_year_ago})",
                                        "fields": "id", "per_page": 1
                                    }, timeout=15)
                                has_recent = ri.status_code == 200 and bool(ri.json().get("data"))
                            except Exception:
                                has_recent = False
                        if has_active_line or has_recent:
                            missing_names.append(c.get("Full_Name", ""))
                    if not missing_names:
                        continue
                    total_missing += len(missing_names)
                    accounts_with_missing += 1
                    account_details.append((acc_name, missing_names))
                    # בדוק אם יש כבר הערה "חסר פספורט" ב-Account
                    note_content = "חסר פספורט:\n" + "\n".join(f"• {n}" for n in missing_names)
                    try:
                        rn = requests.get(f"{domain}/crm/v2/Accounts/{acc_id}/Notes",
                            headers=headers_z, timeout=15)
                        existing_note_id = None
                        if rn.status_code == 200:
                            for note in rn.json().get("data", []):
                                if "חסר פספורט" in (note.get("Note_Title", "") or "") or \
                                   "חסר פספורט" in (note.get("Note_Content", "") or ""):
                                    existing_note_id = note.get("id")
                                    break
                        if existing_note_id:
                            # עדכן הערה קיימת
                            requests.put(
                                f"{domain}/crm/v2/Notes/{existing_note_id}",
                                headers={**headers_z, "Content-Type": "application/json"},
                                json={"data": [{"id": existing_note_id, "Note_Title": "חסר פספורט", "Note_Content": note_content}]},
                                timeout=15)
                        else:
                            # צור הערה חדשה
                            requests.post(
                                f"{domain}/crm/v2/Notes",
                                headers={**headers_z, "Content-Type": "application/json"},
                                json={"data": [{
                                    "Note_Title": "חסר פספורט",
                                    "Note_Content": note_content,
                                    "Parent_Id": {"id": acc_id},
                                    "se_module": "Accounts"
                                }]},
                                timeout=15)
                    except Exception as e_note:
                        pass  # המשך גם אם הערה נכשלה
                # בנה סיכום מפורט
                summary_lines = [f"✅ *סריקת חסר פספורט הסתיימה*"]
                summary_lines.append(f"📋 בעלי בתים: {accounts_with_missing} | 👤 לקוחות: {total_missing}\n")
                for acc_name_d, names_list in account_details:
                    summary_lines.append(f"*{acc_name_d}*:")
                    for n in names_list:
                        summary_lines.append(f"  • {n}")
                    summary_lines.append("")
                _send_reply("\n".join(summary_lines), from_number)
            except Exception as e:
                _send_reply(f"❌ שגיאה: {e}", from_number)
        threading.Thread(target=_find_missing_passport, daemon=True).start()
        return "⏳ מתחיל סריקה..."

    # === פונקציית עזר: בניית רשימת זוגות כפולים ===
    def _build_merge_list(dups):
        lines = [f"🔀 *{len(dups)} זוגות נותרים:*\n"]
        for idx, (c1, c2, ratio) in enumerate(dups, 1):
            v1 = (c1.get("Visa_Name1") or "").strip()
            v2 = (c2.get("Visa_Name1") or "").strip()
            a1 = c1.get("Account_Name", {}).get("name", "") if isinstance(c1.get("Account_Name"), dict) else str(c1.get("Account_Name", ""))
            a2 = c2.get("Account_Name", {}).get("name", "") if isinstance(c2.get("Account_Name"), dict) else str(c2.get("Account_Name", ""))
            pct = int(ratio * 100)
            lines.append(f"{idx}. *{c1.get('Full_Name')}* ({a1}) ⇔️ *{c2.get('Full_Name')}* ({a2})\n   ויזה: {v1} / {v2} | דמיון: {pct}%")
        lines.append("\nשלח מספר זוג למיזוג (לדוגמא: 1,3) או 0 לסיום")
        return "\n".join(lines)

    # === מיזוג לקוחות - חיפוש כפילוים לפי שם ויזה ===
    if msg_s == "מיזוג לקוחות":
        sessions.pop(from_number, None)
        _send_reply("⏳ מחפש לקוחות כפולים לפי שם ויזה...", from_number)
        def _find_duplicates():
            try:
                import difflib
                # שלוף כל הלקוחות (Zoho לא תומך ב-is_not_empty לשדה זה, מסננים בצד שלנו)
                all_contacts_raw = []
                page = 1
                while True:
                    batch, info = zoho_get_full("Contacts", {
                        "fields": "Full_Name,Visa_Name1,Account_Name,id,Created_Time",
                        "per_page": 200,
                        "page": page
                    })
                    if not batch:
                        break
                    all_contacts_raw.extend(batch)
                    if not info.get("more_records", False):
                        break
                    page += 1
                # סנן רק לקוחות עם שם ויזה
                all_contacts = [c for c in all_contacts_raw if (c.get("Visa_Name1") or "").strip()]
                if not all_contacts:
                    _send_reply("❌ לא נמצאו לקוחות עם שם ויזה", from_number)
                    return
                # מצא כפילוים - שם ויזה זהה או דומה (>85%)
                duplicates = []  # [(contact1, contact2, similarity)]
                checked = set()
                for i, c1 in enumerate(all_contacts):
                    v1 = (c1.get("Visa_Name1") or "").strip().upper()
                    if not v1 or c1["id"] in checked:
                        continue
                    for c2 in all_contacts[i+1:]:
                        if c2["id"] in checked:
                            continue
                        v2 = (c2.get("Visa_Name1") or "").strip().upper()
                        if not v2:
                            continue
                        # חשב דמיון
                        ratio = difflib.SequenceMatcher(None, v1, v2).ratio()
                        if ratio >= 0.85:
                            duplicates.append((c1, c2, ratio))
                if not duplicates:
                    _send_reply("✅ לא נמצאו לקוחות כפולים!", from_number)
                    return
                # הצג את הכפילוים
                lines = [f"🔀 *נמצאו {len(duplicates)} זוגות כפולים:*\n"]
                for idx, (c1, c2, ratio) in enumerate(duplicates, 1):
                    v1 = (c1.get("Visa_Name1") or "").strip()
                    v2 = (c2.get("Visa_Name1") or "").strip()
                    a1 = c1.get("Account_Name", {}).get("name", "") if isinstance(c1.get("Account_Name"), dict) else str(c1.get("Account_Name", ""))
                    a2 = c2.get("Account_Name", {}).get("name", "") if isinstance(c2.get("Account_Name"), dict) else str(c2.get("Account_Name", ""))
                    pct = int(ratio * 100)
                    lines.append(f"{idx}. *{c1.get('Full_Name')}* ({a1}) ↔️ *{c2.get('Full_Name')}* ({a2})\n   ויזה: {v1} / {v2} | דמיון: {pct}%")
                lines.append("\nשלח מספר זוג למיזוג (לדוגמא: 1,3) או 0 לביטול")
                sessions[from_number] = {"pending": "pick_merge_pairs", "duplicates": duplicates}
                _send_reply("\n".join(lines), from_number)
            except Exception as e:
                _send_reply(f"❌ שגיאה בחיפוש כפילוים: {e}", from_number)
        threading.Thread(target=_find_duplicates, daemon=True).start()
        return "⏳ מחפש..."

    # === מיזוג - בחירת זוגות למיזוג ===
    if pending == "pick_merge_pairs":
        duplicates = sessions[from_number].get("duplicates", [])
        if msg_s in ["0", "ביטול"]:
            sessions.pop(from_number, None)
            return "✅ בוטל"
        # פרס מספרים
        try:
            chosen_indices = [int(x.strip()) - 1 for x in msg_s.split(",") if x.strip().isdigit()]
        except:
            return "❓ שלח מספרים מופרדים בפסיקות (לדוגמא: 1,3) או 0 לביטול"
        invalid = [i+1 for i in chosen_indices if i < 0 or i >= len(duplicates)]
        if invalid:
            return f"❓ מספרים לא חוקיים: {invalid}. שלח מספרים בין 1 ל-{len(duplicates)}"
        chosen_pairs = [duplicates[i] for i in chosen_indices]
        # הצג אישור לכל זוג
        lines = ["🔄 *אישור מיזוג:*\n"]
        for c1, c2, ratio in chosen_pairs:
            a1 = c1.get("Account_Name", {}).get("name", "") if isinstance(c1.get("Account_Name"), dict) else str(c1.get("Account_Name", ""))
            a2 = c2.get("Account_Name", {}).get("name", "") if isinstance(c2.get("Account_Name"), dict) else str(c2.get("Account_Name", ""))
            lines.append(f"• *{c1.get('Full_Name')}* ({a1}) + *{c2.get('Full_Name')}* ({a2})")
            lines.append(f"  הישאר הלקוח עם חשבוניות אחרונות יותר, השני יסומן כלא פעיל")
        lines.append("\nכתוב *כן* לאישור או *לא* לביטול")
        sessions[from_number] = {"pending": "confirm_merge", "pairs": chosen_pairs, "duplicates": duplicates, "chosen_indices": chosen_indices}
        return "\n".join(lines)

    # === מיזוג - אישור וביצוע ===
    if pending == "confirm_merge":
        pairs = sessions[from_number].get("pairs", [])
        all_duplicates = sessions[from_number].get("duplicates", [])
        chosen_indices_done = sessions[from_number].get("chosen_indices", [])
        if msg_s not in ["כן", "yes", "y"]:
            sessions.pop(from_number, None)
            return "✅ בוטל"
        sessions.pop(from_number, None)
        _send_reply("⏳ מתחיל מיזוג...", from_number)
        def _do_merge():
            try:
                token, domain = get_access_token()
                headers_z = {"Authorization": f"Zoho-oauthtoken {token}"}
                results = []
                discarded_contacts = []
                for c1, c2, ratio in pairs:
                    # קבע מי ישאר (חשבוניות אחרונות יותר) ומי יסומן
                    inv1 = zoho_get("Invoices/search", {"criteria": f"(Contact_Name:equals:{c1['id']})", "fields": "id,Created_Time", "per_page": 200})
                    inv2 = zoho_get("Invoices/search", {"criteria": f"(Contact_Name:equals:{c2['id']})", "fields": "id,Created_Time", "per_page": 200})
                    def _latest_invoice_date(invs):
                        dates = []
                        for inv in invs:
                            ct = inv.get("Created_Time") or ""
                            if ct:
                                dates.append(ct)
                        return max(dates) if dates else ""
                    d1 = _latest_invoice_date(inv1)
                    d2 = _latest_invoice_date(inv2)
                    # הלקוח עם חשבוניות אחרונות יותר = ישאר
                    if d1 >= d2:
                        keep, discard = c1, c2
                        keep_invs, discard_invs = inv1, inv2
                    else:
                        keep, discard = c2, c1
                        keep_invs, discard_invs = inv2, inv1
                    keep_id = keep["id"]
                    discard_id = discard["id"]
                    moved_inv = 0
                    moved_att = 0
                    moved_notes = 0
                    moved_payments = 0
                    # 1. העבר חשבוניות מהישן לחדש
                    for inv in discard_invs:
                        res = zoho_put(f"Invoices/{inv['id']}", {"data": [{"id": inv["id"], "Contact_Name": {"id": keep_id}}]})
                        if res.get("data", [{}])[0].get("code") == "SUCCESS":
                            moved_inv += 1
                    # 1b. העבר בקרת תשלומים (CustomModule1) מהלקוח הישן לחדש
                    pay_page = 1
                    while True:
                        pay_batch, pay_info = zoho_get_full("CustomModule1/search", {
                            "criteria": f"(Contact:equals:{discard_id})",
                            "fields": "id,Contact,Invoice",
                            "per_page": 200,
                            "page": pay_page
                        })
                        if not pay_batch:
                            break
                        for pay in pay_batch:
                            pay_id = pay["id"]
                            res_p = zoho_put(f"CustomModule1/{pay_id}", {"data": [{"id": pay_id, "Contact": {"id": keep_id}}]})
                            if res_p.get("data", [{}])[0].get("code") == "SUCCESS":
                                moved_payments += 1
                        if not pay_info.get("more_records", False):
                            break
                        pay_page += 1
                    # 2. העבר קבצים מצורפים (Attachments) - הורד והעלה מחדש
                    r_att = requests.get(f"{domain}/crm/v2/Contacts/{discard_id}/Attachments", headers=headers_z, timeout=15)
                    if r_att.status_code == 200 and r_att.json().get("data"):
                        for att in r_att.json()["data"]:
                            att_id = att["id"]
                            # הורד את הקובץ
                            r_dl = requests.get(f"{domain}/crm/v2/Contacts/{discard_id}/Attachments/{att_id}", headers=headers_z, timeout=30)
                            if r_dl.status_code != 200:
                                continue
                            file_bytes = r_dl.content
                            fname = att.get("File_Name", "file.jpg")
                            content_type = r_dl.headers.get("content-type", "application/octet-stream")
                            # העלה ללקוח החדש
                            import io
                            upload_r = requests.post(
                                f"{domain}/crm/v2/Contacts/{keep_id}/Attachments",
                                headers={"Authorization": f"Zoho-oauthtoken {token}"},
                                files={"file": (fname, io.BytesIO(file_bytes), content_type)},
                                timeout=30
                            )
                            if upload_r.status_code in [200, 201]:
                                moved_att += 1
                    # 3. העבר הערות (Notes)
                    r_notes = requests.get(f"{domain}/crm/v2/Contacts/{discard_id}/Notes", headers=headers_z, timeout=15)
                    if r_notes.status_code == 200 and r_notes.json().get("data"):
                        for note in r_notes.json()["data"]:
                            note_body = note.get("Note_Content", "")
                            note_title = note.get("Note_Title", "")
                            # צור הערה חדשה בלקוח החדש
                            new_note = {
                                "data": [{
                                    "Note_Title": note_title or f"מיזוג מ-{discard.get('Full_Name', '')}",
                                    "Note_Content": note_body,
                                    "Parent_Id": {"id": keep_id},
                                    "se_module": "Contacts"
                                }]
                            }
                            r_new_note = requests.post(f"{domain}/crm/v2/Notes", headers={**headers_z, "Content-Type": "application/json"}, json=new_note, timeout=15)
                            if r_new_note.status_code in [200, 201]:
                                moved_notes += 1
                    # 4. סמן את הלקוח הישן כלא פעיל
                    zoho_put(f"Contacts/{discard_id}", {"data": [{"id": discard_id, "Contact_Status": "לא פעיל"}]})
                    discarded_contacts.append({"id": discard_id, "name": discard.get("Full_Name", "")})
                    results.append(
                        f"✅ מוזג: *{discard.get('Full_Name')}* → *{keep.get('Full_Name')}*\n"
                        f"   חשבוניות: {moved_inv} | תשלומים: {moved_payments} | קבצים: {moved_att} | הערות: {moved_notes}"
                    )
                summary = "🔀 *סיכום מיזוג:*\n" + "\n".join(results)
                # חשב את הזוגות הנותרים (בלי הזוגות שמוזגו)
                remaining_duplicates = [d for i, d in enumerate(all_duplicates) if i not in chosen_indices_done]
                # שאל אם למחוק את הלקוחות הישנים
                if discarded_contacts:
                    names_list = "\n".join(f"  • {c['name']}" for c in discarded_contacts)
                    summary += f"\n\n🗑️ *הלקוחות הישנים סומנו כלא פעילים:*\n{names_list}\n\nהאם למחוק אותם לגמרי מ-Zoho?\nשלח *כן* למחיקה או כל דבר אחר לביטול"
                    sessions[from_number] = {"pending": "confirm_delete_merged", "discarded_contacts": discarded_contacts, "remaining_duplicates": remaining_duplicates}
                elif remaining_duplicates:
                    # אין מחיקה - הצג מייד את הרשימה הנותרת
                    summary += "\n\n" + _build_merge_list(remaining_duplicates)
                    sessions[from_number] = {"pending": "pick_merge_pairs", "duplicates": remaining_duplicates}
                _send_reply(summary, from_number)
            except Exception as e:
                _send_reply(f"❌ שגיאה במיזוג: {e}", from_number)
        threading.Thread(target=_do_merge, daemon=True).start()
        return "⏳ מיזוג מתבצע..."

    # === אישור מחיקת לקוחות ישנים אחרי מיזוג ===
    if pending == "confirm_delete_merged":
        discarded_contacts = sessions[from_number].get("discarded_contacts", [])
        remaining_duplicates = sessions[from_number].get("remaining_duplicates", [])
        sessions.pop(from_number, None)
        if msg_s not in ["כן", "yes", "y"]:
            # ביטול מחיקה - הצג זוגות נותרים אם יש
            if remaining_duplicates:
                sessions[from_number] = {"pending": "pick_merge_pairs", "duplicates": remaining_duplicates}
                return "✅ בוטל מחיקה - הלקוחות נשמרו כלא פעילים\n\n" + _build_merge_list(remaining_duplicates)
            return "✅ בוטל - הלקוחות הישנים נשמרו כלא פעילים"
        _send_reply("⏳ מוחק לקוחות ישנים...", from_number)
        def _do_delete_merged():
            try:
                deleted = []
                failed = []
                for c in discarded_contacts:
                    res = zoho_delete(f"Contacts/{c['id']}")
                    code = (res.get("data", [{}])[0].get("code") or "") if res.get("data") else ""
                    if code == "SUCCESS":
                        deleted.append(c["name"])
                    else:
                        failed.append(c["name"])
                lines = ["🗑️ *סיכום מחיקה:*"]
                if deleted:
                    lines.append("✅ נמחקו:")
                    lines.extend(f"  • {n}" for n in deleted)
                if failed:
                    lines.append("❌ נכשלו:")
                    lines.extend(f"  • {n}" for n in failed)
                # הצג זוגות נותרים אם יש
                if remaining_duplicates:
                    lines.append("\n" + _build_merge_list(remaining_duplicates))
                    sessions[from_number] = {"pending": "pick_merge_pairs", "duplicates": remaining_duplicates}
                _send_reply("\n".join(lines), from_number)
            except Exception as e:
                _send_reply(f"❌ שגיאה במחיקה: {e}", from_number)
        threading.Thread(target=_do_delete_merged, daemon=True).start()
        return "⏳ מוחק..."

    # === תיקון פרופיל (ללא שם) - בחירת בעל בית מהפעילים ===
    if msg_s == "תיקון פרופיל":
        sessions.pop(from_number, None)
        def _load_active_accounts_fix():
            try:
                token2, domain2 = get_access_token()
                h2 = {"Authorization": f"Zoho-oauthtoken {token2}"}
                import datetime as _dt
                one_year_ago = (_dt.datetime.utcnow() - _dt.timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
                all_accs = []
                page = 1
                while True:
                    r = requests.get(f"{domain2}/crm/v5/Accounts",
                        headers=h2,
                        params={"fields": "Account_Name", "per_page": 200, "page": page})
                    if r.status_code != 200: break
                    batch = r.json().get("data", [])
                    if not batch: break
                    all_accs.extend(batch)
                    if not r.json().get("info", {}).get("more_records", False): break
                    page += 1
                if not all_accs:
                    _send_reply("\u274c \u05dc\u05d0 \u05e0\u05de\u05e6\u05d0\u05d5 \u05d1\u05e2\u05dc\u05d9 \u05d1\u05ea\u05d9\u05dd", from_number)
                    return
                active_accs = []
                for acc in all_accs:
                    acc_id = acc.get("id")
                    try:
                        inv_r = requests.get(
                            f"{domain2}/crm/v5/Invoices/search",
                            headers=h2,
                            params={
                                "criteria": f"(Account_Name.id:equals:{acc_id})AND(Created_Time:greater_equal:{one_year_ago})",
                                "fields": "id",
                                "per_page": 1
                            }
                        )
                        if inv_r.status_code == 200 and inv_r.json().get("data"):
                            active_accs.append(acc)
                    except Exception:
                        pass
                if not active_accs:
                    _send_reply("\u274c \u05dc\u05d0 \u05e0\u05de\u05e6\u05d0\u05d5 \u05d1\u05e2\u05dc\u05d9 \u05d1\u05ea\u05d9\u05dd \u05e4\u05e2\u05d9\u05dc\u05d9\u05dd", from_number)
                    return
                active_accs.sort(key=lambda a: a.get("Account_Name", ""))
                sessions[from_number] = {
                    "pending": "fix_profile_pick_account",
                    "accounts": active_accs
                }
                all_lines = [f"{j}. {a.get('Account_Name','')}" for j, a in enumerate(active_accs, 1)]
                footer = "\n\u05e9\u05dc\u05d7 \u05de\u05e1\u05e4\u05e8 \u05d1\u05e2\u05dc \u05d4\u05d1\u05d9\u05ea (0 \u05dc\u05d1\u05d9\u05d8\u05d5\u05dc):"
                MAX_CHARS = 1400
                chunks = []
                current_chunk = ["\U0001f3e0 *\u05ea\u05d9\u05e7\u05d5\u05df \u05e4\u05e8\u05d5\u05e4\u05d9\u05dc - \u05d1\u05d7\u05e8 \u05d1\u05e2\u05dc \u05d1\u05d9\u05ea (\u05e4\u05e2\u05d9\u05dc\u05d9\u05dd):*"]
                current_len = len(current_chunk[0])
                for line in all_lines:
                    if current_len + len(line) + 1 > MAX_CHARS:
                        chunks.append("\n".join(current_chunk))
                        current_chunk = []
                        current_len = 0
                    current_chunk.append(line)
                    current_len += len(line) + 1
                if current_chunk:
                    chunks.append("\n".join(current_chunk))
                for i, chunk in enumerate(chunks):
                    if i == len(chunks) - 1:
                        _send_reply(chunk + footer, from_number)
                    else:
                        _send_reply(chunk, from_number)
            except Exception as e:
                _send_reply(f"\u274c \u05e9\u05d2\u05d9\u05d0\u05d4: {e}", from_number)
        threading.Thread(target=_load_active_accounts_fix, daemon=True).start()
        return "\u23f3 \u05d8\u05d5\u05e2\u05df \u05e8\u05e9\u05d9\u05de\u05ea \u05d1\u05e2\u05dc\u05d9 \u05d1\u05ea\u05d9\u05dd \u05e4\u05e2\u05d9\u05dc\u05d9\u05dd..."

    # === תיקון פרופיל - בחירת בעל בית ===
    if pending == "fix_profile_pick_account":
        accounts = session.get("accounts", [])
        choice = message.strip()
        if choice == "0":
            sessions.pop(from_number, None)
            return "\u274c \u05d1\u05d5\u05d8\u05dc"
        if not choice.isdigit() or not (1 <= int(choice) <= len(accounts)):
            return f"\u2753 \u05e9\u05dc\u05d7 \u05de\u05e1\u05e4\u05e8 \u05d1\u05d9\u05df 1 \u05dc-{len(accounts)}, \u05d0\u05d5 0 \u05dc\u05d1\u05d9\u05d8\u05d5\u05dc"
        idx = int(choice) - 1
        account = accounts[idx]
        aname = account.get("Account_Name", "")
        sessions.pop(from_number, None)
        def _load_contacts_for_fix(account=account, aname=aname):
            try:
                token2, domain2 = get_access_token()
                h2 = {"Authorization": f"Zoho-oauthtoken {token2}"}
                acc_id = account.get("id")
                rc = requests.get(f"{domain2}/crm/v2/Contacts/search",
                    headers=h2,
                    params={"criteria": f"(Account_Name:equals:{acc_id})", "fields": "Full_Name,id", "per_page": 200})
                contacts = rc.json().get("data", []) if rc.status_code == 200 else []
                if not contacts:
                    _send_reply(f"\u274c \u05dc\u05d0 \u05e0\u05de\u05e6\u05d0\u05d5 \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea \u05d1\u05d1\u05d9\u05ea {aname}", from_number)
                    return
                contacts.sort(key=lambda c: c.get("Full_Name", ""))
                sessions[from_number] = {
                    "pending": "fix_profile_pick_contacts",
                    "account": account,
                    "contacts": contacts
                }
                all_lines = [f"{j}. {c.get('Full_Name','')}" for j, c in enumerate(contacts, 1)]
                footer = "\n\u05e9\u05dc\u05d7 \u05de\u05e1\u05e4\u05e8\u05d9\u05dd \u05de\u05d5\u05e4\u05e8\u05d3\u05d9\u05dd \u05d1\u05e4\u05e1\u05d9\u05e7\u05d5\u05ea (1,3,5) \u05d0\u05d5 '\u05d4\u05db\u05dc', \u05d0\u05d5 0 \u05dc\u05d1\u05d9\u05d8\u05d5\u05dc:"
                MAX_CHARS = 1400
                chunks = []
                current_chunk = [f"\U0001f465 *\u05dc\u05e7\u05d5\u05d7\u05d5\u05ea \u05e9\u05dc {aname}:*"]
                current_len = len(current_chunk[0])
                for line in all_lines:
                    if current_len + len(line) + 1 > MAX_CHARS:
                        chunks.append("\n".join(current_chunk))
                        current_chunk = []
                        current_len = 0
                    current_chunk.append(line)
                    current_len += len(line) + 1
                if current_chunk:
                    chunks.append("\n".join(current_chunk))
                for i, chunk in enumerate(chunks):
                    if i == len(chunks) - 1:
                        _send_reply(chunk + footer, from_number)
                    else:
                        _send_reply(chunk, from_number)
            except Exception as e:
                _send_reply(f"\u274c \u05e9\u05d2\u05d9\u05d0\u05d4: {e}", from_number)
        threading.Thread(target=_load_contacts_for_fix, daemon=True).start()
        return f"\u23f3 \u05d8\u05d5\u05e2\u05df \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea \u05e9\u05dc {aname}..."

    # === תיקון פרופיל - בחירת לקוחות ===
    if pending == "fix_profile_pick_contacts":
        account = session.get("account", {})
        contacts = session.get("contacts", [])
        aname = account.get("Account_Name", "")
        choice = message.strip()
        if choice == "0":
            sessions.pop(from_number, None)
            return "\u274c \u05d1\u05d5\u05d8\u05dc"
        import re as _re_fp
        if choice.strip() == "\u05d4\u05db\u05dc":
            nums = list(range(1, len(contacts) + 1))
        else:
            nums = [int(x) for x in _re_fp.findall(r'\d+', choice) if 1 <= int(x) <= len(contacts)]
        if not nums:
            return f"\u2753 \u05e9\u05dc\u05d7 \u05de\u05e1\u05e4\u05e8\u05d9\u05dd \u05d1\u05d9\u05df 1 \u05dc-{len(contacts)}, \u05d0\u05d5 0 \u05dc\u05d1\u05d9\u05d8\u05d5\u05dc"
        chosen = [contacts[i-1] for i in nums]
        sessions.pop(from_number, None)
        def _load_files_for_chosen(chosen=chosen, account=account, aname=aname):
            try:
                token2, domain2 = get_access_token()
                h2 = {"Authorization": f"Zoho-oauthtoken {token2}"}
                img_exts = ('.jpg','.jpeg','.png','.webp','.heic','.heif')
                contacts_with_files = []
                for c in chosen:
                    cid = c["id"]
                    r = requests.get(f"{domain2}/crm/v2/Contacts/{cid}/Attachments", headers=h2)
                    atts = r.json().get("data", []) if r.status_code == 200 else []
                    image_atts = [a for a in atts if a.get("File_Name","").lower().endswith(img_exts)]
                    contacts_with_files.append((c, image_atts))
                # שמור session לפני שליחת תמונות
                sessions[from_number] = {
                    "pending": "fix_profile_choose_file",
                    "account": account,
                    "contacts_with_files": contacts_with_files
                }
                # שלח תמונות לוואטסאפ לכל לקוח
                for c, image_atts in contacts_with_files:
                    cname = c.get("Full_Name", "")
                    cid = c["id"]
                    if not image_atts:
                        _send_reply(f"*{cname}*: \u05d0\u05d9\u05df \u05ea\u05de\u05d5\u05e0\u05d5\u05ea", from_number)
                        continue
                    # שלח כותרת שם לקוח
                    _send_reply(f"\U0001f464 *{cname}* ({len(image_atts)} \u05ea\u05deונות):", from_number)
                    for j, a in enumerate(image_atts, 1):
                        try:
                            r2 = requests.get(f"{domain2}/crm/v2/Contacts/{cid}/Attachments/{a['id']}", headers=h2, timeout=15)
                            if r2.status_code == 200:
                                img_url = _store_temp_image(r2.content, "image/jpeg", ttl=600)
                                _send_whatsapp_image(img_url, f"{j}. {a.get('File_Name','')}", from_number)
                                time.sleep(0.5)
                            else:
                                _send_reply(f"  {j}. {a.get('File_Name','')} (\u05dc\u05d0 \u05e0\u05d8ען)", from_number)
                        except Exception as img_e:
                            _send_reply(f"  {j}. {a.get('File_Name','')} (\u05e9\u05d2\u05d9\u05d0\u05d4: {str(img_e)[:30]})", from_number)
                        time.sleep(0.3)
                # שלח הוראות בסוף
                footer_lines = [
                    "\u05dc\u05ea\u05d9\u05e7\u05d5\u05df \u05d9\u05d7\u05d9\u05d3: [\u05e9\u05dd \u05dc\u05e7\u05d5\u05d7] [\u05de\u05e1\u05e4\u05e8 \u05e7\u05d5\u05d1\u05e5]",
                    "  \u05dc\u05d3\u05d5\u05d2\u05de\u05d0: \u05d9\u05d5\u05e1\u05d9 \u05db\u05d4\u05df 2",
                    "\u05dc\u05ea\u05d9\u05e7\u05d5\u05df \u05de\u05e8\u05d5\u05d1\u05d4 (\u05d0\u05d5\u05ea\u05d5 \u05e7\u05d5\u05d1\u05e5): [\u05de\u05e1\u05e4\u05e8\u05d9 \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea] \u05e7\u05d5\u05d1\u05e5 [\u05de\u05e1\u05e4\u05e8]",
                    "  \u05dc\u05d3\u05d5\u05d2\u05de\u05d0: 1,3,5 \u05e7\u05d5\u05d1\u05e5 2",
                    "0 = \u05d1\u05d9\u05d8\u05d5\u05dc"
                ]
                _send_reply("\n".join(footer_lines), from_number)
            except Exception as e:
                _send_reply(f"\u274c \u05e9\u05d2\u05d9\u05d0\u05d4: {e}", from_number)
        threading.Thread(target=_load_files_for_chosen, daemon=True).start()
        names = ", ".join(c.get("Full_Name","") for c in chosen)
        return f"\u23f3 \u05d8\u05d5\u05e2\u05df \u05e7\u05d1\u05e6\u05d9\u05dd \u05e2\u05d1\u05d5\u05e8: {names}..."

    # === תיקון פרופיל - בחירת קובץ (יחיד או מרובה) ===
    if pending == "fix_profile_choose_file":
        account = session.get("account", {})
        contacts_with_files = session.get("contacts_with_files", [])
        aname = account.get("Account_Name", "")
        choice = message.strip()
        if choice == "0":
            sessions.pop(from_number, None)
            return "\u274c \u05d1\u05d5\u05d8\u05dc"
        import re as _re_fp2
        # תיקון מרובה: "1,3,5 קובץ 2" או "הכל קובץ 2"
        multi_match = _re_fp2.match(r'^(\u05d4\u05db\u05dc|[\d,\s]+)\s+\u05e7\u05d5\u05d1\u05e5\s+(\d+)$', choice)
        if multi_match:
            sel_str = multi_match.group(1).strip()
            file_num = int(multi_match.group(2))
            if sel_str == "\u05d4\u05db\u05dc":
                sel_nums = list(range(1, len(contacts_with_files) + 1))
            else:
                sel_nums = [int(x) for x in _re_fp2.findall(r'\d+', sel_str) if 1 <= int(x) <= len(contacts_with_files)]
            if not sel_nums:
                return "\u2753 \u05de\u05e1\u05e4\u05e8\u05d9 \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea \u05dc\u05d0 \u05ea\u05e7\u05d9\u05e0\u05d9\u05dd"
            to_update = []
            for n in sel_nums:
                c, image_atts = contacts_with_files[n-1]
                if 1 <= file_num <= len(image_atts):
                    to_update.append((c, image_atts[file_num-1]))
                else:
                    to_update.append((c, None))
            sessions.pop(from_number, None)
            def _do_multi_fix(to_update=to_update, account=account, aname=aname, contacts_with_files=contacts_with_files, file_num=file_num):
                try:
                    token2, domain2 = get_access_token()
                    h2 = {"Authorization": f"Zoho-oauthtoken {token2}"}
                    for c, att in to_update:
                        cname = c.get("Full_Name", "")
                        cid = c["id"]
                        if att is None:
                            _send_reply(f"\u26a0\ufe0f {cname}: \u05e7\u05d5\u05d1\u05e5 {file_num} \u05dc\u05d0 \u05e7\u05d9\u05d9\u05dd", from_number)
                            continue
                        r2 = requests.get(f"{domain2}/crm/v2/Contacts/{cid}/Attachments/{att['id']}", headers=h2)
                        if r2.status_code != 200:
                            _send_reply(f"\u274c {cname}: \u05dc\u05d0 \u05d4\u05e6\u05dc\u05d7\u05ea\u05d9 \u05dc\u05d4\u05d5\u05e8\u05d9\u05d3 {att['File_Name']}", from_number)
                            continue
                        face_bytes, face_dbg = _crop_face_center(r2.content)
                        if not face_bytes:
                            _send_reply(f"\u26a0\ufe0f {cname}: \u05d7\u05d9\u05ea\u05d5\u05da \u05e4\u05e0\u05d9\u05dd \u05e0\u05db\u05e9\u05dc ({face_dbg[:50]})", from_number)
                            continue
                        photo_resp = requests.post(
                            f"{domain2}/crm/v2/Contacts/{cid}/photo",
                            headers=h2,
                            files={"file": ("profile.jpg", face_bytes, "image/jpeg")}
                        )
                        if photo_resp.status_code in [200, 201, 202]:
                            _send_reply(f"\u2705 {cname}: \u05e2\u05d5\u05d3\u05db\u05df \u05de-{att['File_Name']}", from_number)
                        else:
                            _send_reply(f"\u274c {cname}: \u05e9\u05d2\u05d9\u05d0\u05d4 Zoho ({photo_resp.status_code})", from_number)
                        import time as _t; _t.sleep(0.3)
                    # הצג שוב את הרשימה לתיקון נוסף
                    lines = [f"\U0001f4cb *\u05e7\u05d1\u05e6\u05d9 \u05ea\u05de\u05d5\u05e0\u05d4 - {aname}:*", ""]
                    for cc, image_atts2 in contacts_with_files:
                        ccname = cc.get("Full_Name", "")
                        if not image_atts2:
                            lines.append(f"*{ccname}*: \u05d0\u05d9\u05df \u05ea\u05de\u05d5\u05e0\u05d5\u05ea")
                        else:
                            lines.append(f"*{ccname}*:")
                            for j, a in enumerate(image_atts2, 1):
                                lines.append(f"  {j}. {a.get('File_Name','')}")
                    lines.append("")
                    lines.append("\u05dc\u05ea\u05d9\u05e7\u05d5\u05df \u05d9\u05d7\u05d9\u05d3: [\u05e9\u05dd \u05dc\u05e7\u05d5\u05d7] [\u05de\u05e1\u05e4\u05e8 \u05e7\u05d5\u05d1\u05e5]")
                    lines.append("\u05dc\u05ea\u05d9\u05e7\u05d5\u05df \u05de\u05e8\u05d5\u05d1\u05d4: [\u05de\u05e1\u05e4\u05e8\u05d9 \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea] \u05e7\u05d5\u05d1\u05e5 [\u05de\u05e1\u05e4\u05e8]")
                    lines.append("0 = \u05e1\u05d9\u05d5\u05dd")
                    sessions[from_number] = {
                        "pending": "fix_profile_choose_file",
                        "account": account,
                        "contacts_with_files": contacts_with_files
                    }
                    _send_reply("\n".join(lines), from_number)
                except Exception as e:
                    _send_reply(f"\u274c \u05e9\u05d2\u05d9\u05d0\u05d4: {e}", from_number)
            threading.Thread(target=_do_multi_fix, daemon=True).start()
            return f"\u23f3 \u05de\u05e2\u05d3\u05db\u05df {len(to_update)} \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea \u05e2\u05dd \u05e7\u05d5\u05d1\u05e5 {file_num}..."
        # תיקון יחיד: "יוסי כהן 2"
        single_match = _re_fp2.match(r'^(.+?)\s+(\d+)$', choice)
        if single_match:
            name_part = single_match.group(1).strip()
            file_num = int(single_match.group(2))
            matched = [(i, c, atts) for i, (c, atts) in enumerate(contacts_with_files)
                       if name_part.lower() in c.get("Full_Name","").lower()]
            if not matched:
                return f"\u2753 \u05dc\u05d0 \u05de\u05e6\u05d0\u05ea\u05d9 \u05dc\u05e7\u05d5\u05d7 \u05d1\u05e9\u05dd '{name_part}'"
            if len(matched) > 1:
                names = ", ".join(c.get("Full_Name","") for _, c, _ in matched)
                return f"\u2753 \u05e0\u05de\u05e6\u05d0\u05d5 \u05db\u05de\u05d4 \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea: {names}. \u05e9\u05dc\u05d7 \u05e9\u05dd \u05de\u05d3\u05d5\u05d9\u05e7 \u05d9\u05d5\u05ea\u05e8."
            _, c, image_atts = matched[0]
            cname = c.get("Full_Name", "")
            cid = c["id"]
            if not image_atts:
                return f"\u274c {cname}: \u05d0\u05d9\u05df \u05ea\u05de\u05d5\u05e0\u05d5\u05ea"
            if not (1 <= file_num <= len(image_atts)):
                return f"\u2753 {cname}: \u05e7\u05d5\u05d1\u05e5 {file_num} \u05dc\u05d0 \u05e7\u05d9\u05d9\u05dd (\u05d9\u05e9 {len(image_atts)} \u05e7\u05d1\u05e6\u05d9\u05dd)"
            att = image_atts[file_num - 1]
            sessions.pop(from_number, None)
            def _do_single_fix(c=c, att=att, cname=cname, cid=cid, account=account, aname=aname, contacts_with_files=contacts_with_files):
                try:
                    token2, domain2 = get_access_token()
                    h2 = {"Authorization": f"Zoho-oauthtoken {token2}"}
                    r2 = requests.get(f"{domain2}/crm/v2/Contacts/{cid}/Attachments/{att['id']}", headers=h2)
                    if r2.status_code != 200:
                        _send_reply(f"\u274c \u05dc\u05d0 \u05d4\u05e6\u05dc\u05d7\u05ea\u05d9 \u05dc\u05d4\u05d5\u05e8\u05d9\u05d3 {att['File_Name']}", from_number)
                        return
                    face_bytes, face_dbg = _crop_face_center(r2.content)
                    if not face_bytes:
                        _send_reply(f"\u26a0\ufe0f {cname}: \u05d7\u05d9\u05ea\u05d5\u05da \u05e4\u05e0\u05d9\u05dd \u05e0\u05db\u05e9\u05dc ({face_dbg[:50]})", from_number)
                    else:
                        photo_resp = requests.post(
                            f"{domain2}/crm/v2/Contacts/{cid}/photo",
                            headers=h2,
                            files={"file": ("profile.jpg", face_bytes, "image/jpeg")}
                        )
                        if photo_resp.status_code in [200, 201, 202]:
                            _send_reply(f"\u2705 {cname}: \u05e2\u05d5\u05d3\u05db\u05df \u05de-{att['File_Name']}", from_number)
                        else:
                            _send_reply(f"\u274c {cname}: \u05e9\u05d2\u05d9\u05d0\u05d4 Zoho ({photo_resp.status_code})", from_number)
                    # הצג שוב את הרשימה לתיקון נוסף
                    lines = [f"\U0001f4cb *\u05e7\u05d1\u05e6\u05d9 \u05ea\u05de\u05d5\u05e0\u05d4 - {aname}:*", ""]
                    for cc, image_atts2 in contacts_with_files:
                        ccname = cc.get("Full_Name", "")
                        if not image_atts2:
                            lines.append(f"*{ccname}*: \u05d0\u05d9\u05df \u05ea\u05de\u05d5\u05e0\u05d5\u05ea")
                        else:
                            lines.append(f"*{ccname}*:")
                            for j, a in enumerate(image_atts2, 1):
                                lines.append(f"  {j}. {a.get('File_Name','')}")
                    lines.append("")
                    lines.append("\u05dc\u05ea\u05d9\u05e7\u05d5\u05df \u05d9\u05d7\u05d9\u05d3: [\u05e9\u05dd \u05dc\u05e7\u05d5\u05d7] [\u05de\u05e1\u05e4\u05e8 \u05e7\u05d5\u05d1\u05e5]")
                    lines.append("\u05dc\u05ea\u05d9\u05e7\u05d5\u05df \u05de\u05e8\u05d5\u05d1\u05d4: [\u05de\u05e1\u05e4\u05e8\u05d9 \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea] \u05e7\u05d5\u05d1\u05e5 [\u05de\u05e1\u05e4\u05e8]")
                    lines.append("0 = \u05e1\u05d9\u05d5\u05dd")
                    sessions[from_number] = {
                        "pending": "fix_profile_choose_file",
                        "account": account,
                        "contacts_with_files": contacts_with_files
                    }
                    _send_reply("\n".join(lines), from_number)
                except Exception as e:
                    _send_reply(f"\u274c \u05e9\u05d2\u05d9\u05d0\u05d4: {e}", from_number)
            threading.Thread(target=_do_single_fix, daemon=True).start()
            return f"\u23f3 \u05de\u05e2\u05d3\u05db\u05df \u05e4\u05e8\u05d5\u05e4\u05d9\u05dc \u05e9\u05dc {cname}..."
        return "\u2753 \u05e4\u05d5\u05e8\u05de\u05d8 \u05dc\u05d0 \u05de\u05d5\u05db\u05e8.\n\u05dc\u05ea\u05d9\u05e7\u05d5\u05df \u05d9\u05d7\u05d9\u05d3: [\u05e9\u05dd \u05dc\u05e7\u05d5\u05d7] [\u05de\u05e1\u05e4\u05e8 \u05e7\u05d5\u05d1\u05e5]\n\u05dc\u05ea\u05d9\u05e7\u05d5\u05df \u05de\u05e8\u05d5\u05d1\u05d4: [\u05de\u05e1\u05e4\u05e8\u05d9 \u05dc\u05e7\u05d5\u05d7\u05d5\u05ea] \u05e7\u05d5\u05d1\u05e5 [\u05de\u05e1\u05e4\u05e8]\n0 = \u05d1\u05d9\u05d8\u05d5\u05dc"

    # === תיקון פרופיל [שם לקוח] ===
    if msg_s.startswith("תיקון פרופיל "):
        name_q = msg_s[len("תיקון פרופיל "):].strip()
        if name_q:
            contacts = _word_search_contacts(name_q)
            if not contacts:
                for word in name_q.split():
                    if len(word) >= 2:
                        contacts = _word_search_contacts(word)
                        if contacts: break
            if not contacts:
                return f"❓ לא מצאתי לקוח בשם *{name_q}*"
            if len(contacts) == 1:
                contact = contacts[0]
                cid = contact["id"]
                cname = contact.get("Full_Name", "")
                sessions.pop(from_number, None)
                def _load_atts_fix_direct():
                    try:
                        token2, domain2 = get_access_token()
                        h2 = {"Authorization": f"Zoho-oauthtoken {token2}"}
                        # טען קבצים של הלקוח
                        r = requests.get(f"{domain2}/crm/v2/Contacts/{cid}/Attachments", headers=h2)
                        atts = r.json().get("data", []) if r.status_code == 200 else []
                        img_exts = ('.jpg','.jpeg','.png','.webp','.heic','.heif')
                        image_atts = [a for a in atts if a.get("File_Name","").lower().endswith(img_exts)]
                        if not image_atts:
                            _send_reply(f"❌ אין תמונות בקבצים של {cname}", from_number)
                            return
                        # טען שאר לקוחות מאותו בית
                        acc_info = contact.get("Account_Name", {})
                        acc_id = acc_info.get("id", "") if isinstance(acc_info, dict) else ""
                        acc_name = acc_info.get("name", "") if isinstance(acc_info, dict) else ""
                        account_contacts = []
                        if acc_id:
                            rc = requests.get(f"{domain2}/crm/v2/Contacts/search",
                                headers=h2,
                                params={"criteria": f"(Account_Name:equals:{acc_id})", "fields": "Full_Name,id", "per_page": 200})
                            if rc.status_code == 200:
                                account_contacts = rc.json().get("data", [])
                        lines = [f"📋 *תמונות של {cname}:*"]
                        for j, a in enumerate(image_atts, 1):
                            lines.append(f"{j}. {a.get('File_Name','')}")
                        lines.append("\nשלח מספר לבחירה (0 = ביטול)")
                        sessions[from_number] = {
                            "pending": "pick_attachment_fix_profile",
                            "contact": contact,
                            "image_atts": image_atts,
                            "account_contacts": account_contacts,
                            "account_name": acc_name
                        }
                        _send_reply("\n".join(lines), from_number)
                    except Exception as e:
                        _send_reply(f"❌ שגיאה: {e}", from_number)
                threading.Thread(target=_load_atts_fix_direct, daemon=True).start()
                return f"⏳ טוען קבצים של {cname}..."
            # כמה לקוחות - הצג תפריט בחירה
            sessions[from_number] = {"pending": "choose_contact_fix_profile", "contacts": contacts}
            return _format_contact_choice_menu(contacts, "תיקון פרופיל")

    # === פרופיל בית - כל לקוחות בבית ===
    if msg_s.startswith("פרופיל בית "):
        name_q = msg_s[len("פרופיל בית "):].strip()
        if name_q:
            accounts = _word_search_accounts(name_q)
            if not accounts:
                # נסה כל מילה בנפרד
                for word in name_q.split():
                    if len(word) >= 2:
                        accounts = _word_search_accounts(word)
                        if accounts: break
            if not accounts:
                return f"❓ לא מצאתי בעל בית בשם *{name_q}*"
            if len(accounts) == 1:
                aname = accounts[0].get("Account_Name", "")
                sessions[from_number] = {"pending": "confirm_bulk_profile", "account": accounts[0]}
                return (f"🔍 תעדכן פרופילים לכל לקוחות בית *{aname}*\n"
                        f"התהליך מחפש קובץ 'פרופיל' בקבצים ומעדכן תמונת פרופיל.\n"
                        f"האם להתחיל?\n1. כן\n2. לא")
            sessions[from_number] = {"pending": "choose_account_bulk_profile", "accounts": accounts, "name_q": name_q}
            return _format_account_choice_menu(accounts, "עדכון פרופילים")

    # === בחירת בית לבדיקת פרופילים ===
    if pending == "choose_account_check_profile":
        accounts = session.get("accounts", [])
        choice = message.strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(accounts):
                sessions.pop(from_number, None)
                aname = accounts[idx].get("Account_Name", "")
                sessions[from_number] = {"pending": "confirm_check_profile_beit", "account": accounts[idx]}
                return (f"🔍 בדיקת פרופילים לכל לקוחות בית *{aname}*\n"
                        f"האם להתחיל?\n1. כן\n2. לא")
        return f"❓ כתוב מספר בין 1 ל-{len(accounts)}"

    # === אישור בדיקת פרופילים ===
    if pending == "confirm_check_profile_beit":
        account = session.get("account", {})
        choice = message.strip()
        if choice in ["1", "כן"]:
            sessions.pop(from_number, None)
            def _run_check_profiles():
                result_msg, contacts_data = _scan_profiles_for_account(account)
                if contacts_data:
                    sessions[from_number] = {
                        "pending": "review_profile_beit",
                        "account": account,
                        "contacts_data": contacts_data
                    }
                _send_reply(result_msg, from_number)
            threading.Thread(target=_run_check_profiles, daemon=True).start()
            aname = account.get("Account_Name", "")
            return f"⏳ סורק פרופילים - *{aname}*... תקבל עדכון בסיום."
        elif choice in ["2", "לא"]:
            sessions.pop(from_number, None)
            return "❌ בוטל"
        return "❓ כתוב 1 (כן) או 2 (לא)"



    # === בדוק פרופיל בית - סקירה ובחירת מי לתקן ===
    if msg_s.startswith("בדוק פרופיל בית "):
        name_q = msg_s[len("בדוק פרופיל בית "):].strip()
        if name_q:
            accounts = _word_search_accounts(name_q)
            if not accounts:
                for word in name_q.split():
                    if len(word) >= 2:
                        accounts = _word_search_accounts(word)
                        if accounts: break
            if not accounts:
                return f"❓ לא מצאתי בעל בית בשם *{name_q}*"
            if len(accounts) == 1:
                aname = accounts[0].get("Account_Name", "")
                sessions[from_number] = {"pending": "confirm_check_profile_beit", "account": accounts[0]}
                return (f"🔍 בדיקת פרופילים לכל לקוחות בית *{aname}*\n"
                        f"הבוט יסרוק את כל הלקוחות ויראה מי יש לו תמונה ומי לא.\n"
                        f"האם להתחיל?\n1. כן\n2. לא")
            sessions[from_number] = {"pending": "choose_account_check_profile", "accounts": accounts, "name_q": name_q}
            return _format_account_choice_menu(accounts, "בדיקת פרופילים")

    # === פרופיל כללי - בחירת סוג סינון ===
    if msg_s == "פרופיל כללי":
        sessions.pop(from_number, None)
        sessions[from_number] = {"pending": "profile_filter_type"}
        return ("\U0001f50d *\u05e4\u05e8\u05d5\u05e4\u05d9\u05dc \u05db\u05dc\u05dc\u05d9 - \u05e1\u05d9\u05e0\u05d5\u05df \u05d1\u05e2\u05dc\u05d9 \u05d1\u05ea\u05d9\u05dd:*\n\n"
                "1\ufe0f\u20e3 \u05e4\u05e2\u05d9\u05dc\u05d9\u05dd (\u05d7\u05e9\u05d1\u05d5\u05e0\u05d9\u05ea \u05d1\u05e9\u05e0\u05d4 \u05d4\u05d0\u05d7\u05e8\u05d5\u05e0\u05d4)\n"
                "2\ufe0f\u20e3 \u05dc\u05d0 \u05e4\u05e2\u05d9\u05dc\u05d9\u05dd (\u05dc\u05dc\u05d0 \u05d7\u05e9\u05d1\u05d5\u05e0\u05d9\u05ea \u05d1\u05e9\u05e0\u05d4 \u05d4\u05d0\u05d7\u05e8\u05d5\u05e0\u05d4)\n"
                "3\ufe0f\u20e3 \u05d4\u05db\u05dc\n\n"
                "\u05e9\u05dc\u05d7 1, 2 \u05d0\u05d5 3:")

    # === פרופיל כללי - קבלת סינון וטעינת בעלי בתים ===
    if pending == "profile_filter_type":
        choice_f = message.strip()
        if choice_f == "0":
            sessions.pop(from_number, None)
            return "❌ בוטל"
        if choice_f not in ("1", "2", "3"):
            return "❓ שלח 1 (פעילים), 2 (לא פעילים) או 3 (הכל)"
        filter_mode = {"1": "active", "2": "inactive", "3": "all"}[choice_f]
        sessions.pop(from_number, None)
        def _load_all_accounts_for_profile(filter_mode=filter_mode):
            try:
                token2, domain2 = get_access_token()
                h2 = {"Authorization": f"Zoho-oauthtoken {token2}"}
                all_accs = []
                page = 1
                while True:
                    r = requests.get(f"{domain2}/crm/v5/Accounts",
                        headers=h2,
                        params={"fields": "Account_Name", "per_page": 200, "page": page})
                    if r.status_code != 200: break
                    batch = r.json().get("data", [])
                    if not batch: break
                    all_accs.extend(batch)
                    info = r.json().get("info", {})
                    if not info.get("more_records", False): break
                    page += 1
                if not all_accs:
                    _send_reply("❌ לא נמצאו בעלי בתים", from_number)
                    return

                # סינון לפי פעילות חשבוניות
                if filter_mode in ("active", "inactive"):
                    import datetime as _dt
                    one_year_ago = (_dt.datetime.utcnow() - _dt.timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
                    _send_reply(f"🔍 בודק פעילות חשבוניות עבור {len(all_accs)} בעלי בתים...", from_number)
                    filtered = []
                    for acc in all_accs:
                        acc_id = acc.get("id")
                        acc_name = acc.get("Account_Name", "")
                        try:
                            inv_r = requests.get(
                                f"{domain2}/crm/v5/Invoices/search",
                                headers=h2,
                                params={
                                    "criteria": f"(Account_Name.id:equals:{acc_id})AND(Created_Time:greater_equal:{one_year_ago})",
                                    "fields": "id",
                                    "per_page": 1
                                }
                            )
                            has_recent = inv_r.status_code == 200 and bool(inv_r.json().get("data"))
                        except Exception:
                            has_recent = False
                        if filter_mode == "active" and has_recent:
                            filtered.append(acc)
                        elif filter_mode == "inactive" and not has_recent:
                            filtered.append(acc)
                    label = "פעילים" if filter_mode == "active" else "לא פעילים"
                    _send_reply(f"✅ נמצאו {len(filtered)} בעלי בתים {label}", from_number)
                    all_accs = filtered

                if not all_accs:
                    _send_reply("❌ לא נמצאו בעלי בתים מתאימים", from_number)
                    return
                # מיין לפי שם
                all_accs.sort(key=lambda a: a.get("Account_Name", ""))
                sessions[from_number] = {
                    "pending": "pick_accounts_general_profile",
                    "accounts": all_accs
                }
                # בנה שורות ופצל להודעות של עד 1500 תווים
                all_lines = [f"{j}. {a.get('Account_Name','')}" for j, a in enumerate(all_accs, 1)]
                footer = "\nשלח מספרים מופרדים בפסיקות (1,3,5) או 'הכל' לבחירת הכל, או 0 לביטול"
                MAX_CHARS = 1400
                chunks = []
                current_chunk = ["🏠 *בחר בעלי בתים לעדכון פרופיל:*"]
                current_len = len(current_chunk[0])
                for line in all_lines:
                    if current_len + len(line) + 1 > MAX_CHARS:
                        chunks.append("\n".join(current_chunk))
                        current_chunk = []
                        current_len = 0
                    current_chunk.append(line)
                    current_len += len(line) + 1
                if current_chunk:
                    chunks.append("\n".join(current_chunk))
                # שלח כל חלק בנפרד
                for i, chunk in enumerate(chunks):
                    if i == len(chunks) - 1:
                        _send_reply(chunk + footer, from_number)
                    else:
                        _send_reply(chunk, from_number)
            except Exception as e:
                _send_reply(f"❌ שגיאה: {e}", from_number)
        threading.Thread(target=_load_all_accounts_for_profile, daemon=True).start()
        return "⏳ טוען רשימת בעלי בתים..."

    # === פרופיל כללי - בחירת בעלי בתים ===
    if pending == "pick_accounts_general_profile":
        accounts_list = session.get("accounts", [])
        choice = message.strip()
        if choice == "0":
            sessions.pop(from_number, None)
            return "❌ בוטל"
        # פרס מספרים
        import re as _re
        if choice.strip() == "הכל":
            nums = list(range(1, len(accounts_list) + 1))
        else:
            nums = [int(x) for x in _re.findall(r'\d+', choice) if 1 <= int(x) <= len(accounts_list)]
        if not nums:
            return f"❓ שלח מספרים בין 1 ל-{len(accounts_list)}, או 0 לביטול"
        chosen = [accounts_list[i-1] for i in nums]
        sessions.pop(from_number, None)
        names = ", ".join(a.get("Account_Name","") for a in chosen)
        def _run_general_profile():
            try:
                total_updated = 0
                total_skipped = 0
                for acc in chosen:
                    aname = acc.get("Account_Name", "")
                    _send_reply(f"⏳ מעדכן פרופילים - *{aname}*...", from_number)
                    ret = bulk_profile_update_for_account(acc, from_number)
                    # הפונקציה מחזירה tuple (result, used_att_ids) או string
                    if isinstance(ret, tuple):
                        result = ret[0]
                    else:
                        result = ret
                    if result:
                        _send_reply(str(result), from_number)
                _send_reply(f"✅ *סיום פרופיל כללי*\nעודכנו {len(chosen)} בעלי בתים: {names}", from_number)
            except Exception as e:
                _send_reply(f"❌ שגיאה: {e}", from_number)
        threading.Thread(target=_run_general_profile, daemon=True).start()
        return f"⏳ מתחיל עדכון פרופילים ל-{len(chosen)} בעלי בתים..."

    # === פספורט כללי - בחירת בעלי בתים לעדכון ===
    if msg_s == "פספורט כללי":
        sessions[from_number] = {"pending": "passport_filter_type"}
        return ("U0001f4ce *פספורט כללי* - בחר סוג בעלי בתים:\n"
                "1. פעילים (יש חשבונית בשנה אחרונה)\n"
                "2. לא פעילים\n"
                "3. הכל\n"
                "0. ביטול")
    # === פספורט כללי - קבלת סינון וטעינת בעלי בתים ===
    if pending == "passport_filter_type":
        choice_f = message.strip()
        if choice_f == "0":
            sessions.pop(from_number, None)
            return "❌ בוטל"
        if choice_f not in ("1", "2", "3"):
            return "❓ שלח 1 (פעילים), 2 (לא פעילים) או 3 (הכל)"
        filter_mode = {"1": "active", "2": "inactive", "3": "all"}[choice_f]
        sessions.pop(from_number, None)
        def _load_all_accounts_for_passport(filter_mode=filter_mode):
            try:
                token2, domain2 = get_access_token()
                h2 = {"Authorization": f"Zoho-oauthtoken {token2}"}
                all_accs = []
                page = 1
                while True:
                    r = requests.get(f"{domain2}/crm/v5/Accounts",
                        headers=h2,
                        params={"fields": "Account_Name", "per_page": 200, "page": page})
                    if r.status_code != 200: break
                    batch = r.json().get("data", [])
                    if not batch: break
                    all_accs.extend(batch)
                    info = r.json().get("info", {})
                    if not info.get("more_records", False): break
                    page += 1
                if not all_accs:
                    _send_reply("❌ לא נמצאו בעלי בתים", from_number)
                    return
                # סינון לפי פעילות חשבוניות
                if filter_mode in ("active", "inactive"):
                    import datetime as _dt
                    one_year_ago = (_dt.datetime.utcnow() - _dt.timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
                    _send_reply(f"\U0001f50d בודק פעילות חשבוניות עבור {len(all_accs)} בעלי בתים...", from_number)
                    filtered = []
                    for acc in all_accs:
                        acc_id = acc.get("id")
                        try:
                            inv_r = requests.get(
                                f"{domain2}/crm/v5/Invoices/search",
                                headers=h2,
                                params={
                                    "criteria": f"(Account_Name.id:equals:{acc_id})AND(Created_Time:greater_equal:{one_year_ago})",
                                    "fields": "id",
                                    "per_page": 1
                                }
                            )
                            has_recent = inv_r.status_code == 200 and bool(inv_r.json().get("data"))
                        except Exception:
                            has_recent = False
                        if filter_mode == "active" and has_recent:
                            filtered.append(acc)
                        elif filter_mode == "inactive" and not has_recent:
                            filtered.append(acc)
                    label = "פעילים" if filter_mode == "active" else "לא פעילים"
                    _send_reply(f"\u2705 נמצאו {len(filtered)} בעלי בתים {label}", from_number)
                    all_accs = filtered
                if not all_accs:
                    _send_reply("❌ לא נמצאו בעלי בתים מתאימים", from_number)
                    return
                all_accs.sort(key=lambda a: a.get("Account_Name", ""))
                sessions[from_number] = {
                    "pending": "pick_accounts_general_passport",
                    "accounts": all_accs
                }
                all_lines = [f"{j}. {a.get('Account_Name','')}" for j, a in enumerate(all_accs, 1)]
                footer = "\nשלח מספרים מופרדים בפסיקות (1,3,5) או 'הכל' לבחירת הכל, או 0 לביטול"
                MAX_CHARS = 1400
                chunks = []
                current_chunk = ["\U0001f4ce *בחר בעלי בתים לעדכון פספורט:*"]
                current_len = len(current_chunk[0])
                for line in all_lines:
                    if current_len + len(line) + 1 > MAX_CHARS:
                        chunks.append("\n".join(current_chunk))
                        current_chunk = []
                        current_len = 0
                    current_chunk.append(line)
                    current_len += len(line) + 1
                if current_chunk:
                    chunks.append("\n".join(current_chunk))
                for i, chunk in enumerate(chunks):
                    if i == len(chunks) - 1:
                        _send_reply(chunk + footer, from_number)
                    else:
                        _send_reply(chunk, from_number)
            except Exception as e:
                _send_reply(f"❌ שגיאה: {e}", from_number)
        threading.Thread(target=_load_all_accounts_for_passport, daemon=True).start()
        return "⏳ טוען רשימת בעלי בתים..."

    # === פספורט כללי - בחירת בעלי בתים ===
    if pending == "pick_accounts_general_passport":
        accounts_list = session.get("accounts", [])
        choice = message.strip()
        if choice == "0":
            sessions.pop(from_number, None)
            return "❌ בוטל"
        import re as _re2
        if choice.strip() == "הכל":
            nums = list(range(1, len(accounts_list) + 1))
        else:
            nums = [int(x) for x in _re2.findall(r'\d+', choice) if 1 <= int(x) <= len(accounts_list)]
        if not nums:
            return f"❓ שלח מספרים בין 1 ל-{len(accounts_list)}, או 0 לביטול"
        chosen = [accounts_list[i-1] for i in nums]
        sessions.pop(from_number, None)
        names = ", ".join(a.get("Account_Name","") for a in chosen)
        def _run_general_passport():
            try:
                for acc in chosen:
                    aname = acc.get("Account_Name", "")
                    _send_reply(f"⏳ מעדכן פספורטים - *{aname}*...", from_number)
                    result = bulk_passport_update_for_account(acc, from_number)
                    _send_reply(result, from_number)
                _send_reply(f"✅ *סיום פספורט כללי*\nעודכנו {len(chosen)} בעלי בתים: {names}", from_number)
            except Exception as e:
                _send_reply(f"❌ שגיאה: {e}", from_number)
        threading.Thread(target=_run_general_passport, daemon=True).start()
        return f"⏳ מתחיל עדכון פספורטים ל-{len(chosen)} בעלי בתים..."

    # === פספורט בית - בחירת כמה בעלי בתים ===
    if pending == "pick_multi_accounts_passport":
        accounts_list = session.get("accounts", [])
        choice = message.strip()
        if choice == "0":
            sessions.pop(from_number, None)
            return "❌ בוטל"
        import re as _re3
        if choice.strip() == "הכל":
            nums = list(range(1, len(accounts_list) + 1))
        else:
            nums = [int(x) for x in _re3.findall(r'\d+', choice) if 1 <= int(x) <= len(accounts_list)]
        if not nums:
            return f"❓ שלח מספרים בין 1 ל-{len(accounts_list)}, או 0 לביטול"
        chosen = [accounts_list[i-1] for i in nums]
        sessions.pop(from_number, None)
        names = ", ".join(a.get("Account_Name","") for a in chosen)
        def _run_multi_passport():
            try:
                for acc in chosen:
                    aname = acc.get("Account_Name", "")
                    _send_reply(f"⏳ מעדכן פספורטים - *{aname}*...", from_number)
                    result = bulk_passport_update_for_account(acc, from_number)
                    _send_reply(result, from_number)
                _send_reply(f"✅ *סיום עדכון פספורטים*\nעודכנו {len(chosen)} בעלי בתים: {names}", from_number)
            except Exception as e:
                _send_reply(f"❌ שגיאה: {e}", from_number)
        threading.Thread(target=_run_multi_passport, daemon=True).start()
        return f"⏳ מתחיל עדכון פספורטים ל-{len(chosen)} בעלי בתים..."

    # === עדכון פספורט בית - כל לקוחות בבית ===
    if msg_s.startswith("פספורט בית ") or msg_s.startswith("עדכון פספורט בית "):
        prefix = "פספורט בית " if msg_s.startswith("פספורט בית ") else "עדכון פספורט בית "
        name_q = msg_s[len(prefix):].strip()
        if name_q:
            accounts = _word_search_accounts(name_q)
            if not accounts:
                return f"❓ לא מצאתי בעל בית בשם *{name_q}*"
            if len(accounts) == 1:
                # בית אחד - התחל ישירות
                aname = accounts[0].get("Account_Name", "")
                def _run_single_passport(acc=accounts[0]):
                    result = bulk_passport_update_for_account(acc, from_number)
                    _send_reply(result, from_number)
                threading.Thread(target=_run_single_passport, daemon=True).start()
                return f"⏳ מתחיל עדכון פספורטים - *{aname}*..."
            # כמה בתוצאות - אפשר בחירת כמה
            lines = [f"U0001f3e0 בחר בעלי בתים לעדכון פספורט:"]
            for j, a in enumerate(accounts, 1):
                lines.append(f"{j}. {a.get('Account_Name','')}")
            lines.append("\nשלח מספרים מופרדים בפסיקות (1,3,5) או 'הכל' או 0 לביטול")
            sessions[from_number] = {"pending": "pick_multi_accounts_passport", "accounts": accounts}
            return "\n".join(lines)

    # === סטטוס לקוח ===
    if msg_s.startswith("סטטוס "):
        name_q = msg_s[len("סטטוס "):].strip()
        if name_q:
            contacts = _word_search_contacts(name_q)
            if not contacts:
                return f"❓ לא מצאתי לקוח בשם *{name_q}*"
            if len(contacts) == 1:
                sessions.pop(from_number, None)
                return build_customer_status(name_q, contact=contacts[0])
            sessions[from_number] = {"pending": "choose_contact_status", "contacts": contacts, "name_q": name_q}
            return _format_contact_choice_menu(contacts, "סטטוס")

    # === דוח בית ===
    if msg_s.startswith("סטטוס בית ") or msg_s.startswith("דוח בית "):
        name_q = msg_s.split(" ", 2)[2].strip() if len(msg_s.split(" ", 2)) > 2 else ""
        if name_q:
            accounts = _word_search_accounts(name_q)
            if not accounts:
                return f"❓ לא מצאתי בעל בית בשם *{name_q}*"
            if len(accounts) == 1:
                sessions.pop(from_number, None)
                report, ordered_contacts = build_landlord_report(name_q, account=accounts[0])
                if ordered_contacts:
                    sessions[from_number] = {"pending": "choose_landlord_contact", "contacts": ordered_contacts}
                parts = split_message(report)
                if len(parts) == 1: return parts[0]
                def _send_lr_rest():
                    time.sleep(0.8)
                    for p in parts[1:]:
                        try: twilio_client.messages.create(from_=TWILIO_WHATSAPP_FROM, to=f"whatsapp:{from_number.replace('whatsapp:', '')}", body=p); time.sleep(0.5)
                        except: pass
                threading.Thread(target=_send_lr_rest, daemon=True).start()
                return parts[0]
            sessions[from_number] = {"pending": "choose_account_report", "accounts": accounts, "name_q": name_q}
            return _format_account_choice_menu(accounts, "דוח")

    # === מחק חשבונית - אישור מחיקה ===
    if pending == "confirm_delete_invoice":
        invoices_to_delete = session.get("invoices_to_delete", [])
        if message.strip() in ["כן", "yes", "1"]:
            sessions.pop(from_number, None)
            deleted = []
            for inv in invoices_to_delete:
                delete_invoice_with_payment(inv["id"])
                subject = inv.get("Subject", inv.get("id", ""))
                log_action("מחיקה", f"נמחקה חשבונית: {subject}")
                deleted.append(subject)
            return f"✅ {len(deleted)} חשבונית/ות נמחקו בהצלחה!\n" + "\n".join([f"📄 {s}" for s in deleted])

        elif message.strip() in ["לא", "no", "2"]:
            sessions.pop(from_number, None)
            return "❌ המחיקה בוטלה"
        else:
            return "❓ כתוב *כן* למחיקה או *לא* לביטול"

    # === זיהוי פקודה חדשה כשיש session פתוח ===
    if pending and _looks_like_new_command(message):
        # שמור את הפקודה החדשה ושאל אם לעבור
        sessions[from_number] = {"pending": "confirm_switch", "new_message": message, "old_session": session}
        return "🔄 יש לך פעולה קודמת שלא הסתיימה.\nלעבור לפקודה החדשה?\n\n1. כן, עבור לפקודה החדשה\n2. לא, חזור לפעולה הקודמת"

    if pending == "confirm_switch":
        msg = message.strip()
        if msg in ["1", "כן", "yes"]:
            # עבור לפקודה החדשה
            new_message = session.get("new_message", "")
            sessions.pop(from_number, None)
            return handle_command(new_message, from_number)
        elif msg in ["2", "לא", "no"]:
            # חזור לפעולה הקודמת
            old_session = session.get("old_session", {})
            sessions[from_number] = old_session
            pending_type = old_session.get("pending", "")
            return f"👌 חוזר לפעולה הקודמת. אנא ענה על השאלה:"
        else:
            return "כתוב 1 (כן) או 2 (לא)"

    if pending == "product_choice":
        options = session["options"]
        context = session["context"]
        chosen = None
        msg_lower = message.strip().lower()
        # בחירה לפי מספר
        if msg_lower.isdigit():
            idx = int(msg_lower) - 1
            if 0 <= idx < len(options):
                chosen = options[idx]
        # בחירה לפי שם
        if not chosen:
            for opt in options:
                if msg_lower in opt.get("Product_Name", "").lower():
                    chosen = opt
                    break
        if not chosen:
            lines = [f"{i+1}. {p.get('Product_Name', '')} - ₪{p.get('Unit_Price', 0)}" for i, p in enumerate(options)]
            return f"לא הצלחתי לזהות. בחר מספר:\n" + "\n".join(lines)
        sessions.pop(from_number, None)
        product = chosen
        contact_name = context["contact_name"]
        account_name = context["account_name"]
        custom_price = context.get("custom_price", 0)
        quantity = context.get("quantity", 1)
        final_price = custom_price if custom_price and custom_price > 0 else product.get("Unit_Price", 0)
        contacts, accounts = find_contact_by_name_and_account(contact_name, account_name)
        if not contacts:
            return f"❌ לא מצאתי לקוח '{contact_name}' אצל '{account_name}'"
        if len(contacts) > 1:
            sessions[from_number] = {"pending": "contact_choice", "options": contacts, "context": {"product": product, "custom_price": custom_price, "quantity": quantity}}
            names = "\n".join([f"{i+1}. {c['Full_Name']}" for i, c in enumerate(contacts)])
            return f"מצאתי כמה לקוחות:\n{names}\n\nכתוב חלק מהשם או מספר לבחירה:"
        contact = contacts[0]
        acc_id = contact.get("Account_Name", {}).get("id") if isinstance(contact.get("Account_Name"), dict) else None
        if not acc_id and accounts:
            acc_id = accounts[0]["id"]
        inv_id = create_invoice(contact["id"], acc_id, product["id"], final_price, contact["Full_Name"], quantity)
        return build_invoice_confirmation(contact, product, final_price, quantity) if inv_id else "❌ שגיאה ביצירת החשבונית"

    if pending == "contact_choice":
        options = session["options"]
        context = session["context"]
        product = context["product"]
        custom_price = context.get("custom_price", 0)
        quantity = context.get("quantity", 1)
        final_price = custom_price if custom_price and custom_price > 0 else product.get("Unit_Price", 0)
        chosen = pick_best_match(options, message)
        if not chosen:
            names = "\n".join([f"{i+1}. {c['Full_Name']}" for i, c in enumerate(options)])
            return f"לא הצלחתי לזהות. בחר מספר:\n{names}"
        sessions.pop(from_number, None)
        acc_id = chosen.get("Account_Name", {}).get("id") if isinstance(chosen.get("Account_Name"), dict) else None
        inv_id = create_invoice(chosen["id"], acc_id, product["id"], final_price, chosen["Full_Name"], quantity)
        return build_invoice_confirmation(chosen, product, final_price, quantity) if inv_id else "❌ שגיאה ביצירת החשבונית"

    if pending == "account_choice":
        options = session["options"]
        context = session["context"]
        chosen = None
        msg_lower = message.strip().lower()
        # בחירה לפי מספר
        if msg_lower.isdigit():
            idx = int(msg_lower) - 1
            if 0 <= idx < len(options):
                chosen = options[idx]
        # בחירה לפי שם
        if not chosen:
            for opt in options:
                if msg_lower in opt.get("Account_Name", "").lower():
                    chosen = opt
                    break
        if not chosen:
            lines = [f"{i+1}. {a.get('Account_Name', '')}" for i, a in enumerate(options)]
            return f"לא הצלחתי לזהות. בחר מספר:\n" + "\n".join(lines)
        sessions.pop(from_number, None)
        # חזור לפעולה המקורית עם ה-account שנבחר
        original_action = context.get("original_action")
        if original_action == "active_lines":
            acc_id = chosen["id"]
            acc_display = chosen.get("Account_Name", "")
            total_lines, active_contacts = get_active_lines_for_account(acc_id, acc_display)
            if total_lines == 0:
                return f"📊 {acc_display}\n🔌 0 קווים פעילים"
            details = "\n".join([f"  • {c['name']} ({c['lines']})" for c in active_contacts[:20]])
            extra = f"\n  ... ועוד {len(active_contacts) - 20}" if len(active_contacts) > 20 else ""
            return (f"📊 {acc_display}\n"
                    f"🔌 {total_lines} קווים פעילים\n"
                    f"👥 {len(active_contacts)} לקוחות:\n{details}{extra}")
        elif original_action == "active_lines_invoice":
            contact_name = context.get("contact_name", "")
            acc_id = chosen["id"]
            acc_display = chosen.get("Account_Name", "")
            total_lines, active_contacts = get_active_lines_for_account(acc_id, acc_display)
            if total_lines == 0:
                return f"❌ אין קווים פעילים ל-{acc_display}"
            products = find_product("כרטיס 050 מקומי קו פעיל")
            if not products:
                return f"❌ לא מצאתי מוצר 'כרטיס 050 מקומי- קו פעיל'"
            product = products[0]
            contacts, _ = find_contact_by_name_and_account(contact_name, chosen.get("Account_Name", ""))
            if not contacts:
                return f"❌ לא מצאתי לקוח '{contact_name}' אצל '{acc_display}'"
            if len(contacts) > 1:
                sessions[from_number] = {
                    "pending": "contact_choice",
                    "options": contacts,
                    "context": {"product": product, "custom_price": 0, "quantity": total_lines}
                }
                names = "\n".join([f"{i+1}. {c['Full_Name']}" for i, c in enumerate(contacts)])
                return f"🔌 {total_lines} קווים פעילים ל-{acc_display}\n\nמצאתי כמה לקוחות:\n{names}\n\nכתוב חלק מהשם או מספר לבחירה:"
            contact = contacts[0]
            c_acc_id = contact.get("Account_Name", {}).get("id") if isinstance(contact.get("Account_Name"), dict) else acc_id
            final_price = product.get("Unit_Price", 0)
            inv_id = create_invoice(contact["id"], c_acc_id, product["id"], final_price, contact["Full_Name"], total_lines)
            if inv_id:
                acc_name = contact.get("Account_Name", {}).get("name", acc_display) if isinstance(contact.get("Account_Name"), dict) else acc_display
                return (f"✅ חשבונית קווים פעילים נוצרה!\n"
                        f"👤 {contact['Full_Name']}\n"
                        f"🏠 {acc_name}\n"
                        f"📦 {product.get('Product_Name')} x{total_lines}\n"
                        f"🔌 {total_lines} קווים פעילים\n"
                        f"💰 ₪{final_price} ליחידה | סה\"כ ₪{final_price * total_lines}")
            return "❌ שגיאה ביצירת החשבונית"
        elif original_action == "create_contact":
            contact_name = context.get("contact_name", "")
            acc_id = chosen["id"]
            acc_display = chosen.get("Account_Name", "")
            existing = zoho_get("Contacts/search", {"word": contact_name})
            if existing:
                for c in existing:
                    c_acc = c.get("Account_Name", {})
                    c_acc_id = c_acc.get("id") if isinstance(c_acc, dict) else None
                    if c_acc_id == acc_id:
                        return f"⚠️ לקוח '{c['Full_Name']}' כבר קיים אצל '{acc_display}'!"
            new_id = create_zoho_contact(contact_name, acc_id, acc_display)
            if new_id:
                moshav = get_moshav_for_account(acc_display)
                moshav_line = f"\n📍 {moshav}" if moshav else ""
                return (f"✅ לקוח חדש נוצר!\n"
                        f"👤 {contact_name}\n"
                        f"🏠 {acc_display}"
                        f"{moshav_line}\n"
                        f"📱 0")
            return "❌ שגיאה ביצירת הלקוח"
        elif original_action == "create_invoice":
            context_data = context
            product = context_data.get("product")
            contact_name = context_data.get("contact_name", "")
            custom_price = context_data.get("custom_price", 0)
            quantity = context_data.get("quantity", 1)
            final_price = custom_price if custom_price and custom_price > 0 else product.get("Unit_Price", 0)
            contacts, _ = find_contact_by_name_and_account(contact_name, chosen.get("Account_Name", ""))
            if not contacts:
                return f"❌ לא מצאתי לקוח '{contact_name}' אצל '{chosen.get('Account_Name', '')}'"
            if len(contacts) > 1:
                sessions[from_number] = {"pending": "contact_choice", "options": contacts, "context": {"product": product, "custom_price": custom_price, "quantity": quantity}}
                names = "\n".join([f"{i+1}. {c['Full_Name']}" for i, c in enumerate(contacts)])
                return f"מצאתי כמה לקוחות:\n{names}\n\nכתוב חלק מהשם או מספר לבחירה:"
            contact = contacts[0]
            acc_id = contact.get("Account_Name", {}).get("id") if isinstance(contact.get("Account_Name"), dict) else chosen["id"]
            inv_id = create_invoice(contact["id"], acc_id, product["id"], final_price, contact["Full_Name"], quantity)
            return build_invoice_confirmation(contact, product, final_price, quantity) if inv_id else "❌ שגיאה ביצירת החשבונית"
        return "❌ שגיאה פנימית"

    if pending == "payment_contact_choice":
        options = session["options"]
        context = session["context"]
        chosen = pick_best_match(options, message)
        if not chosen:
            names = "\n".join([f"{i+1}. {c['Full_Name']}" for i, c in enumerate(options)])
            return f"לא הצלחתי לזהות. בחר מספר:\n{names}"
        sessions.pop(from_number, None)
        return _process_payment_for_contact(chosen, context["amount"], context["method"], from_number)

    if pending == "payment_invoice_choice":
        options = session["options"]
        context = session["context"]
        contact = context["contact"]
        chosen = pick_best_match(options, message)
        if not chosen:
            lines = "\n".join([f"{i+1}. {inv.get('Subject','')}" for i, inv in enumerate(options)])
            return f"לא הצלחתי לזהות. בחר מספר:\n{lines}"
        sessions.pop(from_number, None)
        pay_amount = context["amount"] if context["amount"] else chosen.get("Grand_Total", 0)
        pay_method = context["method"] if context["method"] else "מזומן"
        success = mark_invoice_paid(chosen["id"], pay_amount, pay_method)
        if success:
            acc_name = contact.get("Account_Name", {}).get("name", "") if isinstance(contact.get("Account_Name"), dict) else ""
            log_action("תשלום", f"תשלום: {contact['Full_Name']} @ {acc_name} ₪{pay_amount} {pay_method}")
            return (f"✅ תשלום עודכן!\n"
                    f"👤 {contact['Full_Name']}\n"
                    f"🏠 {acc_name}\n"
                    f"💰 ₪{pay_amount} | {pay_method}\n"
                    f"📄 {chosen.get('Subject', '')}")
        return "❌ שגיאה בעדכון התשלום"

    # === מחק חשבונית אחרונה / מחק חשבונית אחרונה כפול ===
    msg_stripped = message.strip()
    delete_count = 0
    if msg_stripped == "מחק חשבונית אחרונה":
        delete_count = 1
    elif msg_stripped in ["מחק חשבונית אחרונה כפול", "מחק חשבונית אחרונה x2", "מחק 2 חשבוניות אחרונות"]:
        delete_count = 2
    elif msg_stripped in ["מחק חשבונית אחרונה משולש", "מחק חשבונית אחרונה x3", "מחק 3 חשבוניות אחרונות"]:
        delete_count = 3
    
    if delete_count > 0:
        # שלוף את N החשבוניות האחרונות
        token, domain_url = get_access_token()
        headers_z = {"Authorization": f"Zoho-oauthtoken {token}"}
        resp = requests.get(f"{domain_url}/crm/v5/Invoices", headers=headers_z, params={
            "fields": "Subject,Status,Grand_Total,Contact_Name,Account_Name,Created_Time,Invoiced_Items",
            "sort_by": "Created_Time",
            "sort_order": "desc",
            "per_page": delete_count
        })
        invoices_list = resp.json().get("data", []) if resp.status_code == 200 else []
        if not invoices_list:
            return "❌ לא מצאתי חשבוניות במערכת"
        
        # שלוף כל חשבונית בנפרד כדי לקבל את Invoiced_Items
        full_invoices = []
        for inv_stub in invoices_list:
            inv_id = inv_stub["id"]
            r2 = requests.get(f"{domain_url}/crm/v5/Invoices/{inv_id}", headers=headers_z)
            if r2.status_code == 200:
                full_inv = r2.json().get("data", [inv_stub])[0]
            else:
                full_inv = inv_stub
            full_invoices.append(full_inv)
        
        # בנה הודעת אישור עם פרטי כל החשבוניות
        lines = [f"🗑️ האם למחוק {len(full_invoices)} חשבונית/ות אחרונות?\n"]
        for i, inv in enumerate(full_invoices, 1):
            contact_obj = inv.get("Contact_Name", {})
            contact_name = contact_obj.get("name", "") if isinstance(contact_obj, dict) else str(contact_obj)
            account_obj = inv.get("Account_Name", {})
            account_name = account_obj.get("name", "") if isinstance(account_obj, dict) else str(account_obj)
            total = inv.get("Grand_Total", 0)
            items = inv.get("Invoiced_Items", []) or []
            product_name = ""
            if items:
                pn = items[0].get("Product_Name", {})
                product_name = pn.get("name", "") if isinstance(pn, dict) else str(pn)
            lines.append(
                f"{'─'*20}\n"
                f"👤 {contact_name}\n"
                f"🏠 {account_name}\n"
                f"💰 ₪{total}\n"
                f"📦 {product_name}"
            )
        lines.append("\n\nכתוב *כן* למחיקה או *לא* לביטול")
        
        sessions[from_number] = {
            "pending": "confirm_delete_invoice",
            "invoices_to_delete": invoices_list
        }
        return "\n".join(lines)

    # === Fallback: זיהוי ידני של פקודות מיוחדות לפני Gemini ===
    msg_lower = message.strip().lower()
    intent = None
    
    # זיהוי "קווים פעילים" או "חשבונית קווים פעילים"
    if "קווים פעילים" in msg_lower:
        words = message.strip().split()
        # הסר את המילים "קווים", "פעילים", "חשבונית", "פתח"
        skip_words = ["קווים", "פעילים", "חשבונית", "פתח", "לי", "כמה", "יש", "ל", "של"]
        remaining = [w for w in words if w.lower() not in skip_words]
        
        if "חשבונית" in msg_lower:
            # חשבונית קווים פעילים [לקוח] [בעל בית]
            # נניח שהמילה הראשונה היא הלקוח והשאר בעל הבית
            if len(remaining) >= 2:
                contact = remaining[0]
                account = " ".join(remaining[1:])
            elif len(remaining) == 1:
                contact = ""
                account = remaining[0]
            else:
                contact = ""
                account = ""
            intent = {"action": "active_lines_invoice", "contact": contact, "account": account}
        else:
            # קווים פעילים [בעל בית]
            account = " ".join(remaining) if remaining else ""
            intent = {"action": "active_lines", "account": account}
        print(f"Fallback detected active_lines: {intent}")
    
    if not intent:
        intent = parse_intent(message)
    action = intent.get("action")
    print(f"action={action}, intent={intent}")

    if action == "create_invoice":
        product_name = intent.get("product", "")
        contact_name = intent.get("contact", "")
        account_name = intent.get("account", "")
        custom_price = intent.get("price", 0)  # מחיר מותאם אישית (0 = מחיר ברירת מחדל)
        quantity = intent.get("quantity", 1)  # כמות יחידות (1 = ברירת מחדל)
        quantity = max(1, min(30, int(quantity))) if quantity else 1  # הגבל ל-1-30

        products = find_product(product_name)
        if not products:
            return f"❌ לא מצאתי מוצר '{product_name}'"

        # אם יש יותר ממוצר אחד - נסה לצמצם
        if len(products) > 1:
            # סינון נוסף: רק מוצרים שהשם שלהם באמת מכיל את כל מילות החיפוש
            product_words = product_name.strip().lower().split()
            exact_matches = [p for p in products if all(w in p.get("Product_Name", "").lower() for w in product_words)]
            if len(exact_matches) == 1:
                # נשאר רק מוצר אחד - בחר אוטומטית!
                products = exact_matches
                print(f"find_product: auto-selected '{exact_matches[0].get('Product_Name')}' (only exact match)")
            elif exact_matches:
                products = exact_matches  # צמצם לרשימה המסוננת
            
            # אם עדיין יותר ממוצר אחד - הצג רשימה
            if len(products) > 1:
                show = products[:10]
                sessions[from_number] = {
                    "pending": "product_choice",
                    "options": show,
                    "context": {"contact_name": contact_name, "account_name": account_name, "custom_price": custom_price, "quantity": quantity}
                }
                lines = [f"{i+1}. {p.get('Product_Name', '')} - ₪{p.get('Unit_Price', 0)}" for i, p in enumerate(show)]
                extra = f"\n... ועוד {len(products) - 10}" if len(products) > 10 else ""
                return f"🔍 מצאתי {len(products)} מוצרים עבור '{product_name}':\n" + "\n".join(lines) + extra + "\n\nכתוב מספר לבחירה:"

        product = products[0]
        # קבע מחיר: מותאם אישית או מחיר המוצר
        final_price = custom_price if custom_price and custom_price > 0 else product.get("Unit_Price", 0)
        print(f"Product found: {product.get('Product_Name')} id={product.get('id')} default_price={product.get('Unit_Price')} final_price={final_price}")

        contacts, accounts = find_contact_by_name_and_account(contact_name, account_name)
        if not contacts:
            return f"❌ לא מצאתי לקוח '{contact_name}' אצל '{account_name}'"
        if len(contacts) > 1:
            sessions[from_number] = {"pending": "contact_choice", "options": contacts, "context": {"product": product, "custom_price": custom_price, "quantity": quantity}}
            names = "\n".join([f"{i+1}. {c['Full_Name']}" for i, c in enumerate(contacts)])
            return f"מצאתי כמה לקוחות:\n{names}\n\nכתוב חלק מהשם או מספר לבחירה:"
        contact = contacts[0]
        acc_id = contact.get("Account_Name", {}).get("id") if isinstance(contact.get("Account_Name"), dict) else None
        if not acc_id and accounts:
            acc_id = accounts[0]["id"]
        inv_id = create_invoice(contact["id"], acc_id, product["id"], final_price, contact["Full_Name"], quantity)
        return build_invoice_confirmation(contact, product, final_price, quantity) if inv_id else "❌ שגיאה ביצירת החשבונית"

    elif action == "payment":
        return handle_payment(intent.get("contact", ""), intent.get("account", ""),
                              intent.get("amount"), intent.get("method", "מזומן"), from_number)

    elif action == "update_status":
        contact_name = intent.get("contact", "")
        account_name = intent.get("account", "")
        status = intent.get("status", "paid")
        zoho_status = "שולם" if status == "paid" else "לא שולם"
        label = "שולם ✅" if status == "paid" else "לא שולם ❌"
        contacts, _ = find_contact_by_name_and_account(contact_name, account_name)
        if not contacts:
            return f"❌ לא מצאתי לקוח '{contact_name}'"
        contact = contacts[0]
        invoices = zoho_get("Invoices/search", {"word": contact["Full_Name"]})
        if not invoices:
            return f"❌ לא מצאתי חשבוניות עבור {contact['Full_Name']}"
        inv = invoices[0]
        result = zoho_put(f"Invoices/{inv['id']}", {"data": [{"id": inv["id"], "Status": zoho_status}]})
        if result.get("data", [{}])[0].get("code") == "SUCCESS":
            return f"✅ עודכן ל{label}\n👤 {contact['Full_Name']}"
        return "❌ שגיאה בעדכון"

    elif action == "create_contact":
        contact_name = intent.get("contact", "")
        account_name = intent.get("account", "")
        if not contact_name:
            return "❌ לא ציינת שם ללקוח. לדוגמה: הוסף לקוח סוויט לאילן"
        if not account_name:
            return "❌ לא ציינת שם בעל בית. לדוגמה: הוסף לקוח סוויט לאילן"
        
        # חפש את בעל הבית (Account) ב-Zoho
        accounts = zoho_get("Accounts/search", {"word": account_name})
        if not accounts:
            return f"❌ לא מצאתי בעל בית בשם '{account_name}'"
        
        account = best_account_match(accounts, account_name)
        if not account:
            return show_account_choice(accounts, account_name, from_number, "create_contact", {"contact_name": contact_name})
        acc_id = account["id"]
        acc_display = account.get("Account_Name", account_name)
        
        # בדוק שהלקוח לא קיים כבר
        existing = zoho_get("Contacts/search", {"word": contact_name})
        if existing:
            for c in existing:
                c_acc = c.get("Account_Name", {})
                c_acc_id = c_acc.get("id") if isinstance(c_acc, dict) else None
                if c_acc_id == acc_id:
                    return f"⚠️ לקוח '{c['Full_Name']}' כבר קיים אצל '{acc_display}'!"
        
        # צור את הלקוח
        new_id = create_zoho_contact(contact_name, acc_id, acc_display)
        if new_id:
            moshav = get_moshav_for_account(acc_display)
            moshav_line = f"\n📍 {moshav}" if moshav else ""
            return (f"✅ לקוח חדש נוצר!\n"
                    f"👤 {contact_name}\n"
                    f"🏠 {acc_display}"
                    f"{moshav_line}\n"
                    f"📱 0")
        return "❌ שגיאה ביצירת הלקוח"

    elif action == "active_lines":
        account_name = intent.get("account", "")
        if not account_name:
            return "❌ לא ציינת שם בעל בית. לדוגמה: קווים פעילים אילן"
        accounts = zoho_get("Accounts/search", {"word": account_name})
        if not accounts:
            return f"❌ לא מצאתי בעל בית בשם '{account_name}'"
        account = best_account_match(accounts, account_name)
        if not account:
            return show_account_choice(accounts, account_name, from_number, "active_lines")
        acc_id = account["id"]
        acc_display = account.get("Account_Name", account_name)
        total_lines, active_contacts = get_active_lines_for_account(acc_id, acc_display)
        if total_lines == 0:
            return f"📊 {acc_display}\n🔌 0 קווים פעילים"
        details = "\n".join([f"  • {c['name']} ({c['lines']})" for c in active_contacts[:20]])
        extra = f"\n  ... ועוד {len(active_contacts) - 20}" if len(active_contacts) > 20 else ""
        return (f"📊 {acc_display}\n"
                f"🔌 {total_lines} קווים פעילים\n"
                f"👥 {len(active_contacts)} לקוחות:\n{details}{extra}")

    elif action == "active_lines_invoice":
        contact_name = intent.get("contact", "")
        account_name = intent.get("account", "")
        if not contact_name or not account_name:
            return "❌ חסר שם לקוח או בעל בית. לדוגמה: חשבונית קווים פעילים סומניק אילן"
        # מצא את בעל הבית
        accounts = zoho_get("Accounts/search", {"word": account_name})
        if not accounts:
            return f"❌ לא מצאתי בעל בית בשם '{account_name}'"
        account = best_account_match(accounts, account_name)
        if not account:
            return show_account_choice(accounts, account_name, from_number, "active_lines_invoice", {"contact_name": contact_name})
        acc_id = account["id"]
        acc_display = account.get("Account_Name", account_name)
        # ספור קווים פעילים של בעל הבית
        total_lines, active_contacts = get_active_lines_for_account(acc_id, acc_display)
        if total_lines == 0:
            return f"❌ אין קווים פעילים ל-{acc_display}"
        # מצא את המוצר "כרטיס 050 מקומי- קו פעיל"
        products = find_product("כרטיס 050 מקומי קו פעיל")
        if not products:
            return f"❌ לא מצאתי מוצר 'כרטיס 050 מקומי- קו פעיל'"
        product = products[0]
        # מצא את הלקוח
        contacts, _ = find_contact_by_name_and_account(contact_name, account_name)
        if not contacts:
            return f"❌ לא מצאתי לקוח '{contact_name}' אצל '{account_name}'"
        if len(contacts) > 1:
            sessions[from_number] = {
                "pending": "contact_choice",
                "options": contacts,
                "context": {"product": product, "custom_price": 0, "quantity": total_lines}
            }
            names = "\n".join([f"{i+1}. {c['Full_Name']}" for i, c in enumerate(contacts)])
            return f"🔌 {total_lines} קווים פעילים ל-{acc_display}\n\nמצאתי כמה לקוחות:\n{names}\n\nכתוב חלק מהשם או מספר לבחירה:"
        contact = contacts[0]
        c_acc_id = contact.get("Account_Name", {}).get("id") if isinstance(contact.get("Account_Name"), dict) else acc_id
        final_price = product.get("Unit_Price", 0)
        inv_id = create_invoice(contact["id"], c_acc_id, product["id"], final_price, contact["Full_Name"], total_lines)
        if inv_id:
            acc_name = contact.get("Account_Name", {}).get("name", acc_display) if isinstance(contact.get("Account_Name"), dict) else acc_display
            return (f"✅ חשבונית קווים פעילים נוצרה!\n"
                    f"👤 {contact['Full_Name']}\n"
                    f"🏠 {acc_name}\n"
                    f"📦 {product.get('Product_Name')} x{total_lines}\n"
                    f"🔌 {total_lines} קווים פעילים\n"
                    f"💰 ₪{final_price} ליחידה | סה\"כ ₪{final_price * total_lines}")
        return "❌ שגיאה ביצירת החשבונית"

    elif action == "query":
        account_name = intent.get("account", "")
        invoices = zoho_get("Invoices/search", {"word": account_name})
        open_inv = [i for i in invoices if i.get("Status") in ["לא שולם", "Created", None]]
        if not open_inv:
            return f"✅ אין חשבוניות פתוחות עבור '{account_name}'"
        lines = [f"• {i.get('Subject', '')} - ₪{i.get('Grand_Total', 0)}" for i in open_inv]
        return f"📋 {len(open_inv)} חשבוניות פתוחות:\n" + "\n".join(lines)

    return ("❓ לא הבנתי. לדוגמה:\n"
            "• '050 לטייה של איציק' - חשבונית חדשה\n"
            "• 'טונגצאי בוי שער דוד שילם 120 מזומן' - תשלום\n"
            "• 'קווים פעילים אילן' - בדיקת קווים")

def bulk_passport_update_for_account(account: dict, from_number: str) -> str:
    """
    עובר על כל לקוחות של בעל בית שאין להם שם ויזה,
    מחפש פספורט בקבצים ומעדכן Visa_Name1.
    משלח עדכוני ביניים בוואטסאפ ומחזיר סיכום סופי.
    """
    aid = account["id"]
    aname = account.get("Account_Name", "")
    token, domain = get_access_token()
    headers_z = {"Authorization": f"Zoho-oauthtoken {token}"}

    # שלוף כל לקוחות בבית
    all_contacts = []
    page = 1
    while True:
        batch, info = zoho_get_full("Contacts/search", {
            "criteria": f"(Account_Name:equals:{aid})",
            "fields": "Full_Name,Visa_Name1,id",
            "per_page": 200,
            "page": page
        })
        if not batch:
            break
        all_contacts.extend(batch)
        if not info.get("more_records", False):
            break
        page += 1

    if not all_contacts:
        return f"❌ לא נמצאו לקוחות בבית *{aname}*"
    # עבד על כל הלקוחות (כולל מי שיש לו שם ויזה)
    missing = all_contacts
    # שלח עדכון ראשון
    _send_reply(
        f"\U0001f50d מתחיל עיבוד {len(missing)} לקוחות - *{aname}*",
        from_number
    )
    updated = []
    skipped = []
    failed = []
    cancel_flags.pop(from_number, None)  # נקה דגל ביטול ישן
    for contact in missing:
        # בדוק אם המשתמש ביקש ביטול
        if cancel_flags.get(from_number):
            cancel_flags.pop(from_number, None)
            _send_reply(f"⛔ עיבוד פספורט בוטל - *{aname}*\nעודכנו: {len(updated)}, דולגו: {len(skipped)}", from_number)
            return f"⛔ בוטל על ידי המשתמש"
        cname = contact.get("Full_Name", "")
        cid = contact["id"]
        # שלוף קבצים מצורפים
        r = requests.get(f"{domain}/crm/v2/Contacts/{cid}/Attachments", headers=headers_z, timeout=15)
        if r.status_code != 200 or not r.json().get("data"):
            skipped.append(f"⏩ {cname} - אין קבצים")
            continue

        attachments = r.json()["data"]

        # חלק לשתי רשימות: פספורט בשם, ושאר
        def _is_passport_file(att):
            fname = att.get("File_Name", "").lower()
            return ("פספורט" in fname or "passport" in fname or "תעודה" in fname or "id" in fname or "visa" in fname)
        passport_files = [a for a in attachments if _is_passport_file(a)]
        other_files = [a for a in attachments if not _is_passport_file(a)]
        # נסה קודם קבצי פספורט, ורק אם לא מצא - שאר הקבצים
        attachments_sorted = passport_files + other_files

        found_name = None
        for att in attachments_sorted:
            att_id = att["id"]
            r2 = requests.get(f"{domain}/crm/v2/Contacts/{cid}/Attachments/{att_id}", headers=headers_z)
            if r2.status_code != 200:
                continue
            img_bytes = r2.content
            content_type = r2.headers.get("content-type", "image/jpeg").split(";")[0].strip()
            if "png" in content_type:
                mime = "image/png"
            elif "webp" in content_type:
                mime = "image/webp"
            else:
                mime = "image/jpeg"

            import base64
            img_b64 = base64.b64encode(img_bytes).decode("utf-8")
            gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
            payload = {
                "contents": [{"parts": [
                    {"inline_data": {"mime_type": mime, "data": img_b64}},
                    {"text": "Look at this image. If it is a passport or official ID document, extract ONLY the full English name (given name + surname as printed). Return ONLY the name in UPPERCASE, nothing else. Format: FIRSTNAME LASTNAME. If the image is NOT a passport or official ID document (e.g. it is a photo of a person, a selfie, a document in a different language without Latin name, or any other non-ID image), return exactly the word: NONE"}
                ]}],
                "generationConfig": {"temperature": 0}
            }
            try:
                gr = requests.post(gemini_url, json=payload, timeout=30)
                if gr.status_code == 200:
                    import re as _re
                    extracted = gr.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                    extracted_clean = _re.sub(r'[^A-Za-z\s]', '', extracted).strip().upper()
                    # ודא שזה שם אמיתי: עד 4 מילים, לא יותר מ-50 תווים, לא מכיל מילות סירוב (= לא הסבר מגמיני)
                    _refusal_words = {"SORRY", "CANNOT", "UNABLE", "DOCUMENT", "IMAGE", "PASSPORT", "REQUEST", "EXTRACT", "PROVIDED", "THEREFORE", "FULFILL", "PHOTOGRAPH", "PERSON", "LOCATED", "NONE"}
                    word_count = len(extracted_clean.split())
                    has_refusal = any(w in extracted_clean.split() for w in _refusal_words)
                    if extracted_clean and 1 <= word_count <= 4 and len(extracted_clean) <= 50 and not has_refusal:
                        found_name = extracted_clean
                        break
            except Exception as e:
                print(f"Gemini error for {cname}: {e}")
                continue

        if found_name:
            upd = requests.put(f"{domain}/crm/v2/Contacts",
                               headers={**headers_z, "Content-Type": "application/json"},
                               json={"data": [{"id": cid, "Visa_Name1": found_name}]})
            if upd.status_code == 200 and upd.json().get("data", [{}])[0].get("code") == "SUCCESS":
                updated.append(f"✅ {cname} → {found_name}")
            else:
                failed.append(f"❌ {cname} - שגיאה בעדכון")
        else:
            skipped.append(f"⏩ {cname} - לא נמצא פספורט")

        time.sleep(0.3)  # מנע עומס יתר על Gemini API

    # סיכום
    lines = [f"🏠 *סיכום עדכון פספורטים - {aname}*", "─" * 28]
    if updated:
        lines.append(f"\n✅ עודכנו ({len(updated)}):")
        lines.extend(updated)
    if skipped:
        lines.append(f"\n⏩ דולגו ({len(skipped)}):")
        lines.extend(skipped)
    if failed:
        lines.append(f"\n❌ שגיאות ({len(failed)}):")
        lines.extend(failed)
    return "\n".join(lines)


# ─── Profile photo helpers ────────────────────────────────────────────────────
def _crop_face_center(img_bytes: bytes):
    """
    מחלץ פנים מהתמונה באמצעות MediaPipe Face Detection.
    מדויק ומהיר, ללא צורך בקבצי מודל חיצוניים.
    מחזיר tuple: (face_bytes_or_None, debug_message)
    """
    import io
    import numpy as np
    import gc
    from PIL import Image, ImageOps

    debug_lines = []

    try:
        # פתח תמונה ותקן EXIF rotation
        img_pil = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img_pil = ImageOps.exif_transpose(img_pil)
        iw, ih = img_pil.size
        debug_lines.append(f"תמונה: {iw}x{ih}")

        arr = np.array(img_pil)

        # הגבל גודל תמונה ל-1200px לחסכון בזיכרון
        MAX_DIM = 1200
        if iw > MAX_DIM or ih > MAX_DIM:
            ratio = MAX_DIM / max(iw, ih)
            img_pil = img_pil.resize((int(iw * ratio), int(ih * ratio)), Image.LANCZOS)
            iw, ih = img_pil.size
            arr = np.array(img_pil)
            debug_lines.append(f"שוקלל ל: {iw}x{ih}")

        best_box = None
        best_conf = 0.0

        # נסה MediaPipe Face Detection
        try:
            import mediapipe as mp
            mp_face = mp.solutions.face_detection
            with mp_face.FaceDetection(model_selection=1, min_detection_confidence=0.3) as detector:
                results = detector.process(arr)
                if results.detections:
                    for det in results.detections:
                        conf = det.score[0] if det.score else 0.0
                        bb = det.location_data.relative_bounding_box
                        x1 = int(bb.xmin * iw)
                        y1 = int(bb.ymin * ih)
                        x2 = int((bb.xmin + bb.width) * iw)
                        y2 = int((bb.ymin + bb.height) * ih)
                        # ודא גבולות תקינים
                        x1, y1 = max(0, x1), max(0, y1)
                        x2, y2 = min(iw, x2), min(ih, y2)
                        w, h = x2 - x1, y2 - y1
                        debug_lines.append(f"MediaPipe: conf={conf:.2f} ({x1},{y1})-({x2},{y2}) {w}x{h}")
                        if conf > best_conf:
                            best_conf = conf
                            best_box = (x1, y1, x2, y2)
                    if best_box:
                        debug_lines.append(f"MediaPipe מצא פנים! confidence={best_conf:.2f}")
                else:
                    debug_lines.append("MediaPipe לא מצא פנים")
        except ImportError:
            debug_lines.append("MediaPipe לא מותקן, מנסה OpenCV")
        except Exception as mp_err:
            debug_lines.append(f"MediaPipe שגיאה: {str(mp_err)[:80]}")
        finally:
            gc.collect()  # שחרר זיכרון MediaPipe

        # fallback: נסה OpenCV אם MediaPipe לא מצא
        if best_box is None:
            try:
                import cv2
                debug_lines.append("מנסה OpenCV Haar Cascade כ-fallback")
                gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
                cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
                face_cascade = cv2.CascadeClassifier(cascade_path)
                faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
                if len(faces) == 0:
                    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.05, minNeighbors=3, minSize=(20, 20))
                debug_lines.append(f"Haar: {len(faces)} פנים")
                if len(faces) > 0:
                    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
                    best_box = (x, y, x + w, y + h)
            except Exception as cv_err:
                debug_lines.append(f"OpenCV שגיאה: {str(cv_err)[:80]}")

        if best_box is None:
            return _fallback_crop(img_pil, iw, ih, debug_lines, "no face detected")

        x1, y1, x2, y2 = best_box
        debug_lines.append(f"פנים נבחרות: ({x1},{y1})-({x2},{y2})")

        # הוסף padding של 20% סביב הפנים (זום קרוב לפנים)
        fw = x2 - x1
        fh = y2 - y1
        pad_w = int(fw * 0.2)
        pad_h = int(fh * 0.2)

        px1 = max(0, x1 - pad_w)
        py1 = max(0, y1 - pad_h)
        px2 = min(iw, x2 + pad_w)
        py2 = min(ih, y2 + pad_h)

        # חתוך לריבוע מרכזי
        side = max(px2 - px1, py2 - py1)
        cx = (px1 + px2) // 2
        cy = (py1 + py2) // 2
        sx1 = max(0, cx - side // 2)
        sy1 = max(0, cy - side // 2)
        sx2 = min(iw, sx1 + side)
        sy2 = min(ih, sy1 + side)
        if sx2 > iw: sx1 = max(0, iw - side); sx2 = iw
        if sy2 > ih: sy1 = max(0, ih - side); sy2 = ih

        debug_lines.append(f"חיתוך סופי: ({sx1},{sy1})-({sx2},{sy2})")

        cropped = img_pil.crop((sx1, sy1, sx2, sy2))
        final = cropped.resize((400, 400), Image.LANCZOS)
        buf = io.BytesIO()
        final.save(buf, format="JPEG", quality=90)
        face_bytes = buf.getvalue()
        debug_lines.append(f"חיתוך הצליח! ({len(face_bytes)//1000}KB)")
        del arr, img_pil, buf
        gc.collect()
        return face_bytes, "\n".join(debug_lines)

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        debug_lines.append(f"Exception: {str(e)[:100]}")
        debug_lines.append(tb[-300:])
        return None, "\n".join(debug_lines)

def _fallback_crop(img_pil, iw, ih, debug_lines, reason):
    """
    חיתוך fallback - מחפש צבע עור בתמונה כדי למצוא את הפנים
    """
    import io
    import numpy as np
    from PIL import Image
    debug_lines.append(f"Fallback crop ({reason}): skin-tone detection")

    try:
        # המר ל-numpy וחפש צבע עור
        arr = np.array(img_pil)
        r, g, b = arr[:,:,0], arr[:,:,1], arr[:,:,2]

        # פילטר צבע עור בסיסי
        skin_mask = (
            (r > 80) & (g > 40) & (b > 20) &
            (r > g) & (r > b) &
            (r - g > 10) &
            (np.abs(r.astype(int) - g.astype(int)) > 10)
        )

        # מצא שורות עם הכי הרבה צבע עור
        row_sums = skin_mask.sum(axis=1)
        best_row = int(np.argmax(row_sums))
        debug_lines.append(f"Skin row: {best_row} ({row_sums[best_row]} pixels)")

        # חתוך ריבוע סביב השורה הזו
        half = min(ih // 4, iw // 2)  # גודל הריבוע
        y1 = max(0, best_row - half)
        y2 = min(ih, best_row + half)
        # מצא את העמודה האופקית
        col_sums = skin_mask[y1:y2, :].sum(axis=0)
        best_col = int(np.argmax(col_sums)) if col_sums.max() > 0 else iw // 2
        x1 = max(0, best_col - half)
        x2 = min(iw, best_col + half)
        side = max(x2 - x1, y2 - y1)
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2
        sx1 = max(0, cx - side // 2)
        sy1 = max(0, cy - side // 2)
        sx2 = min(iw, sx1 + side)
        sy2 = min(ih, sy1 + side)
        debug_lines.append(f"Skin crop: ({sx1},{sy1})-({sx2},{sy2})")
    except Exception as e:
        debug_lines.append(f"Skin detection failed: {e}, using center")
        # אם נכשל - חתוך מרכז התמונה
        side = min(iw, ih // 2)
        sx1 = iw // 2 - side // 2
        sy1 = ih // 4
        sx2 = sx1 + side
        sy2 = sy1 + side

    cropped = img_pil.crop((sx1, sy1, sx2, sy2))
    final = cropped.resize((400, 400), Image.LANCZOS)
    buf = io.BytesIO()
    final.save(buf, format="JPEG", quality=90)
    return buf.getvalue(), "\n".join(debug_lines)

def handle_profile_image_upload(name_q: str, media_url: str, media_type: str, from_number: str) -> str:
    """
    מטפל בתמונת פרופיל נכנסת:
    1. מחפש לקוח לפי שם
    2. מעלה תמונה מקורית כ-attachment בשם 'פרופיל'
    3. מחלץ פנים ומגדיר כתמונת פרופיל ב-Zoho
    """
    contacts = _word_search_contacts(name_q)
    if not contacts:
        for word in name_q.split():
            if len(word) >= 2:
                contacts = _word_search_contacts(word, per_page=15)
                if contacts:
                    break
    if not contacts:
        return f"❓ לא מצאתי לקוח בשם *{name_q}* - נסה שם קצר יותר"

    if len(contacts) > 1:
        sessions[from_number] = {
            "pending": "choose_contact_profile_upload",
            "contacts": contacts,
            "name_q": name_q,
            "media_url": media_url,
            "media_type": media_type
        }
        return _format_contact_choice_menu(contacts, "העלאת פרופיל")

    return _do_profile_upload(contacts[0], media_url, media_type)


def _do_profile_upload(contact: dict, media_url: str, media_type: str) -> str:
    """
    מוריד תמונה:
    שלב 1 - מעלה תמונה מקורית כ-attachment 'פרופיל'
    שלב 2 - חותך פנים עם Gemini ושומר כקובץ חדש 'פרופיל_פנים'
    שלב 3 - מעלה 'פרופיל_פנים' כ-attachment ומגדיר כתמונת פרופיל ב-Zoho
    """
    contact_id = contact["id"]
    contact_name = contact.get("Full_Name", "")
    token, domain = get_access_token()
    headers_z = {"Authorization": f"Zoho-oauthtoken {token}"}

    # ── שלב 1: הורד תמונה מקורית ──────────────────────────────────────────
    try:
        img_resp = requests.get(media_url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), timeout=30)
        if img_resp.status_code != 200:
            return f"❌ שגיאה בהורדת התמונה (status {img_resp.status_code})"
        img_bytes = img_resp.content
    except Exception as e:
        return f"❌ שגיאה בהורדת התמונה: {e}"

    ext = "jpg"
    if "png" in media_type: ext = "png"
    elif "webp" in media_type: ext = "webp"
    orig_file_name = f"פרופיל.{ext}"

    # ── שלב 2: העלה תמונה מקורית כ-attachment ─────────────────────────────
    orig_upload_ok = False
    try:
        upload_resp = requests.post(
            f"{domain}/crm/v2/Contacts/{contact_id}/Attachments",
            headers=headers_z,
            files={"file": (orig_file_name, img_bytes, media_type)}
        )
        print(f"Original attachment upload: {upload_resp.status_code} {upload_resp.text[:200]}")
        orig_upload_ok = upload_resp.status_code in [200, 201]
    except Exception as e:
        print(f"Original attachment error: {e}")

    if not orig_upload_ok:
        return f"❌ שגיאה בהעלאת הקובץ המקורי ל-Zoho"

    # ── שלב 3: חתוך פנים עם Gemini ─────────────────────────────────────────
    face_bytes = None
    face_file_name = "פרופיל_פנים.jpg"
    face_debug = ""
    face_bytes, face_debug = _crop_face_center(img_bytes)

    if not face_bytes:
        return (f"📎 קובץ '{orig_file_name}' הועלה לקבצים המצורפים\n"
                f"⚠️ לא הצלחתי לחתוך פנים:\n{face_debug}")

    # ── שלב 4: העלה תמונת פנים כ-attachment נפרד ─────────────────────────
    face_att_ok = False
    try:
        face_att_resp = requests.post(
            f"{domain}/crm/v2/Contacts/{contact_id}/Attachments",
            headers=headers_z,
            files={"file": (face_file_name, face_bytes, "image/jpeg")}
        )
        print(f"Face attachment upload: {face_att_resp.status_code} {face_att_resp.text[:200]}")
        face_att_ok = face_att_resp.status_code in [200, 201]
    except Exception as e:
        print(f"Face attachment error: {e}")

    # ── שלב 5: הגדר תמונת פנים כתמונת פרופיל ב-Zoho ─────────────────────
    photo_ok = False
    photo_status = ""
    try:
        photo_resp = requests.post(
            f"{domain}/crm/v2/Contacts/{contact_id}/photo",
            headers=headers_z,
            files={"file": ("profile.jpg", face_bytes, "image/jpeg")}
        )
        photo_status = f"{photo_resp.status_code}"
        print(f"Photo upload status: {photo_resp.status_code} {photo_resp.text[:200]}")
        photo_ok = photo_resp.status_code in [200, 201, 202]
    except Exception as e:
        photo_status = str(e)[:50]
        print(f"Profile photo error: {e}")

    # ── בנה תשובה ─────────────────────────────────────────────────────────
    lines = [f"👤 {contact_name}"]
    lines.append(f"📎 '{orig_file_name}' הועלה לקבצים המצורפים")
    if face_debug:
        lines.append(face_debug)
    if face_att_ok:
        lines.append(f"📎 '{face_file_name}' הועלה לקבצים המצורפים")
    if photo_ok:
        lines.append(f"🖼️ תמונת פרופיל עודכנה עם מיקוד פנים ✅")
    else:
        lines.append(f"⚠️ לא הצלחתי לעדכן תמונת פרופיל (status {photo_status})")
    return "\n".join(lines)



def _scan_profiles_for_account(account: dict) -> tuple:
    """
    סורק את כל לקוחות בעל הבית ובודק אם יש להם תמונת פרופיל.
    מחזיר (message_str, contacts_data)
    contacts_data = [(contact, has_photo: bool, atts: list)]
    """
    aid = account["id"]
    aname = account.get("Account_Name", "")
    token, domain = get_access_token()
    headers_z = {"Authorization": f"Zoho-oauthtoken {token}"}

    all_contacts = []
    page = 1
    while True:
        batch, info = zoho_get_full("Contacts/search", {
            "criteria": f"(Account_Name:equals:{aid})",
            "fields": "Full_Name,id",
            "per_page": 200, "page": page
        })
        if not batch: break
        all_contacts.extend(batch)
        if not info.get("more_records", False): break
        page += 1

    if not all_contacts:
        return f"❌ לא נמצאו לקוחות - *{aname}*", []

    contacts_data = []
    for contact in all_contacts:
        cid = contact["id"]
        # Check if has profile photo by trying GET /photo
        photo_r = requests.get(f"{domain}/crm/v2/Contacts/{cid}/photo", headers=headers_z)
        has_photo = photo_r.status_code == 200 and len(photo_r.content) > 1000

        # Get attachments list
        att_r = requests.get(f"{domain}/crm/v2/Contacts/{cid}/Attachments", headers=headers_z)
        atts = att_r.json().get("data", []) if att_r.status_code == 200 else []
        contacts_data.append((contact, has_photo, atts))
        time.sleep(0.15)

    # Build summary message
    lines = [f"📋 *סקירת פרופילים - {aname}*", "─" * 28]
    for i, (contact, has_photo, atts) in enumerate(contacts_data, 1):
        cname = contact.get("Full_Name", "")
        img_count = sum(1 for a in atts if a.get("File_Name","").lower().endswith(('.jpg','.jpeg','.png','.webp')))
        status = "✅" if has_photo else "❌"
        lines.append(f"{i}. {status} {cname} ({img_count} תמונות)")

    no_photo_nums = [str(i) for i, (_, has_photo, _) in enumerate(contacts_data, 1) if not has_photo]
    lines.append("")
    lines.append(f"✅ יש תמונה: {sum(1 for _,h,_ in contacts_data if h)}")
    lines.append(f"❌ אין תמונה: {sum(1 for _,h,_ in contacts_data if not h)}")
    lines.append("")
    lines.append("שלח מספרים של מי לתקן (מופרדים בפסיקים)")
    if no_photo_nums:
        lines.append(f"💡 ללא תמונה: {', '.join(no_photo_nums)}")
    lines.append("0 = סיום")

    return "\n".join(lines), contacts_data


def _fix_profiles_from_next_attachment(to_fix: list, account: dict, from_number: str, used_att_ids: dict = None) -> tuple:
    """
    מנסה לתקן פרופילים עבור רשימת לקוחות.
    to_fix = [(contact, atts)]
    used_att_ids = {contact_id: [att_id, ...]} - קבצים שכבר שימשו (לדלג עליהם)
    מחזיר (result_str, new_used_att_ids)
    """
    import re as _re
    if used_att_ids is None:
        used_att_ids = {}
    new_used_att_ids = {}

    def _is_image_file(fname: str) -> bool:
        return fname.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif'))

    aname = account.get("Account_Name", "")
    token, domain = get_access_token()
    headers_z = {"Authorization": f"Zoho-oauthtoken {token}"}

    updated = []
    failed = []
    total = len(to_fix)

    for i, (contact, atts) in enumerate(to_fix, 1):
        cname = contact.get("Full_Name", "")
        cid = contact["id"]
        cname_lower = cname.lower()

        # קבצים שכבר שימשו עבור לקוח זה
        already_used = set(used_att_ids.get(cid, []))
        image_atts = [a for a in atts if _is_image_file(a.get("File_Name", ""))]
        if not image_atts:
            failed.append(f"{cname} (אין תמונות)")
            _send_reply(f"⏩ [{i}/{total}] {cname} - אין תמונות בקבצים", from_number)
            continue

        # Priority order: פרופיל → מכשיר → שם לקוח → שאר (לא פספורט) → פספורט
        passport_atts = [a for a in image_atts if "פספורט" in a.get("File_Name","").lower() or "passport" in a.get("File_Name","").lower()]
        non_passport = [a for a in image_atts if a not in passport_atts]

        ordered = []
        # 1. פרופיל
        for a in image_atts:
            fn = a.get("File_Name","").lower()
            if ("פרופיל" in fn or "profile" in fn) and a not in ordered:
                ordered.append(a)
        # 2. מכשיר
        for a in image_atts:
            fn = a.get("File_Name","").lower()
            if "מכשיר" in fn and a not in ordered:
                ordered.append(a)
        # 3. שם לקוח
        name_words = [w for w in cname_lower.split() if len(w) >= 2]
        for a in non_passport:
            fn = a.get("File_Name","").lower()
            if any(w in fn for w in name_words) and a not in ordered:
                ordered.append(a)
        # 4. שאר (לא פספורט)
        for a in non_passport:
            if a not in ordered:
                ordered.append(a)
        # 5. פספורט - רק אם אין אחרים
        if not non_passport:
            for a in passport_atts:
                if a not in ordered:
                    ordered.append(a)

        success = False
        for att in ordered:
            # דלג על קבצים שכבר שימשו (לפי זיכרון סשן)
            if att['id'] in already_used:
                _send_reply(f"⏩ [{i}/{total}] {cname} - דולג על {att['File_Name']} (כבר שימש)", from_number)
                continue
            r2 = requests.get(f"{domain}/crm/v2/Contacts/{cid}/Attachments/{att['id']}", headers=headers_z)
            if r2.status_code != 200:
                continue
            face_bytes, face_dbg = _crop_face_center(r2.content)
            if not face_bytes:
                _send_reply(f"⚠️ [{i}/{total}] {cname} - {att['File_Name']}: חיתוך נכשל ({face_dbg[:40]})", from_number)
                already_used.add(att['id'])  # סמן כנסה
                continue
            try:
                photo_resp = requests.post(
                    f"{domain}/crm/v2/Contacts/{cid}/photo",
                    headers=headers_z,
                    files={"file": ("profile.jpg", face_bytes, "image/jpeg")}
                )
                if photo_resp.status_code in [200, 201, 202]:
                    updated.append(cname)
                    _send_reply(f"✅ [{i}/{total}] {cname} - עודכן מ: {att['File_Name']}", from_number)
                    new_used_att_ids[cid] = list(already_used | {att['id']})
                    success = True
                    break
                else:
                    _send_reply(f"⚠️ [{i}/{total}] {cname} - {att['File_Name']}: שגיאה Zoho ({photo_resp.status_code})", from_number)
            except Exception as e:
                _send_reply(f"⚠️ [{i}/{total}] {cname} - שגיאה: {str(e)[:40]}", from_number)
            time.sleep(0.3)

        if not success:
            failed.append(cname)
            new_used_att_ids[cid] = list(already_used)

        time.sleep(0.5)

    lines = [f"🏠 *סיכום תיקון פרופילים - {aname}*", "─" * 28]
    lines.append(f"✅ עודכנו: {len(updated)}")
    if failed:
        lines.append(f"❌ נכשלו: {len(failed)}")
        for n in failed:
            lines.append(f"  • {n}")
    return "\n".join(lines), new_used_att_ids


import os as _os
import json as _json
import time as _time_mod
import threading as _threading

_RESUME_DIR = "/tmp/bot_logs"

def _save_resume_state(aid: str, aname: str, from_number: str, completed_ids: set, total: int):
    """שומר מצב ריצה לאחר כל לקוח מעובד."""
    try:
        _os.makedirs(_RESUME_DIR, exist_ok=True)
        state_file = f"{_RESUME_DIR}/profile_resume_{aid}.json"
        started_at = _time_mod.time()
        if _os.path.exists(state_file):
            try:
                with open(state_file) as f:
                    old = _json.load(f)
                    started_at = old.get("started_at", started_at)
            except:
                pass
        state = {
            "aid": aid,
            "aname": aname,
            "from_number": from_number,
            "completed_ids": list(completed_ids),
            "total": total,
            "started_at": started_at,
            "status": "running"
        }
        with open(state_file, "w") as f:
            _json.dump(state, f)
    except Exception:
        pass

def _clear_resume_state(aid: str):
    """מוחק קובץ מצב בסיום מוצלח."""
    try:
        state_file = f"{_RESUME_DIR}/profile_resume_{aid}.json"
        if _os.path.exists(state_file):
            _os.remove(state_file)
    except:
        pass

def _auto_resume_on_startup():
    """בודק אם יש ריצות לא גמורות ומחדש אותן - פעם אחת בלבד."""
    import time
    time.sleep(10)
    try:
        if not _os.path.exists(_RESUME_DIR):
            return
        for fname in _os.listdir(_RESUME_DIR):
            if not fname.startswith("profile_resume_") or not fname.endswith(".json"):
                continue
            state_file = f"{_RESUME_DIR}/{fname}"
            try:
                with open(state_file) as f:
                    state = _json.load(f)
            except:
                _os.remove(state_file)
                continue
            if state.get("status") != "running":
                continue
            age = _time_mod.time() - state.get("started_at", 0)
            if age > 7200:
                _os.remove(state_file)
                continue
            state["status"] = "resuming"
            with open(state_file, "w") as f:
                _json.dump(state, f)
            aid = state["aid"]
            aname = state["aname"]
            from_number = state["from_number"]
            completed_ids = set(state.get("completed_ids", []))
            total = state.get("total", 0)
            done = len(completed_ids)
            _send_reply(
                f"\U0001f504 *חידוש אוטומטי - פרופיל כללי*\n"
                f"\U0001f3e0 בית: {aname}\n"
                f"\u23f8 נעצר בלקוח {done}/{total}\n"
                f"\u23f3 ממשיך מלקוח {done+1}...",
                from_number
            )
            try:
                token, domain = get_access_token()
                headers_z = {"Authorization": f"Zoho-oauthtoken {token}"}
                r = requests.get(f"{domain}/crm/v2/Accounts/{aid}", headers=headers_z)
                if r.status_code == 200:
                    accounts = r.json().get("data", [])
                    if accounts:
                        bulk_profile_update_for_account(accounts[0], from_number, skip_ids=completed_ids)
                        return
            except Exception:
                pass
            _os.remove(state_file)
    except Exception:
        pass

_threading.Thread(target=_auto_resume_on_startup, daemon=True).start()

def bulk_profile_update_for_account(account: dict, from_number: str, skip_ids: set = None) -> str:
    """
    עובר על כל לקוחות של בעל בית ומחפש תמונת פרופיל לפי סדר עדיפויות:
    1. קובץ בשם 'פרופיל' (כולל וריאציות)
    2. קובץ בשם 'מכשיר' או שמכיל 'מכשיר'
    3. קובץ שמכיל את שם הלקוח
    4. כל קובץ תמונה (jpg/jpeg/png) - קובץ קובץ
    5. פספורט - רק אם זה הקובץ היחיד שיש
    """
    import re

    def _is_image_file(fname: str) -> bool:
        return fname.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif'))

    def _pick_best_attachment(atts: list, contact_name: str) -> tuple:
        """
        בוחר את הקובץ הטוב ביותר לפי סדר עדיפויות.
        מחזיר (attachment, reason) או (None, reason)
        """
        if not atts:
            return None, "אין קבצים"

        name_lower = contact_name.lower()
        image_atts = [a for a in atts if _is_image_file(a.get("File_Name", ""))]
        passport_atts = [a for a in atts if "פספורט" in a.get("File_Name", "").lower() or "passport" in a.get("File_Name", "").lower()]
        non_passport_images = [a for a in image_atts if a not in passport_atts]

        # שלב 1: קובץ בשם פרופיל
        for a in image_atts:
            fn = a.get("File_Name", "").lower()
            if "פרופיל" in fn or "profile" in fn:
                return a, f"קובץ פרופיל: {a['File_Name']}"

        # שלב 2: קובץ בשם מכשיר
        for a in image_atts:
            fn = a.get("File_Name", "").lower()
            if "מכשיר" in fn:
                return a, f"קובץ מכשיר: {a['File_Name']}"

        # שלב 3: קובץ שמכיל את שם הלקוח
        # נסה כל מילה בשם הלקוח (לפחות 2 תווים)
        name_words = [w for w in name_lower.split() if len(w) >= 2]
        for a in non_passport_images:
            fn = a.get("File_Name", "").lower()
            for word in name_words:
                if word in fn:
                    return a, f"קובץ עם שם לקוח ({word}): {a['File_Name']}"

        # שלב 4: כל קובץ תמונה שאינו פספורט
        if non_passport_images:
            a = non_passport_images[0]
            return a, f"תמונה ראשונה (לא פספורט): {a['File_Name']}"

        # שלב 5: פספורט - רק אם זה הקובץ היחיד
        if passport_atts and len(image_atts) == len(passport_atts):
            a = passport_atts[0]
            return a, f"פספורט (היחיד): {a['File_Name']}"

        # אין תמונות בכלל
        if not image_atts:
            return None, "אין קבצי תמונה"

        return None, "לא נמצא קובץ מתאים"

    aid = account["id"]
    aname = account.get("Account_Name", "")
    token, domain = get_access_token()
    headers_z = {"Authorization": f"Zoho-oauthtoken {token}"}
    if skip_ids is None:
        skip_ids = set()

    all_contacts = []
    page = 1
    while True:
        batch, info = zoho_get_full("Contacts/search", {
            "criteria": f"(Account_Name:equals:{aid})",
            "fields": "Full_Name,id",
            "per_page": 200, "page": page
        })
        if not batch: break
        all_contacts.extend(batch)
        if not info.get("more_records", False): break
        page += 1

    if not all_contacts:
        return f"❌ לא נמצאו לקוחות - *{aname}*"

    # סרוק כל לקוח ובחר את הקובץ הטוב ביותר
    to_process = []   # [(contact, attachment, reason)]
    no_image = []     # [cname]
    cancel_flags.pop(from_number, None)  # נקה דגל ביטול ישן
    for contact in all_contacts:
        # בדוק אם המשתמש ביקש ביטול
        if cancel_flags.get(from_number):
            cancel_flags.pop(from_number, None)
            _send_reply(f"⛔ עיבוד פרופיל בוטל - *{aname}*", from_number)
            return f"⛔ בוטל על ידי המשתמש"
        cid = contact["id"]
        cname = contact.get("Full_Name", "")
        # בדוק אם כבר יש פרופיל קיים ב-Zoho - אם כן, דלג
        if cid not in skip_ids:
            photo_check = requests.get(f"{domain}/crm/v2/Contacts/{cid}/photo", headers=headers_z)
            if photo_check.status_code == 200:
                no_image.append(f"{cname} (כבר יש פרופיל)")
                time.sleep(0.1)
                continue
        else:
            no_image.append(f"{cname} (כבר עובד)")
            time.sleep(0.1)
            continue
        r = requests.get(f"{domain}/crm/v2/Contacts/{cid}/Attachments", headers=headers_z)
        if r.status_code == 200 and r.json().get("data"):
            atts = r.json()["data"]
            best_att, reason = _pick_best_attachment(atts, cname)
            if best_att:
                to_process.append((contact, best_att, reason))
            else:
                no_image.append(f"{cname} ({reason})")
        else:
            no_image.append(f"{cname} (אין קבצים)")
        time.sleep(0.1)

    # שלח סיכום לפני התחלה - רק מספרים, ללא רשימת קבצים
    total = len(all_contacts)
    will_update = len(to_process)
    will_skip = len(no_image)
    already_has = sum(1 for x in no_image if "כבר יש פרופיל" in x or "כבר עובד" in x)
    summary_lines = [
        f"🔍 *סיכום לפני עדכון פרופילים - {aname}*",
        "─" * 28,
        f"👥 סה\"כ לקוחות: {total}",
        f"🟢 יעודכנו: {will_update}",
        f"⏩ ידולגו (כבר יש פרופיל): {already_has}",
        f"⏩ ידולגו (אין תמונה): {will_skip - already_has}",
        f"\n⏳ מתחיל עיבוד {will_update} לקוחות...",
    ]
    _send_reply("\n".join(summary_lines), from_number)

    if not to_process:
        return f"❌ לא נמצאו תמונות בבית *{aname}*"

    updated = []
    failed = []

    used_att_ids = {}  # {contact_id: att_id} - מעקב אחר קבצים ששימשו

    for i, (contact, att, reason) in enumerate(to_process, 1):
        cname = contact.get("Full_Name", "")
        cid = contact["id"]

        r2 = requests.get(f"{domain}/crm/v2/Contacts/{cid}/Attachments/{att['id']}", headers=headers_z)
        if r2.status_code != 200:
            failed.append(cname)
            _send_reply(f"❌ [{i}/{len(to_process)}] {cname} - שגיאה בהורדת קובץ ({r2.status_code})", from_number)
            continue

        face_bytes, face_dbg = _crop_face_center(r2.content)
        if not face_bytes:
            failed.append(cname)
            _send_reply(f"❌ [{i}/{len(to_process)}] {cname} - חיתוך נכשל: {face_dbg[:60]}", from_number)
            continue

        try:
            photo_resp = requests.post(
                f"{domain}/crm/v2/Contacts/{cid}/photo",
                headers=headers_z,
                files={"file": ("profile.jpg", face_bytes, "image/jpeg")}
            )
            if photo_resp.status_code in [200, 201, 202]:
                updated.append(cname)
                used_att_ids[cid] = [att['id']]  # שמור את ה-attachment ששימש
                skip_ids.add(cid)
                _save_resume_state(aid, aname, from_number, skip_ids, len(to_process))
                _send_reply(f"✅ [{i}/{len(to_process)}] {cname} - פרופיל עודכן ({reason})", from_number)
            else:
                failed.append(cname)
                _send_reply(f"❌ [{i}/{len(to_process)}] {cname} - שגיאה ({photo_resp.status_code})", from_number)
        except Exception as e:
            failed.append(cname)
            _send_reply(f"❌ [{i}/{len(to_process)}] {cname} - שגיאה: {str(e)[:50]}", from_number)

        time.sleep(0.5)

    # סיכום סופי
    _clear_resume_state(aid)
    lines = [f"🏠 *סיכום סופי - פרופילים {aname}*", "─" * 28]
    lines.append(f"✅ עודכנו: {len(updated)}")
    if no_image:
        lines.append(f"⏩ דולגו (אין תמונה): {len(no_image)}")
    if failed:
        lines.append(f"❌ שגיאות: {len(failed)}")
        for n in failed:
            lines.append(f"  • {n}")
    return "\n".join(lines), used_att_ids

def _send_reply(reply: str, from_number: str, original_msg: str = ""):
    """שולח תשובה ל-WhatsApp, מפצל אוטומטית לכמה חלקים אם ארוך מדי."""
    quote = f"📩 \"{original_msg}\"\n─────────────\n" if original_msg else ""
    full_reply = quote + reply
    parts = split_message(full_reply)
    for i, part in enumerate(parts):
        twilio_client.messages.create(from_=TWILIO_WHATSAPP_FROM, to=from_number, body=part)
        if i < len(parts) - 1:
            time.sleep(0.5)


def handle_passport_image_upload(name_q: str, media_url: str, media_type: str, from_number: str) -> str:
    """
    מטפל בתמונת פספורט נכנסת:
    1. מחפש לקוח לפי שם
    2. אם כמה - שומר session ומבקש בחירה
    3. אם אחד - מעלה תמונה ל-Zoho + מחלץ שם + מעדכן Visa_Name1
    """
    # חיפוש לקוח
    contacts = _word_search_contacts(name_q)
    if not contacts:
        for word in name_q.split():
            if len(word) >= 2:
                contacts = _word_search_contacts(word, per_page=15)
                if contacts:
                    break
    if not contacts:
        return f"❓ לא מצאתי לקוח בשם *{name_q}* - נסה שם קצר יותר"

    if len(contacts) > 1:
        sessions[from_number] = {
            "pending": "choose_contact_passport_upload",
            "contacts": contacts,
            "name_q": name_q,
            "media_url": media_url,
            "media_type": media_type
        }
        return _format_contact_choice_menu(contacts, "העלאת פספורט")

    return _do_passport_upload_and_update(contacts[0], media_url, media_type)


def _do_passport_upload_and_update(contact: dict, media_url: str, media_type: str) -> str:
    """מוריד תמונה מ-Twilio, מעלה ל-Zoho, מחלץ שם ומעדכן Visa_Name1."""
    contact_id = contact["id"]
    contact_name = contact.get("Full_Name", "")
    token, domain = get_access_token()
    headers_z = {"Authorization": f"Zoho-oauthtoken {token}"}

    # הורד תמונה מ-Twilio (עם אימות)
    try:
        img_resp = requests.get(media_url,
                                auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
                                timeout=30)
        if img_resp.status_code != 200:
            return f"❌ שגיאה בהורדת התמונה (status {img_resp.status_code})"
        img_bytes = img_resp.content
    except Exception as e:
        return f"❌ שגיאה בהורדת התמונה: {e}"

    # קבע סיומת קובץ
    ext = "jpg"
    if "png" in media_type:
        ext = "png"
    elif "webp" in media_type:
        ext = "webp"
    file_name = f"פספורט.{ext}"

    # העלה ל-Zoho כ-attachment
    try:
        upload_resp = requests.post(
            f"{domain}/crm/v2/Contacts/{contact_id}/Attachments",
            headers=headers_z,
            files={"file": (file_name, img_bytes, media_type)}
        )
        print(f"Zoho upload status: {upload_resp.status_code} {upload_resp.text[:200]}")
        if upload_resp.status_code not in [200, 201]:
            return f"❌ שגיאה בהעלאת הקובץ ל-Zoho (status {upload_resp.status_code})"
    except Exception as e:
        return f"❌ שגיאה בהעלאת הקובץ: {e}"

    # חלץ שם באנגלית מהתמונה עם Gemini Vision
    import base64
    img_b64 = base64.b64encode(img_bytes).decode("utf-8")
    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": media_type, "data": img_b64}},
                {"text": "Look at this image. If it is a passport or official ID document, extract ONLY the full English name (given name + surname as printed). Return ONLY the name in UPPERCASE, nothing else. Format: FIRSTNAME LASTNAME. If the image is NOT a passport or official ID document (e.g. it is a photo of a person, a selfie, a document in a different language without Latin name, or any other non-ID image), return exactly the word: NONE"}
            ]
        }],
        "generationConfig": {"temperature": 0}
    }
    try:
        gr = requests.post(gemini_url, json=payload, timeout=30)
        if gr.status_code == 200:
            import re as _re
            extracted = gr.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            extracted_clean = _re.sub(r'[^A-Za-z\s]', '', extracted).strip().upper()
            _refusal_words = {"SORRY", "CANNOT", "UNABLE", "DOCUMENT", "IMAGE", "PASSPORT", "REQUEST", "EXTRACT", "PROVIDED", "THEREFORE", "FULFILL", "PHOTOGRAPH", "PERSON", "LOCATED", "NONE"}
            word_count = len(extracted_clean.split())
            has_refusal = any(w in extracted_clean.split() for w in _refusal_words)
            if extracted_clean and 1 <= word_count <= 4 and len(extracted_clean) <= 50 and not has_refusal:
                upd = requests.put(f"{domain}/crm/v2/Contacts",
                                   headers={**headers_z, "Content-Type": "application/json"},
                                   json={"data": [{"id": contact_id, "Visa_Name1": extracted_clean}]})
                if upd.status_code == 200 and upd.json().get("data", [{}])[0].get("code") == "SUCCESS":
                    return (f"✅ פספורט הועלה ושם ויזה עודכן!\n"
                            f"👤 {contact_name}\n"
                            f"🪪 {extracted_clean}\n"
                            f"📎 הקובץ נשמר בשם: {file_name}")
                return f"📎 הקובץ הועלה אך שגיאה בעדכון שם ויזה"
    except Exception as e:
        print(f"Gemini vision error in upload: {e}")

    return f"📎 הקובץ הועלה בשם {file_name} אך לא הצלחתי לחלץ שם"


# ─── Webhook ───────────────────────────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        incoming_msg = request.values.get("Body", "").strip()
        from_number  = request.values.get("From", "")
        num_media    = int(request.values.get("NumMedia", 0))
        print(f"=== WEBHOOK: msg='{incoming_msg}' from='{from_number}' NumMedia={num_media} ===")
        
        # Ensure from_number is in correct format
        from_number = from_number.replace(" ", "+")
        if from_number and not from_number.startswith("whatsapp:"):
            from_number = f"whatsapp:{from_number}"
        if "whatsapp:" in from_number and "+" not in from_number:
            from_number = from_number.replace("whatsapp:", "whatsapp:+")
        
        print(f"=== Fixed from_number: '{from_number}' ===")

        # === טיפול בתמונת פספורט נכנסת ===
        # פורמט: תמונה + כיתוב "פספורט [שם לקוח]"
        if num_media > 0 and incoming_msg.strip().startswith("פרופיל "):
            name_q = incoming_msg.strip()[len("פרופיל "):].strip()
            media_url = request.values.get("MediaUrl0", "")
            media_type = request.values.get("MediaContentType0", "image/jpeg")
            reply = handle_profile_image_upload(name_q, media_url, media_type, from_number)
            _send_reply(reply, from_number, incoming_msg)
            return str(MessagingResponse())

        if num_media > 0 and incoming_msg.strip().startswith("פספורט "):
            name_q = incoming_msg.strip()[len("פספורט "):].strip()
            media_url = request.values.get("MediaUrl0", "")
            media_type = request.values.get("MediaContentType0", "image/jpeg")
            reply = handle_passport_image_upload(name_q, media_url, media_type, from_number)
            _send_reply(reply, from_number, incoming_msg)
            return str(MessagingResponse())
        
        reply = handle_command(incoming_msg, from_number)
        
        # הוסף ציטוט של ההודעה המקורית בתחילת התשובה
        quote = f"📩 \"{incoming_msg}\"\n─────────────\n"
        full_reply = quote + reply
        
        # פצל הודעה ל-1400 תווים מקסימום (תמיכה בכמה חלקים)
        parts = split_message(full_reply)
        print(f"=== Reply: {len(full_reply)} chars, {len(parts)} part(s) ===")
        for i, part in enumerate(parts):
            twilio_client.messages.create(from_=TWILIO_WHATSAPP_FROM, to=from_number, body=part)
            if i < len(parts) - 1:
                import time as _time; _time.sleep(0.5)
        print(f"=== {len(parts)} message(s) sent successfully ===")
        return str(MessagingResponse())
    except Exception as e:
        print(f"=== WEBHOOK ERROR: {e} ===")
        try:
            resp = MessagingResponse()
            resp.message(f"❌ שגיאה: {str(e)[:100]}")
            return str(resp)
        except:
            return str(MessagingResponse()), 200

@app.route("/api/create_invoice_and_pay", methods=["POST"])
def create_invoice_and_pay_api():
    """
    Endpoint שנקרא מהתוכנה (checkbox) כשמסמנים לקוח כשולם.
    מקבל: { contact_name, payment_method }
    מבצע: מוצא לקוח ב-Zoho → יוצר חשבונית → מסמן כשולם → שולח WhatsApp
    """
    try:
        data = request.get_json(force=True)
        contact_name_raw = (data.get("contact_name") or "").strip()
        payment_method = (data.get("payment_method") or "מזומן").strip()
        if not contact_name_raw:
            return {"success": False, "error": "contact_name is required"}, 400
        # הסר ספרה מסוף השם (הספרה מציינת יום תשלום, לא קשורה לשם הלקוח)
        import re as _re
        contact_name = _re.sub(r'\s+\d+$', '', contact_name_raw).strip()
        if contact_name != contact_name_raw:
            print(f"Stripped trailing number: '{contact_name_raw}' → '{contact_name}'")
        print(f"=== create_invoice_and_pay: contact='{contact_name}' method='{payment_method}' ===")
        # 1. מצא את המוצר 'כרטיס 050 מקומי- קו פעיל'ל'
        products = find_product("כרטיס 050 מקומי קו פעיל")
        if not products:
            return {"success": False, "error": "Product 'כרטיס 050 מקומי- קו פעיל' not found"}, 404
        product = products[0]

        # 2. מצא את הלקוח ב-Zoho לפי שם
        contacts_raw = zoho_get("Contacts/search", {"word": contact_name,
                                                    "fields": "id,Full_Name,Account_Name"})
        if not contacts_raw:
            return {"success": False, "error": f"Contact '{contact_name}' not found in Zoho"}, 404

        # בחר את הלקוח הכי מתאים (התאמה מדויקת עדיפה)
        contact = next((c for c in contacts_raw if c.get("Full_Name", "").strip() == contact_name), contacts_raw[0])
        contact_id = contact["id"]
        acc_obj = contact.get("Account_Name", {})
        account_id = acc_obj.get("id") if isinstance(acc_obj, dict) else None

        if not account_id:
            return {"success": False, "error": f"No account (landlord) linked to contact '{contact_name}'"}, 400

        # 3. צור חשבונית
        price = product.get("Unit_Price", 0)
        inv_id = create_invoice(contact_id, account_id, product["id"], price, contact["Full_Name"], 1)
        if not inv_id:
            return {"success": False, "error": "Failed to create invoice in Zoho"}, 500

        # 4. סמן חשבונית כשולם
        mark_invoice_paid(inv_id, price, payment_method)

        # 5. שלח הודעת WhatsApp לאישור
        acc_name = acc_obj.get("name", "") if isinstance(acc_obj, dict) else ""
        msg = (f"✅ חשבונית נוצרה ושולמה!\n"
               f"👤 {contact['Full_Name']}\n"
               f"🏠 {acc_name}\n"
               f"📦 {product.get('Product_Name')}\n"
               f"💰 ₪{price} - {payment_method}")

        owner_number = os.environ.get("OWNER_WHATSAPP", "")
        if owner_number:
            try:
                twilio_client.messages.create(
                    from_=TWILIO_WHATSAPP_FROM,
                    to=f"whatsapp:{owner_number}",
                    body=msg
                )
            except Exception as e:
                print(f"WhatsApp notify error: {e}")

        print(f"=== create_invoice_and_pay SUCCESS: inv_id={inv_id} ===")
        return {"success": True, "invoice_id": inv_id, "contact": contact['Full_Name']}, 200

    except Exception as e:
        print(f"=== create_invoice_and_pay ERROR: {e} ===")
        return {"success": False, "error": str(e)}, 500


@app.route("/health")
def health():
    return "✅ Zoho WhatsApp Agent is running! (optimized)", 200

@app.route("/")
def index():
    return "✅ Zoho CRM WhatsApp Agent - Active (optimized)", 200

# ─── טעינת קאש מוצרים בהפעלה (ברקע) ─────────────────────────────────────────
def preload_cache():
    """טוען מוצרים ברקע כשהשרת עולה"""
    try:
        time.sleep(5)  # חכה שהשרת יעלה
        load_all_products()
    except Exception as e:
        print(f"Preload cache error: {e}")

# ─── Temp image store for WhatsApp media sending ─────────────────────────────
import uuid as _uuid
_temp_images = {}  # token -> (bytes, content_type, expires_at)

@app.route("/tmp_img/<token>")
def serve_temp_image(token):
    from flask import send_file as _send_file
    import io as _io
    entry = _temp_images.get(token)
    if not entry:
        return "Not found", 404
    img_bytes, content_type, expires_at = entry
    if time.time() > expires_at:
        _temp_images.pop(token, None)
        return "Expired", 410
    return _send_file(_io.BytesIO(img_bytes), mimetype=content_type)

def _store_temp_image(img_bytes: bytes, content_type: str = "image/jpeg", ttl: int = 300) -> str:
    """שמור תמונה זמנית ומחזיר URL ציבורי"""
    token = _uuid.uuid4().hex
    _temp_images[token] = (img_bytes, content_type, time.time() + ttl)
    railway_url = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if railway_url:
        base = f"https://{railway_url}"
    else:
        base = "https://web-production-12a94.up.railway.app"
    return f"{base}/tmp_img/{token}"

def _send_whatsapp_image(img_url: str, caption: str, to_number: str):
    """שלח תמונה בוואטסאפ עם כיתוב"""
    try:
        twilio_client.messages.create(
            from_=TWILIO_WHATSAPP_FROM,
            to=to_number,
            body=caption,
            media_url=[img_url]
        )
    except Exception as e:
        print(f"[SEND IMAGE] Error: {e}")
        _send_reply(f"⚠️ שגיאה בשליחת תמונה: {str(e)[:60]}", to_number)

# הפעל טעינת קאש ברקע
threading.Thread(target=preload_cache, daemon=True).start()

# הפעל תזמון דוח יומי ברקע (23:30 כל יום)
threading.Thread(target=_daily_report_scheduler, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
