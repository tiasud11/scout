"""Eligibility gate (brief section 6). Pure rules, no model, no network.

Statuses
  eligible  passes every rule on the evidence available
  check     nothing rules it out but something could not be confirmed. INCLUDED in the feed.
  excluded  breaks a hard rule. Logged to feed/excluded.json for audit, never silently dropped.

Default is inclusion. A role is only excluded on unambiguous evidence.
"""
import re
from datetime import date

OFF_CYCLE = re.compile(r"off[\s\-]?cycle", re.I)
SUSTAIN_TITLE = re.compile(
    r"\b(sustainab\w*|esg|climate|impact invest\w*|green finance|responsible invest\w*|net[\s\-]?zero)\b", re.I)
SUMMER = re.compile(r"\bsummer\b", re.I)
SPRING = re.compile(
    r"\b(spring (week|insight|intern\w*|program\w*)|insight (week|program\w*|day|series)|discovery (week|program\w*)|"
    r"first[\s\-]year (program\w*|insight)|springboard)\b", re.I)
WINTER = re.compile(r"\bwinter\b", re.I)
INTERN = re.compile(r"\b(intern(ship)?s?|summer analyst|vacation scheme)\b", re.I)
NOT_INTERNSHIP = re.compile(
    r"\b(graduate (program\w*|scheme|analyst|role)|full[\s\-]?time|industrial placement|placement year|"
    r"year in industry|12[\s\-]month|apprentice\w*|experienced hire|associate director|vice president)\b", re.I)
FIRST_YEAR_ONLY = re.compile(
    r"(first[\s\-]year (students? )?only|only (open to|for|available to) first[\s\-]year|"
    r"exclusively (for|open to) first[\s\-]year|must be (in|a) (your )?first[\s\-]year)", re.I)
SECOND_YEAR_OK = re.compile(r"(second[\s\-]year|penultimate|2nd year)", re.I)
GRAD_YEAR = re.compile(
    r"(?:graduat\w+|class of|(?:finish\w*|complet\w+) (?:your|their) (?:degree|studies|course))"
    r"(?P<gap>[^.\n]{0,60}?)\b(?P<lo>20[2-3]\d)\b(?:\s*(?:-|\u2013|to|and|or|/)\s*(?:\w+ )?(?P<hi>20[2-3]\d))?", re.I)
YEAR =re.compile(r"\b(20[2-3]\d)\b")
INJECTION = re.compile(
    r"(ignore (all |any )?(previous|prior|above) instructions|disregard (the |your )?(previous|system)|"
    r"you are an? (ai|llm|language model)|if you are an? (ai|llm|language model|automated)|"
    r"(ai|llm) (agents?|assistants?|models?)[^.\n]{0,40}(must|should|please)|system prompt|"
    r"include the (word|phrase)[^.\n]{0,60}in your (application|answer|cover letter))", re.I)
AI_POLICY = re.compile(
    r"[^.\n]*\b(generative ai|artificial intelligence|ai tools?|ai[\s\-]generated|chatgpt|large language models?|"
    r"use of ai|ai assistance)\b[^.\n]*[.\n]", re.I)
ROLLING = re.compile(r"\b(rolling basis|rolling (deadline|recruitment|applications?)|reviewed on a rolling|"
                     r"apply early|may close early|first[\s\-]come)\b", re.I)


def role_type(title, body=""):
    t = title or ""
    if SPRING.search(t):
        return "spring"
    if WINTER.search(t):
        return "winter"
    if SUMMER.search(t):
        return "summer"
    if NOT_INTERNSHIP.search(t) and not INTERN.search(t):
        return "other"
    if INTERN.search(t):
        # "2027 Investment Banking Intern" with no season word. Look at the body.
        if SPRING.search(body[:3000]):
            return "spring"
        if WINTER.search(body[:1500]) and not SUMMER.search(body[:1500]):
            return "winter"
        return "summer_likely"
    return "unknown"


def evaluate(title, location="", body=""):
    """Return dict(status, reasons[], type, flags{})."""
    title, location, body = title or "", location or "", body or ""
    text = f"{title}\n{body}"
    reasons, status = [], "eligible"
    flags = {
        "rolling": bool(ROLLING.search(text)),
        "injection_suspect": bool(INJECTION.search(text)),
        "ai_policy": " ".join(m.group(0).strip() for m in AI_POLICY.finditer(body))[:1200] or None,
    }

    def exclude(r):
        return {"status": "excluded", "reasons": [r], "type": rtype, "flags": flags}

    rtype = role_type(title, body)

    # Rule 4. No off cycle, ever. Title is decisive. Body only counts if title has no season.
    if OFF_CYCLE.search(title):
        return exclude("off cycle in title")
    if rtype in ("unknown", "summer_likely") and OFF_CYCLE.search(body[:1500]):
        return exclude("off cycle in description, no season in title")

    # Rule 5. Standalone sustainable finance = sustainability is the role itself (in the title).
    if SUSTAIN_TITLE.search(title):
        return exclude("standalone sustainable finance role (title)")

    # Rule 3. Programme type.
    if rtype == "other":
        return exclude("not an internship or spring week (graduate, placement or experienced)")
    if rtype in ("unknown", "summer_likely"):
        status = "check"
        reasons.append("programme type not explicit, confirm summer 2027 or spring 2027")

    # Year. Exclude only if a different programme year is explicit and 2027 never appears.
    years = set(YEAR.findall(title))
    if years and "2027" not in years and not (rtype == "winter" and years & {"2026", "2027"}):
        if not re.search(r"\b2027\b", body[:4000]):
            return exclude(f"programme year {sorted(years)} not 2027")
    if not years and "2027" not in body[:4000]:
        status = "check"
        reasons.append("no programme year stated")

    # Rule 2. Graduation year 2028 acceptable.
    grad_years = []
    for m in GRAD_YEAR.finditer(text):
        if re.search(r"(intern|summer|spring|program)", m.group("gap"), re.I):
            continue  # the year belongs to the programme, not to graduation
        lo = int(m.group("lo"))
        hi = int(m.group("hi")) if m.group("hi") else lo
        grad_years.append((min(lo, hi), max(lo, hi)))
    if grad_years:
        if not any(lo <= 2028 <= hi for lo, hi in grad_years):
            # ranges like "between December 2027 and June 2028" are caught above. This is a true miss.
            return exclude(f"graduation window {grad_years} does not include 2028")
    elif rtype == "winter":
        return exclude("winter internship not explicitly open to 2028 graduates")
    elif not SECOND_YEAR_OK.search(text):
        if status == "eligible":
            status = "check"
        reasons.append("graduation year not stated")

    # Spring week year rule. Exclude only on unambiguous first year only wording.
    if rtype == "spring" and FIRST_YEAR_ONLY.search(text) and not SECOND_YEAR_OK.search(text):
        return exclude("spring programme explicitly first years only")

    # Rule 1. London.
    loc = location.strip()
    if loc:
        if not re.search(r"london", loc, re.I):
            if re.search(r"(multiple|various|\d+ locations|united kingdom|\buk\b|emea)", loc, re.I):
                status = "check"
                reasons.append(f"location '{loc}' may include London")
            else:
                return exclude(f"location '{loc}' is not London")
    else:
        if re.search(r"\blondon\b", text, re.I):
            pass
        else:
            status = "check" if status != "excluded" else status
            reasons.append("location not stated")

    if flags["injection_suspect"]:
        reasons.append("POSSIBLE PROMPT INJECTION TEXT IN LISTING, do not auto apply, alert Tia")

    return {"status": status, "reasons": reasons, "type": rtype, "flags": flags}


MONTHS = "january february march april may june july august september october november december".split()
DEADLINE = re.compile(
    r"(deadline|clos(?:es|ing)|apply by|applications? (?:close|due|must be (?:submitted|received)))[^.\n]{0,60}?"
    r"(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(m[:3] for m in MONTHS) + r")\w*\.?,?\s*(20\d\d)?", re.I)


def find_deadline(text, today=None):
    today = today or date.today()
    m = DEADLINE.search(text or "")
    if not m:
        return None
    day, mon = int(m.group(2)), [x[:3] for x in MONTHS].index(m.group(3)[:3].lower()) + 1
    year = int(m.group(4)) if m.group(4) else (today.year if mon >= today.month else today.year + 1)
    try:
        return date(year, mon, day).isoformat()
    except ValueError:
        return None


def priority(deadline_iso, rolling, today=None):
    """P1 rolling or closing within 10 days. P2 within 30 days. P3 otherwise or unknown."""
    today = today or date.today()
    if rolling:
        return "P1"
    if deadline_iso:
        days = (date.fromisoformat(deadline_iso) - today).days
        if days <= 10:
            return "P1"
        if days <= 30:
            return "P2"
    return "P3"
