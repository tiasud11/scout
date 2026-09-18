"""Scout. Scheduled, cheap, read only. Finds and logs new roles. Never applies, never logs in.

Run:  python -m scout.scout            (respects the UK time guard)
      FORCE=1 python -m scout.scout    (manual run, ignores the guard)
"""
import hashlib
import json
import os
import sys
import traceback
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import adapters, classify, gate

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("SCOUT_DATA_DIR", ROOT))          # override only for tests
STATE = DATA / "state" / "seen.json"
FEED = DATA / "feed"
LONDON = ZoneInfo("Europe/London")
TARGETS = [(9, 0), (14, 30), (20, 30)]     # UK local time. 30 min before the Applicator runs at 09:30, 15:00, 21:00
WINDOW_MIN = 28                             # must finish before the Applicator starts. A cron that fires later than this is skipped
DETAIL_CAP = int(os.environ.get("DETAIL_CAP", "80"))   # postings opened per run. Overflow waits for next run.


def in_window(now=None):
    now = now or datetime.now(LONDON)
    mins = now.hour * 60 + now.minute
    return any(0 <= mins - (h * 60 + m) < WINDOW_MIN for h, m in TARGETS)


def load_json(p, default):
    return json.loads(p.read_text()) if p.exists() else default


def key_for(item):
    basis = adapters.clean_url(item.get("url", "")) or item.get("title", "")
    return hashlib.sha1(f"{basis}|{' '.join(item.get('title', '').lower().split())}".encode()).hexdigest()[:16]


def build_record(src, item, today):
    title, url, loc = item.get("title", ""), item.get("url", ""), item.get("location", "")
    pre = gate.evaluate(title, loc, "")
    body = ""
    if pre["status"] != "excluded" and url:
        body = adapters.page_text(url)
    res = gate.evaluate(title, loc, body) if body else pre
    rec = {
        "id": key_for(item),
        "firm": item.get("firm") or src.get("firm") or src["id"],
        "role_title": title, "division": src.get("division", ""), "location": loc or ("London" if "london" in body.lower()[:6000] else ""),
        "url": url, "source": src["id"],
        "date_posted": item.get("posted") or None, "date_found": today.isoformat(),
        "deadline": gate.find_deadline(item.get("deadline_hint", "") and f"deadline {item['deadline_hint']}" or body, today),
        "rolling": res["flags"]["rolling"], "type": res["type"],
        "eligibility": res["status"], "eligibility_notes": res["reasons"],
        "ai_policy": res["flags"]["ai_policy"], "injection_suspect": res["flags"]["injection_suspect"],
        "classified_by": "rules",
    }
    if res["status"] != "excluded":
        m = classify.classify(title, url, loc, body)
        if m and "_error" not in m:
            rec["classified_by"] = "rules+haiku"
            for k_model, k_rec in (("division", "division"), ("location", "location"), ("deadline", "deadline"),
                                   ("date_posted", "date_posted")):
                if m.get(k_model) and not rec.get(k_rec):
                    rec[k_rec] = m[k_model]
            if m.get("firm") and src.get("aggregator"):
                rec["firm"] = m["firm"]
            if m.get("rolling") is True:
                rec["rolling"] = True
            if m.get("ai_policy_verbatim") and m["ai_policy_verbatim"] in body:   # verbatim or nothing
                rec["ai_policy"] = m["ai_policy_verbatim"]
            if m.get("injection_suspect"):
                rec["injection_suspect"] = True
            if m["status"] == "excluded":
                if res["status"] == "check":
                    rec["eligibility"] = "excluded"
                    rec["eligibility_notes"] = [f"haiku: {m.get('reason', '')}"]
                else:   # rules said eligible, model disagrees. Default is inclusion, so keep and flag.
                    rec["eligibility"] = "check"
                    rec["eligibility_notes"].append(f"haiku disagreed: {m.get('reason', '')}")
            elif m["status"] == "eligible" and res["status"] == "check":
                rec["eligibility"] = "eligible"
                rec["eligibility_notes"] = [f"haiku confirmed: {m.get('reason', '')}"]
        elif m:
            rec["eligibility_notes"].append(f"haiku error, rules only: {m['_error']}")
    rec["priority"] = gate.priority(rec["deadline"], rec["rolling"], today)
    rec["_body"] = body
    if rec["injection_suspect"]:
        rec["priority"] = "HOLD"
    return rec


def main():
    if not (os.environ.get("FORCE") or os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch") and not in_window():
        print("Outside the UK run windows, exiting. (Two UTC crons cover BST and GMT, one of each pair no-ops.)")
        return 0
    today = datetime.now(LONDON).date()
    cfg = yaml.safe_load(Path(os.environ.get("SOURCES_FILE", ROOT / "sources.yaml")).read_text())
    state = load_json(STATE, {"seen": {}, "sources": {}})
    seen = state["seen"]
    roles = load_json(FEED / "roles.json", [])
    excluded = load_json(FEED / "excluded.json", [])
    known_urls = {adapters.clean_url(r["url"]) for r in roles + excluded if r.get("url")}
    health, new_roles, opened = [], [], 0

    for src in cfg["sources"]:
        if src.get("enabled") is False:
            continue
        try:
            items = adapters.ADAPTERS[src["type"]](src)
            health.append({"source": src["id"], "ok": True, "items": len(items)})
        except Exception as e:
            health.append({"source": src["id"], "ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"})
            traceback.print_exc()
            continue
        if not items:
            health[-1]["warn"] = "zero items, selector or URL may have changed"
        for item in items:
            k = key_for(item)
            if k in seen:
                continue
            cu = adapters.clean_url(item.get("url", ""))
            if cu and cu in known_urls:          # same posting already found via another source
                seen[k] = today.isoformat()
                continue
            if opened >= DETAIL_CAP:             # leave unseen so the next run picks it up
                continue
            opened += 1
            try:
                rec = build_record(src, item, today)
            except Exception as e:
                health.append({"source": src["id"], "ok": False, "error": f"record {item.get('title')}: {e}"})
                continue
            body = rec.pop("_body", "")
            if body and rec["eligibility"] != "excluded":     # saved so the Applicator can draft without any browser
                (FEED / "postings").mkdir(parents=True, exist_ok=True)
                (FEED / "postings" / f"{rec['id']}.txt").write_text(body)
                rec["posting_text"] = f"feed/postings/{rec['id']}.txt"
            seen[k] = today.isoformat()
            known_urls.add(cu)
            (excluded if rec["eligibility"] == "excluded" else new_roles).append(rec)
        state["sources"][src["id"]] = today.isoformat()

    roles.extend(new_roles)
    FEED.mkdir(parents=True, exist_ok=True)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    (FEED / "roles.json").write_text(json.dumps(roles, indent=1, ensure_ascii=False))
    (FEED / "excluded.json").write_text(json.dumps(excluded[-2000:], indent=1, ensure_ascii=False))
    stamp = datetime.now(LONDON).strftime("%Y-%m-%d %H:%M")
    (FEED / "health.json").write_text(json.dumps({"run": stamp, "haiku": classify.enabled(), "opened": opened,
                                                   "new": len(new_roles), "sources": health}, indent=1))
    (FEED / "new_latest.json").write_text(json.dumps({"run": stamp, "roles": new_roles}, indent=1, ensure_ascii=False))
    STATE.write_text(json.dumps(state, indent=0))
    order = {"HOLD": 0, "P1": 1, "P2": 2, "P3": 3}
    lines = [f"# Scout feed, last run {stamp} UK", "",
             "| Pri | Firm | Role | Type | Deadline | Rolling | Status | Found | Link |", "|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(roles, key=lambda r: (order.get(r["priority"], 9), r["deadline"] or "9999", r["firm"]))[:600]:
        lines.append(f"| {r['priority']} | {r['firm']} | {r['role_title'][:90]} | {r['type']} | {r['deadline'] or '?'} | "
                     f"{'yes' if r['rolling'] else ''} | {r['eligibility']} | {r['date_found']} | [open]({r['url']}) |")
    (FEED / "README.md").write_text("\n".join(lines) + "\n")
    bad = [h for h in health if not h["ok"]]
    print(f"{stamp}: {len(new_roles)} new, {opened} opened, {len(bad)} source errors, haiku={'on' if classify.enabled() else 'off'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
