content = open('app.py', encoding='utf-8').read()

# Find the _m3 line and the elif _m3 block, add product code check after _m3 match
old = '''        elif _m3:
            _pay_rest, _pay_amount_str = _m3.group(1).strip(), _m3.group(2)'''

new = '''        elif _m3:
            # אל תטפל כתשלום אם המספר מתחיל ב-0 (קוד מוצר כמו 050, 019)
            if not _m3.group(2).startswith('0'):
                _pay_rest, _pay_amount_str = _m3.group(1).strip(), _m3.group(2)'''

if old in content:
    content = content.replace(old, new, 1)
    open('app.py', 'w', encoding='utf-8').write(content)
    print('Fix OK')
else:
    print('NOT FOUND')
    idx = content.find('elif _m3:')
    print(f'idx={idx}')
    print(repr(content[idx:idx+100]))
