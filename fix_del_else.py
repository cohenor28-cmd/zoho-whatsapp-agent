content = open('app.py', encoding='utf-8').read()

old = '''        else:
            # אם זו פקודה חדשה (לא כן/לא) - נקה סשן ועבד אותה
            sessions.pop(from_number, None)
            return handle_command(message, from_number)
    # === זיהוי פקודה חדשה כשיש session פתוח ==='''

new = '''        else:
            sessions.pop(from_number, None)
            # ספרה - אל תעביר ל-MENU_SHORTCUTS (שם "2"="מחק חשבונית")
            # פשוט בטל ועבד מחדש רק אם זו לא ספרה
            if _msg_del.isdigit():
                return handle_command(_msg_del, from_number)
            return handle_command(message, from_number)
    # === זיהוי פקודה חדשה כשיש session פתוח ==='''

if old in content:
    content = content.replace(old, new, 1)
    open('app.py', 'w', encoding='utf-8').write(content)
    print('Fix OK')
else:
    print('NOT FOUND')
    idx = content.find('# אם זו פקודה חדשה (לא כן/לא)')
    print(f'idx={idx}')
    if idx >= 0:
        print(repr(content[idx-50:idx+200]))
