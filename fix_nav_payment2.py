lines = open('app.py', encoding='utf-8').readlines()

# Find the line with _process_payment_for_contact(contact_obj_nav
target_line = None
for i, line in enumerate(lines):
    if '_process_payment_for_contact(contact_obj_nav' in line and 'from_number)' in line:
        target_line = i
        print(f"Found at line {i+1}: {repr(line)}")
        break

if target_line is None:
    print("NOT FOUND")
else:
    # Replace just that return line with the full new block
    old_line = lines[target_line]
    indent = '            '
    
    new_lines = [
        f'{indent}_pay_res_nav = _process_payment_for_contact(contact_obj_nav, _amt_nav, _method_nav, from_number,\n',
        f'{indent}                                                        aname_session=aname, account_id_session="")\n',
        f'{indent}# אם נוצר payment_invoice_choice - החזר שאלה מיד\n',
        f'{indent}if sessions.get(from_number, {{}}).get("pending") == "payment_invoice_choice":\n',
        f'{indent}    return _pay_res_nav\n',
        f'{indent}# שלח אישור תשלום\n',
        f'{indent}twilio_client.messages.create(\n',
        f'{indent}    from_=TWILIO_WHATSAPP_FROM,\n',
        f'{indent}    to=f"whatsapp:{{from_number.replace(\'whatsapp:\', \'\')}}",\n',
        f'{indent}    body=_pay_res_nav)\n',
        f'{indent}# בנה דוח לקוח מעודכן\n',
        f'{indent}try:\n',
        f'{indent}    import time as _time_nav; _time_nav.sleep(2)\n',
        f'{indent}    _status_nav, _aname_nav, _cid_nav2 = build_customer_status(cname_nav, contact=contact_obj_nav)\n',
        f'{indent}    if _aname_nav:\n',
        f'{indent}        sessions[from_number] = {{"pending": "customer_status_nav", "aname": _aname_nav,\n',
        f'{indent}                                 "cid": _cid_nav2, "cname": cname_nav}}\n',
        f'{indent}    return _status_nav\n',
        f'{indent}except Exception as _e_nav:\n',
        f'{indent}    print(f"Error rebuilding customer status: {{_e_nav}}")\n',
        f'{indent}    return ""\n',
    ]
    
    lines[target_line] = ''.join(new_lines)
    open('app.py', 'w', encoding='utf-8').writelines(lines)
    print("Fixed!")
