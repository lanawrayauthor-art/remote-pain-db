#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Remote-work reader-pain corpus collector
========================================
Collects the FULL corpus (all posts + all comments) of given subreddit(s)
for a given time window from the Arctic Shift public research mirror of
Reddit (near-real-time archive), into a resumable, deduplicated database.

Design principles:
  - COLLECT EVERYTHING, FLAG LATER: no keyword filtering at collection time,
    so the pain layer is a transparent, editable annotation, not a bias.
  - RESUMABLE: state.json cursors; rerun anytime -> appends new data only.
  - SCALABLE: add subreddits or extend window via CLI flags; dedupe by id.

Usage:
  python3 collector.py --phase posts
  python3 collector.py --phase comments [--after E --before E --tag part1]
  python3 collector.py --phase context      # fetch parent posts older than window
  python3 collector.py --phase export       # build CSVs + pain layer + term stats
  python3 collector.py --phase all
Optional: --subreddits remotework,WorkFromHome  --days 365
"""
import argparse, csv, json, os, re, sqlite3, sys, time, urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE_DIR, 'data')
EXPORTS = os.path.join(BASE_DIR, 'exports')
os.makedirs(DATA, exist_ok=True); os.makedirs(EXPORTS, exist_ok=True)
DB_PATH = os.path.join(BASE_DIR, 'db.sqlite')
STATE_PATH = os.path.join(DATA, 'state.json')
LEXICON_PATH = os.path.join(BASE_DIR, 'pain_lexicon.json')
API = 'https://arctic-shift.photon-reddit.com/api'
UA = {'User-Agent': 'nonfiction-author-reader-pain-research/1.0'}
REQUEST_DELAY = 0.05

POST_FIELDS = ['id','subreddit','created_utc','author','title','selftext','score',
               'upvote_ratio','num_comments','link_flair_text','permalink','url',
               'removed_by_category','retrieved_on','over_18','edited','locked']
COMMENT_FIELDS = ['id','link_id','parent_id','subreddit','created_utc','author',
                  'body','score','permalink','retrieved_on','edited','distinguished']

def log(msg):
    print(f'[{datetime.now(timezone.utc).strftime("%H:%M:%S")}] {msg}', flush=True)

def dstr(ts):
    try: return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime('%Y-%m-%d')
    except Exception: return ''

def get_json(url, retries=4):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:
            code = getattr(e, 'code', None)
            wait = min(8, 2 ** (i + 1))
            log(f'  retry {i+1}/{retries} [{code or type(e).__name__}] wait {wait}s :: {url[:130]}')
            time.sleep(wait)
    return None

def load_state():
    return json.load(open(STATE_PATH)) if os.path.exists(STATE_PATH) else {}

def save_state(s):
    json.dump(s, open(STATE_PATH, 'w'), indent=1)

def cursor_path(key):
    return os.path.join(DATA, f'cursor_{key}.json')

def load_cursor(key, default):
    p = cursor_path(key)
    if os.path.exists(p):
        try: return int(json.load(open(p))['cursor'])
        except Exception: return default
    # backward compat: cursor previously stored in shared state.json
    return int(load_state().get(key, default))

def save_cursor(key, val):
    tmp = cursor_path(key) + '.tmp'
    json.dump({'cursor': int(val)}, open(tmp, 'w'))
    os.replace(tmp, cursor_path(key))

def db():
    con = sqlite3.connect(DB_PATH, timeout=120)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('''CREATE TABLE IF NOT EXISTS posts(
        id TEXT PRIMARY KEY, subreddit TEXT, created_utc INTEGER, created_date TEXT,
        author TEXT, title TEXT, selftext TEXT, score INTEGER, upvote_ratio REAL,
        num_comments INTEGER, link_flair_text TEXT, permalink TEXT, url TEXT,
        removed_by_category TEXT, retrieved_on INTEGER, over_18 INTEGER,
        edited TEXT, locked INTEGER, in_window INTEGER DEFAULT 1)''')
    con.execute('''CREATE TABLE IF NOT EXISTS comments(
        id TEXT PRIMARY KEY, link_id TEXT, parent_id TEXT, subreddit TEXT,
        created_utc INTEGER, created_date TEXT, author TEXT, body TEXT,
        score INTEGER, permalink TEXT, retrieved_on INTEGER, edited TEXT,
        distinguished TEXT)''')
    con.execute('CREATE INDEX IF NOT EXISTS idx_c_link ON comments(link_id)')
    con.execute('CREATE INDEX IF NOT EXISTS idx_p_date ON posts(created_utc)')
    con.execute('CREATE INDEX IF NOT EXISTS idx_c_date ON comments(created_utc)')
    return con

def pick(row, fields):
    out = {}
    for f in fields:
        v = row.get(f)
        if isinstance(v, (dict, list)): v = json.dumps(v, ensure_ascii=False)
        out[f] = v
    return out

def upsert(con, table, rec, fields):
    rec = dict(rec)
    rec['created_date'] = dstr(rec.get('created_utc') or 0)
    cols = fields + ['created_date']
    placeholders = ','.join('?' for _ in cols)
    # keep the freshest snapshot (greater retrieved_on wins)
    con.execute(
        f'INSERT INTO {table} ({",".join(cols)}) VALUES ({placeholders}) '
        f'ON CONFLICT(id) DO UPDATE SET '
        + ','.join(f'{c}=excluded.{c}' for c in cols if c != 'id')
        + ' WHERE COALESCE(excluded.retrieved_on,0) >= COALESCE(' + table + '.retrieved_on,0)',
        [rec.get(c) for c in cols])

# ---------------------------------------------------------------- collection
def collect(kind, sub, after_ts, before_ts, tag='', budget=0, sink='jsonl'):
    """kind: 'posts' | 'comments'; ascending pagination with overlap-safe cursor.
    budget: max seconds for this run; 0 = unlimited. Stops cleanly, resumable.
    sink: 'jsonl' (lock-free, for parallel shards; merge later) or 'db'."""
    fields = POST_FIELDS if kind == 'posts' else COMMENT_FIELDS
    key = f'{kind}_{sub}' + (f'_{tag}' if tag else '')
    cursor = load_cursor(key, after_ts)
    if sink == 'db':
        con = db(); fh = None
    else:
        con = None
        fh = open(os.path.join(DATA, f'raw_{key}.jsonl'), 'a', encoding='utf-8')
    total = 0
    t0 = time.time()
    lim = os.environ.get('FORCE_LIMIT', 'auto')
    log(f'START {key}: {dstr(cursor)} -> {dstr(before_ts)} [sink={sink}]')
    while cursor < before_ts:
        if budget and time.time() - t0 > budget:
            if con: con.commit(); con.close()
            if fh: fh.flush(); fh.close()
            log(f'PAUSE {key} at {dstr(cursor)} (+{total} this chunk) — resumable')
            return 'paused'
        url = (f'{API}/{kind}/search?subreddit={sub}&limit={lim}&sort=asc'
               f'&after={cursor}&before={before_ts}')
        d = get_json(url)
        if d is None:
            if lim == 'auto':
                log(f'FALLBACK to limit=100 at {dstr(cursor)} (dense region)')
                lim = '100'; continue
            log(f'ABORT page at {dstr(cursor)} — resume later from state'); break
        if lim != 'auto' and not os.environ.get('FORCE_LIMIT'):
            lim = 'auto'  # dense page passed, restore fast mode
        rows = d.get('data', [])
        if not rows:
            cursor = before_ts
            save_cursor(key, cursor)
            break
        for r in rows:
            rec = pick(r, fields)
            if con:
                upsert(con, kind, rec, fields)
            else:
                fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
        total += len(rows)
        last = int(rows[-1]['created_utc'])
        if con: con.commit()
        if fh: fh.flush(); os.fsync(fh.fileno())
        # overlap-safe advance: never lose same-second boundary items, always progress
        cursor = max(last - 1, cursor + 1)
        save_cursor(key, cursor)
        if total % 5000 < 1000:
            log(f'{key}: +{total} … at {dstr(cursor)}')
        time.sleep(REQUEST_DELAY)
    if con: con.commit(); con.close()
    if fh: fh.flush(); fh.close()
    log(f'DONE {key}: +{total} rows this run')

def merge_jsonl():
    """Single-writer merge of all shard JSONL files into SQLite (dedup by id)."""
    import glob
    con = db()
    for path in sorted(glob.glob(os.path.join(DATA, 'raw_posts_*.jsonl'))
                       + glob.glob(os.path.join(DATA, 'raw_comments_*.jsonl'))):
        base = os.path.basename(path)
        kind = 'posts' if base.startswith('raw_posts_') else 'comments'
        fields = POST_FIELDS if kind == 'posts' else COMMENT_FIELDS
        n = 0
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line: continue
                try: rec = json.loads(line)
                except json.JSONDecodeError: continue
                upsert(con, kind, rec, fields); n += 1
        con.commit()
        log(f'merged {n} rows from {base}')
    con.close()

def collect_context_posts():
    """Fetch parent posts (older than window) for comments whose post is missing."""
    con = db()
    missing = [r[0] for r in con.execute(
        """SELECT DISTINCT substr(link_id,4) FROM comments
           WHERE substr(link_id,4) NOT IN (SELECT id FROM posts)""")]
    log(f'context posts to fetch: {len(missing)}')
    got = 0
    for i in range(0, len(missing), 20):
        batch = missing[i:i+20]
        d = get_json(f'{API}/posts/ids?ids={",".join(batch)}')
        if d is None:
            log('  ids endpoint failed for a batch — skipping'); continue
        for r in d.get('data', []):
            rec = pick(r, POST_FIELDS); 
            upsert(con, 'posts', rec, POST_FIELDS)
            con.execute('UPDATE posts SET in_window=0 WHERE id=?', (rec['id'],))
            got += 1
        con.commit(); time.sleep(REQUEST_DELAY)
    con.close(); log(f'context posts fetched: {got}/{len(missing)}')

# ---------------------------------------------------------------- pain layer
def compile_lexicon():
    lex = json.load(open(LEXICON_PATH))
    compiled = {}
    for theme, terms in lex.items():
        pats = []
        for t in terms:
            esc = re.escape(t.lower()).replace(r'\ ', r'\s+')
            pats.append(rf'\b{esc}\b')
        compiled[theme] = (re.compile('|'.join(pats)), terms)
    return compiled

def pain_scan(text, compiled):
    tl = (text or '').lower()
    hits = {}
    for theme, (rx, _terms) in compiled.items():
        found = set(m.group(0) for m in rx.finditer(tl))
        if found: hits[theme] = sorted(found)
    return hits

STOPWORDS = set('''a about above after again all am an and any are as at be because been
before being below between both but by cant cannot could did do does doing down during
each few for from further had has have having he her here hers herself him himself his
how i if in into is it its itself just like me more most my myself no nor not now of off
on once only or other our ours ourselves out over own same she should so some such than
that the their theirs them themselves then there these they this those through to too
under until up very was we were what when where which while who whom why will with would
you your yours yourself yourselves im ive dont doesnt didnt isnt arent wasnt youre thats
theyre hes shes get got getting go going one two also even still really much many way
been being able https http www com amp deleted removed gt lt still know think make made
people work working job jobs remote day days time year years week weeks month months
want need feel feels felt say said see thing things lot pretty actually right back
'''.split())

def top_terms(texts, n=200):
    uni, bi = Counter(), Counter()
    word_rx = re.compile(r"[a-z][a-z'\-]{2,}")
    for t in texts:
        words = [w for w in word_rx.findall((t or '').lower()) if w not in STOPWORDS]
        uni.update(words)
        bi.update(f'{a} {b}' for a, b in zip(words, words[1:]))
    return uni.most_common(n), bi.most_common(n)

# ------------------------------------------------------------------- export
XLSX_CELL_LIMIT = 30000
def trunc(s):
    s = s or ''
    return (s[:XLSX_CELL_LIMIT] + ' …[TRUNCATED — full text in db.sqlite]') if len(s) > XLSX_CELL_LIMIT else s

def export():
    con = db(); con.row_factory = sqlite3.Row
    compiled = compile_lexicon()

    # depth computation for comments
    log('export: computing comment depth…')
    parent_map = {}
    for r in con.execute('SELECT id, parent_id, link_id FROM comments'):
        parent_map[r['id']] = (r['parent_id'] or '', r['link_id'] or '')
    depth_cache = {}
    def depth(cid, hop=0):
        if cid in depth_cache: return depth_cache[cid]
        if hop > 80: return -1
        pid, _ = parent_map.get(cid, ('', ''))
        if pid.startswith('t3_'): d = 0
        elif pid.startswith('t1_'):
            pd = depth(pid[3:], hop + 1)
            d = pd + 1 if pd is not None and pd >= 0 else -1
        else: d = -1
        depth_cache[cid] = d; return d

    # posts export + pain layer
    log('export: posts.csv + pain layer…')
    theme_counts_posts = Counter(); pain_rows = []
    with open(os.path.join(EXPORTS, 'posts.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['post_id','created_date','in_window','author','title','selftext',
                    'score_snapshot','upvote_ratio','num_comments_snapshot',
                    'comments_collected','flair','removed_by_category','locked',
                    'pain_themes','pain_terms','permalink_url'])
        cc = dict(con.execute("SELECT substr(link_id,4), COUNT(*) FROM comments GROUP BY 1"))
        for r in con.execute('SELECT * FROM posts ORDER BY created_utc'):
            hits = pain_scan((r['title'] or '') + '\n' + (r['selftext'] or ''), compiled)
            for th in hits: theme_counts_posts[th] += 1
            themes = ';'.join(sorted(hits))
            terms = ';'.join(sorted(set(t for v in hits.values() for t in v)))
            row = [r['id'], r['created_date'], r['in_window'], r['author'],
                   trunc(r['title']), trunc(r['selftext']), r['score'],
                   r['upvote_ratio'], r['num_comments'], cc.get(r['id'], 0),
                   r['link_flair_text'], r['removed_by_category'], r['locked'],
                   themes, terms, 'https://www.reddit.com' + (r['permalink'] or '')]
            w.writerow(row)
            if hits: pain_rows.append(row)
    with open(os.path.join(EXPORTS, 'pain_posts.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['post_id','created_date','in_window','author','title','selftext',
                    'score_snapshot','upvote_ratio','num_comments_snapshot',
                    'comments_collected','flair','removed_by_category','locked',
                    'pain_themes','pain_terms','permalink_url'])
        w.writerows(pain_rows)

    # comments export + pain layer
    log('export: comments.csv…')
    theme_counts_comments = Counter()
    with open(os.path.join(EXPORTS, 'comments.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['comment_id','post_id','parent_id','depth','created_date','author',
                    'body','score_snapshot','distinguished','pain_themes','pain_terms',
                    'permalink_url'])
        for r in con.execute('SELECT * FROM comments ORDER BY link_id, created_utc'):
            hits = pain_scan(r['body'], compiled)
            for th in hits: theme_counts_comments[th] += 1
            w.writerow([r['id'], (r['link_id'] or '')[3:], r['parent_id'], depth(r['id']),
                        r['created_date'], r['author'], trunc(r['body']), r['score'],
                        r['distinguished'], ';'.join(sorted(hits)),
                        ';'.join(sorted(set(t for v in hits.values() for t in v))),
                        'https://www.reddit.com' + (r['permalink'] or '')])

    # theme stats
    with open(os.path.join(EXPORTS, 'pain_theme_counts.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(['theme','posts_with_theme','comments_with_theme'])
        for th in sorted(set(theme_counts_posts) | set(theme_counts_comments)):
            w.writerow([th, theme_counts_posts.get(th, 0), theme_counts_comments.get(th, 0)])

    # data-driven vocabulary (no taxonomy imposed)
    log('export: top_terms.csv…')
    texts = [ (r[0] or '') + ' ' + (r[1] or '') for r in con.execute(
        'SELECT title, selftext FROM posts WHERE in_window=1') ]
    texts += [ r[0] or '' for r in con.execute('SELECT body FROM comments') ]
    uni, bi = top_terms(texts)
    with open(os.path.join(EXPORTS, 'top_terms.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(['type','term','count'])
        for t, c in uni: w.writerow(['unigram', t, c])
        for t, c in bi: w.writerow(['bigram', t, c])

    # manifest
    stats = {
        'collected_at_utc': datetime.now(timezone.utc).isoformat(),
        'source': 'Arctic Shift public research mirror (arctic-shift.photon-reddit.com)',
        'posts_total': con.execute('SELECT COUNT(*) FROM posts').fetchone()[0],
        'posts_in_window': con.execute('SELECT COUNT(*) FROM posts WHERE in_window=1').fetchone()[0],
        'context_posts_outside_window': con.execute('SELECT COUNT(*) FROM posts WHERE in_window=0').fetchone()[0],
        'comments_total': con.execute('SELECT COUNT(*) FROM comments').fetchone()[0],
        'posts_date_range': list(con.execute(
            'SELECT MIN(created_date), MAX(created_date) FROM posts WHERE in_window=1').fetchone()),
        'comments_date_range': list(con.execute(
            'SELECT MIN(created_date), MAX(created_date) FROM comments').fetchone()),
        'authors_unique': con.execute(
            "SELECT COUNT(DISTINCT author) FROM ("
            "SELECT author FROM posts WHERE in_window=1 UNION ALL SELECT author FROM comments)"
        ).fetchone()[0],
        'caveats': [
            'score/num_comments are mirror-ingest snapshots, not live values; treat as approximate engagement, text is the primary asset',
            'pain layer is a derived, editable annotation (pain_lexicon.json), NOT a collection filter — the corpus is complete',
            'pagination is overlap-safe; residual same-second boundary loss is theoretically possible but deduped and negligible',
            'removed/deleted items may retain original text captured at ingest — a research bonus; check removed_by_category',
        ],
    }
    json.dump(stats, open(os.path.join(EXPORTS, 'manifest.json'), 'w'),
              indent=2, ensure_ascii=False)
    log('export complete'); print(json.dumps(stats, indent=2, ensure_ascii=False))
    con.close()

# --------------------------------------------------------------------- main
if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--phase', required=True,
                    choices=['posts','comments','context','merge','export','all'])
    ap.add_argument('--subreddits', default='remotework')
    ap.add_argument('--days', type=int, default=365)
    ap.add_argument('--after', type=int, default=None)
    ap.add_argument('--before', type=int, default=None)
    ap.add_argument('--tag', default='')
    ap.add_argument('--max-seconds', type=int, default=0)
    a = ap.parse_args()

    now = int(time.time())
    state = load_state()
    window_after = state.get('window_after') or (now - a.days * 86400)
    if 'window_after' not in state:
        state['window_after'] = window_after; save_state(state)
    after_ts = a.after or window_after
    before_ts = a.before or state.get('window_before') or now
    if 'window_before' not in state:
        state['window_before'] = before_ts; save_state(state)

    for sub in a.subreddits.split(','):
        sub = sub.strip()
        if a.phase in ('posts', 'all'):
            collect('posts', sub, after_ts, before_ts, a.tag, a.max_seconds)
        if a.phase in ('comments', 'all'):
            collect('comments', sub, after_ts, before_ts, a.tag, a.max_seconds)
    if a.phase == 'merge':
        merge_jsonl()
    if a.phase in ('context', 'all'):
        merge_jsonl()
        collect_context_posts()
    if a.phase in ('export', 'all'):
        export()
