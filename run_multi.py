#!/usr/bin/env python3
"""Plan-driven runner: data/plan_multi.json = [{kind, sub, after, before, tag}].
Spawns collector workers for pending items, kills at deadline, prints progress."""
import json, os, subprocess, sys, time

os.chdir(os.path.dirname(os.path.abspath(__file__)))
budget = int(sys.argv[1]) if len(sys.argv) > 1 else 135
plan = json.load(open('data/plan_multi.json'))

def cur(it):
    p = f"data/cursor_{it['kind']}_{it['sub']}_{it['tag']}.json"
    try: return json.load(open(p))['cursor']
    except Exception: return it['after']

procs = []
for it in plan:
    if cur(it) >= it['before']: continue
    procs.append(subprocess.Popen(
        ['python3','collector.py','--phase',it['kind'],'--subreddits',it['sub'],
         '--after',str(it['after']),'--before',str(it['before']),
         '--tag',it['tag'],'--max-seconds',str(budget)],
        stdout=open(f"log_{it['kind']}_{it['sub']}_{it['tag']}.txt",'a'),
        stderr=subprocess.STDOUT))
if not procs:
    print('ALL DONE'); sys.exit(0)
deadline = time.time() + budget + 25
for p in procs:
    try: p.wait(timeout=max(1, deadline - time.time()))
    except subprocess.TimeoutExpired: pass
for p in procs:
    if p.poll() is None: p.kill()

pend = []
for it in plan:
    c = cur(it)
    pct = 100*min(1,(c-it['after'])/max(1,it['before']-it['after']))
    if pct < 100: pend.append(f"{it['sub'][:4]}/{it['tag']}:{pct:.0f}%")
print('pending:', ', '.join(pend) if pend else '—')
print('ALL DONE' if not pend else 'CONTINUE')
