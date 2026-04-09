content = open('app.py', encoding='utf-8').read()

# Fix 1: detect payment method from full `choice` string, not just `_pay_rest`
# Also fix _m3 to capture trailing payment method keyword
old = '''        if _pay_amount_str is not None:
            # בדוק אם השאר מכיל שם לקוח מהרשימה
            all_c_inline = contacts + [(c, contact_ids_map.get(c, "")) for c in rest_contacts]
            _matched_contact = None
            _pay_method_inline = "מזומן"
            _name_part = _pay_rest
            # חפש שיטת תשלום בסוף הטקסט
            for _m_kw in ["העבר", "ציאפ", "אשראי", "המחאה", "גיהוץ", "gmt", "019"]:
                if _m_kw in _pay_rest.lower():
                    _pay_method_inline = _detect_payment_method(_pay_rest)
                    # הסר מילת התשלום מהשם
                    for _rm in ["העברה ציאפ", "ציאפ", "העברה", "אשראי", "המחאה", "גיהוץ", "gmt", "019"]:
                        _name_part = _re.sub(_rm, '', _name_part, flags=_re.IGNORECASE).strip()
                    break'''

new = '''        if _pay_amount_str is not None:
            # בדוק אם השאר מכיל שם לקוח מהרשימה
            all_c_inline = contacts + [(c, contact_ids_map.get(c, "")) for c in rest_contacts]
            _matched_contact = None
            # חפש שיטת תשלום ב-choice המלא (לא רק ב-_pay_rest)
            _pay_method_inline = _detect_payment_method(choice)
            _name_part = _pay_rest
            # הסר מילות תשלום מהשם
            for _rm in ["העברה ציאפ", "ציאפ", "העברה", "אשראי", "המחאה", "גיהוץ", "gmt", "019"]:
                _name_part = _re.sub(_rm, '', _name_part, flags=_re.IGNORECASE).strip()'''

if old in content:
    content = content.replace(old, new, 1)
    print('Fix 1 OK - payment method from full choice')
else:
    print('Fix 1 NOT FOUND')
    idx = content.find('if _pay_amount_str is not None:')
    print(repr(content[idx:idx+400]))

# Fix 2: after payment in landlord report, DON'T send a second report automatically
# Instead, just update the session and return the payment confirmation
# The duplicate report issue: the thread sends a report for aname_session which may be wrong
# Solution: lock to current session's aname and don't send if session changed

old2 = '''                # אחרי תשלום - שלח אישור ואחרכך דוח מעודכן
                def _send_updated_report():
                    import time as _time
                    _time.sleep(1.5)
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
                        twilio_client.messages.create(
                            from_=TWILIO_WHATSAPP_FROM,
                            to=f"whatsapp:{from_number.replace('whatsapp:', '')}",
                            body=_rep)
                    except Exception as _e:
                        print(f"Error sending updated report: {_e}")
                import threading as _threading
                _threading.Thread(target=_send_updated_report, daemon=True).start()
                return _pay_result'''

new2 = '''                # אחרי תשלום - שלח אישור ואחרכך דוח מעודכן
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

if old2 in content:
    content = content.replace(old2, new2, 1)
    print('Fix 2 OK - locked report to current session aname')
else:
    print('Fix 2 NOT FOUND')

open('app.py', 'w', encoding='utf-8').write(content)
print('Done')
