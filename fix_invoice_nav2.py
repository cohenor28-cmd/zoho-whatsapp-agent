content = open('app.py', encoding='utf-8').read()

old = '''        sessions.pop(from_number, None)
        return None  # ימשיך לטיפול רגיל

    # === תשלום ללקוח דרך סטטוס בית: בחירת לקוח ===
    if pending'''

new = '''        # חשבונית חדשה מעמוד לקוח - זיהוי מילות מוצר
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

    # === תשלום ללקוח דרך סטטוס בית: בחירת לקוח ===
    if pending'''

if old in content:
    content = content.replace(old, new, 1)
    open('app.py', 'w', encoding='utf-8').write(content)
    print('Fix 2 OK')
else:
    print('NOT FOUND')
