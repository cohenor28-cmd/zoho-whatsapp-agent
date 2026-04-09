lines = open('app.py', encoding='utf-8').readlines()

# Find the confirm_delete_invoice else block
target = None
for i, line in enumerate(lines):
    if 'return handle_command(message, from_number)' in line:
        # Check if it's inside confirm_delete_invoice context (look back ~10 lines)
        context = ''.join(lines[max(0,i-15):i])
        if 'confirm_delete_invoice' in context and '_msg_del' in context:
            target = i
            print(f"Found at line {i+1}: {repr(line)}")
            break

if target is None:
    print("NOT FOUND")
else:
    old_line = lines[target]
    indent = '            '
    # Replace with: if digit, cancel and let choose_landlord_contact handle it
    # if not digit, call handle_command
    new_lines = [
        f'{indent}# ספרה - כנראה בחירה מדוח בית - נקה סשן ועבד כפקודה חדשה\n',
        f'{indent}# אבל לא דרך handle_command כי MENU_SHORTCUTS["2"] = "מחק חשבונית"\n',
        f'{indent}# במקום זה - נקה סשן ועבד ישירות\n',
        f'{indent}return handle_command(message, from_number)\n',
    ]
    # Actually the fix is simpler: we need to prevent MENU_SHORTCUTS from triggering
    # when called from confirm_delete_invoice. The real fix is to not use handle_command
    # for digit-only messages from confirm_delete_invoice.
    # Instead, just clear session and return empty (let the next message be processed fresh)
    lines[target] = f'{indent}# ספרה או פקודה לא מוכרת - בטל מחיקה ועבד מחדש\n{indent}return handle_command(message, from_number)\n'
    
    # Actually we need a different approach: check if it's a digit
    lines[target] = (
        f'{indent}# פקודה לא מוכרת - נקה סשן\n'
        f'{indent}# אם ספרה - אל תעביר ל-MENU_SHORTCUTS (שם "2" = מחק חשבונית)\n'
        f'{indent}if _msg_del.isdigit():\n'
        f'{indent}    return "❌ המחיקה בוטלה"\n'
        f'{indent}return handle_command(message, from_number)\n'
    )
    open('app.py', 'w', encoding='utf-8').writelines(lines)
    print("Fixed!")
