content = open('app.py', encoding='utf-8').read()
fixes = 0

# Fix 1: choose_account_report - store account_id in session
old1 = '''                if ordered_contacts:
                    sessions[from_number] = {"pending": "choose_landlord_contact", "contacts": ordered_contacts,
                        "rest": rest_contacts, "contact_ids": contact_ids_map,
                        "by_contact": by_contact_map, "active_lines": active_lines_map, "aname": accounts[idx].get("Account_Name", name_q)}
                return report
        return f"❓ כתוב מספר בין 1 ל-{len(accounts)}"
    # === בחירת לקוח מסטטוס בית (לחיצה על ספרה) ==='''

new1 = '''                if ordered_contacts:
                    sessions[from_number] = {"pending": "choose_landlord_contact", "contacts": ordered_contacts,
                        "rest": rest_contacts, "contact_ids": contact_ids_map,
                        "by_contact": by_contact_map, "active_lines": active_lines_map,
                        "aname": accounts[idx].get("Account_Name", name_q),
                        "account_id": accounts[idx].get("id", "")}
                return report
        return f"❓ כתוב מספר בין 1 ל-{len(accounts)}"
    # === בחירת לקוח מסטטוס בית (לחיצה על ספרה) ==='''

if old1 in content:
    content = content.replace(old1, new1, 1)
    fixes += 1
    print('Fix 1 OK - store account_id in choose_account_report')
else:
    print('Fix 1 NOT FOUND')

# Fix 2: single account case - store account_id
old2 = '''                if ordered_contacts:
                    sessions[from_number] = {"pending": "choose_landlord_contact", "contacts": ordered_contacts,
                        "rest": rest_contacts, "contact_ids": contact_ids_map,
                        "by_contact": by_contact_map, "active_lines": active_lines_map, "aname": _full_aname_single}'''

new2 = '''                if ordered_contacts:
                    sessions[from_number] = {"pending": "choose_landlord_contact", "contacts": ordered_contacts,
                        "rest": rest_contacts, "contact_ids": contact_ids_map,
                        "by_contact": by_contact_map, "active_lines": active_lines_map,
                        "aname": _full_aname_single, "account_id": accounts[0].get("id", "")}'''

if old2 in content:
    content = content.replace(old2, new2, 1)
    fixes += 1
    print('Fix 2 OK - store account_id in single account case')
else:
    print('Fix 2 NOT FOUND')

# Fix 3: in choose_landlord_contact, read account_id from session and pass to build_landlord_report
# Find the aname_session and add account_id_session
old3 = '''        contacts = session.get("contacts", [])  # list of (cname, cid) - top 8
        rest_contacts = session.get("rest", [])  # remaining contacts
        contact_ids_map = session.get("contact_ids", {})
        by_contact_map = session.get("by_contact", {})
        active_lines_map = session.get("active_lines", {})
        aname_session = session.get("aname", "")'''

new3 = '''        contacts = session.get("contacts", [])  # list of (cname, cid) - top 8
        rest_contacts = session.get("rest", [])  # remaining contacts
        contact_ids_map = session.get("contact_ids", {})
        by_contact_map = session.get("by_contact", {})
        active_lines_map = session.get("active_lines", {})
        aname_session = session.get("aname", "")
        account_id_session = session.get("account_id", "")'''

if old3 in content:
    content = content.replace(old3, new3, 1)
    fixes += 1
    print('Fix 3 OK - read account_id_session from session')
else:
    print('Fix 3 NOT FOUND')

# Fix 4: when rebuilding report after payment, pass account object with id
# Find the synchronous rebuild block
old4 = '''                # עדכן דוח בית - סינכרוני, ללא thread
                try:
                    _result = build_landlord_report(aname_session)'''

new4 = '''                # עדכן דוח בית - סינכרוני, ללא thread
                try:
                    _account_obj = {"id": account_id_session, "Account_Name": aname_session} if account_id_session else None
                    _result = build_landlord_report(aname_session, account=_account_obj)'''

if old4 in content:
    content = content.replace(old4, new4, 1)
    fixes += 1
    print('Fix 4 OK - pass account object to rebuild')
else:
    print('Fix 4 NOT FOUND')

# Fix 5: same for multi-payment rebuild
old5 = '''                # עדכן דוח בית - סינכרוני
                try:
                    _result = build_landlord_report(aname_session)'''

new5 = '''                # עדכן דוח בית - סינכרוני
                try:
                    _account_obj_m = {"id": account_id_session, "Account_Name": aname_session} if account_id_session else None
                    _result = build_landlord_report(aname_session, account=_account_obj_m)'''

if old5 in content:
    content = content.replace(old5, new5, 1)
    fixes += 1
    print('Fix 5 OK - pass account object to multi-payment rebuild')
else:
    print('Fix 5 NOT FOUND')

# Fix 6: when updating session after rebuild, also preserve account_id
old6 = '''                    if _ordered:
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

new6 = '''                    if _ordered:
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

count6 = content.count(old6)
if count6 > 0:
    content = content.replace(old6, new6)
    fixes += count6
    print(f'Fix 6 OK - preserve account_id in {count6} rebuild session(s)')
else:
    print('Fix 6 NOT FOUND')

open('app.py', 'w', encoding='utf-8').write(content)
print(f'Total fixes applied: {fixes}')
