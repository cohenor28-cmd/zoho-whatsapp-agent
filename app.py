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

ZOHO_CLIENT_ID     = os.environ.get("ZOHO_CLIENT_ID", "1000.LPPMMUTV1XRWEJORHZSOHYCFO3D7LK")
ZOHO_CLIENT_SECRET = os.environ.get("ZOHO_CLIENT_SECRET", "04a1d9e45e722904e8816b703ce053eeb0215789b4")
ZOHO_REFRESH_TOKEN = os.environ.get("ZOHO_REFRESH_TOKEN", "1000.5ffe4ed572ee10f2a138cd9086836c4c.070f08b652f7d4c01f4a30bd4c66c972")
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

# מאגר כל המוצרים מ-Zoho (שם → ID)
PRODUCT_CATALOG = {
    'שעון - Galaxy Watch 8 Gps 44m': '2617612000096928023',
    'רמקול - Jbl Flip 7': '2617612000096924001',
    'ראוטר סלולרי- Cudy N300': '2617612000096484031',
    'רמקול - מיני דגם ZQS-1202': '2617612000096279001',
    'בלוטות - TWS BeeTech BT150': '2617612000096278001',
    'בלוטות - Fineblue Tws': '2617612000096260039',
    'מכשיר - Galaxy Tab A11+ Lte': '2617612000095825001',
    'מכשיר - Oppo Reno 15F': '2617612000095637001',
    'בלוטות - Besus Tws רונלייט': '2617612000095021047',
    'מתנה - שקית הגנה מפני מים': '2617612000094134006',
    'אופניים - Smart Bike V8 Bigfoot': '2617612000093893001',
    'שעון - Galaxy Watch 7 Lte 44m': '2617612000093677001',
    'רמקול- Jbl Partybox 520': '2617612000093645001',
    'בלוטות- JBL Tune 230Nc': '2617612000093484001',
    'מכשיר - Oppo Reno 14F': '2617612000092986001',
    'מכשיר - Rog': '2617612000092352001',
    'מכשיר - IPhone 16 Pro Max': '2617612000091971001',
    'מכשיר - IPhone 16 Pro': '2617612000091970001',
    'מכשיר - IPhone 16 Plus': '2617612000091969001',
    'מכשיר - IPhone 16': '2617612000091968001',
    'מכשיר - Galaxy S25 Ultra': '2617612000091967001',
    'מכשיר - Galaxy S25 Plus': '2617612000091966001',
    'מכשיר - Galaxy S25': '2617612000091965001',
    'מקל סלפי': '2617612000091964001',
    'כרטיס זיכרון': '2617612000091963001',
    'מגן מסך': '2617612000091962001',
    'כיסוי טלפון': '2617612000091961001',
    'כבל טעינה': '2617612000091960001',
    'אוזניות': '2617612000091959001',
    'מטען': '2617612000091958001',
    'סים - 050': '2617612000002612066',
    'סים - 050 במספר של 055': '2617612000052642106',
    'הוצאה קווים - רמי לוי': '2617612000052133001',
    'הוצאה קווים - סלקום': '2617612000066848001',
    'זיכוי': '2617612000065152044',
    'ביטוח רכב': '2617612000063949001',
    'מאוורר באפלו': '2617612000057293001',
    'מקרר גדול': '2617612000056978001',
    'תנור בנוי': '2617612000046544020',
    'מקרן - עגול עלי': '2617612000066263008',
    'מקלדת אלחוטית': '2617612000065230004',
    'מנעול לאופניים- 8 מ"מ': '2617612000065151022',
    'מראה- אופניים חשמליות': '2617612000054935039',
    'סוגר / חובק - אופניים חשמליות': '2617612000053143064',
    'ידית ברקס - אופניים חשמליות': '2617612000053118074',
    'צג אנלוגי- אופניים חשמליות': '2617612000053087176',
    'צג לאופניים - מסך גדול עלי': '2617612000066235013',
    'בקר לאופניים - 600W': '2617612000063695135',
    'בקר אופניים - 350W': '2617612000063615249',
    'מטען - לאופניים צאקיין 60V': '2617612000065152001',
    'סוללה לאופניים חשמליות- 48V 15A': '2617612000060436195',
    'סוללה לאופניים חשמליות- 48V 20a': '2617612000060433097',
    'סוללה - אופניים חשמליות 48v15A': '2617612000053544157',
    'אופניים - סיירה חצי עבה Siera': '2617612000058717001',
    'אופניים - סיירה קטן 770': '2617612000065580001',
    'אופניים 4 - BMX צארומי': '2617612000055065001',
    'אופניים - Apex 1 Stark': '2617612000048657099',
    'אופניים - Smart Bike V8 Bigfoot': '2617612000093893001',
    'מזגן נייד - Prosonic דגם 3': '2617612000062462052',
    'מזגן נייד - Peerless דגם 2': '2617612000061769045',
    'מזגן נייד - Tornado 14': '2617612000059557031',
    'סיגריה אלקטרונית חד פעמית - 15,000': '2617612000060436134',
    'סיגריה אלקטרונית- חד פעמי 12,000': '2617612000067138002',
    'ציפוי חלונות לרכב': '2617612000066397001',
    'מטען נייד - 5 אלף נדבק': '2617612000065871037',
    'מטען נייד 10 אלף -Aspor A322': '2617612000060922001',
    'מטען נייד 10 אלף - Pb51': '2617612000060921001',
    'מטען נייד 20 אלף - PB51': '2617612000060920001',
    'מטען נייד 20,000 - Demaco': '2617612000051806001',
    'מתאם - IPhone לטעינה וAux': '2617612000051805001',
    'מתאם - IPhone לאוזניה Aux': '2617612000051768004',
    'מתאם חיבור - לHDMI': '2617612000051767023',
    'מטען - ראש וכבל 25W סמסונג': '2617612000068463072',
    'מטען - ראש מהיר 45W': '2617612000067542001',
    'משטח טעינה - Eco wch 430': '2617612000068968001',
    'שעון - קאסיו פשוט Casio': '2617612000053170058',
    'בלוטות - Airtag Apple': '2617612000061786001',
    'בלוטות - רולר BeeTech H15': '2617612000061785001',
    'בלוטות - Airpods Pro 2 Joy': '2617612000061180001',
    'בלוטות - Airpords 2 Joy': '2617612000061179001',
    'בלוטות - Lenovo LivepodsLP10': '2617612000056250037',
    'בלוטות - Jbl Wave 300': '2617612000053087181',
    'בלוטות- Oppo Buds 2': '2617612000057213001',
    'בלוטות - רולר Beetech H15': '2617612000060923001',
    'בלוטוס - JBL Tune Beam Flex': '2617612000057298001',
    'בלוטוס - קשת Max Pro': '2617612000064724001',
    'בלוטוס- אוזניות ספורט L17B': '2617612000064682028',
    'בלוטות קשת - Xo be36': '2617612000068971001',
    'בלוטות רולר - Bpower': '2617612000068970001',
    'בלוטות - Besus Tws רונלייט': '2617612000095021047',
    'בלוטות - TWS BeeTech BT150': '2617612000096278001',
    'בלוטות - Fineblue Tws': '2617612000096260039',
    'בלוטות- JBL Tune 230Nc': '2617612000093484001',
    'רמקול - Jbl Encore': '2617612000054282001',
    'רמקול - Jbl Encore + Mic': '2617612000048499001',
    'רמקול - Jbl Partybox 710': '2617612000067358042',
    'רמקול- Jbl Partybox 520': '2617612000093645001',
    'רמקול - Jbl Flip 7': '2617612000096924001',
    'רמקול - מיני דגם ZQS-1202': '2617612000096279001',
    'טאבלט - Blackview Tab 80': '2617612000060346001',
    'טאבלט - Galaxy Tab A9 Plus 4G': '2617612000060318028',
    'טאבלט - Samsung S9 Ultra': '2617612000052583001',
    'טאבלט - Galaxy S8 Ultra': '2617612000046345001',
    'טאבלט - Silverline 1081': '2617612000068342082',
    'טאבלט - Ipad mini 8.3 wifi': '2617612000068313088',
    'מכשיר - Galaxy S22 Ultra': '2617612000061178001',
    'מכשיר - Galaxy S23 Ultra': '2617612000057226185',
    'מכשיר - Galaxy S24 Ultra': '2617612000053567042',
    'מכשיר - Galaxy S24 Plus 256Gb': '2617612000061172022',
    'מכשיר - Galaxy A55': '2617612000061170002',
    'מכשיר - Galaxy A15': '2617612000055873007',
    'מכשיר - Galaxy S21 Plus': '2617612000048486005',
    'מכשיר - IPhone 15 Plus 256Gb': '2617612000061176006',
    'מכשיר - IPhone 15 Plus 128Gb': '2617612000061176001',
    'מכשיר - IPhone 15 Pro Max 256': '2617612000061175001',
    'מכשיר - IPhone 15 128Gb': '2617612000050954026',
    'מכשיר - IPhone 14 Plus': '2617612000053598034',
    'מכשיר - IPhone 14 Pro Max': '2617612000048437055',
    'מכשיר - IPhone 13 Pro Max': '2617612000058716056',
    'מכשיר - IPhone 12 Pro Max': '2617612000052728001',
    'מכשיר - IPhone XS Max': '2617612000046608039',
    'מכשיר - Oppo Reno 10 5G': '2617612000056081028',
    'מכשיר - Oppo Reno 11F': '2617612000057211001',
    'מכשיר - Oppo Reno 12F': '2617612000064832123',
    'מכשיר - Oppo Reno 14F': '2617612000092986001',
    'מכשיר - Oppo Reno 15F': '2617612000095637001',
    'מכשיר - Oppo A18': '2617612000053055001',
    'מכשיר - OnePlus 11 5G 256Gb': '2617612000052832163',
    'מכשיר -OnePlus N30 5G': '2617612000059510031',
    'מכשיר - Redmi Note 12 Pro': '2617612000046849082',
    'מכשיר - Vivo Y76 5G': '2617612000068342077',
    'מכשיר - Vivo Y21': '2617612000068254059',
    'מכשיר - Galaxy Tab A11+ Lte': '2617612000095825001',
    'מכשיר - Rog': '2617612000092352001',
    'מכשיר - IPhone 16 Pro Max': '2617612000091971001',
    'מכשיר - IPhone 16 Pro': '2617612000091970001',
    'מכשיר - IPhone 16 Plus': '2617612000091969001',
    'מכשיר - IPhone 16': '2617612000091968001',
    'מכשיר - Galaxy S25 Ultra': '2617612000091967001',
    'מכשיר - Galaxy S25 Plus': '2617612000091966001',
    'מכשיר - Galaxy S25': '2617612000091965001',
}

def find_product(product_name):
    """חיפוש מוצר לפי שם - תחילה התאמה מדויקת, אחר כך חלקית"""
    if not product_name:
        return []
    
    product_name_lower = product_name.strip().lower()
    
    # 1. התאמה מדויקת מהמאגר המקומי
    for name, pid in PRODUCT_CATALOG.items():
        if name.lower() == product_name_lower:
            data = zoho_get(f"Products/{pid}", {"fields": "Product_Name,Unit_Price,id"})
            return [data] if isinstance(data, dict) else (data if data else [])
    
    # 2. התאמה חלקית מהמאגר המקומי (המוצר מכיל את מה שנשלח)
    matches = []
    for name, pid in PRODUCT_CATALOG.items():
        if product_name_lower in name.lower() or name.lower() in product_name_lower:
            matches.append((name, pid))
    
    if matches:
        # קח את ההתאמה הקצרה ביותר (הכי ספציפית)
        matches.sort(key=lambda x: len(x[0]))
        name, pid = matches[0]
        print(f"Product fuzzy match: '{product_name}' → '{name}' ({pid})")
        data = zoho_get(f"Products/{pid}", {"fields": "Product_Name,Unit_Price,id"})
        return [data] if isinstance(data, dict) else (data if data else [])
    
    # 3. חיפוש ב-Zoho API (גיבוי)
    print(f"Product not in catalog, searching Zoho API for: '{product_name}'")
    return zoho_get("Products/search", {"word": product_name, "fields": "Product_Name,Unit_Price,id"})

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
אתה עוזר חכם שמנתח פקודות קצרות בעברית ומחזיר JSON בלבד. אסור לך לשאול שאלות - תמיד תחזיר JSON.

הפורמט הנפוץ ביותר הוא: [מוצר] [שם לקוח] [שם בעל בית/מקום]
לדוגמה: "050 סוויט אילן" = מוצר 050 סוויט, לקוח לא ידוע, בעל בית אילן.

כלל חשוב: אם ההודעה מכילה שם מוצר (כמו 050, סוביט, סוויט, כרטיס, מבטחים) - זו תמיד יצירת חשבונית!

הפקודות האפשריות:
1. יצירת חשבונית: {"action": "create_invoice", "product": "...", "contact": "...", "account": "..."}
2. תשלום חשבונית: {"action": "payment", "contact": "...", "account": "...", "amount": 120, "method": "מזומן"}
3. שאילתת חשבוניות פתוחות: {"action": "query", "type": "open_invoices", "account": "..."}
4. לא מובן: {"action": "unknown"}

כללים:
- "שילם", "שולם", "שלם", "תשלום", "מזומן" בלי מוצר = action: payment
- אם יש שם מוצר בהודעה = תמיד action: create_invoice
- contact = שם הלקוח הספציפי (אם לא ברור - שים "")
- account = שם בעל הבית / מקום העבודה / הנכס
- אמצעי תשלום: "מזומן", "העברה", "צ'ק", "אשראי" - ברירת מחדל "מזומן"

דוגמאות ליצירת חשבונית:
- "050 סוויט אילן" → {"action": "create_invoice", "product": "050 סוויט", "contact": "", "account": "אילן"}
- "050 לטייה של איציק" → {"action": "create_invoice", "product": "050", "contact": "טייה", "account": "איציק"}
- "סוביט אילן שילם 120 מזומן" → {"action": "payment", "contact": "אילן", "account": "", "amount": 120, "method": "מזומן"}
- "כרטיס 050 גדול אידיאל" → {"action": "create_invoice", "product": "כרטיס 050 גדול", "contact": "", "account": "אידיאל"}
- "מבטחים שעה אילן" → {"action": "create_invoice", "product": "מבטחים שעה", "contact": "", "account": "אילן"}

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
        print(f"Gemini response: {r.text[:500]}")
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
            print(f"Gemini error response: {r.text}")
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
