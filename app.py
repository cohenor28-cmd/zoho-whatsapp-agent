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

ZOHO_CLIENT_ID     = os.environ["ZOHO_CLIENT_ID"]
ZOHO_CLIENT_SECRET = os.environ["ZOHO_CLIENT_SECRET"]
ZOHO_REFRESH_TOKEN = os.environ["ZOHO_REFRESH_TOKEN"]
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
    return _token_cache["access_token"], domain

def zoho_get(endpoint, params=None):
    token, domain = get_access_token()
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    r = requests.get(f"{domain}/crm/v5/{endpoint}", headers=headers, params=params)
    if r.status_code in [200, 201]:
        return r.json().get("data", [])
    return []

def zoho_post(endpoint, data):
    token, domain = get_access_token()
    headers = {"Authorization": f"Zoho-oauthtoken {token}", "Content-Type": "application/json"}
    r = requests.post(f"{domain}/crm/v5/{endpoint}", headers=headers, json=data)
    return r.json()

def zoho_put(endpoint, data):
    token, domain = get_access_token()
    headers = {"Authorization": f"Zoho-oauthtoken {token}", "Content-Type": "application/json"}
    r = requests.put(f"{domain}/crm/v5/{endpoint}", headers=headers, json=data)
    return r.json()

# ─── CRM actions ───────────────────────────────────────────────────────────────
def find_contact_by_name_and_account(contact_name, account_name):
    contacts = zoho_get("Contacts/search", {"word": contact_name})
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
    return matches, accounts

def find_product(product_name):
    shortcuts = {"050": "2617612000002612066"}
    if product_name in shortcuts:
        data = zoho_get(f"Products/{shortcuts[product_name]}")
        return data if isinstance(data, list) else [data]
    return zoho_get("Products/search", {"word": product_name})

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
אתה עוזר חכם שמנתח פקודות בעברית ומחזיר JSON בלבד.

הפקודות האפשריות:
1. יצירת חשבונית: {"action": "create_invoice", "product": "...", "contact": "...", "account": "..."}
2. תשלום חשבונית: {"action": "payment", "contact": "...", "account": "...", "amount": 120, "method": "מזומן"}
3. עדכון סטטוס: {"action": "update_status", "contact": "...", "account": "...", "status": "paid"/"not_paid"}
4. שאילתת חשבוניות פתוחות: {"action": "query", "type": "open_invoices", "account": "..."}
5. לא מובן: {"action": "unknown"}

הערות:
- "שילם", "שולם", "שלם", "תשלום" = action: payment
- אמצעי תשלום: "מזומן", "העברה", "צ'ק", "אשראי" - ברירת מחדל "מזומן"
- contact = שם הלקוח, account = שם בעל הבית / מקום העבודה

דוגמאות:
- "050 לטייה של איציק" → {"action": "create_invoice", "product": "050", "contact": "טייה", "account": "איציק"}
- "טונגצאי בוי שער דוד שילם 120 מזומן" → {"action": "payment", "contact": "טונגצאי בוי", "account": "שער דוד", "amount": 120, "method": "מזומן"}
- "סומניק של אילן שלם 200 העברה" → {"action": "payment", "contact": "סומניק", "account": "אילן", "amount": 200, "method": "העברה"}
- "כמה חשבוניות פתוחות לאילן?" → {"action": "query", "type": "open_invoices", "account": "אילן"}

החזר JSON בלבד, ללא טקסט נוסף.
"""

def parse_intent(message):
    if not GEMINI_API_KEY:
        return {"action": "unknown"}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
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
        if r.status_code == 200:
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            # נקה markdown אם יש
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            return json.loads(text)
    except Exception as e:
        print(f"Gemini error: {e}")
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
    session = sessions.get(from_number, {})
    pending = session.get("pending")

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

    if action == "create_invoice":
        product_name = intent.get("product", "")
        contact_name = intent.get("contact", "")
        account_name = intent.get("account", "")
        products = find_product(product_name)
        if not products:
            return f"❌ לא מצאתי מוצר '{product_name}'"
        product = products[0]
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
    incoming_msg = request.values.get("Body", "").strip()
    from_number  = request.values.get("From", "")
    reply = handle_command(incoming_msg, from_number)
    twilio_client.messages.create(from_=TWILIO_WHATSAPP_FROM, to=from_number, body=reply)
    return str(MessagingResponse())

@app.route("/health")
def health():
    return "✅ Zoho WhatsApp Agent is running!", 200

@app.route("/")
def index():
    return "✅ Zoho CRM WhatsApp Agent - Active", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
