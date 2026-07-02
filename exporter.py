#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Staged exporter for the remote-work pain corpus.
Parts: posts | comments | stats  (run in that order; each fits a short call)."""
import csv, json, os, re, sys, sqlite3
from collections import Counter
from datetime import datetime, timezone
from collector import (db, EXPORTS, DATA, LEXICON_PATH, log, trunc, top_terms)

def build_scanner():
    lex = {k: v for k, v in json.load(open(LEXICON_PATH)).items()
           if not k.startswith('_')}
    term2themes = {}
    for theme, terms in lex.items():
        for t in terms:
            term2themes.setdefault(t.lower(), set()).add(theme)
    # longest-first so multiword phrases win over substrings at same position
    ordered = sorted(term2themes, key=len, reverse=True)
    pats = [re.escape(t).replace(r'\ ', r'\s+') for t in ordered]
    rx = re.compile(r'\b(?:' + '|'.join(pats) + r')\b')
    norm = {t: re.sub(r'\s+', ' ', t) for t in term2themes}
    return rx, term2themes, norm

RX, T2T, NORM = build_scanner()

def scan(text):
    tl = (text or '').lower()
    themes, terms = set(), set()
    for m in RX.finditer(tl):
        t = re.sub(r'\s+', ' ', m.group(0))
        if t in T2T:
            themes |= T2T[t]; terms.add(t)
    return themes, terms

def export_posts():
    con = db(); con.row_factory = sqlite3.Row
    cc = dict(con.execute("SELECT substr(link_id,4), COUNT(*) FROM comments GROUP BY 1"))
    theme_counts = Counter(); pain_rows = []
    hdr = ['post_id','subreddit','created_date','in_window','author','title','selftext',
           'score_snapshot','upvote_ratio','num_comments_snapshot','comments_collected',
           'flair','removed_by_category','locked','pain_themes','pain_terms','permalink_url']
    with open(os.path.join(EXPORTS, 'posts.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(hdr); n = 0
        for r in con.execute('SELECT * FROM posts ORDER BY created_utc'):
            themes, terms = scan((r['title'] or '') + '\n' + (r['selftext'] or ''))
            for th in themes: theme_counts[f"{r['subreddit']}|{th}"] += 1
            row = [r['id'], r['subreddit'], r['created_date'], r['in_window'], r['author'],
                   trunc(r['title']), trunc(r['selftext']), r['score'], r['upvote_ratio'],
                   r['num_comments'], cc.get(r['id'], 0), r['link_flair_text'],
                   r['removed_by_category'], r['locked'], ';'.join(sorted(themes)),
                   ';'.join(sorted(terms)),
                   'https://www.reddit.com' + (r['permalink'] or '')]
            w.writerow(row); n += 1
            if themes: pain_rows.append(row)
    with open(os.path.join(EXPORTS, 'pain_posts.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(hdr); w.writerows(pain_rows)
    json.dump({'counts': theme_counts, 'flagged': len(pain_rows), 'total': n}, open(os.path.join(DATA, 'theme_counts_posts.json'), 'w'))
    log(f'posts export: {n} rows, {len(pain_rows)} flagged as pain')

def export_comments():
    con = db(); con.row_factory = sqlite3.Row
    log('computing comment depth…')
    parent = {r[0]: (r[1] or '') for r in con.execute('SELECT id, parent_id FROM comments')}
    depth_cache = {}
    def depth(cid):
        chain = []
        cur = cid
        while True:
            if cur in depth_cache:
                d = depth_cache[cur]; break
            p = parent.get(cur)
            if p is None or len(chain) > 150:
                for c in chain + [cur]: depth_cache[c] = -1
                return -1
            if p.startswith('t3_'):
                depth_cache[cur] = 0; d = 0; break
            if not p.startswith('t1_'):
                for c in chain + [cur]: depth_cache[c] = -1
                return -1
            chain.append(cur); cur = p[3:]
        if d < 0:
            for c in chain: depth_cache[c] = -1
            return -1
        dd = d
        for c in reversed(chain):
            dd += 1; depth_cache[c] = dd
        return depth_cache[cid]
    theme_counts = Counter()
    hdr = ['comment_id','subreddit','post_id','parent_id','depth','created_date','author','body',
           'score_snapshot','distinguished','pain_themes','pain_terms','permalink_url']
    pain_f = open(os.path.join(EXPORTS, 'pain_comments.csv'), 'w', newline='', encoding='utf-8-sig')
    pw = csv.writer(pain_f); pw.writerow(hdr)
    with open(os.path.join(EXPORTS, 'comments.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(hdr); n = pain_n = 0
        for r in con.execute('SELECT * FROM comments ORDER BY link_id, created_utc'):
            themes, terms = scan(r['body'])
            for th in themes: theme_counts[f"{r['subreddit']}|{th}"] += 1
            row = [r['id'], r['subreddit'], (r['link_id'] or '')[3:], r['parent_id'], depth(r['id']),
                   r['created_date'], r['author'], trunc(r['body']), r['score'],
                   r['distinguished'], ';'.join(sorted(themes)), ';'.join(sorted(terms)),
                   'https://www.reddit.com' + (r['permalink'] or '')]
            w.writerow(row); n += 1
            if themes: pw.writerow(row); pain_n += 1
            if n % 100000 == 0: log(f'  …{n} comments written')
    pain_f.close()
    json.dump({'counts': theme_counts, 'flagged': pain_n, 'total': n}, open(os.path.join(DATA, 'theme_counts_comments.json'), 'w'))
    log(f'comments export: {n} rows, {pain_n} flagged as pain')

def export_stats():
    con = db()
    jp = json.load(open(os.path.join(DATA, 'theme_counts_posts.json')))
    jc = json.load(open(os.path.join(DATA, 'theme_counts_comments.json')))
    sp, sc = Counter(jp['counts']), Counter(jc['counts'])
    tp, tc = Counter(), Counter()
    for k, v in sp.items(): tp[k.split('|',1)[1]] += v
    for k, v in sc.items(): tc[k.split('|',1)[1]] += v
    with open(os.path.join(EXPORTS, 'pain_theme_counts.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(['theme','posts_with_theme','comments_with_theme','total'])
        for th in sorted(set(tp) | set(tc), key=lambda t: -(tp.get(t,0)+tc.get(t,0))):
            w.writerow([th, tp.get(th,0), tc.get(th,0), tp.get(th,0)+tc.get(th,0)])
    subs = sorted({k.split('|',1)[0] for k in list(sp)+list(sc)})
    with open(os.path.join(EXPORTS, 'pain_theme_by_sub.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(['subreddit','theme','posts_with_theme','comments_with_theme','total'])
        for s in subs:
            for th in sorted(set(tp) | set(tc), key=lambda t: -(sp.get(f'{s}|{t}',0)+sc.get(f'{s}|{t}',0))):
                p_, c_ = sp.get(f'{s}|{th}',0), sc.get(f'{s}|{th}',0)
                if p_ or c_: w.writerow([s, th, p_, c_, p_+c_])
    log('top terms…')
    texts = [(r[0] or '') + ' ' + (r[1] or '') for r in con.execute(
        'SELECT title, selftext FROM posts WHERE in_window=1')]
    texts += [r[0] or '' for r in con.execute('SELECT body FROM comments')]
    uni, bi = top_terms(texts, 250)
    with open(os.path.join(EXPORTS, 'top_terms.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(['type','term','count'])
        for t, c in uni: w.writerow(['unigram', t, c])
        for t, c in bi: w.writerow(['bigram', t, c])
    q = lambda s: con.execute(s).fetchone()
    stats = {
        'collected_at_utc': datetime.now(timezone.utc).isoformat(),
        'subreddits': 'r/remotework, r/WorkFromHome, r/telecommuting',
        'by_subreddit': {s: {'posts_in_window': q(f"SELECT COUNT(*) FROM posts WHERE in_window=1 AND lower(subreddit)=lower('{s}')")[0],
                             'comments': q(f"SELECT COUNT(*) FROM comments WHERE lower(subreddit)=lower('{s}')")[0]}
                         for s in ['remotework','WorkFromHome','telecommuting']},
        'window': '2025-07-02 .. 2026-07-02 (365 days)',
        'source': 'Arctic Shift public research mirror (arctic-shift.photon-reddit.com); direct Reddit blocks datacenter access',
        'posts_in_window': q('SELECT COUNT(*) FROM posts WHERE in_window=1')[0],
        'context_posts_outside_window': q('SELECT COUNT(*) FROM posts WHERE in_window=0')[0],
        'comments_total': q('SELECT COUNT(*) FROM comments')[0],
        'comments_on_context_threads': q(
            "SELECT COUNT(*) FROM comments WHERE substr(link_id,4) IN (SELECT id FROM posts WHERE in_window=0)")[0],
        'orphan_thread_ids_unfetched': q(
            "SELECT COUNT(DISTINCT substr(link_id,4)) FROM comments WHERE substr(link_id,4) NOT IN (SELECT id FROM posts)")[0],
        'unique_authors': q("SELECT COUNT(DISTINCT author) FROM (SELECT author FROM posts WHERE in_window=1 UNION ALL SELECT author FROM comments)")[0],
        'posts_flagged_pain': jp['flagged'],
        'comments_flagged_pain': jc['flagged'],
        'caveats': [
            'score/num_comments — снапшоти дзеркала на момент інджесту, не живі значення; для ранжування використовуй як приблизний сигнал, головний актив — текст',
            'шар болів (pain_*) — похідна розмітка за редагованим pain_lexicon.json, НЕ фільтр збору; корпус повний незалежно від словника',
            'частина видалених/модерованих дописів зберігає оригінальний текст, захоплений до видалення (див. removed_by_category) — дослідницький бонус',
            'пагінація overlap-safe з дедуплікацією; теоретична втрата на межах секунд < 0.1%',
        ],
    }
    json.dump(stats, open(os.path.join(EXPORTS, 'manifest.json'), 'w'), indent=2, ensure_ascii=False)
    print(json.dumps(stats, indent=2, ensure_ascii=False))

if __name__ == '__main__':
    part = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if part in ('posts', 'all'): export_posts()
    if part in ('comments', 'all'): export_comments()
    if part in ('stats', 'all'): export_stats()
