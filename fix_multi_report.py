content = open('app.py', encoding='utf-8').read()

old = '''            if _multi_results:
                def _send_updated_report_multi():
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
                _threading.Thread(target=_send_updated_report_multi, daemon=True).start()
                return "\\n".join(_multi_results)'''

new = '''            if _multi_results:
                _aname_multi = aname_session
                _fn_multi = from_number
                def _send_updated_report_multi(_aname=_aname_multi, _fn=_fn_multi):
                    import time as _time
                    _time.sleep(1.5)
                    try:
                        _cur = sessions.get(_fn, {})
                        if _cur.get("aname", "") != _aname and _cur.get("pending") == "choose_landlord_contact":
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
                _threading.Thread(target=_send_updated_report_multi, daemon=True).start()
                return "\\n".join(_multi_results)'''

if old in content:
    content = content.replace(old, new, 1)
    open('app.py', 'w', encoding='utf-8').write(content)
    print('OK - fixed multi-payment report thread')
else:
    print('NOT FOUND')
