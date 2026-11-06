# Dutch case law: the rechtbanken (district courts): zeeland west brabant

Every decision of the rechtbanken (district courts): zeeland west brabant that the Rechtspraak publishes with its text, as a git history: one file per decision, one
commit per decision, dated the day it was decided. 47,070 decisions from 1990-10-12 to 2026-11-06. Part of
[lexifina/global-law](https://github.com/lexifina/global-law) at `netherlands/rechtbanken-zeeland-west-brabant/caselaw`.

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
`judgments/netherlands/national/acts/<act>/art-*.jsonl`. 2,244 are linked; links to acts or articles the law
record does not hold are counted in `unresolved.csv`.

## What it does not show
- Decisions published only as metadata, and decisions not published at all.
- Anonymisation is the court's own.

## How it is built and the source
`tools/build.py` from the inputs in REPRODUCE.md, through git fast-import; the same inputs give the same hashes. The
decisions are the Rechtspraak's own open data (Raad voor de Rechtspraak), in the public domain; each file links to its
page on rechtspraak.nl. The arrangement in this repository is dedicated to the public domain under CC0 1.0.
