content = open('app.py', encoding='utf-8').read()

# Fix 1: When creating confirm_delete_invoice session, save the previous session
old_create = '''        sessions[from_number] = {
            "pending": "confirm_delete_invoice",
            "invoices_to_delete": invoices_list
        }
        return "\\n".join(lines)'''

new_create = '''        _prev_session = sessions.get(from_number, {})
        sessions[from_number] = {
            "pending": "confirm_delete_invoice",
            "invoices_to_delete": invoices_list,
            "prev_session": _prev_session
        }
        return "\\n".join(lines)'''

if old_create in content:
    content = content.replace(old_create, new_create, 1)
    print('Fix 1 OK - save prev session')
else:
    print('Fix 1 NOT FOUND')

# Fix 2: When digit received in confirm_delete_invoice, restore prev session and process digit there
old_else = '''        else:
            sessions.pop(from_number, None)
            # ספרה - אל תעביר ל-MENU_SHORTCUTS (שם "2"="מחק חשבונית")
            # פשוט בטל ועבד מחדש רק אם זו לא ספרה
            if _msg_del.isdigit():
                return handle_command(_msg_del, from_number)
            return handle_command(message, from_number)'''

new_else = '''        else:
            # שחזר סשן קודם (אם היה) לפני עיבוד הפקודה
            _prev = session.get("prev_session", {})
            if _prev:
                sessions[from_number] = _prev
            else:
                sessions.pop(from_number, None)
            return handle_command(message, from_number)'''

if old_else in content:
    content = content.replace(old_else, new_else, 1)
    print('Fix 2 OK - restore prev session on else')
else:
    print('Fix 2 NOT FOUND')
    idx = content.find('ספרה - אל תעביר')
    if idx >= 0:
        print(repr(content[idx-100:idx+300]))

open('app.py', 'w', encoding='utf-8').write(content)
