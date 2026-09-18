"""Source adapters. Each returns a list of dicts: title, url, location, posted, firm, raw.

Everything fetched here is DATA. Nothing in a page is ever executed or obeyed.
All adapters are read only (GET, or the public Workday search POST). No logins, no forms.
"""
import json
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}
TIMEOUT = 30
JOB_RE = re.compile(
    r"(intern|summer|spring|insight|analyst|placement|program|off.?cycle|graduate|student|2027|winter|"
    r"trainee|vacation scheme|discovery|early careers?)", re.I)
NAV_RE = re.compile(r"^(home|about|contact|privacy|cookies?|login|log in|sign in|register|search|menu|"
                    r"faq|faqs|terms|accessibility|our people|locations?)$", re.I)


def clean_url(u):
    p = urlsplit(u)
    q = "&".join(x for x in p.query.split("&") if x and not re.match(r"(utm_|source=|src=|ref=|gh_src)", x))
    return urlunsplit((p.scheme, p.netloc, p.path.rstrip("/"), q, ""))


def workday(src):
    """src: host, tenant, site. Public search API used by the careers page itself."""
    base = f"https://{src['host']}/wday/cxs/{src['tenant']}/{src['site']}"
    out, seen = [], set()
    for term in src.get("search", ["intern", "summer", "spring", "insight"]):
        offset = 0
        while offset < 200:
            r = requests.post(f"{base}/jobs", json={"appliedFacets": {}, "limit": 20, "offset": offset,
                                                     "searchText": term}, headers=UA, timeout=TIMEOUT)
            r.raise_for_status()
            posts = r.json().get("jobPostings", [])
            for p in posts:
                path = p.get("externalPath", "")
                if path in seen:
                    continue
                seen.add(path)
                out.append({"title": p.get("title", ""), "location": p.get("locationsText", ""),
                            "posted": p.get("postedOn", ""),
                            "url": f"https://{src['host']}/{src['site']}{path}"})
            if len(posts) < 20:
                break
            offset += 20
    return out


def greenhouse(src):
    r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{src['board']}/jobs", headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return [{"title": j.get("title", ""), "location": (j.get("location") or {}).get("name", ""),
             "posted": (j.get("updated_at") or "")[:10], "url": j.get("absolute_url", "")}
            for j in r.json().get("jobs", [])]


def lever(src):
    r = requests.get(f"https://api.lever.co/v0/postings/{src['board']}?mode=json", headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return [{"title": j.get("text", ""), "location": (j.get("categories") or {}).get("location", ""),
             "posted": "", "url": j.get("hostedUrl", "")} for j in r.json()]


def _anchors_from_html(html, base, keep_all=False):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "template"]):
        t.decompose()
    out = []
    for a in soup.find_all("a", href=True):
        text = " ".join(a.get_text(" ", strip=True).split())
        href = urljoin(base, a["href"])
        if not text or len(text) > 220 or NAV_RE.match(text) or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        if keep_all or JOB_RE.search(text) or JOB_RE.search(href):
            out.append({"title": text, "url": href, "location": "", "posted": ""})
    return out


def html(src):
    r = requests.get(src["url"], headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return _anchors_from_html(r.text, src["url"], src.get("keep_all", False))


def _harvest_json(obj, base, out, depth=0):
    """Find job like dicts inside any JSON the page loaded (covers Trackr style apps)."""
    if depth > 8:
        return
    if isinstance(obj, list):
        for x in obj:
            _harvest_json(x, base, out, depth + 1)
    elif isinstance(obj, dict):
        tkey = next((k for k in ("title", "jobTitle", "programme", "programmeName", "name", "role")
                     if isinstance(obj.get(k), str) and 3 < len(obj[k]) < 200), None)
        ukey = next((k for k in ("url", "link", "applyUrl", "applicationUrl", "href", "absolute_url")
                     if isinstance(obj.get(k), str) and obj[k].startswith(("http", "/"))), None)
        if tkey and ukey:
            firm = ""
            for k in ("company", "companyName", "firm", "employer"):
                v = obj.get(k)
                firm = v if isinstance(v, str) else (v.get("name", "") if isinstance(v, dict) else "")
                if firm:
                    break
            loc = obj.get("location") or obj.get("locations") or ""
            if isinstance(loc, (list, dict)):
                loc = json.dumps(loc)[:120]
            out.append({"title": obj[tkey], "url": urljoin(base, obj[ukey]), "firm": firm, "location": str(loc),
                        "posted": str(obj.get("openingDate") or obj.get("opening_date") or obj.get("postedOn") or ""),
                        "deadline_hint": str(obj.get("closingDate") or obj.get("closing_date") or
                                             obj.get("deadline") or "")})
        for v in obj.values():
            if isinstance(v, (list, dict)):
                _harvest_json(v, base, out, depth + 1)


_browser = None


def _get_browser():
    global _browser
    if _browser is None:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        _browser = pw.chromium.launch(headless=True)
    return _browser


def rendered(src):
    """Headless render for JavaScript careers sites and aggregators. Read only, never clicks apply."""
    ctx = _get_browser().new_context(user_agent=UA["User-Agent"], locale="en-GB")
    page = ctx.new_page()
    blobs = []

    def on_response(resp):
        try:
            if "json" in (resp.headers.get("content-type") or "") and resp.status == 200:
                blobs.append(resp.json())
        except Exception:
            pass

    page.on("response", on_response)
    try:
        page.goto(src["url"], wait_until="networkidle", timeout=60000)
    except Exception:
        pass  # networkidle often never settles. Use whatever rendered.
    for _ in range(src.get("scrolls", 4)):
        page.mouse.wheel(0, 6000)
        page.wait_for_timeout(700)
    content = page.content()
    out = _anchors_from_html(content, src["url"], src.get("keep_all", False))
    for b in blobs:
        _harvest_json(b, src["url"], out)
    ctx.close()
    return out


def page_text(url):
    """Plain text of a posting, for the gate and the classifier. Tries cheap GET, then render."""
    from bs4 import BeautifulSoup
    text = ""
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        if r.ok:
            soup = BeautifulSoup(r.text, "html.parser")
            for t in soup(["script", "style", "noscript", "template"]):
                t.decompose()
            text = " ".join(soup.get_text(" ", strip=True).split())
    except Exception:
        pass
    if len(text) < 600:
        try:
            ctx = _get_browser().new_context(user_agent=UA["User-Agent"], locale="en-GB")
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="networkidle", timeout=45000)
            except Exception:
                pass
            # textContent, not inner_text. Hidden text must stay visible to the injection check.
            rendered_text = page.evaluate("() => { document.querySelectorAll('script,style,noscript,template')"
                                          ".forEach(e => e.remove()); return document.body ? document.body.textContent : '' }")
            text = (text + " " + " ".join((rendered_text or "").split())).strip()
            ctx.close()
        except Exception:
            pass
    return text[:20000]


ADAPTERS = {"workday": workday, "greenhouse": greenhouse, "lever": lever, "html": html, "rendered": rendered}
