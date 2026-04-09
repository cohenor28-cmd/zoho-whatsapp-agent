content = open('app.py', encoding='utf-8').read()
fixes = 0

# Fix 1a: Add sleep(2) before multi-payment report rebuild
old1a = '''                # עדכן דוח בית - סינכרוני
                try:
                    _account_obj_m = {"id": account_id_session, "Account_Name": aname_session} if account_id_session else None
                    _result = build_landlord_report(aname_session, account=_account_obj_m)'''

new1a = '''                # עדכן דוח בית - סינכרוני
                try:
                    import time as _time_m; _time_m.sleep(2)  # המתן לZoho לעדכן
                    _account_obj_m = {"id": account_id_session, "Account_Name": aname_session} if account_id_session else None
                    _result = build_landlord_report(aname_session, account=_account_obj_m)'''

if old1a in content:
    content = content.replace(old1a, new1a, 1)
    fixes += 1
    print('Fix 1a OK - sleep(2) before multi-payment rebuild')
else:
    print('Fix 1a NOT FOUND')

# Fix 1b: Add sleep(2) before single-payment report rebuild
old1b = '''                # עדכן דוח בית - סינכרוני, ללא thread
                try:
                    _account_obj = {"id": account_id_session, "Account_Name": aname_session} if account_id_session else None
                    _result = build_landlord_report(aname_session, account=_account_obj)'''

new1b = '''                # עדכן דוח בית - סינכרוני, ללא thread
                try:
                    import time as _time_s; _time_s.sleep(2)  # המתן לZoho לעדכן
                    _account_obj = {"id": account_id_session, "Account_Name": aname_session} if account_id_session else None
                    _result = build_landlord_report(aname_session, account=_account_obj)'''

if old1b in content:
    content = content.replace(old1b, new1b, 1)
    fixes += 1
    print('Fix 1b OK - sleep(2) before single-payment rebuild')
else:
    print('Fix 1b NOT FOUND')

# Fix 2: customer_status_nav "תפריט" causes NoneType error
# When "תפריט" is typed in customer_status_nav, session is cleared and None is returned
# Then handle_command tries len(None) - fix: handle "תפריט" explicitly
old2 = '''    if pending == "customer_status_nav":
        aname = session.get("aname", "")
        cid_nav = session.get("cid", "")
        cname_nav = session.get("cname", "")
        if message.strip() == "7":
            # תשלום דרך סטטוס לקוח - עבור ל-payment flow
            sessions.pop(from_number, None)
            return handle_command(f"תשלום {cname_nav}", from_number)
        if message.strip() == "8" and aname:
            sessions.pop(from_number, None)
            result = build_landlord_report(aname)
            report, ordered_contacts = result[0], result[1]
            rest_contacts = result[2] if len(result) > 2 else []
            contact_ids_map = result[3] if len(result) > 3 else {}
            by_contact_map = result[4] if len(result) > 4 else {}
            active_lines_map = result[5] if len(result) > 5 else {}
            if ordered_contacts:
                sessions[from_number] = {"pending": "choose_landlord_contact", "contacts": ordered_contacts,
                    "rest": rest_contacts, "contact_ids": contact_ids_map,
                    "by_contact": by_contact_map, "active_lines": active_lines_map, "aname": aname}
            return report
        sessions.pop(from_number, None)
        return None  # ימשיך לטיפול רגיל'''

new2 = '''    if pending == "customer_status_nav":
        aname = session.get("aname", "")
        cid_nav = session.get("cid", "")
        cname_nav = session.get("cname", "")
        msg_nav = message.strip()
        if msg_nav == "7":
            # תשלום דרך סטטוס לקוח - עבור ל-payment flow
            sessions.pop(from_number, None)
            return handle_command(f"תשלום {cname_nav}", from_number)
        if msg_nav == "8" and aname:
            sessions.pop(from_number, None)
            account_id_nav = session.get("account_id", "")
            _acct_obj_nav = {"id": account_id_nav, "Account_Name": aname} if account_id_nav else None
            result = build_landlord_report(aname, account=_acct_obj_nav)
            report, ordered_contacts = result[0], result[1]
            rest_contacts = result[2] if len(result) > 2 else []
            contact_ids_map = result[3] if len(result) > 3 else {}
            by_contact_map = result[4] if len(result) > 4 else {}
            active_lines_map = result[5] if len(result) > 5 else {}
            if ordered_contacts:
                sessions[from_number] = {"pending": "choose_landlord_contact", "contacts": ordered_contacts,
                    "rest": rest_contacts, "contact_ids": contact_ids_map,
                    "by_contact": by_contact_map, "active_lines": active_lines_map, "aname": aname,
                    "account_id": account_id_nav}
            return report
        # תשלום ישיר מדף לקוח: "100" או "100 מזומן" או "100 019"
        import re as _re_nav
        _pay_nav = _re_nav.match(r'^(\d+(?:[.,]\d+)?)(?:\s+(.+))?$', msg_nav)
        if _pay_nav:
            _amt_nav = float(_pay_nav.group(1).replace(',', ''))
            _rest_nav = _pay_nav.group(2) or ""
            _method_nav = _detect_payment_method(_rest_nav) if _rest_nav else "מזומן"
            sessions.pop(from_number, None)
            contact_obj_nav = {"id": cid_nav, "Full_Name": cname_nav, "Account_Name": {"name": aname}}
            return _process_payment_for_contact(contact_obj_nav, _amt_nav, _method_nav, from_number)
        sessions.pop(from_number, None)
        return None  # ימשיך לטיפול רגיל'''

if old2 in content:
    content = content.replace(old2, new2, 1)
    fixes += 1
    print('Fix 2 OK - customer_status_nav: handle תפריט + direct payment + account_id')
else:
    print('Fix 2 NOT FOUND')

# Fix 3: payment_invoice_choice - when user picks invoice number, the digit also triggers
# choose_landlord_contact which interprets it as a contact selection
# The fix: payment_invoice_choice must be handled BEFORE choose_landlord_contact
# Check current order in the file
idx_invoice = content.find('if pending == "payment_invoice_choice":')
idx_landlord = content.find('if pending == "choose_landlord_contact":')
print(f'payment_invoice_choice at char {idx_invoice}, choose_landlord_contact at char {idx_landlord}')
if idx_invoice > idx_landlord:
    print('FIX 3 NEEDED: payment_invoice_choice comes AFTER choose_landlord_contact - need to reorder')
else:
    print('Fix 3 OK: payment_invoice_choice already before choose_landlord_contact')

open('app.py', 'w', encoding='utf-8').write(content)
print(f'Total fixes applied: {fixes}')
