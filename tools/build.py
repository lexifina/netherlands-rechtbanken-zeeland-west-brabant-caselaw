"""Stage 2: one Dutch case-law repo from the parsed buckets, straight into git fast-import.

  python build.py <repo group> <parsed dir> <out repo dir> --buckets b1,b2 --today YYYY-MM-DD [--law-repo DIR]

One commit per decision (Kind: court), dated the day decided (noon Amsterdam); then one Lexifina documentation commit
with README, REPRODUCE.md, corpus.json and the judgment overlay judgments/netherlands/national/acts/<act>/art-*.jsonl,
each row pinned to the netherlands-law commit in force on the law version date the court cites (the jci g= date) or
else on the decision day. Deterministic: the same input gives the same hashes."""
from __future__ import annotations

import argparse
import bisect
import gzip
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'swiss-caselaw'))
from build import law_index, slug, email_local, EPOCH_NOON  # noqa: E402  (shared with the Swiss build)

LAW_CORPUS = 'netherlands/national/acts'
AMS = timezone(timedelta(hours=1))
COMMITTER = b'Lexifina <corpus@lexifina.com>'
ISO = re.compile(r'^\d{4}-\d{2}-\d{2}$')
NAMES = {'hoge-raad': 'Hoge Raad (and its Parket)', 'raad-van-state': 'Raad van State',
         'centrale-raad-van-beroep': 'Centrale Raad van Beroep', 'college-van-beroep': 'College van Beroep voor het bedrijfsleven',
         'gerechtshoven': 'the gerechtshoven (courts of appeal)', 'overige': 'other courts (Caribbean courts and others)'}


def akey(s: str) -> str:
    parts = re.split(r'[:.\-]', s.strip().lower())
    return ':'.join(re.sub(r'^0+(?=\d)', '', x) for x in parts if x)


def resolve(idx: dict, bwb: str, art: str | None):
    folder = idx['bwb'].get(bwb)
    if not folder or not art or '(oud)' in art or 'oud' == art.strip().lower()[-3:]:
        return None
    files = idx['arts'].get(folder, {})
    a = re.sub(r'\s+', '', art.strip().lower())
    for k in (akey(a), akey(a.split(':')[-1]) if ':' in a else None):
        if k and k in files:
            return files[k]
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('group'), ap.add_argument('parsed'), ap.add_argument('out')
    ap.add_argument('--buckets', required=True), ap.add_argument('--today', required=True)
    ap.add_argument('--law-repo', default=str(Path.home() / 'Documents' / 'netherlands-law'))
    ap.add_argument('--input-note', default='')
    ap.add_argument('--chunk-bytes', type=int, default=2_500_000_000)
    a = ap.parse_args()
    out, group = Path(a.out), a.group
    idx = json.load(open(HERE / 'bwb_index.json', encoding='utf-8'))
    days, shas, _ = law_index(Path(a.law_repo))
    recs = []
    for b in a.buckets.split(','):
        for f in sorted((Path(a.parsed) / b).glob('*.jsonl.gz')):
            with gzip.open(f, 'rt', encoding='utf-8') as fh:
                recs += [json.loads(l) for l in fh]
    seen = {}
    for r in recs:  # one record per ECLI: the latest modified wins
        if r['ecli'] not in seen or (r.get('modified') or '') > (seen[r['ecli']].get('modified') or ''):
            seen[r['ecli']] = r
    recs = sorted(seen.values(), key=lambda r: ((r['decided'] or r['published'] or '9999')[:10], r['court_code'], r['ecli']))
    print(f'{group}: {len(recs)} decisions', file=sys.stderr, flush=True)

    if out.exists() and any(out.iterdir()):
        raise SystemExit(f'{out} is not empty')
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(out)], check=True)
    fi = subprocess.Popen(['git', '-C', str(out), 'fast-import', '--quiet', '--done', f'--export-marks={out / ".git" / "marks"}',
                          '--big-file-threshold=64m'], stdin=subprocess.PIPE)
    w = fi.stdin.write
    overlay, unresolved = {}, {}
    mark = raw = clamp = 0
    checkpoints, undated = [], []
    first = None
    for r in recs:
        day = (r['decided'] or '')[:10]
        if not ISO.match(day):
            day = (r['published'] or '')[:10]
        if not ISO.match(day):
            undated.append(r)
            continue
        first = first or day
        code = r['court_code'].lower()
        fname = r['ecli'].replace(':', '_')
        path = f'decisions/{code}/{day[:4]}/{fname}.md'
        court = r['court'] or r['court_code']
        cites = []
        for bwb, art, g, label in r['refs']:
            p = resolve(idx, bwb, art)
            if not p:
                unresolved[(bwb, art or '')] = unresolved.get((bwb, art or ''), 0) + 1
                continue
            cites.append((p, label, g))
        fm = {'ecli': r['ecli'], 'court': court, 'court_code': r['court_code'], 'docket': r['docket'], 'decided': r['decided'],
              'published': r['published'], 'type': r['type'], 'procedure': r['procedure'], 'subject': r['subject'],
              'source_url': r['url']}
        lines = ['---'] + [f'{k}: {json.dumps(v, ensure_ascii=False)}' for k, v in fm.items() if v not in (None, '')]
        if r['sources']:
            lines.append('published_in: ' + json.dumps(r['sources'], ensure_ascii=False))
        if r['relations']:
            lines.append('relations: ' + json.dumps(r['relations'], ensure_ascii=False))
        if cites:
            lines.append('cites_law: ' + json.dumps(sorted({f'{LAW_CORPUS}/{p}' for p, _, _ in cites}), ensure_ascii=False))
        lines += ['---', '', f"# {(r['title'] or r['ecli']).strip()}", '']
        if r['summary'] and r['summary'].strip() not in ('-', ''):
            lines += ['## Inhoudsindicatie', '', r['summary'].strip(), '']
        lines += ['## ' + ('Conclusie' if r['type'] == 'conclusie' else 'Uitspraak'), '', r['text'].strip(), '']
        body = '\n'.join(lines).encode('utf-8')
        raw += len(body)
        d0 = date.fromisoformat(day)
        if d0.year < 1970:
            clamp += 1
            ts = EPOCH_NOON + clamp - 3600
        else:
            ts = int(datetime(d0.year, d0.month, d0.day, 12, tzinfo=AMS).timestamp())
        author = f"{court} {r['ecli']}".replace('<', '').replace('>', '')
        email = f"{email_local(r['ecli'])}@caselaw.invalid"
        what = (r['subject'] or r['procedure'] or r['type']).split(';')[0].strip()
        tr = ['Kind: court', f'In-force: {day}']
        if r['decided']:
            tr.append(f"Decided: {r['decided'][:10]}")
        if r['published'] and ISO.match(r['published'][:10]):
            tr.append(f"Published: {r['published'][:10]}")
        tr += [f'Court: {court}', f"ECLI: {r['ecli']}"]
        if r['docket']:
            tr.append(f"Docket: {r['docket']}")
        tr.append(f"Source: {r['url']}")
        for p, label, _ in cites[:60]:
            tr.append(f'Cites: {LAW_CORPUS}/{p} ({label})')
        msg = (f"{court} {r['ecli']}: {what}"[:200] + '\n\n' + '\n'.join(tr) + '\n').encode('utf-8')
        mark += 1
        w(b'commit refs/heads/main\nmark :%d\n' % mark)
        w(b'author %s <%s> %d +0100\ncommitter %s %d +0100\n' % (author.encode('utf-8'), email.encode(), ts, COMMITTER, ts))
        w(b'data %d\n%s\n' % (len(msg), msg))
        w(b'M 100644 inline %s\ndata %d\n%s\n' % (path.encode('utf-8'), len(body), body))
        if raw >= (len(checkpoints) + 1) * a.chunk_bytes:
            checkpoints.append(mark)
        done = set()
        for p, label, g in cites:
            if p in done:
                continue
            done.add(p)
            td = g if g and ISO.match(g) else day
            i = bisect.bisect_right(days, td) - 1
            row = {'decided': day, 'name': f"{court} {r['ecli']}", 'court': court, 'citation': r['ecli'], 'url': r['url'],
                   'how': label, 'mentions': sum(1 for q, _, _ in cites if q == p), 'decision': path}
            if g and ISO.match(g) and g != day:
                row['text_date'] = g
            if i >= 0:
                row['law_commit'] = shas[i]
            overlay.setdefault(p, []).append(row)

    docs = {}
    for p, rows in overlay.items():
        rows.sort(key=lambda x: (x['decided'], x['citation']))
        docs[f'judgments/{LAW_CORPUS}/{p[:-3]}.jsonl'] = ''.join(json.dumps(x, ensure_ascii=False, sort_keys=True) + '\n' for x in rows)
    docs['unresolved.csv'] = 'bwb,artikel,references\n' + ''.join(f'{k[0]},{json.dumps(k[1], ensure_ascii=False)},{n}\n'
                                                                    for k, n in sorted(unresolved.items(), key=lambda kv: (-kv[1], kv[0])))
    for r in undated:
        docs[f"decisions/{r['court_code'].lower()}/undated/{r['ecli'].replace(':', '_')}.md"] = \
            f"---\necli: {json.dumps(r['ecli'])}\nsource_url: {json.dumps(r['url'])}\n---\n\n# {r['title'] or r['ecli']}\n\n{r['text']}\n"
    where = NAMES.get(group) or ('the rechtbanken (district courts): ' + group.removeprefix('rechtbanken-').replace('-', ' '))
    n_refs = sum(len(v) for v in overlay.values())
    gl_path = f'netherlands/{group}/caselaw'
    docs['corpus.json'] = json.dumps({
        'corpus': f'nl/{group}/caselaw', 'path': gl_path, 'name': f'Case law: Netherlands, {where}',
        'label': f'Netherlands case law ({where})', 'language': 'nl', 'kind': 'judgments', 'history': 'in-force',
        'coverage': f'{mark} published decisions with text (uitspraken and conclusies), one file and one commit each, dated the day decided; {n_refs} citations of law linked to netherlands-law.',
        'history_from': first, 'record_to': a.today, 'checked_on': a.today,
        'record_note': 'Court decisions, not laws: every commit is Kind: court. In-force is the day decided. Decisions the courts publish only as metadata (no text) are not included. Commits before 1970 are dated 1970-01-01, a second apart; In-force holds the real day.',
        'in_force_trailer': 'In-force', 'correction_trailer': 'Corrected',
        'dates': {'commit': 'decided', 'other_trailers': ['Decided', 'Published'], 'time': '12:00 Europe/Amsterdam'},
        'overlay': {'path': f'judgments/{LAW_CORPUS}/<act>/art-*.jsonl', 'law_corpus': LAW_CORPUS,
                    'fields': ['decided', 'text_date', 'name', 'court', 'citation', 'url', 'how', 'mentions', 'decision', 'law_commit']},
    }, ensure_ascii=False, indent=2) + '\n'
    repo = f'netherlands-{group}-caselaw'
    docs['README.md'] = f"""# Dutch case law: {where}

Every decision of {where} that the Rechtspraak publishes with its text, as a git history: one file per decision, one
commit per decision, dated the day it was decided. {mark:,} decisions from {first} to {a.today}. Part of
[lexifina/global-law](https://github.com/lexifina/global-law) at `{gl_path}`.

## Questions as git commands

| Question | Command |
|---|---|
| What did the court decide? | `cat decisions/<court code>/<year>/ECLI_NL_<...>.md` |
| A decision by its ECLI | `git log --grep='^ECLI: ECLI:NL:HR:2019:1234$' --stat` |
| Decisions given in a window | `git log --since=2024-03-01 --until=2024-03-31 --format='%ad %an' --date=short` |
| Decisions applying art. 6:162 BW | `cat judgments/netherlands/national/acts/bw-boek-6/art-162.jsonl` |
| The law's text as the court applied it | each overlay row's `law_commit` is the netherlands-law commit in force on the law version the court cites (`text_date`) or on the decision day |
| Who wrote a line? | `git blame decisions/...`: each line is credited to the decision (court and ECLI) |

## Dates
The commit date is the day the decision was given, at noon Amsterdam (`In-force:` and `Decided:`); `Published:` is the
day it was published on rechtspraak.nl. Git cannot hold dates before 1970, so earlier decisions are committed on
1970-01-01, a second apart, and `In-force:` holds the real day.

## Author
The author of each commit is the court and the decision's ECLI, at an address that cannot exist. These are court
rulings, not laws: every commit carries `Kind: court`, and global-law's tools credit them as such and leave them out of
lists of laws.

## The judgment overlay
The courts' own links to the law (`Wetsverwijzing`, by BWB id and article) are filed under the law's paths:
`judgments/netherlands/national/acts/<act>/art-*.jsonl`. {n_refs:,} are linked; links to acts or articles the law
record does not hold are counted in `unresolved.csv`.

## What it does not show
- Decisions published only as metadata, and decisions not published at all.
- Anonymisation is the court's own.

## How it is built and the source
`tools/build.py` from the inputs in REPRODUCE.md, through git fast-import; the same inputs give the same hashes. The
decisions are the Rechtspraak's own open data (Raad voor de Rechtspraak), in the public domain; each file links to its
page on rechtspraak.nl. The arrangement in this repository is dedicated to the public domain under CC0 1.0.
"""
    docs['REPRODUCE.md'] = f"""# Reproducing this repository

Input: the decision XML (open-rechtspraak format) of {a.input_note or 'the yearly archives named in the build log'},
parsed by `tools/parse.py`, and lexifina/netherlands-law `main` for the overlay (`tools/bwb_index.py`).

```
python tools/parse.py <archives> <parsed>
python tools/build.py {group} <parsed> <empty folder> --buckets {a.buckets} --today {a.today}
```

Gives {mark:,} decision commits and one documentation commit, hash for hash.
"""
    for f in ('parse.py', 'build.py', 'bwb_index.py'):
        docs[f'tools/{f}'] = (HERE / f).read_text(encoding='utf-8')
    ts = int(datetime.fromisoformat(a.today + 'T12:00:00+01:00').timestamp())
    msg = f'Documentation: README, REPRODUCE, corpus.json and the judgment overlay ({n_refs} citations)\n'.encode()
    w(b'commit refs/heads/main\nauthor Lexifina <corpus@lexifina.com> %d +0100\ncommitter %s %d +0100\n' % (ts, COMMITTER, ts))
    w(b'data %d\n%s\n' % (len(msg), msg))
    for p in sorted(docs):
        b = docs[p].encode('utf-8')
        w(b'M 100644 inline %s\ndata %d\n%s\n' % (p.encode('utf-8'), len(b), b))
    w(b'done\n')
    fi.stdin.close()
    if fi.wait():
        raise SystemExit('fast-import failed')
    marks = dict(l.split() for l in (out / '.git' / 'marks').read_text().splitlines())
    (out / '.git' / 'push-checkpoints').write_text(''.join(marks[f':{m}'] + '\n' for m in checkpoints))
    print(json.dumps({'group': group, 'decisions': mark, 'undated': len(undated), 'overlay_rows': n_refs,
                      'unresolved_refs': sum(unresolved.values()), 'raw_gb': round(raw / 1e9, 2), 'checkpoints': len(checkpoints)}), flush=True)


if __name__ == '__main__':
    main()
