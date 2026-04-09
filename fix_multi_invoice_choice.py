content = open('app.py', encoding='utf-8').read()

old = '''            if _multi_results:
                # שלח אישורי תשלום
                _combined = "\\n\\n".join(_multi_results)
                twilio_client.messages.create(
                    from_=TWILIO_WHATSAPP_FROM,
                    to=f"whatsapp:{from_number.replace('whatsapp:', '')}",
                    body=_combined)
                # עדכן דוח בית - סינכרוני
                try:
                    import time as _time_m; _time_m.sleep(2)  # המתן לZoho לעדכן
                    _account_obj_m = {"id": account_id_session, "Account_Name": aname_session} if account_id_session else None
                    _result = build_landlord_report(aname_session, account=_account_obj_m)
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
                            "active_lines": _alines, "aname": aname_session,
                            "account_id": account_id_session}
                    else:
                        sessions.pop(from_number, None)
                    return _rep
                except Exception as _e:
                    print(f"Error building updated report: {_e}")
                    return ""'''

new = '''            if _multi_results:
                # בדוק אם אחד מהתשלומים יצר payment_invoice_choice (כמה חשבוניות)
                _has_invoice_choice = sessions.get(from_number, {}).get("pending") == "payment_invoice_choice"
                if _has_invoice_choice:
                    # שלח את אישורי התשלומים שכבר נעשו, ואז שאל על החשבונית
                    _done_results = [r for r in _multi_results if "✅" in r]
                    _question_results = [r for r in _multi_results if "איזו לסמן" in r]
                    if _done_results:
                        twilio_client.messages.create(
                            from_=TWILIO_WHATSAPP_FROM,
                            to=f"whatsapp:{from_number.replace('whatsapp:', '')}",
                            body="\\n\\n".join(_done_results))
                    # עדכן context עם aname לדוח מאוחר יותר
                    if sessions.get(from_number, {}).get("pending") == "payment_invoice_choice":
                        sessions[from_number]["context"]["aname_session"] = aname_session
                        sessions[from_number]["context"]["account_id_session"] = account_id_session
                    return "\\n\\n".join(_question_results) if _question_results else _multi_results[-1]
                # אין שאלת חשבונית - שלח אישורים ובנה דוח
                _combined = "\\n\\n".join(_multi_results)
                twilio_client.messages.create(
                    from_=TWILIO_WHATSAPP_FROM,
                    to=f"whatsapp:{from_number.replace('whatsapp:', '')}",
                    body=_combined)
                # עדכן דוח בית - סינכרוני
                try:
                    import time as _time_m; _time_m.sleep(2)  # המתן לZoho לעדכן
                    _account_obj_m = {"id": account_id_session, "Account_Name": aname_session} if account_id_session else None
                    _result = build_landlord_report(aname_session, account=_account_obj_m)
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
                            "active_lines": _alines, "aname": aname_session,
                            "account_id": account_id_session}
                    else:
                        sessions.pop(from_number, None)
                    return _rep
                except Exception as _e:
                    print(f"Error building updated report: {_e}")
                    return ""'''

if old in content:
    content = content.replace(old, new, 1)
    open('app.py', 'w', encoding='utf-8').write(content)
    print('Fix OK')
else:
    print('NOT FOUND')
