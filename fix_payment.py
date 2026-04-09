content = open('app.py', encoding='utf-8').read()

old = '''def _process_payment_for_contact(contact, amount, method, from_number):
    open_invoices = find_open_invoices_for_contact(contact["Full_Name"])
    if not open_invoices:
        return f"❌ לא מצאתי חשבוניות פתוחות עבור {contact['Full_Name']}"
    if len(open_invoices) == 1:
        inv = open_invoices[0]
        pay_amount = amount if amount else inv.get("Grand_Total", 0)
        pay_method = method if method else "מזומן"
        success = mark_invoice_paid(inv["id"], pay_amount, pay_method)
        if success:
            acc_name = contact.get("Account_Name", {}).get("name", "") if isinstance(contact.get("Account_Name"), dict) else ""
            return (f"✅ תשלום עודכן!\\n"
                    f"👤 {contact['Full_Name']}\\n"
                    f"🏠 {acc_name}\\n"
                    f"💰 ₪{pay_amount} | {pay_method}\\n"
                    f"📄 {inv.get('Subject', '')}")
        return "❌ שגיאה בעדכון התשלום"
    acc_name_pay = contact.get("Account_Name", {}).get("name", "") if isinstance(contact.get("Account_Name"), dict) else str(contact.get("Account_Name", ""))
    sessions[from_number] = {
        "pending": "payment_invoice_choice",
        "options": open_invoices,
        "context": {"contact": contact, "amount": amount, "method": method}
    }
    lines = "\\n".join([f"{i+1}. {inv.get('Subject','')} - ₪{inv.get('Grand_Total',0)}"
                       for i, inv in enumerate(open_invoices)])
    return (f"👤 *{contact['Full_Name']}* | 🏠 {acc_name_pay}\\n"'''

new = '''def _process_payment_for_contact(contact, amount, method, from_number):
    open_invoices = find_open_invoices_for_contact(contact["Full_Name"])
    if not open_invoices:
        return f"❌ לא מצאתי חשבוניות פתוחות עבור {contact['Full_Name']}"
    acc_name_pay = contact.get("Account_Name", {}).get("name", "") if isinstance(contact.get("Account_Name"), dict) else str(contact.get("Account_Name", ""))
    if len(open_invoices) == 1:
        inv = open_invoices[0]
        inv_status = inv.get("Status", "")
        pay_amount = amount if amount else inv.get("Grand_Total", 0)
        pay_method = method if method else "מזומן"
        success = mark_invoice_paid(inv["id"], pay_amount, pay_method)
        if success:
            acc_name = contact.get("Account_Name", {}).get("name", "") if isinstance(contact.get("Account_Name"), dict) else ""
            status_note = " (שולם חלקית)" if inv_status == "שולם חלקית" else ""
            return (f"✅ תשלום עודכן!\\n"
                    f"👤 {contact['Full_Name']}\\n"
                    f"🏠 {acc_name}\\n"
                    f"💰 ₪{pay_amount} | {pay_method}\\n"
                    f"📄 {inv.get('Subject', '')}{status_note}")
        return "❌ שגיאה בעדכון התשלום"
    # כמה חשבוניות - תמיד שאל לאיזו לשייך
    sessions[from_number] = {
        "pending": "payment_invoice_choice",
        "options": open_invoices,
        "context": {"contact": contact, "amount": amount, "method": method}
    }
    status_labels = {"לא שולם": "לא שולם", "שולם חלקית": "חלקי", "Unpaid": "לא שולם", "Sent": "נשלח", "Draft": "טיוטה"}
    lines = "\\n".join([f"{i+1}. {inv.get('Subject','')} - ₪{inv.get('Grand_Total',0)} [{status_labels.get(inv.get('Status',''), inv.get('Status',''))}]"
                       for i, inv in enumerate(open_invoices)])
    return (f"👤 *{contact['Full_Name']}* | 🏠 {acc_name_pay}\\n"'''

if old in content:
    content = content.replace(old, new, 1)
    open('app.py', 'w', encoding='utf-8').write(content)
    print('OK - fixed _process_payment_for_contact')
else:
    print('NOT FOUND - searching...')
    idx = content.find('def _process_payment_for_contact')
    print(repr(content[idx:idx+600]))
