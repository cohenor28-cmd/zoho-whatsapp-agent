import os
import json
import requests
from datetime import date
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client

app = Flask(__name__)

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

# ─── In-memory token cache ─────────────────────────────────────────────────────
_token_cache = {"access_token": os.environ.get("ZOHO_ACCESS_TOKEN", ""), "api_domain": ZOHO_API_DOMAIN}

# ─── Session memory (per phone number) ────────────────────────────────────────
sessions = {}

# ─── Zoho helpers ──────────────────────────────────────────────────────────────
def get_access_token():
    token = _token_cache.get("access_token", "")
    domain = _token_cache.get("api_domain", ZOHO_API_DOMAIN)
    if token:
        headers = {"Authorization": f"Zoho-oauthtoken {token}"}
        test = requests.get(f"{domain}/crm/v5/users?type=CurrentUser", headers=headers)
        if test.status_code != 401:
            return token, domain
    # Refresh
    r = requests.post("https://accounts.zoho.com/oauth/v2/token", params={
        "grant_type": "refresh_token",
        "client_id": ZOHO_CLIENT_ID,
        "client_secret": ZOHO_CLIENT_SECRET,
        "refresh_token": ZOHO_REFRESH_TOKEN
    })
    if r.status_code == 200 and "access_token" in r.json():
        _token_cache["access_token"] = r.json()["access_token"]
        print(f"Token refreshed successfully")
    else:
        print(f"Token refresh failed: {r.status_code} {r.text[:200]}")
    return _token_cache["access_token"], domain

def zoho_get(endpoint, params=None):
    token, domain = get_access_token()
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    r = requests.get(f"{domain}/crm/v5/{endpoint}", headers=headers, params=params)
    print(f"zoho_get {endpoint} status={r.status_code}")
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

def zoho_put(endpoint, data):
    token, domain = get_access_token()
    headers = {"Authorization": f"Zoho-oauthtoken {token}", "Content-Type": "application/json"}
    r = requests.put(f"{domain}/crm/v5/{endpoint}", headers=headers, json=data)
    print(f"zoho_put {endpoint} status={r.status_code}")
    return r.json()

# ─── CRM actions ───────────────────────────────────────────────────────────────
def find_contact_by_name_and_account(contact_name, account_name):
    contacts = zoho_get("Contacts/search", {"word": contact_name}) if contact_name else []
    accounts = zoho_get("Accounts/search", {"word": account_name}) if account_name else []
    account_ids = [a["id"] for a in accounts]
    matches = []
    for c in contacts:
        c_acc = c.get("Account_Name")
        if account_name:
            if c_acc and c_acc.get("id") in account_ids:
                matches.append(c)
        else:
            matches.append(c)
    print(f"find_contact: '{contact_name}' @ '{account_name}' → {len(matches)} matches, {len(accounts)} accounts")
    return matches, accounts

def find_product(product_name):
    """חיפוש מוצר לפי שם ב-Zoho API - תומך בחיפוש חלקי"""
    if not product_name:
        print("find_product: empty product name")
        return []

    print(f"find_product: searching for '{product_name}'")
    product_lower = product_name.strip().lower()

    # חיפוש ישיר ב-Zoho לפי שם
    results = zoho_get("Products/search", {"word": product_name, "fields": "Product_Name,Unit_Price,id"})
    if results:
        # סינון - רק מוצרים שמכילים את כל המילים שהמשתמש כתב
        words = product_lower.split()
        filtered = []
        for p in results:
            pname = p.get("Product_Name", "").lower()
            if all(w in pname for w in words):
                filtered.append(p)
        # אם יש התאמה מדויקת - החזר אותה
        if filtered:
            print(f"find_product: filtered to {len(filtered)} results for '{product_name}'")
            return filtered
        # אם אין סינון מדויק - החזר הכל
        print(f"find_product: found {len(results)} results for '{product_name}' (no exact filter match, returning all)")
        return results

    # אם לא נמצא - נסה חיפוש עם מילה ראשונה בלבד
    words = product_name.strip().split()
    if len(words) > 1:
        first_word = words[0]
        print(f"find_product: retry with first word '{first_word}'")
        results2 = zoho_get("Products/search", {"word": first_word, "fields": "Product_Name,Unit_Price,id"})
        if results2:
            # סנן לפי כל המילים
            filtered2 = [p for p in results2 if all(w in p.get("Product_Name", "").lower() for w in product_lower.split())]
            if filtered2:
                return filtered2
            # אם אין - חפש התאמה חלקית
            for p in results2:
                pname = p.get("Product_Name", "").lower()
                if product_lower in pname or pname in product_lower:
                    print(f"find_product: matched '{p.get('Product_Name')}' via first-word search")
                    return [p]
            print(f"find_product: first-word search returned {len(results2)} results")
            return results2

    print(f"find_product: no results for '{product_name}'")
    return []

def find_open_invoices_for_contact(contact_name):
    invoices = zoho_get("Invoices/search", {"word": contact_name,
                                             "fields": "Subject,Status,Grand_Total,Contact_Name,Account_Name"})
    return [i for i in invoices if i.get("Status") in ["לא שולם", None, ""]]

def mark_invoice_paid(invoice_id, amount, method):
    method_label = method if method else "מזומן"
    note = f"שולם ₪{amount} ב{method_label}"
    result = zoho_put(f"Invoices/{invoice_id}", {"data": [{
        "id": invoice_id,
        "Status": "שולם",
        "Description": note
    }]})
    return result.get("data", [{}])[0].get("code") == "SUCCESS"

def create_invoice(contact_id, account_id, product_id, price, contact_name):
    today = date.today().strftime("%Y-%m-%d")
    payload = {"data": [{
        "Subject": f"חשבונית - {contact_name} - {today}",
        "Account_Name": {"id": account_id},
        "Contact_Name": {"id": contact_id},
        "Invoiced_Date": today,
        "Status": "לא שולם",
        "Invoiced_Items": [{
            "Product_Name": {"id": product_id},
            "Quantity": 1,
            "List_Price": price
        }]
    }]}
    result = zoho_post("Invoices", payload)
    print(f"create_invoice result: {json.dumps(result, ensure_ascii=False)[:300]}")
    if result.get("data") and result["data"][0].get("code") == "SUCCESS":
        return result["data"][0]["details"]["id"]
    return None

def build_invoice_confirmation(contact, product):
    acc_name = contact.get("Account_Name", {}).get("name", "") if isinstance(contact.get("Account_Name"), dict) else ""
    return (f"✅ חשבונית נוצרה!\n"
            f"👤 {contact['Full_Name']}\n"
            f"🏠 {acc_name}\n"
            f"📦 {product.get('Product_Name')}\n"
            f"💰 ₪{product.get('Unit_Price', 0)} | לא שולם")

# ─── AI intent parser (Google Gemini) ─────────────────────────────────────────
SYSTEM_PROMPT = """
אתה עוזר חכם שמנתח פקודות קצרות בעברית ומחזיר JSON בלבד. אסור לך לשאול שאלות - תמיד תחזיר JSON.

הפורמט הנפוץ ביותר הוא: [מוצר] [שם לקוח] [שם בעל בית/מקום]
לדוגמה: "050 סוויט אילן" = מוצר 050 סוויט, לקוח לא ידוע, בעל בית אילן.

כלל חשוב: אם ההודעה מכילה שם מוצר (כמו 050, סוביט, סוויט, כרטיס, מקל סלפי, בלוטוס, מכשיר, אופניים, טאבלט, רמקול, מזגן, סוללה, מטען, שעון) - זו תמיד יצירת חשבונית!

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
1. יצירת חשבונית: {"action": "create_invoice", "product": "...", "contact": "...", "account": "..."}
2. תשלום חשבונית: {"action": "payment", "contact": "...", "account": "...", "amount": 120, "method": "מזומן"}
3. שאילתת חשבוניות פתוחות: {"action": "query", "type": "open_invoices", "account": "..."}
4. לא מובן: {"action": "unknown"}

כללים:
- "שילם", "שולם", "שלם", "תשלום", "מזומן" בלי מוצר = action: payment
- אם יש שם מוצר בהודעה = תמיד action: create_invoice
- contact = שם הלקוח הספציפי (אם לא ברור - שים "")
- account = שם בעל הבית / מקום העבודה / הנכס (השם המקוצר כפי שמופיע ברשימה למעלה)
- אמצעי תשלום: "מזומן", "העברה", "צ'ק", "אשראי" - ברירת מחדל "מזומן"

דוגמאות ליצירת חשבונית:
- "050 סוויט אילן" → {"action": "create_invoice", "product": "050 סוויט", "contact": "", "account": "אילן"}
- "050 לטייה של איציק" → {"action": "create_invoice", "product": "050", "contact": "טייה", "account": "איציק"}
- "כרטיס 050 גדול אידיאל" → {"action": "create_invoice", "product": "כרטיס 050 גדול", "contact": "", "account": "אידיאל"}
- "מקל סלפי טונגצאי שער דוד" → {"action": "create_invoice", "product": "מקל סלפי", "contact": "טונגצאי", "account": "שער דוד"}
- "בלוטוס JBL דורון" → {"action": "create_invoice", "product": "בלוטוס JBL", "contact": "", "account": "דורון"}
- "מכשיר גלקסי רני ישע" → {"action": "create_invoice", "product": "מכשיר גלקסי", "contact": "רני", "account": "ישע"}

דוגמאות לתשלום:
- "טונגצאי בוי שער דוד שילם 120 מזומן" → {"action": "payment", "contact": "טונגצאי בוי", "account": "שער דוד", "amount": 120, "method": "מזומן"}
- "סוביט אילן שילם 120 מזומן" → {"action": "payment", "contact": "", "account": "אילן", "amount": 120, "method": "מזומן"}
- "כמה חשבוניות פתוחות לאילן?" → {"action": "query", "type": "open_invoices", "account": "אילן"}

החזר JSON בלבד, ללא טקסט נוסף, ללא הסברים.
"""

def parse_intent(message):
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
        r = requests.post(url, json=payload, timeout=15)
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
    sessions[from_number] = {
        "pending": "payment_invoice_choice",
        "options": open_invoices,
        "context": {"contact": contact, "amount": amount, "method": method}
    }
    lines = "\n".join([f"{i+1}. {inv.get('Subject','')} - ₪{inv.get('Grand_Total',0)}"
                       for i, inv in enumerate(open_invoices)])
    return f"מצאתי {len(open_invoices)} חשבוניות פתוחות:\n{lines}\n\nאיזו לסמן כשולם?"

# ─── Main handler ──────────────────────────────────────────────────────────────
def handle_command(message, from_number):
    print(f"handle_command: '{message}' from {from_number}")
    session = sessions.get(from_number, {})
    pending = session.get("pending")

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
        contacts, accounts = find_contact_by_name_and_account(contact_name, account_name)
        if not contacts:
            return f"❌ לא מצאתי לקוח '{contact_name}' אצל '{account_name}'"
        if len(contacts) > 1:
            sessions[from_number] = {"pending": "contact_choice", "options": contacts, "context": {"product": product}}
            names = "\n".join([f"{i+1}. {c['Full_Name']}" for i, c in enumerate(contacts)])
            return f"מצאתי כמה לקוחות:\n{names}\n\nכתוב חלק מהשם או מספר לבחירה:"
        contact = contacts[0]
        acc_id = contact.get("Account_Name", {}).get("id") if isinstance(contact.get("Account_Name"), dict) else None
        if not acc_id and accounts:
            acc_id = accounts[0]["id"]
        inv_id = create_invoice(contact["id"], acc_id, product["id"], product.get("Unit_Price", 0), contact["Full_Name"])
        return build_invoice_confirmation(contact, product) if inv_id else "❌ שגיאה ביצירת החשבונית"

    if pending == "contact_choice":
        options = session["options"]
        context = session["context"]
        product = context["product"]
        chosen = pick_best_match(options, message)
        if not chosen:
            names = "\n".join([f"{i+1}. {c['Full_Name']}" for i, c in enumerate(options)])
            return f"לא הצלחתי לזהות. בחר מספר:\n{names}"
        sessions.pop(from_number, None)
        acc_id = chosen.get("Account_Name", {}).get("id") if isinstance(chosen.get("Account_Name"), dict) else None
        inv_id = create_invoice(chosen["id"], acc_id, product["id"], product.get("Unit_Price", 0), chosen["Full_Name"])
        return build_invoice_confirmation(chosen, product) if inv_id else "❌ שגיאה ביצירת החשבונית"

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
            return (f"✅ תשלום עודכן!\n"
                    f"👤 {contact['Full_Name']}\n"
                    f"🏠 {acc_name}\n"
                    f"💰 ₪{pay_amount} | {pay_method}\n"
                    f"📄 {chosen.get('Subject', '')}")
        return "❌ שגיאה בעדכון התשלום"

    intent = parse_intent(message)
    action = intent.get("action")
    print(f"action={action}, intent={intent}")

    if action == "create_invoice":
        product_name = intent.get("product", "")
        contact_name = intent.get("contact", "")
        account_name = intent.get("account", "")

        products = find_product(product_name)
        if not products:
            return f"❌ לא מצאתי מוצר '{product_name}'"

        # אם יש יותר ממוצר אחד - הצג רשימה לבחירה
        if len(products) > 1:
            # הגבל ל-10 תוצאות
            show = products[:10]
            sessions[from_number] = {
                "pending": "product_choice",
                "options": show,
                "context": {"contact_name": contact_name, "account_name": account_name}
            }
            lines = [f"{i+1}. {p.get('Product_Name', '')} - ₪{p.get('Unit_Price', 0)}" for i, p in enumerate(show)]
            extra = f"\n... ועוד {len(products) - 10}" if len(products) > 10 else ""
            return f"🔍 מצאתי {len(products)} מוצרים עבור '{product_name}':\n" + "\n".join(lines) + extra + "\n\nכתוב מספר לבחירה:"

        product = products[0]
        print(f"Product found: {product.get('Product_Name')} id={product.get('id')} price={product.get('Unit_Price')}")

        contacts, accounts = find_contact_by_name_and_account(contact_name, account_name)
        if not contacts:
            return f"❌ לא מצאתי לקוח '{contact_name}' אצל '{account_name}'"
        if len(contacts) > 1:
            sessions[from_number] = {"pending": "contact_choice", "options": contacts, "context": {"product": product}}
            names = "\n".join([f"{i+1}. {c['Full_Name']}" for i, c in enumerate(contacts)])
            return f"מצאתי כמה לקוחות:\n{names}\n\nכתוב חלק מהשם או מספר לבחירה:"
        contact = contacts[0]
        acc_id = contact.get("Account_Name", {}).get("id") if isinstance(contact.get("Account_Name"), dict) else None
        if not acc_id and accounts:
            acc_id = accounts[0]["id"]
        inv_id = create_invoice(contact["id"], acc_id, product["id"], product.get("Unit_Price", 0), contact["Full_Name"])
        return build_invoice_confirmation(contact, product) if inv_id else "❌ שגיאה ביצירת החשבונית"

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
            "• 'כמה חשבוניות פתוחות לאילן?' - שאילתה")

# ─── Webhook ───────────────────────────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        incoming_msg = request.values.get("Body", "").strip()
        from_number  = request.values.get("From", "")
        print(f"=== WEBHOOK: msg='{incoming_msg}' from='{from_number}' ===")
        
        # Ensure from_number is in correct format
        # URL encoding turns + into space, fix it
        from_number = from_number.replace(" ", "+")
        if from_number and not from_number.startswith("whatsapp:"):
            from_number = f"whatsapp:{from_number}"
        if "whatsapp:" in from_number and "+" not in from_number:
            # Fix missing + sign
            from_number = from_number.replace("whatsapp:", "whatsapp:+")
        
        print(f"=== Fixed from_number: '{from_number}' ===")
        
        reply = handle_command(incoming_msg, from_number)
        print(f"=== Reply: '{reply[:100]}' ===")
        
        twilio_client.messages.create(from_=TWILIO_WHATSAPP_FROM, to=from_number, body=reply)
        print(f"=== Message sent successfully ===")
        return str(MessagingResponse())
    except Exception as e:
        print(f"=== WEBHOOK ERROR: {e} ===")
        # Try to send error message back
        try:
            resp = MessagingResponse()
            resp.message(f"❌ שגיאה: {str(e)[:100]}")
            return str(resp)
        except:
            return str(MessagingResponse()), 200

@app.route("/health")
def health():
    return "✅ Zoho WhatsApp Agent is running!", 200

@app.route("/")
def index():
    return "✅ Zoho CRM WhatsApp Agent - Active", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
