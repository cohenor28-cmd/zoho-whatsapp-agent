content = open('app.py', encoding='utf-8').read()

# Find and replace the broken block
broken_start = '    if pending == "payment_invoice_choice":\n        options = session.get("options", [])\n        context = session.get("context", {})\n        contact = context.get("contact", {})\n        chosen = pick_best_match(options, message)\n        if not chosen:\n            lines = "\n".join'

# Find the block start and end
idx = content.find('    if pending == "payment_invoice_choice":\n        options = session.get("options", [])')
if idx == -1:
    print("Block not found")
else:
    # Find end of this block (next "    if pending ==" or "    # ===")
    end_marker = '\n    # === בחירת לקוח מסטטוס בית'
    end_idx = content.find(end_marker, idx)
    if end_idx == -1:
        print("End marker not found")
    else:
        old_block = content[idx:end_idx]
        print(f"Found block ({len(old_block)} chars):")
        print(repr(old_block[:200]))
        
        new_block = '''    # === בחירת חשבונית לתשלום (MUST be before choose_landlord_contact) ===
    if pending == "payment_invoice_choice":
        options = session.get("options", [])
        context = session.get("context", {})
        contact = context.get("contact", {})
        chosen = pick_best_match(options, message)
        if not chosen:
            lines = "\\n".join([f"{i+1}. {inv.get('Subject','')}" for i, inv in enumerate(options)])
            return f"לא הצלחתי לזהות. בחר מספר:\\n{lines}"
        sessions.pop(from_number, None)
        pay_amount = context.get("amount") or chosen.get("Grand_Total", 0)
        pay_method = context.get("method") or "מזומן"
        success = mark_invoice_paid(chosen["id"], pay_amount, pay_method)
        if success:
            acc_name = contact.get("Account_Name", {}).get("name", "") if isinstance(contact.get("Account_Name"), dict) else ""
            log_action("תשלום", f"תשלום: {contact.get('Full_Name','')} @ {acc_name} ₪{pay_amount} {pay_method}")
            return (f"✅ תשלום עודכן!\\n"
                    f"👤 {contact.get('Full_Name','')}\\n"
                    f"🏠 {acc_name}\\n"
                    f"💰 ₪{pay_amount} | {pay_method}\\n"
                    f"📄 {chosen.get('Subject', '')}")
        return "❌ שגיאה בעדכון התשלום"'''
        
        content = content[:idx] + new_block + content[end_idx:]
        open('app.py', 'w', encoding='utf-8').write(content)
        print("Fixed!")
