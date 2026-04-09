content = open('app.py', encoding='utf-8').read()

old = '''        # בדוק תשלום מרובה - מספר זוגות שם+סכום בהודעה אחת
        _multi_pairs = _re.findall(r\'(\\d+(?:[.,]\\d+)?)\\s+([\\u05d0-\\u05ea][\\u05d0-\\u05ea\\s]+?)(?=\\s+\\d|$)\', choice)
        _multi_pairs2 = _re.findall(r\'([\\u05d0-\\u05ea][\\u05d0-\\u05ea\\s]+?)\\s+(\\d+(?:[.,]\\d+)?)(?=\\s+[\\u05d0-\\u05ea]|$)\', choice)
        _all_multi = [(name.strip(), amt) for amt, name in _multi_pairs] + [(name.strip(), amt) for name, amt in _multi_pairs2]
        # סנן כפילויות
        _seen_names = set()
        _unique_multi = []
        for _mn, _ma in _all_multi:
            if _mn not in _seen_names:
                _seen_names.add(_mn)
                _unique_multi.append((_mn, _ma))'''

new = '''        # בדוק תשלום מרובה - מספר זוגות שם+סכום בהודעה אחת
        # פצל לפי שורות ונקודה-פסיק לטיפול נכון בהודעות מרובות שורות
        _lines_choice = _re.split(r\'[\\n;,]+\', choice)
        _unique_multi = []
        _seen_names_m = set()
        for _line in _lines_choice:
            _line = _line.strip()
            if not _line:
                continue
            # תבנית: "שם סכום" (שם לפני סכום) - עדיפות ראשונה
            _lm2 = _re.match(r\'^([\\u05d0-\\u05ea][\\u05d0-\\u05ea\\s]+?)\\s+(\\d+(?:[.,]\\d+)?)(?:\\s+.*)?$\', _line)
            # תבנית: "סכום שם" (סכום לפני שם)
            _lm1 = _re.match(r\'^(\\d+(?:[.,]\\d+)?)\\s+([\\u05d0-\\u05ea][\\u05d0-\\u05ea\\s]+?)(?:\\s+.*)?$\', _line)
            if _lm2:
                _mn, _ma = _lm2.group(1).strip(), _lm2.group(2)
                if _mn not in _seen_names_m:
                    _seen_names_m.add(_mn)
                    _unique_multi.append((_mn, _ma))
            elif _lm1:
                _ma, _mn = _lm1.group(1), _lm1.group(2).strip()
                if _mn not in _seen_names_m:
                    _seen_names_m.add(_mn)
                    _unique_multi.append((_mn, _ma))'''

if old in content:
    content = content.replace(old, new, 1)
    open('app.py', 'w', encoding='utf-8').write(content)
    print('Fix OK')
else:
    print('NOT FOUND - trying alternate search')
    # Try to find the block differently
    idx = content.find('# בדוק תשלום מרובה - מספר זוגות שם+סכום בהודעה אחת')
    if idx >= 0:
        print(f'Found at char {idx}')
        print(repr(content[idx:idx+500]))
    else:
        print('Block not found at all')
