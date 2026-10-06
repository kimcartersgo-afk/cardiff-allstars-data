import json
import re

def parse_row(texts):
    if len(texts) < 13: return None
    match_str = texts[9]
    if ' - ' not in match_str: return None
    parts = match_str.split(' - ', 1)
    home = parts[0].strip()
    away = parts[1].strip()
    score_str = texts[11]
    has_score = False
    if score_str and score_str != '-:-':
        sm = re.match(r'^(\d+)\s*[:\-]\s*(\d+)$', score_str)
        if sm: has_score = True
    status = texts[12].upper().strip()
    is_live = status in ('LIVE', 'IN PROGRESS', 'PLAYING', 'IN_PROGRESS')
    return {'home': home, 'away': away, 'score_str': score_str, 'has_score': has_score, 'status': status, 'is_live': is_live}

html = open('data/debug-previous-matches.html', encoding='utf-8').read()
rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.IGNORECASE | re.DOTALL)
for i, row in enumerate(rows):
    cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.IGNORECASE | re.DOTALL)
    if len(cells) < 13: continue
    texts = []
    for c in cells:
        c = re.sub(r'<span class="ui-column-title">.*?</span>', '', c)
        c = re.sub(r'<[^>]+>', '', c).strip()
        texts.append(c)
    m = parse_row(texts)
    if not m: continue
    target = 'Cardiff Allstars FC'
    if target in m['home'] or target in m['away']:
        print(f"Row {i}: {m}")
