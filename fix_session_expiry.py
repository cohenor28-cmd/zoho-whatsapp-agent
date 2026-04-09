import re
content = open('app.py', encoding='utf-8').read()

# Fix 1: Add timestamp when session is created
# We need to add "ts": time.time() to all sessions[from_number] = {...} assignments
# But that's too many places. Better approach: add expiry check at the top of handle_command

old_session_read = '''def handle_command(message, from_number):
    print(f"handle_command: '{message}' from {from_number}")
    session = sessions.get(from_number, {})
    pending = session.get("pending")'''

new_session_read = '''def handle_command(message, from_number):
    print(f"handle_command: '{message}' from {from_number}")
    import time as _time_exp
    session = sessions.get(from_number, {})
    pending = session.get("pending")
    # === פקיעת סשן אחרי 2 דקות של חוסר פעילות ===
    if pending:
        _last_ts = session.get("_ts", 0)
        if _last_ts and (_time_exp.time() - _last_ts) > 120:  # 2 דקות
            sessions.pop(from_number, None)
            session = {}
            pending = None
    # עדכן timestamp בסשן הנוכחי
    if pending and sessions.get(from_number):
        sessions[from_number]["_ts"] = _time_exp.time()'''

if old_session_read in content:
    content = content.replace(old_session_read, new_session_read, 1)
    print('Fix 1 OK - session expiry')
else:
    print('Fix 1 NOT FOUND')

# Fix 2: Reduce all sleep(2) after payment to sleep(1.5)
old_sleep = '_time_m.sleep(2)  # המתן לZoho לעדכן'
new_sleep = '_time_m.sleep(1.5)  # המתן לZoho לעדכן'
count = content.count(old_sleep)
content = content.replace(old_sleep, new_sleep)
print(f'Fix 2 OK - reduced {count} sleep(2) to sleep(1.5)')

old_sleep2 = '_time_nav.sleep(2)'
new_sleep2 = '_time_nav.sleep(1.5)'
count2 = content.count(old_sleep2)
content = content.replace(old_sleep2, new_sleep2)
print(f'Fix 2b OK - reduced {count2} nav sleep(2) to sleep(1.5)')

# Fix 3: When creating a session, add _ts timestamp
# Replace all: sessions[from_number] = { with sessions[from_number] = {"_ts": __import__('time').time(),
# Actually simpler: just set _ts right after each session assignment
# But that's complex. The check at the top already handles it by reading _ts.
# We need to SET _ts when session is first created.
# Let's add it to the most common session creation pattern:
# sessions[from_number] = {"pending": ... -> sessions[from_number] = {"pending": ..., "_ts": time.time()
# This is too many replacements. Instead, use a helper function.

# Add helper function right before handle_command
old_def = 'def handle_command(message, from_number):'
new_def = '''def _set_session(from_number, data):
    """Set session with timestamp"""
    import time as _t_sess
    data["_ts"] = _t_sess.time()
    sessions[from_number] = data

def handle_command(message, from_number):'''

if old_def in content and '_set_session' not in content:
    content = content.replace(old_def, new_def, 1)
    print('Fix 3 OK - added _set_session helper')

open('app.py', 'w', encoding='utf-8').write(content)
print('Done')
