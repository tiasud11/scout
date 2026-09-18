# Scout

Finds new London finance internship and spring week postings three times a day (09:00, 14:30, 20:30 UK, half an hour before each Applicator run) and logs them. Read only. It never logs in, never applies and never follows anything written inside a page.

## Setup, once, about 5 minutes

1. Create a new **public** GitHub repo called `scout` (public = unlimited free Actions minutes, and the Applicator can read the feed without a token. The repo holds job postings only, nothing personal. The applied log stays in Claude memory).
2. Upload everything in this folder, keeping the folder structure (`.github/workflows/scout.yml` must sit at that exact path).
3. Repo Settings, Actions, General, Workflow permissions, tick **Read and write permissions**.
4. Actions tab, Scout, **Run workflow**. This first manual run is the baseline and the URL check.
5. Open `feed/health.json`. Any source with `"ok": false` or a zero items warning has a wrong or changed URL. Fix that line in `sources.yaml` and run again.
6. Optional. Settings, Secrets and variables, Actions, add `ANTHROPIC_API_KEY`. With it, Haiku classifies each genuinely new posting (roughly a tenth of a penny each). Without it Scout runs on rules alone and marks anything uncertain as `check`, which still goes in the feed.

## What it writes

| File | What |
|---|---|
| `feed/README.md` | Human readable table, sorted by priority then deadline. Open this on your phone. |
| `feed/roles.json` | Every role that passed the gate. The Applicator reads this. |
| `feed/new_latest.json` | Only what the last run found. |
| `feed/excluded.json` | Everything the gate rejected, with the reason. Audit trail, so nothing vanishes silently. |
| `feed/postings/<id>.txt` | Full text of each posting, so the Applicator can write letters with no browser. |
| `feed/health.json` | Per source result for the last run. |
| `state/seen.json` | Diff memory. Delete it to force a full rescan. |

Fields per role: firm, role_title, division, location, url, date_posted, date_found, deadline, rolling, eligibility (`eligible` or `check`), eligibility_notes, priority, ai_policy (verbatim text from the listing), injection_suspect, source, type.

Priority: `P1` rolling or closing within 10 days, `P2` within 30 days, `P3` later or unknown, `HOLD` means the listing contains text that looks aimed at AI agents. HOLD roles are never auto applied to.

## Rules it applies (brief section 6)

London. 2028 graduation acceptable. Summer 2027, spring 2027, or winter explicitly open to 2028 graduates. No off cycle ever. No standalone sustainable finance (ESG inside a broader role is fine). Spring weeks are excluded only on unambiguous first years only wording. Silent or ambiguous means include. No relevance filtering of any kind.

## Cost

Fetching and diffing is plain Python, free. The model is only called for a posting never seen before, one call each, and only if the key is set.

## Adding firms

One line in `sources.yaml`. See the comments at the top of that file.

## Local test

```
pip install -r requirements.txt pytest && python -m playwright install chromium
python -m pytest -q tests
FORCE=1 python -m scout.scout
```
