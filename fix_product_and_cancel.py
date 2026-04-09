content = open('app.py', encoding='utf-8').read()

# ===========================
# Fix 1: In payment_invoice_choice - handle "0" as cancellation with report rebuild
# ===========================
old_pic = '''    if pending == "payment_invoice_choice":
        options = session.get("options", [])
        context = session.get("context", {})
        contact = context.get("contact", {})
        aname_pic = context.get("aname_session", "")
        account_id_pic = context.get("account_id_session", "")
        chosen = pick_best_match(options, message)
        if not chosen:
            lines = "\\n".join([f"{i+1}. {inv.get('Subject','')}" for i, inv in enumerate(options)])
            return f"לא הצלחתי לזהות. בחר מספר:\\n{lines}"'''

new_pic = '''    if pending == "payment_invoice_choice":
        options = session.get("options", [])
        context = session.get("context", {})
        contact = context.get("contact", {})
        aname_pic = context.get("aname_session", "")
        account_id_pic = context.get("account_id_session", "")
        # ביטול - החזר דוח בית
        if message.strip() in ["0", "ביטול", "cancel"]:
            sessions.pop(from_number, None)
            if aname_pic:
                try:
                    _acct_pic0 = {"id": account_id_pic, "Account_Name": aname_pic} if account_id_pic else None
                    _result_pic0 = build_landlord_report(aname_pic, account=_acct_pic0)
                    _rep_pic0 = _result_pic0[0]
                    _ord_pic0 = _result_pic0[1] if len(_result_pic0) > 1 else []
                    _rst_pic0 = _result_pic0[2] if len(_result_pic0) > 2 else []
                    _cids_pic0 = _result_pic0[3] if len(_result_pic0) > 3 else {}
                    _byc_pic0 = _result_pic0[4] if len(_result_pic0) > 4 else {}
                    _aln_pic0 = _result_pic0[5] if len(_result_pic0) > 5 else {}
                    if _ord_pic0:
                        sessions[from_number] = {"pending": "choose_landlord_contact",
                            "contacts": _ord_pic0, "rest": _rst_pic0,
                            "contact_ids": _cids_pic0, "by_contact": _byc_pic0,
                            "active_lines": _aln_pic0, "aname": aname_pic,
                            "account_id": account_id_pic}
                    return _rep_pic0
                except Exception as _e0:
                    print(f"Error rebuilding report on cancel: {_e0}")
            return "❌ בוטל"
        chosen = pick_best_match(options, message)
        if not chosen:
            lines = "\\n".join([f"{i+1}. {inv.get('Subject','')}" for i, inv in enumerate(options)])
            return f"לא הצלחתי לזהות. בחר מספר:\\n{lines}\\n0 לביטול"'''

if old_pic in content:
    content = content.replace(old_pic, new_pic, 1)
    print('Fix 1 OK - cancellation in payment_invoice_choice')
else:
    print('Fix 1 NOT FOUND')

# ===========================
# Fix 2: In choose_landlord_contact - detect product numbers (050, 019, 48, 155, etc.) 
# so "מאנה 050" is treated as invoice, not payment
# ===========================
# Product number patterns: 050, 019, 048, 155, 48, etc. - numbers that are product codes
# These should NOT be treated as payment amounts
old_m3 = '''        # תבנית 3: שם סכום (ללא תשלום): "שם 100" - רק אם השם מכיל אותיות עבריות
        _m3 = _re.match(r'^([\u05d0-\u05ea][\u05d0-\u05ea\s]+?)\s+(\d+(?:[.,]\d+)?)(?:\s+.*)?$', choice)'''

new_m3 = '''        # תבנית 3: שם סכום (ללא תשלום): "שם 100" - רק אם השם מכיל אותיות עבריות
        # אבל לא אם המספר הוא קוד מוצר (050, 019, 048, 155, 48, 3, 5 וכו' - מספרים קטנים או עם אפס מוביל)
        _m3 = _re.match(r'^([\u05d0-\u05ea][\u05d0-\u05ea\s]+?)\s+(\d+(?:[.,]\d+)?)(?:\s+.*)?$', choice)
        # בדוק אם המספר נראה כקוד מוצר ולא סכום תשלום
        _PRODUCT_CODE_PATTERN = _re.compile(r'^0\d+$|^[1-9]\d{0,1}$')  # 050, 019, 1-99 (כמות)
        if _m3:
            _m3_num = _m3.group(2)
            # אם המספר מתחיל ב-0 (כמו 050, 019) - זה קוד מוצר, לא סכום
            if _m3_num.startswith('0'):
                _m3 = None  # לא תשלום - ימשיך לזיהוי חשבונית'''

if old_m3 in content:
    content = content.replace(old_m3, new_m3, 1)
    print('Fix 2 OK - product code detection in _m3')
else:
    print('Fix 2 NOT FOUND')

open('app.py', 'w', encoding='utf-8').write(content)
