content = open('app.py', encoding='utf-8').read()

old = '''        # זיהוי פקודת חשבונית חדשה מתוך דוח בית
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

new = '''        # זיהוי פקודת חשבונית חדשה מתוך דוח בית
        # רק אם ההודעה מכילה מילת מוצר ספציפית - לא כל הודעה עם 2 מילים!
        _invoice_keywords = ["050", "סוויט", "תכלת", "בלוטוס", "מקל סלפי", "אוזניות", "רמקול",
                             "סוללה", "מטען", "שעון", "פלאפון", "מכשיר", "טאבלט", "מזגן",
                             "ראוטר", "כבל", "מגן", "מעמד", "מקלדת", "עכבר", "תיק", "פנס",
                             "מאוורר", "מקרן", "מקרר", "נרתיק", "גיטרה", "חשבונית"]
        _choice_lower = choice.lower()
        _is_invoice_cmd = any(kw in _choice_lower for kw in _invoice_keywords)
        if _is_invoice_cmd:
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
    print('OK - fixed invoice detection (only explicit keywords)')
else:
    print('NOT FOUND')
    idx = content.find('זיהוי פקודת חשבונית חדשה')
    print(repr(content[idx:idx+400]))
