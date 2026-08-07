"""Two CSVs, IDENTICAL columns, ONE unified score (weighted in-degree). NIL where data absent."""
import asyncio, asyncpg, csv, sqlite3, os
from dotenv import load_dotenv
load_dotenv()
PW = os.environ["LOUDRR_PG_PASSWORD"]  # set in .env (never hardcode)
COLS = ['rank', 'user_id', 'username', 'is_voter', 'unified_score', 'unified_score_div5',
        'voting_power', 'elite_followers', 'x_followers', 'sorsa', 'twitterscore']
NIL = 'NIL'


def vendor():
    cur = sqlite3.connect('data/harvest.db').cursor()
    sor = {u.lower(): s for u, s in cur.execute(
        "SELECT username, sorsa_score FROM harvested_scores WHERE username IS NOT NULL AND sorsa_score IS NOT NULL")}
    ts = {u.lower(): s for u, s in cur.execute(
        "SELECT username, twitterscore FROM twitterscore_accounts WHERE username IS NOT NULL AND twitterscore IS NOT NULL")}
    return sor, ts


def writecsv(path, rows):
    rows.sort(key=lambda r: -r['_sc'])
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(COLS)
        for i, r in enumerate(rows, 1):
            w.writerow([i, r['user_id'], r['username'], r['is_voter'], round(r['_sc']),
                        round(r['_sc'] / 5), r['voting_power'], r['elite_followers'],
                        r['x_followers'], r['sorsa'], r['twitterscore']])
    print(f'wrote {path} ({len(rows)} rows)')


async def main():
    os.makedirs('data/exports', exist_ok=True)
    sor, ts = vendor()
    pg = await asyncpg.connect(host='213.199.54.248', port=5433, user='postgres',
                               password=PW, database='loudrr_analytics', timeout=90)

    # ---- VOTERS ----
    wid = {r['fid']: (float(r['sc']), r['c']) for r in await pg.fetch('''
        SELECT e.followee_id fid, sum(s.score) sc, count(*) c
        FROM edges e JOIN smart_set s ON e.follower_id=s.user_id
        JOIN smart_set t ON t.user_id=e.followee_id
        GROUP BY e.followee_id''')}
    vrows = []
    for r in await pg.fetch('SELECT user_id, username, score FROM smart_set'):
        sc, c = wid.get(r['user_id'], (0.0, 0))
        un = (r['username'] or '').lower()
        vrows.append({'user_id': r['user_id'], 'username': r['username'], 'is_voter': 'yes',
                      '_sc': sc, 'voting_power': round(r['score'] or 0),
                      'elite_followers': c, 'x_followers': NIL,
                      'sorsa': round(sor[un], 1) if un in sor else NIL,
                      'twitterscore': round(ts[un], 1) if un in ts else NIL})
    await pg.close()
    writecsv('data/exports/smart_set_voters.csv', vrows)

    # ---- DISCOVERED (already scored; reuse ids+named, same columns) ----
    ids = list(csv.DictReader(open('data/exports/discovered_top_ids.csv', encoding='utf-8')))
    nm = list(csv.DictReader(open('data/exports/discovered_top_named.csv', encoding='utf-8')))
    drows = []
    for a, b in zip(ids, nm):
        drows.append({'user_id': a['user_id'], 'username': b['username'], 'is_voter': 'no',
                      '_sc': float(a['our_score']), 'voting_power': NIL,
                      'elite_followers': a['elite_followers'],
                      'x_followers': b['x_followers'] or NIL,
                      'sorsa': b['sorsa'] or NIL, 'twitterscore': b['twitterscore'] or NIL})
    writecsv('data/exports/discovered_accounts.csv', drows)
    print('\nBOTH files share identical columns:')
    print('  ' + ', '.join(COLS))


asyncio.run(main())
