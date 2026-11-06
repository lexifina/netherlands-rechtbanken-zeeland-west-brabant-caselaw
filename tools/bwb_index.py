"""BWB id -> act folder in lexifina/netherlands-law (from each act README's wetten.overheid.nl link), and each
folder's article files keyed by the normalised article number ('8:69a', '162'). Writes bwb_index.json."""
import json, re, subprocess, sys
from pathlib import Path
LAW = Path(sys.argv[1] if len(sys.argv) > 1 else Path.home() / 'Documents' / 'netherlands-law')
ref = 'origin/main'
names = subprocess.run(['git', '-C', str(LAW), 'ls-tree', '-r', '--name-only', ref], capture_output=True, text=True, encoding='utf-8').stdout.split('\n')
readmes = [n for n in names if n.count('/') == 1 and n.endswith('/README.md')]
raw = subprocess.run(['git', '-C', str(LAW), 'cat-file', '--batch'], input=''.join(f'{ref}:{p}\n' for p in readmes).encode(), capture_output=True).stdout
bwb, i = {}, 0
for p in readmes:
    nl = raw.index(b'\n', i); size = int(raw[i:nl].split()[2]); blob = raw[nl + 1:nl + 1 + size]; i = nl + 2 + size
    m = re.search(rb'wetten\.overheid\.nl/(BWB[RV]\d+)', blob[:2000])
    if m: bwb.setdefault(m.group(1).decode(), p.split('/')[0])
def key(s):
    parts = re.split(r'[:.\-]', s.strip().lower())
    return ':'.join(re.sub(r'^0+(?=\d)', '', x) for x in parts if x)
arts = {}
for n in names:
    m = re.match(r'([^/]+)/art-([^/]+)\.md$', n)
    if m: arts.setdefault(m.group(1), {})[key(m.group(2))] = n
json.dump({'bwb': bwb, 'arts': arts}, open('bwb_index.json', 'w', encoding='utf-8'), ensure_ascii=False)
print(len(readmes), 'acts;', len(bwb), 'with a BWB id;', sum(len(v) for v in arts.values()), 'article files')
for k in ('BWBR0005289', 'BWBR0005537', 'BWBR0001854', 'BWBR0001827', 'BWBR0001903'): print(k, bwb.get(k))
