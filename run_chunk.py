#!/usr/bin/env python3
"""Chunk runner: spawns parallel shard workers for one phase within a safe
time budget, then prints combined progress. Call repeatedly until 'ALL DONE'."""
import json, os, subprocess, sys, time, glob

os.chdir(os.path.dirname(os.path.abspath(__file__)))
phase = sys.argv[1] if len(sys.argv) > 1 else 'posts'
budget = sys.argv[2] if len(sys.argv) > 2 else '235'

sh = json.load(open('data/shards.json'))
b = sh['bounds']

if phase == 'posts':
    shards = [('repair', sh['A'], sh['repair_end'])]
    shards += [(f's{i+1}', b[i], b[i+1]) for i in range(4)]
else:
    cb = sh['cbounds']
    shards = [(f'c{i+1}', cb[i], cb[i+1]) for i in range(len(cb)-1)]

def cursor(kind, tag):
    p = f'data/cursor_{kind}_remotework_{tag}.json'
    if os.path.exists(p):
        return json.load(open(p))['cursor']
    return None

procs, active = [], []
for tag, a, bb in shards:
    cur = cursor(phase, tag)
    if cur is not None and cur >= bb:
        continue  # shard finished
    active.append(tag)
    procs.append(subprocess.Popen(
        ['python3', 'collector.py', '--phase', phase, '--after', str(a),
         '--before', str(bb), '--tag', tag, '--max-seconds', budget],
        stdout=open(f'log_{phase}_{tag}.txt', 'w'), stderr=subprocess.STDOUT))

if not procs:
    print(f'ALL DONE: phase {phase} — every shard reached its boundary')
    sys.exit(0)

t0 = time.time()
deadline = t0 + int(budget) + 25
for p in procs:
    try: p.wait(timeout=max(1, deadline - time.time()))
    except subprocess.TimeoutExpired: pass
for p in procs:
    if p.poll() is None:
        p.kill()
dt = time.time() - t0

rows = 0
for f in glob.glob(f'data/raw_{phase}_*.jsonl'):
    rows += sum(1 for _ in open(f, encoding='utf-8'))
done, pend = [], []
for tag, a, bb in shards:
    cur = cursor(phase, tag) or a
    pct = 100.0 * min(1.0, (cur - a) / max(1, bb - a))
    (done if cur >= bb else pend).append(f'{tag}:{pct:.0f}%')
print(f'chunk {dt:.0f}s | active {active} | raw rows so far: {rows}')
print('done:', ','.join(done) or '—', '| pending:', ','.join(pend) or '—')
print('ALL DONE' if not pend else 'CONTINUE')
