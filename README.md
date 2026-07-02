# База болів віддалених працівників — r/remotework + r/WorkFromHome + r/telecommuting, повний рік

**Вікно:** 2025-07-02 → 2026-07-02 (365 днів)
**Джерело:** Arctic Shift — публічне дослідницьке дзеркало Reddit (прямий Reddit блокує серверний доступ; дзеркало свіже до поточної доби)
**Зібрано:** 2026-07-02

## Обсяг

| Що | Скільки |
|---|---|
| Пости у вікні (всі сабредіти) | **34558** |
| Коментарі (повні гілки, з parent_id) | **432512** |
| Контекстні пости (треди старші за рік) | 1386 |
| Унікальних авторів | 143524 |
| Постів із флагом болю | 6599 |
| Коментарів із флагом болю | 61651 |

Розбивка: r/remotework — 31 589 постів / 410 451 коментар; r/WorkFromHome — 2 844 / 21 982; r/telecommuting — 125 / 79. Порівняння профілів болю по сабредітах — у `pain_theme_by_sub.csv`.

## Головний принцип

**Зібрано ВСЕ, розмічено ПОТІМ.** Жодної фільтрації на вході — корпус повний.
Шар болів — це похідна розмітка за редагованим словником `pain_lexicon.json`
(16 тем, ~200 термінів). Змінюєш словник → переганяєш експорт → нова розмітка
на тому самому корпусі. Це прибирає упередженість відбору.

## Файли

```
db.sqlite            ← ядро бази: таблиці posts, comments (SQLite, індексовано)
pain_lexicon.json    ← словник тем болю (редагується)
collector.py         ← збирач (resumable, дедуплікація, шарди)
exporter.py          ← експорт CSV + розмітка болів + статистика
run_chunk.py         ← оркестратор паралельних шардів
data/cursor_*.json   ← курсори прогресу (для довбирання/розширення)
exports/
  posts.csv              всі пости + колонки pain_themes / pain_terms
  comments.csv           всі коментарі + глибина гілки + pain-колонки
  pain_posts.csv         лише пости з болями (робочий файл)
  pain_comments.csv      лише коментарі з болями (робочий файл)
  pain_theme_counts.csv  розподіл тем
  top_terms.csv          топ-250 уніграм і біграм реальної лексики корпусу
  manifest.json          метадані збору + застереження
```

## Ключові колонки

**posts.csv:** post_id, created_date, in_window (1=у вікні, 0=контекстний старший тред), author, title, selftext, score_snapshot, num_comments_snapshot, comments_collected (скільки реально зібрано в базі), flair, removed_by_category, pain_themes, pain_terms, permalink_url

**comments.csv:** comment_id, post_id (з'єднання з posts), parent_id (t1_… = відповідь на коментар, t3_… = кореневий), depth (0 = кореневий; -1 = предок поза вікном), body, score_snapshot, pain_themes, pain_terms, permalink_url

## Приклади запитів до db.sqlite

```sql
-- Найкоментованіші треди про стеження за працівниками
SELECT p.title, p.created_date, COUNT(c.id) AS n
FROM posts p JOIN comments c ON c.link_id='t3_'||p.id
WHERE lower(p.title||p.selftext) LIKE '%monitor%'
GROUP BY p.id ORDER BY n DESC LIMIT 20;

-- Динаміка теми по місяцях (приклад: RTO)
SELECT substr(created_date,1,7) AS m, COUNT(*) FROM comments
WHERE lower(body) LIKE '%return to office%' OR lower(body) LIKE '%rto%'
GROUP BY m ORDER BY m;
```

## Як розширювати

**Довібрати новий період** (наприклад, через місяць):
```bash
python3 collector.py --phase posts --after <останній_epoch> --before <новий_epoch> --tag ext1
python3 collector.py --phase comments --after <...> --before <...> --tag ext1
python3 collector.py --phase merge && python3 collector.py --phase context
python3 exporter.py all
```

**Додати сабредіт** (r/WorkFromHome, r/overemployed, …):
```bash
python3 collector.py --phase posts --subreddits WorkFromHome --days 365
python3 collector.py --phase comments --subreddits WorkFromHome --days 365
python3 collector.py --phase merge && python3 exporter.py all
```
Усе доливається в ту саму базу з дедуплікацією; колонка subreddit розрізняє джерела.

**Змінити розмітку болів:** редагуй `pain_lexicon.json` → `python3 exporter.py all`.

Технічні нюанси збирача: сторінки `limit=auto` (до 1000 рядків), на «щільних»
ділянках автофолбек на `limit=100` (або `FORCE_LIMIT=100` в env); курсори
атомарні — процес можна вбивати будь-коли без втрат.

## Застереження (чесно)

1. **score / num_comments — снапшоти дзеркала** на момент інджесту, не живі
   значення. Для ранжування — приблизний сигнал; головний актив — текст.
   `comments_collected` у posts.csv — фактична кількість у базі, вона надійна.
2. Частина видалених/модерованих дописів **зберігає оригінальний текст**,
   захоплений до видалення (`removed_by_category`) — дослідницький бонус.
3. Пагінація overlap-safe з дедуплікацією; теоретичні втрати < 0,1%.
4. 2 сирітські треди з 1 335 не вдалося довантажити (їхні коментарі в базі є).

## Використання в книгах

Це публічні дописи, зібрані для дослідження болів аудиторії. Для рукописів —
стандартна практика: перефразування, композитні персонажі, без ніків і
дослівних цитат, що ідентифікують автора.
