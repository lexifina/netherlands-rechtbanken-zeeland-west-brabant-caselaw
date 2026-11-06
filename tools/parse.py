"""Stage 1: every decision XML (open-rechtspraak format) in the yearly archives -> one gzip JSONL per court group and
year, decisions with text only. Parallel by archive.

  python parse.py <cache dir with exports/**.tar.gz> <out dir> [--workers 16] [--xml-dir DIR]

A record: ecli, court, court_code, decided, published, modified, docket, type, procedure, subject, title, summary,
text, refs [[bwb, artikel, g, label]], relations [[type, ecli]], sources [vindplaatsen], url."""
from __future__ import annotations

import argparse
import gzip
import html
import json
import re
import sys
import tarfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ECLI = re.compile(r'<dcterms:identifier>(ECLI:[^<]+)</dcterms:identifier>')
FIRST = lambda tag: re.compile(r'<' + tag + r'\b[^>]*>([^<]*)</' + tag + '>')
CREATOR, DATE, ISSUED, MODIFIED = FIRST('dcterms:creator'), FIRST('dcterms:date'), FIRST('dcterms:issued'), FIRST('dcterms:modified')
ZAAK, TYPE, PROC, SUBJ, TITLE = FIRST('psi:zaaknummer'), FIRST('dcterms:type'), FIRST('psi:procedure'), FIRST('dcterms:subject'), FIRST('dcterms:title')
REF = re.compile(r'<dcterms:references\b[^>]*resourceIdentifier="([^"]*)"[^>]*>([^<]*)</dcterms:references>')
REL = re.compile(r'<dcterms:relation\b([^>]*)>([^<]*)</dcterms:relation>')
LI = re.compile(r'<rdf:li>([^<]*)</rdf:li>')
BODY = re.compile(r'<(uitspraak|conclusie)\b[^>]*>(.*)</\1>', re.S)
INH = re.compile(r'<inhoudsindicatie\b[^>]*>(.*?)</inhoudsindicatie>', re.S)
BLOCK_END = re.compile(r'</(para|title|nr|parablock|paragroup|section|listitem|row|entry|br|alinea)>|<br\s*/>|<(para|title)\b[^>]*/>', re.I)
TAG = re.compile(r'<[^>]+>')


def text_of(xml: str) -> str:
    t = BLOCK_END.sub('\n', xml)
    t = html.unescape(TAG.sub('', t))
    lines = [l.strip() for l in t.replace('\r', '').split('\n')]
    out, blank = [], False
    for l in lines:
        if not l:
            blank = bool(out)
            continue
        if blank:
            out.append('')
        out.append(l)
        blank = False
    return '\n'.join(out)


def group_of(code: str) -> str:
    c = code.upper()
    if c in ('HR', 'PHR'):
        return 'hoge-raad'
    if c == 'RVS':
        return 'raad-van-state'
    if c == 'CRVB':
        return 'centrale-raad-van-beroep'
    if c == 'CBB':
        return 'college-van-beroep'
    if c.startswith('GH'):
        return 'gerechtshoven'
    if c.startswith('RB'):
        return 'rb-' + c[2:].lower()  # merged into rechtbanken repos by region in build.py
    return 'overige'


def parse(raw: bytes) -> dict | None:
    s = raw.decode('utf-8', 'replace')
    body = BODY.search(s)
    if not body:
        return None
    m = ECLI.search(s)
    if not m:
        return None
    ecli = m.group(1).strip()
    rdf = s[:body.start()]
    g = lambda rx: (lambda x: html.unescape(x.group(1)).strip() if x else None)(rx.search(rdf))
    refs = []
    for rid, label in REF.findall(rdf):
        rid = html.unescape(rid)
        mb = re.search(r'(BWB[RV]\d+)', rid)
        if not mb:
            continue
        art = re.search(r'[&?]artikel=([^&]+)', rid)
        gd = re.search(r'[&?]g=(\d{4}-\d{2}-\d{2})', rid)
        refs.append([mb.group(1), art.group(1).strip() if art else None, gd.group(1) if gd else None, html.unescape(label).strip()])
    rels = []
    for attrs, val in REL.findall(rdf):
        lab = re.search(r'rdfs:label="([^"]*)"', attrs)
        rels.append([lab.group(1) if lab else '', html.unescape(val).strip()])
    inh = INH.search(s)
    code = ecli.split(':')[2] if ecli.count(':') >= 3 else 'X'
    return {'ecli': ecli, 'court': g(CREATOR), 'court_code': code, 'decided': g(DATE), 'published': g(ISSUED),
            'modified': g(MODIFIED), 'docket': g(ZAAK), 'type': body.group(1), 'procedure': g(PROC), 'subject': g(SUBJ),
            'title': g(TITLE), 'summary': text_of(inh.group(1)) if inh else '', 'text': text_of(body.group(2)),
            'refs': refs, 'relations': rels, 'sources': [html.unescape(x).strip() for x in LI.findall(rdf)],
            'url': f'https://uitspraken.rechtspraak.nl/details?id={ecli}'}


def one(tar_path: str, out: str) -> dict:
    out_dir = Path(out)
    year = Path(tar_path).name.split('.')[0]
    buckets: dict[str, list] = {}
    n = meta_only = 0
    with tarfile.open(tar_path, 'r:gz') as tf:
        for mem in tf:
            if not mem.isfile() or not mem.name.endswith('.xml'):
                continue
            n += 1
            r = parse(tf.extractfile(mem).read())
            if not r:
                meta_only += 1
                continue
            buckets.setdefault(group_of(r['court_code']), []).append(r)
    for grp, rows in buckets.items():
        d = out_dir / grp
        d.mkdir(parents=True, exist_ok=True)
        with gzip.open(d / f'{year}.jsonl.gz', 'wt', encoding='utf-8', compresslevel=3) as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
    return {'year': year, 'xml': n, 'metadata_only': meta_only, 'by_group': {k: len(v) for k, v in buckets.items()}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cache'), ap.add_argument('out')
    ap.add_argument('--workers', type=int, default=16)
    a = ap.parse_args()
    tars = sorted(Path(a.cache).glob('exports/**/*.tar.gz'), key=lambda p: -p.stat().st_size)
    Path(a.out).mkdir(parents=True, exist_ok=True)
    stats = []
    with ProcessPoolExecutor(a.workers) as ex:
        for s in ex.map(one, [str(t) for t in tars], [a.out] * len(tars)):
            stats.append(s)
            print(json.dumps(s), flush=True)
    (Path(a.out) / 'stats.json').write_text(json.dumps(stats, indent=0))
    tot = {}
    for s in stats:
        for k, v in s['by_group'].items():
            tot[k] = tot.get(k, 0) + v
    print('TOTAL xml', sum(s['xml'] for s in stats), 'metadata-only', sum(s['metadata_only'] for s in stats),
          'with text', sum(tot.values()), json.dumps(dict(sorted(tot.items(), key=lambda kv: -kv[1]))), flush=True)


if __name__ == '__main__':
    main()
