content = open('app.py', encoding='utf-8').read()

# Fix: _process_payment_for_contact needs to receive aname_session and account_id_session
# and store them in the payment_invoice_choice context
# The function signature needs to accept these optional params

old_sig = 'def _process_payment_for_contact(contact, amount, method, from_number):'
new_sig = 'def _process_payment_for_contact(contact, amount, method, from_number, aname_session="", account_id_session=""):'

if old_sig in content:
    content = content.replace(old_sig, new_sig, 1)
    print('Fix sig OK')
else:
    print('Sig NOT FOUND')

# Fix: store aname_session and account_id_session in context
old_ctx = '''    # כמה חשבוניות - תמיד שאל לאיזו לשייך
    sessions[from_number] = {
        "pending": "payment_invoice_choice",
        "options": open_invoices,
        "context": {"contact": contact, "amount": amount, "method": method}
    }'''

new_ctx = '''    # כמה חשבוניות - תמיד שאל לאיזו לשייך
    sessions[from_number] = {
        "pending": "payment_invoice_choice",
        "options": open_invoices,
        "context": {"contact": contact, "amount": amount, "method": method,
                    "aname_session": aname_session, "account_id_session": account_id_session}
    }'''

if old_ctx in content:
    content = content.replace(old_ctx, new_ctx, 1)
    print('Fix ctx OK')
else:
    print('Ctx NOT FOUND')

# Fix: pass aname_session and account_id_session when calling _process_payment_for_contact
# from choose_landlord_contact (single payment)
old_call = '''                _contact_obj = {"id": _cid_pay, "Full_Name": _cname_pay, "Account_Name": {"name": aname_session}}
                _pay_result = _process_payment_for_contact(_contact_obj, _pay_amount, _pay_method_inline, from_number)
                # אם נוצר סשן payment_invoice_choice - יש כמה חשבוניות, החזר שאלה מיד'''

new_call = '''                _contact_obj = {"id": _cid_pay, "Full_Name": _cname_pay, "Account_Name": {"name": aname_session}}
                _pay_result = _process_payment_for_contact(_contact_obj, _pay_amount, _pay_method_inline, from_number, aname_session=aname_session, account_id_session=account_id_session)
                # אם נוצר סשן payment_invoice_choice - יש כמה חשבוניות, החזר שאלה מיד'''

if old_call in content:
    content = content.replace(old_call, new_call, 1)
    print('Fix call OK')
else:
    print('Call NOT FOUND')

open('app.py', 'w', encoding='utf-8').write(content)
print('Done')
