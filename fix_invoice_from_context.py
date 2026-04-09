content = open('app.py', encoding='utf-8').read()

# ===========================
# Fix 1: In choose_landlord_contact - when invoice command detected, inject aname_session
# ===========================
old_invoice_block = '''        if _is_invoice_cmd:
            # שמור את הסשן הנוכחי כדי לחזור אחרי החשבונית
            _saved_session = sessions.get(from_number, {}).copy()
            result = handle_command(choice, from_number)
            # אם הפקודה לא הצליחה לזהות - שחזר סשן
            if result and ("❓" in result or "לא הבנתי" in result):
                sessions[from_number] = _saved_session
                return f"❓ כתוב מספר בין 1 ל-{len(contacts)} או 10 לכל הרשימה"
            return result'''

new_invoice_block = '''        if _is_invoice_cmd:
            # שמור את הסשן הנוכחי כדי לחזור אחרי החשבונית
            _saved_session = sessions.get(from_number, {}).copy()
            # הזרק שם בית לפקודה אם לא מצוין (כי אנחנו כבר בדוח הבית)
            _inv_choice = choice
            if aname_session and aname_session.lower() not in choice.lower():
                _inv_choice = choice + " " + aname_session
            result = handle_command(_inv_choice, from_number)
            # אם הפקודה לא הצליחה לזהות - שחזר סשן
            if result and ("❓" in result or "לא הבנתי" in result):
                sessions[from_number] = _saved_session
                return f"❓ כתוב מספר בין 1 ל-{len(contacts)} או 10 לכל הרשימה"
            return result'''

if old_invoice_block in content:
    content = content.replace(old_invoice_block, new_invoice_block, 1)
    print('Fix 1 OK - inject aname into invoice cmd from landlord report')
else:
    print('Fix 1 NOT FOUND')

# ===========================
# Fix 2: In customer_status_nav - add invoice command support
# ===========================
# Find the block where customer_status_nav handles payment, and add invoice support before the fallthrough
old_nav_fallthrough = '''        sessions.pop(from_number, None)
        return None  # ימשיך לטיפול רגיל
    # === תשלום ללקוח דרך סטטוס בית: בחירת לקוח ==='''

new_nav_fallthrough = '''        # חשבונית חדשה מעמוד לקוח - זיהוי מילות מוצר
        _inv_kws_nav = ["050", "סוויט", "תכלת", "בלוטוס", "מקל סלפי", "אוזניות", "רמקול",
                        "סוללה", "מטען", "שעון", "פלאפון", "מכשיר", "טאבלט", "מזגן",
                        "ראוטר", "כבל", "מגן", "מעמד", "מקלדת", "עכבר", "תיק", "פנס",
                        "מאוורר", "מקרן", "מקרר", "נרתיק", "גיטרה", "חשבונית"]
        _nav_lower = msg_nav.lower()
        _is_inv_nav = any(kw in _nav_lower for kw in _inv_kws_nav)
        if _is_inv_nav:
            # הזרק שם לקוח ושם בית לפקודה אם לא מצוינים
            _inv_nav_cmd = msg_nav
            if cname_nav and cname_nav.lower() not in _nav_lower:
                _inv_nav_cmd = _inv_nav_cmd + " " + cname_nav
            if aname and aname.lower() not in _inv_nav_cmd.lower():
                _inv_nav_cmd = _inv_nav_cmd + " " + aname
            _saved_nav_session = sessions.get(from_number, {}).copy()
            result_nav = handle_command(_inv_nav_cmd, from_number)
            if result_nav and ("❓" in result_nav or "לא הבנתי" in result_nav):
                sessions[from_number] = _saved_nav_session
            return result_nav
        sessions.pop(from_number, None)
        return None  # ימשיך לטיפול רגיל
    # === תשלום ללקוח דרך סטטוס בית: בחירת לקוח ==='''

if old_nav_fallthrough in content:
    content = content.replace(old_nav_fallthrough, new_nav_fallthrough, 1)
    print('Fix 2 OK - invoice support in customer_status_nav')
else:
    print('Fix 2 NOT FOUND')
    idx = content.find('return None  # ימשיך לטיפול רגיל')
    print(f'idx={idx}')

open('app.py', 'w', encoding='utf-8').write(content)
