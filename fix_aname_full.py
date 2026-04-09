content = open('app.py', encoding='utf-8').read()
fixes = 0

# Fix 1: choose_account_report block (line ~2161) - when user picks from list
old1 = '''                result = build_landlord_report(name_q, account=accounts[idx])
                report, ordered_contacts = result[0], result[1]
                rest_contacts = result[2] if len(result) > 2 else []
                contact_ids_map = result[3] if len(result) > 3 else {}
                by_contact_map = result[4] if len(result) > 4 else {}
                active_lines_map = result[5] if len(result) > 5 else {}
                if ordered_contacts:
                    sessions[from_number] = {"pending": "choose_landlord_contact", "contacts": ordered_contacts,
                        "rest": rest_contacts, "contact_ids": contact_ids_map,
                        "by_contact": by_contact_map, "active_lines": active_lines_map, "aname": name_q}
                return report
        return f"❓ כתוב מספר בין 1 ל-{len(accounts)}"
    # === בחירת לקוח מסטטוס בית (לחיצה על ספרה) ==='''

new1 = '''                _chosen_account = accounts[idx]
                _full_aname = _chosen_account.get("Account_Name", name_q)
                result = build_landlord_report(name_q, account=_chosen_account)
                report, ordered_contacts = result[0], result[1]
                rest_contacts = result[2] if len(result) > 2 else []
                contact_ids_map = result[3] if len(result) > 3 else {}
                by_contact_map = result[4] if len(result) > 4 else {}
                active_lines_map = result[5] if len(result) > 5 else {}
                if ordered_contacts:
                    sessions[from_number] = {"pending": "choose_landlord_contact", "contacts": ordered_contacts,
                        "rest": rest_contacts, "contact_ids": contact_ids_map,
                        "by_contact": by_contact_map, "active_lines": active_lines_map, "aname": _full_aname}
                return report
        return f"❓ כתוב מספר בין 1 ל-{len(accounts)}"
    # === בחירת לקוח מסטטוס בית (לחיצה על ספרה) ==='''

if old1 in content:
    content = content.replace(old1, new1, 1)
    fixes += 1
    print('Fix 1 OK - choose_account_report uses full account name')
else:
    print('Fix 1 NOT FOUND')

# Fix 2: single-account case (line ~3954) - when only one account found
old2 = '''            if len(accounts) == 1:
                sessions.pop(from_number, None)
                result = build_landlord_report(name_q, account=accounts[0])
                report, ordered_contacts = result[0], result[1]
                rest_contacts = result[2] if len(result) > 2 else []
                contact_ids_map = result[3] if len(result) > 3 else {}
                by_contact_map = result[4] if len(result) > 4 else {}
                active_lines_map = result[5] if len(result) > 5 else {}
                if ordered_contacts:
                    sessions[from_number] = {"pending": "choose_landlord_contact", "contacts": ordered_contacts,
                        "rest": rest_contacts, "contact_ids": contact_ids_map,
                        "by_contact": by_contact_map, "active_lines": active_lines_map, "aname": name_q}'''

new2 = '''            if len(accounts) == 1:
                sessions.pop(from_number, None)
                _full_aname_single = accounts[0].get("Account_Name", name_q)
                result = build_landlord_report(name_q, account=accounts[0])
                report, ordered_contacts = result[0], result[1]
                rest_contacts = result[2] if len(result) > 2 else []
                contact_ids_map = result[3] if len(result) > 3 else {}
                by_contact_map = result[4] if len(result) > 4 else {}
                active_lines_map = result[5] if len(result) > 5 else {}
                if ordered_contacts:
                    sessions[from_number] = {"pending": "choose_landlord_contact", "contacts": ordered_contacts,
                        "rest": rest_contacts, "contact_ids": contact_ids_map,
                        "by_contact": by_contact_map, "active_lines": active_lines_map, "aname": _full_aname_single}'''

if old2 in content:
    content = content.replace(old2, new2, 1)
    fixes += 1
    print('Fix 2 OK - single account case uses full account name')
else:
    print('Fix 2 NOT FOUND')

# Also fix build_landlord_report itself to use full account name when rebuilding
# The function returns aname from account.get("Account_Name", name_query)
# which is correct - but when we call it with just the name_query string (not account obj)
# it does a new search and may find a different account
# Solution: when rebuilding after payment, pass the account ID directly

open('app.py', 'w', encoding='utf-8').write(content)
print(f'Total fixes: {fixes}')
