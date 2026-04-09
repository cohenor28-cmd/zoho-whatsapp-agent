content = open('app.py', encoding='utf-8').read()

# Fix line 2446: use aname_session as fallback when build_customer_status returns empty aname
old = '''                status_text, aname, cid_s = build_customer_status(cname, contact=contact_obj)
                if aname:
                    sessions[from_number] = {"pending": "customer_status_nav", "aname": aname, "cid": cid_s, "cname": cname}
                return status_text'''

new = '''                status_text, aname, cid_s = build_customer_status(cname, contact=contact_obj)
                _eff_aname = aname or aname_session  # fallback לשם בית מהסשן הנוכחי
                sessions[from_number] = {"pending": "customer_status_nav", "aname": _eff_aname, "cid": cid_s or cid, "cname": cname, "account_id": account_id_session}
                return status_text'''

if old in content:
    content = content.replace(old, new, 1)
    print('Fix OK')
else:
    print('NOT FOUND')
    idx = content.find('status_text, aname, cid_s = build_customer_status(cname, contact=contact_obj)')
    print(f'idx={idx}')
    if idx >= 0:
        print(repr(content[idx-50:idx+300]))

# Also fix the same pattern in choose_contact_status (line 2166)
old2 = '''                status_text, aname, cid = build_customer_status(name_q, contact=contacts[idx])
                cname_s = contacts[idx].get("Full_Name", name_q)
                if aname:
                    sessions[from_number] = {"pending": "customer_status_nav", "aname": aname, "cid": cid, "cname": cname_s}
                return status_text'''

new2 = '''                status_text, aname, cid = build_customer_status(name_q, contact=contacts[idx])
                cname_s = contacts[idx].get("Full_Name", name_q)
                # תמיד צור סשן customer_status_nav גם אם aname ריק
                sessions[from_number] = {"pending": "customer_status_nav", "aname": aname or "", "cid": cid, "cname": cname_s}
                return status_text'''

if old2 in content:
    content = content.replace(old2, new2, 1)
    print('Fix2 OK - choose_contact_status')
else:
    print('Fix2 NOT FOUND')

open('app.py', 'w', encoding='utf-8').write(content)
