content = open('app.py', encoding='utf-8').read()

# Replace the fallthrough line in choose_landlord_contact to detect invoice commands
old = '''        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(contacts):
                cname, cid = contacts[idx]
                sessions.pop(from_number, None)
                # בנה contact dict מינימלי ל-build_customer_status
                contact_obj = {"id": cid, "Full_Name": cname}
                status_text, aname, cid_s = build_customer_status(cname, contact=contact_obj)
                if aname:
                    sessions[from_number] = {"pending": "customer_status_nav", "aname": aname, "cid": cid_s, "cname": cname}
                return status_text
        return f"❓ כתוב מספר בין 1 ל-{len(contacts)} או 10 לכל הרשימה"'''

new = '''        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(contacts):
                cname, cid = contacts[idx]
                sessions.pop(from_number, None)
                # בנה contact dict מינימלי ל-build_customer_status
                contact_obj = {"id": cid, "Full_Name": cname}
                status_text, aname, cid_s = build_customer_status(cname, contact=contact_obj)
                if aname:
                    sessions[from_number] = {"pending": "customer_status_nav", "aname": aname, "cid": cid_s, "cname": cname}
                return status_text
        # זיהוי פקודת חשבונית חדשה מתוך דוח בית
        # אם ההודעה נראית כמו פקודת חשבונית (מכילה שם מוצר) - עבד אותה
        _invoice_keywords = ["050", "סוויט", "תכלת", "בלוטוס", "מקל סלפי", "אוזניות", "רמקול",
                             "סוללה", "מטען", "שעון", "פלאפון", "מכשיר", "טאבלט", "מזגן",
                             "ראוטר", "כבל", "מגן", "מעמד", "מקלדת", "עכבר", "תיק", "פנס",
                             "מאוורר", "מקרן", "מקרר", "נרתיק", "גיטרה", "חשבונית"]
        _choice_lower = choice.lower()
        _is_invoice_cmd = any(kw in _choice_lower for kw in _invoice_keywords)
        # גם אם ה-Gemini יזהה כ-create_invoice
        if _is_invoice_cmd or (len(choice.split()) >= 2 and not choice.isdigit()):
            # שמור את הסשן הנוכחי כדי לחזור אחרי החשבונית
            _saved_session = sessions.get(from_number, {}).copy()
            result = handle_command(choice, from_number)
            # אם הפקודה לא הצליחה לזהות - שחזר סשן
            if result and ("❓" in result or "לא הבנתי" in result):
                sessions[from_number] = _saved_session
                return f"❓ כתוב מספר בין 1 ל-{len(contacts)} או 10 לכל הרשימה"
            return result
        return f"❓ כתוב מספר בין 1 ל-{len(contacts)} או 10 לכל הרשימה"'''

if old in content:
    content = content.replace(old, new, 1)
    open('app.py', 'w', encoding='utf-8').write(content)
    print('OK - fixed invoice from landlord report')
else:
    print('NOT FOUND')
    idx = content.find('if choice.isdigit():')
    # find the one near line 2326
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if 'if choice.isdigit():' in line and 2320 <= i+1 <= 2340:
            print(f"Found at line {i+1}: {repr(line)}")
            print('\n'.join(lines[i:i+20]))
            break
