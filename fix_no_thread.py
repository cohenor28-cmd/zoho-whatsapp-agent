content = open('app.py', encoding='utf-8').read()

# Replace thread-based report sending with direct synchronous send
old = '''            if _matched_contact:
                _cname_pay, _cid_pay = _matched_contact
                _pay_amount = float(_pay_amount_str.replace(',', '.'))
                _contact_obj = {"id": _cid_pay, "Full_Name": _cname_pay, "Account_Name": {"name": aname_session}}
                _pay_result = _process_payment_for_contact(_contact_obj, _pay_amount, _pay_method_inline, from_number)
                # אחרי תשלום - שלח אישור ואחרכך דוח מעודכן
                # שמור את שם הבית הנוכחי לשימוש ב-thread
                _aname_for_report = aname_session
                _fn_for_report = from_number
                def _send_updated_report(_aname=_aname_for_report, _fn=_fn_for_report):
                    import time as _time
                    _time.sleep(1.5)
                    try:
                        # בדוק שהסשן הנוכחי עדיין שייך לאותו בית
                        _cur_session = sessions.get(_fn, {})
                        if _cur_session.get("aname", "") != _aname and _cur_session.get("pending") == "choose_landlord_contact":
                            print(f"[REPORT] Session changed, skipping report for {_aname}")
                            return
                        _result = build_landlord_report(_aname)
                        _rep = _result[0]
                        _ordered = _result[1] if len(_result) > 1 else []
                        _rest_c = _result[2] if len(_result) > 2 else []
                        _cids = _result[3] if len(_result) > 3 else {}
                        _byc = _result[4] if len(_result) > 4 else {}
                        _alines = _result[5] if len(_result) > 5 else {}
                        if _ordered:
                            sessions[_fn] = {"pending": "choose_landlord_contact",
                                "contacts": _ordered, "rest": _rest_c,
                                "contact_ids": _cids, "by_contact": _byc,
                                "active_lines": _alines, "aname": _aname}
                        else:
                            sessions.pop(_fn, None)
                        twilio_client.messages.create(
                            from_=TWILIO_WHATSAPP_FROM,
                            to=f"whatsapp:{_fn.replace('whatsapp:', '')}",
                            body=_rep)
                    except Exception as _e:
                        print(f"Error sending updated report: {_e}")
                import threading as _threading
                _threading.Thread(target=_send_updated_report, daemon=True).start()
                return _pay_result'''

new = '''            if _matched_contact:
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
                    _result = build_landlord_report(aname_session)
                    _rep = _result[0]
                    _ordered = _result[1] if len(_result) > 1 else []
                    _rest_c = _result[2] if len(_result) > 2 else []
                    _cids = _result[3] if len(_result) > 3 else {}
                    _byc = _result[4] if len(_result) > 4 else {}
                    _alines = _result[5] if len(_result) > 5 else {}
                    if _ordered:
                        sessions[from_number] = {"pending": "choose_landlord_contact",
                            "contacts": _ordered, "rest": _rest_c,
                            "contact_ids": _cids, "by_contact": _byc,
                            "active_lines": _alines, "aname": aname_session}
                    else:
                        sessions.pop(from_number, None)
                    return _rep
                except Exception as _e:
                    print(f"Error building updated report: {_e}")
                    return ""'''

if old in content:
    content = content.replace(old, new, 1)
    open('app.py', 'w', encoding='utf-8').write(content)
    print('OK - replaced thread with synchronous send')
else:
    print('NOT FOUND')
    idx = content.find('if _matched_contact:')
    # find the one near line 2265
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if 'if _matched_contact:' in line and 2260 <= i+1 <= 2280:
            print(f"Line {i+1}: {repr(line)}")
            print('\n'.join(lines[i:i+10]))
            break
