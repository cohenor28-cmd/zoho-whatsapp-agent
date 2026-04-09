content = open('app.py', encoding='utf-8').read()
fixes = 0

# Fix: in choose_landlord_contact single-payment block,
# if _process_payment_for_contact set payment_invoice_choice session, return immediately
old1 = '''            if _matched_contact:
                _cname_pay, _cid_pay = _matched_contact
                _pay_amount = float(_pay_amount_str.replace(',', '.'))
                _contact_obj = {"id": _cid_pay, "Full_Name": _cname_pay, "Account_Name": {"name": aname_session}}
                _pay_result = _process_payment_for_contact(_contact_obj, _pay_amount, _pay_method_inline, from_number)
                # שלח אישור תשלום מיד
                twilio_client.messages.create(
                    from_=TWILIO_WHATSAPP_FROM,
                    to=f"whatsapp:{from_number.replace('whatsapp:', '')}",
                    body=_pay_result)
                # עדכן דוח בית - סינכרוני, ללא thread
                try:
                    import time as _time_s; _time_s.sleep(2)  # המתן לZoho לעדכן
                    _account_obj = {"id": account_id_session, "Account_Name": aname_session} if account_id_session else None
                    _result = build_landlord_report(aname_session, account=_account_obj)'''

new1 = '''            if _matched_contact:
                _cname_pay, _cid_pay = _matched_contact
                _pay_amount = float(_pay_amount_str.replace(',', '.'))
                _contact_obj = {"id": _cid_pay, "Full_Name": _cname_pay, "Account_Name": {"name": aname_session}}
                _pay_result = _process_payment_for_contact(_contact_obj, _pay_amount, _pay_method_inline, from_number)
                # אם נוצר סשן payment_invoice_choice - יש כמה חשבוניות, החזר שאלה מיד
                if sessions.get(from_number, {}).get("pending") == "payment_invoice_choice":
                    return _pay_result
                # שלח אישור תשלום מיד
                twilio_client.messages.create(
                    from_=TWILIO_WHATSAPP_FROM,
                    to=f"whatsapp:{from_number.replace('whatsapp:', '')}",
                    body=_pay_result)
                # עדכן דוח בית - סינכרוני, ללא thread
                try:
                    import time as _time_s; _time_s.sleep(2)  # המתן לZoho לעדכן
                    _account_obj = {"id": account_id_session, "Account_Name": aname_session} if account_id_session else None
                    _result = build_landlord_report(aname_session, account=_account_obj)'''

if old1 in content:
    content = content.replace(old1, new1, 1)
    fixes += 1
    print('Fix 1 OK - check payment_invoice_choice session before building report (single payment)')
else:
    print('Fix 1 NOT FOUND')

# Also fix the payment_invoice_choice handler (line ~4239) to send updated report after payment
# Currently it just returns the success message without rebuilding the report
old2 = '''    if pending == "payment_invoice_choice":
        options = session.get("options", [])
        context = session.get("context", {})
        contact = context.get("contact", {})
        chosen = pick_best_match(options, message)
        if not chosen:
            lines = "\\n".join([f"{i+1}. {inv.get('Subject','')}" for i, inv in enumerate(options)])
            return f"לא הצלחתי לזהות. בחר מספר:\\n{lines}"
        sessions.pop(from_number, None)
        pay_amount = context.get("amount") or chosen.get("Grand_Total", 0)
        pay_method = context.get("method") or "מזומן"
        success = mark_invoice_paid(chosen["id"], pay_amount, pay_method)
        if success:
            acc_name = contact.get("Account_Name", {}).get("name", "") if isinstance(contact.get("Account_Name"), dict) else ""
            log_action("תשלום", f"תשלום: {contact.get('Full_Name','')} @ {acc_name} ₪{pay_amount} {pay_method}")
            return (f"✅ תשלום עודכן!\\n"
                    f"👤 {contact.get('Full_Name','')}\\n"
                    f"🏠 {acc_name}\\n"
                    f"💰 ₪{pay_amount} | {pay_method}\\n"
                    f"📄 {chosen.get('Subject', '')}")
        return "❌ שגיאה בעדכון התשלום"
    # === בחירת לקוח מסטטוס בית (לחיצה על ספרה) ===
    if pending == "choose_landlord_contact":'''

new2 = '''    if pending == "payment_invoice_choice":
        options = session.get("options", [])
        context = session.get("context", {})
        contact = context.get("contact", {})
        aname_pic = context.get("aname_session", "")
        account_id_pic = context.get("account_id_session", "")
        chosen = pick_best_match(options, message)
        if not chosen:
            lines = "\\n".join([f"{i+1}. {inv.get('Subject','')}" for i, inv in enumerate(options)])
            return f"לא הצלחתי לזהות. בחר מספר:\\n{lines}"
        sessions.pop(from_number, None)
        pay_amount = context.get("amount") or chosen.get("Grand_Total", 0)
        pay_method = context.get("method") or "מזומן"
        success = mark_invoice_paid(chosen["id"], pay_amount, pay_method)
        if success:
            acc_name = contact.get("Account_Name", {}).get("name", "") if isinstance(contact.get("Account_Name"), dict) else ""
            log_action("תשלום", f"תשלום: {contact.get('Full_Name','')} @ {acc_name} ₪{pay_amount} {pay_method}")
            _confirm_msg = (f"✅ תשלום עודכן!\\n"
                    f"👤 {contact.get('Full_Name','')}\\n"
                    f"🏠 {acc_name}\\n"
                    f"💰 ₪{pay_amount} | {pay_method}\\n"
                    f"📄 {chosen.get('Subject', '')}")
            # אם יש סשן בית - שלח אישור ובנה דוח מעודכן
            if aname_pic:
                twilio_client.messages.create(
                    from_=TWILIO_WHATSAPP_FROM,
                    to=f"whatsapp:{from_number.replace('whatsapp:', '')}",
                    body=_confirm_msg)
                try:
                    import time as _time_pic; _time_pic.sleep(2)
                    _acct_pic = {"id": account_id_pic, "Account_Name": aname_pic} if account_id_pic else None
                    _result_pic = build_landlord_report(aname_pic, account=_acct_pic)
                    _rep_pic = _result_pic[0]
                    _ord_pic = _result_pic[1] if len(_result_pic) > 1 else []
                    _rst_pic = _result_pic[2] if len(_result_pic) > 2 else []
                    _cids_pic = _result_pic[3] if len(_result_pic) > 3 else {}
                    _byc_pic = _result_pic[4] if len(_result_pic) > 4 else {}
                    _aln_pic = _result_pic[5] if len(_result_pic) > 5 else {}
                    if _ord_pic:
                        sessions[from_number] = {"pending": "choose_landlord_contact",
                            "contacts": _ord_pic, "rest": _rst_pic,
                            "contact_ids": _cids_pic, "by_contact": _byc_pic,
                            "active_lines": _aln_pic, "aname": aname_pic,
                            "account_id": account_id_pic}
                    else:
                        sessions.pop(from_number, None)
                    return _rep_pic
                except Exception as _e_pic:
                    print(f"Error rebuilding report after invoice choice: {_e_pic}")
                    return ""
            return _confirm_msg
        return "❌ שגיאה בעדכון התשלום"
    # === בחירת לקוח מסטטוס בית (לחיצה על ספרה) ===
    if pending == "choose_landlord_contact":'''

if old2 in content:
    content = content.replace(old2, new2, 1)
    fixes += 1
    print('Fix 2 OK - payment_invoice_choice now rebuilds report and saves aname/account_id in context')
else:
    print('Fix 2 NOT FOUND')

open('app.py', 'w', encoding='utf-8').write(content)
print(f'Total fixes: {fixes}')
