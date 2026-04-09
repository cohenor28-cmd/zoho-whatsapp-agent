content = open('app.py', encoding='utf-8').read()

old = '''        # תשלום ישיר מדף לקוח: "100" או "100 מזומן" או "100 019"
        import re as _re_nav
        _pay_nav = _re_nav.match(r\'^(\\d+(?:[.,]\\d+)?)(?:\\s+(.+))?$\', msg_nav)
        if _pay_nav:
            _amt_nav = float(_pay_nav.group(1).replace(\',\', \'.\'))
            _rest_nav = _pay_nav.group(2) or ""
            _method_nav = _detect_payment_method(_rest_nav) if _rest_nav else "מזומן"
            sessions.pop(from_number, None)
            contact_obj_nav = {"id": cid_nav, "Full_Name": cname_nav, "Account_Name": {"name": aname}}
            return _process_payment_for_contact(contact_obj_nav, _amt_nav, _method_nav, from_number)'''

new = '''        # תשלום ישיר מדף לקוח: "100" או "100 מזומן" או "100 019"
        import re as _re_nav
        _pay_nav = _re_nav.match(r\'^(\\d+(?:[.,]\\d+)?)(?:\\s+(.+))?$\', msg_nav)
        if _pay_nav:
            _amt_nav = float(_pay_nav.group(1).replace(\',\', \'.\'))
            _rest_nav = _pay_nav.group(2) or ""
            _method_nav = _detect_payment_method(_rest_nav) if _rest_nav else "מזומן"
            sessions.pop(from_number, None)
            contact_obj_nav = {"id": cid_nav, "Full_Name": cname_nav, "Account_Name": {"name": aname}}
            _pay_res_nav = _process_payment_for_contact(contact_obj_nav, _amt_nav, _method_nav, from_number,
                                                        aname_session=aname, account_id_session="")
            # אם נוצר payment_invoice_choice - החזר שאלה מיד
            if sessions.get(from_number, {}).get("pending") == "payment_invoice_choice":
                return _pay_res_nav
            # שלח אישור תשלום
            twilio_client.messages.create(
                from_=TWILIO_WHATSAPP_FROM,
                to=f"whatsapp:{from_number.replace(\'whatsapp:\', \'\')}",
                body=_pay_res_nav)
            # בנה דוח לקוח מעודכן
            try:
                import time as _time_nav; _time_nav.sleep(2)
                _status_nav, _aname_nav, _cid_nav2 = build_customer_status(cname_nav, contact=contact_obj_nav)
                if _aname_nav:
                    sessions[from_number] = {"pending": "customer_status_nav", "aname": _aname_nav,
                                             "cid": _cid_nav2, "cname": cname_nav}
                return _status_nav
            except Exception as _e_nav:
                print(f"Error rebuilding customer status: {_e_nav}")
                return ""'''

if old in content:
    content = content.replace(old, new, 1)
    open('app.py', 'w', encoding='utf-8').write(content)
    print('Fix OK')
else:
    print('NOT FOUND')
    # Show what's there
    idx = content.find('# תשלום ישיר מדף לקוח')
    if idx >= 0:
        print(repr(content[idx:idx+400]))
