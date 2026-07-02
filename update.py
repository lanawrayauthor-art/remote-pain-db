#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Щотижневе оновлення бази однією командою:  python3 update.py
Делта-збір від останньої межі вікна (з перекриттям 3 дні для пізніх
коментарів) → merge → context → повний експорт. Все з дедуплікацією.

Прапорці:
  --skip-export     лише зібрати і злити (експорт окремо: python3 exporter.py all)
  --max-seconds N   бюджет на кожен збір (для обмежених середовищ; 0 = без ліміту)
"""
import argparse, json, os, sqlite3, subprocess, sys, time
from datetime import datetime, timezone

os.chdir(os.path.dirname(os.path.abspath(__file__)))
from collector import collect, merge_jsonl, collect_context_posts, load_state, save_state, db

OVERLAP = 3 * 86400  # перекриття: ловить коментарі, що з'явились під свіжими постами

ap = argparse.ArgumentParser()
ap.add_argument('--skip-export', action='store_true')
ap.add_argument('--max-seconds', type=int, default=0)
a = ap.parse_args()

subs = json.load(open('data/subs.json'))
state = load_state()
now = int(time.time())
after = int(state['window_before']) - OVERLAP
tag = 'ext' + datetime.now(timezone.utc).strftime('%Y%m%d')

con = db()
before_counts = {t: con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
                 for t in ('posts', 'comments')}
con.close()

print(f'Δ-вікно: {datetime.fromtimestamp(after, tz=timezone.utc):%Y-%m-%d} → '
      f'{datetime.fromtimestamp(now, tz=timezone.utc):%Y-%m-%d} | сабредіти: {", ".join(subs)}')

for sub in subs:
    collect('posts', sub, after, now, tag=tag, budget=a.max_seconds)
    collect('comments', sub, after, now, tag=tag, budget=a.max_seconds)

merge_jsonl()
# архівуємо злиті raw-файли, щоб наступні merge не перечитували історію
import glob, shutil
os.makedirs('data/archive', exist_ok=True)
for f in glob.glob('data/raw_*.jsonl'):
    shutil.move(f, os.path.join('data/archive', os.path.basename(f)))
collect_context_posts()

state = load_state()
state['window_before'] = now
save_state(state)

con = db()
for t in ('posts', 'comments'):
    n = con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
    print(f'{t}: {before_counts[t]} → {n}  (+{n - before_counts[t]})')
con.close()

if not a.skip_export:
    subprocess.run([sys.executable, 'exporter.py', 'all'], check=True)
    print('Експорт оновлено. База готова.')
