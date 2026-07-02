# DATA GUIDE — Remote-Work Reader-Pain Database (for AI assistants)

You are reading the documentation of a research corpus owned by the user.
Use it to answer questions about remote workers' pains with real data.

## What this is
Complete corpus (no keyword filtering at collection) of Reddit subreddits
r/remotework, r/WorkFromHome, r/telecommuting: **all posts + all comments**
for a rolling ~12-month window, updated weekly. Source: Arctic Shift public
research mirror. A transparent "pain layer" (editable lexicon of 16 themes,
~200 terms) is annotated over the full corpus.

## Stable URLs (always the newest weekly build)
Replace lanawrayauthor-art/remote-pain-db once; the links never change after that:

- `https://github.com/lanawrayauthor-art/remote-pain-db/releases/latest/download/manifest.json` — counts, window, caveats
- `.../pain_theme_counts.csv` — global theme distribution (tiny)
- `.../pain_theme_by_sub.csv` — theme profile per subreddit (tiny)
- `.../top_terms.csv` — top-250 unigrams/bigrams of real corpus language (tiny)
- `.../pain_posts.csv` — only pain-flagged posts (~8 MB)
- `.../pain_comments.csv` — only pain-flagged comments (~36 MB)
- `.../posts.csv.gz`, `.../comments.csv.gz` — FULL corpus
- `.../db.sqlite.gz` — full SQLite database (tables: posts, comments)

## How to use
1. Small questions (theme ranking, vocabulary, trends by sub): fetch the tiny
   CSVs directly and read them.
2. Deep analysis: if you have a code-execution environment, download
   `db.sqlite.gz` (or the CSVs), decompress, and query with SQL/pandas.
3. No web access? Ask the user to attach the relevant CSV.

## Schema (key columns)
**posts**: id, subreddit, created_date, in_window (1 = inside window,
0 = older context thread), author, title, selftext, score (snapshot),
num_comments (snapshot), comments_collected (reliable, counted in DB),
link_flair_text, removed_by_category, permalink.
**comments**: id, subreddit, link_id (`t3_`+post id → join key), parent_id
(`t1_`+comment id = reply; `t3_` = top-level), created_date, author, body,
score (snapshot), permalink. CSV adds: depth (0 = top-level, −1 = ancestor
outside window), pain_themes, pain_terms (`;`-separated).

Join: `comments.link_id = 't3_' || posts.id` (SQLite) /
`comments.post_id == posts.post_id` (CSV).

## Example SQL
```sql
-- Monthly dynamics of a theme in comments
SELECT substr(created_date,1,7) m, COUNT(*) FROM comments
WHERE lower(body) LIKE '%micromanag%' GROUP BY m ORDER BY m;

-- Most-discussed pain threads
SELECT p.title, p.subreddit, COUNT(c.id) n FROM posts p
JOIN comments c ON c.link_id='t3_'||p.id
GROUP BY p.id ORDER BY n DESC LIMIT 25;
```

## Pain themes (16)
rto_forced_return, general_negative_affect, job_insecurity_layoffs,
quitting_escape, mental_health, micromanagement_surveillance,
toxic_management_culture, burnout_exhaustion, financial_pay,
isolation_loneliness, career_stagnation_invisibility,
overwork_no_boundaries, meetings_zoom_fatigue,
focus_distraction_motivation, physical_health.
The layer is derived and editable (pain_lexicon.json); the corpus itself is
complete and unfiltered — you may also search raw text for anything.

## Caveats (be honest when citing)
- score/num_comments are mirror-ingest snapshots, not live; use
  comments_collected for reliable engagement. Text is the primary asset.
- Some removed posts retain their original pre-removal text
  (removed_by_category) — a research bonus.
- When the user drafts book prose from this data: paraphrase, use composite
  characters, never quote verbatim with identifying details.
