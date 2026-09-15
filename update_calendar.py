"""
Canarias Basketball calendar updater (ACB + EuroCup + extras)
Roamalytics

What it does, once a day from GitHub Actions:
  1. Scrapes https://cbcanarias.net/temporada/ (ACB + EuroCup fixtures, Canary time)
  2. Reads extra_fixtures.csv (friendlies, playoff placeholders, anything not on the club page)
  3. Cross-checks every game in the next CHECK_DAYS days against the league sources
     (ACB calendar PDF, Euroleague games API). League source wins on a conflict.
  4. Merges into the existing .ics files: add new, update the same event if
     date/time changed, NEVER delete. Last season stays. History stays if the
     club removes a game from its page.
  5. Writes the files only if something changed. The workflow commits them.

Outputs (repo root):
  acb_calendar_tfe_fixtures.ics         ACB only
  eurocup_calendar_tfe_fixtures.ics     EuroCup only
  extras_calendar_tfe_fixtures.ics      friendlies / cups / playoff placeholders
  canarias-basketball-acb-calendar.ics  everything (the URL people already subscribe to)

Usage:
  python update_calendar.py                      normal run
  python update_calendar.py --dry-run            print what would change, write nothing
  python update_calendar.py --html=tests/x.html  parse a saved copy of the club page instead of fetching
  python update_calendar.py --no-crosscheck      skip league-site verification
"""

import csv
import hashlib
import io
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

# ------------------------------------------------------------------ config

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLUB_URL = "https://cbcanarias.net/temporada/"
EXTRA_CSV = os.path.join(BASE_DIR, "extra_fixtures.csv")

TZID = "Atlantic/Canary"
CLUB_SHORT = "Canarias"                 # how the club appears in event titles (kept from last season)
HOME_VENUE = "Pabellón Santiago Martín, La Laguna"
CHECK_DAYS = 15                          # cross-check window
MIN_EXPECTED = 30                        # refuse to touch files if the scrape looks broken
GAME_HOURS = 2

OUTPUTS = {
    "ACB":     ("acb_calendar_tfe_fixtures.ics",        "Canarias ACB"),
    "EC":      ("eurocup_calendar_tfe_fixtures.ics",    "Canarias EuroCup"),
    "EXTRAS":  ("extras_calendar_tfe_fixtures.ics",     "Canarias amistosos y playoffs"),
    "ALL":     ("canarias-basketball-acb-calendar.ics", "Canarias Basketball"),
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

# Liga Endesa 2026-27 opponents. Anything else on the club page is EuroCup.
ACB_KEYWORDS = ["joventut", "barça", "barca", "zaragoza", "girona", "lleida", "manresa",
                "baskonia", "coruña", "coruna", "obradoiro", "andorra", "real madrid",
                "burgos", "breogán", "breogan", "bilbao", "murcia", "unicaja", "valencia"]

CLUB_ALIASES = ["la laguna tenerife", "lenovo tenerife", "iberostar tenerife",
                "cb canarias", "canarias", "tenerife"]

# Club page is ALL CAPS; map to the names used everywhere else
NAME_FIX = {
    "BARÇA": "Barça", "UCAM MURCIA": "UCAM Murcia", "KIDS&US MANRESA": "Kids&Us Manresa",
    "ILERNA LLEIDA": "iLERNA Lleida", "FIATC GIRONA": "FIATC Girona",
    "CLUJ NAPOCA": "U-BT Cluj-Napoca", "U-BT CLUJ NAPOCA": "U-BT Cluj-Napoca",
    "MORABANC ANDORRA": "MoraBanc Andorra", "UNICAJA MÁLAGA": "Unicaja",
    "SAN PABLO BURGOS": "San Pablo Burgos", "SIAULAI BASKETBALL": "Siauliai",
    "SIAULIAI BASKETBALL": "Siauliai", "REYER VENEZIA": "Reyer Venezia",
    "SURNE BILBAO BASKET": "Surne Bilbao",
}

MONTHS_ES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
             "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
             "noviembre": 11, "diciembre": 12}


# ------------------------------------------------------------------ helpers

def log(msg):
    print(msg, flush=True)


def is_club(name):
    n = name.lower()
    return any(a in n for a in CLUB_ALIASES)


def competition_for(opponent):
    return "ACB" if any(k in opponent.lower() for k in ACB_KEYWORDS) else "EC"


def tidy_name(name):
    n = " ".join(name.split())
    if is_club(n):
        return CLUB_SHORT
    if n.upper() in NAME_FIX:
        return NAME_FIX[n.upper()]
    return " ".join(w.capitalize() if w.isupper() else w for w in n.split())


def parse_date_es(text):
    m = re.search(r"(\d{1,2})\s+(?:de\s+)?([a-záéíóú]+),?\s+(?:de\s+)?(\d{4})", text.lower())
    if not m or m.group(2) not in MONTHS_ES:
        return None
    return date(int(m.group(3)), MONTHS_ES[m.group(2)], int(m.group(1)))


def parse_time(text):
    m = re.search(r"(\d{1,2}):(\d{2})", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def season_start_year(d=None):
    """Basketball season starts in autumn: Sep 2026 and May 2027 both belong to 2026-27."""
    d = d or date.today()
    return d.year if d.month >= 7 else d.year - 1


def acb_pdf_url():
    y = season_start_year()
    return f"https://acb.com/docs/calendario/calendarioLigaEndesa{y}{str(y + 1)[-2:]}.pdf"


def eurocup_api_url():
    y = season_start_year()
    return f"https://api-live.euroleague.net/v2/competitions/U/seasons/U{y}/games"


def event_slug(url):
    m = re.search(r"/event/([^/?#]+)", url or "")
    return m.group(1).rstrip("/") if m else ""


def stable_key(f):
    """Identity that survives a date/time change. Last season's UUID events are never this key."""
    slug = event_slug(f.get("url") or "")
    if slug:
        return "event|" + slug
    y = season_start_year(f["date"])
    return "|".join([
        "row",
        (f.get("competition") or "").lower(),
        f"{y}-{y + 1}",
        (f.get("home") or "").lower(),
        (f.get("away") or "").lower(),
        (f.get("round") or "").lower(),
    ])


def uid_for(f):
    """UID for a *new* event. Existing events keep whatever UID they already have."""
    return hashlib.sha1(stable_key(f).encode()).hexdigest()[:20] + "@canarias-calendar"


def legacy_uid_for(f):
    """UID used before date was removed from the key. Kept so already-published games update in place."""
    raw = f"{f['competition']}|{f['date'].isoformat()}|{f['home'].lower()}|{f['away'].lower()}"
    return hashlib.sha1(raw.encode()).hexdigest()[:20] + "@canarias-calendar"


def opponent_of(f):
    return f["away"] if is_club(f["home"]) else f["home"]


def title_core(f):
    t = f"{f['competition']}: {f['home']} - {f['away']}"
    if f.get("round"):
        t += f" ({f['round']})"
    return t


# ------------------------------------------------------------------ 1. club site

def fetch(url, headers=None, **kw):
    r = requests.get(url, headers=headers or HEADERS, timeout=30, **kw)
    r.raise_for_status()
    return r


def parse_club_page(html):
    """
    The club page is a SportsPress table. Each fixture is three <a> tags sharing the same
    /event/ href: the date, the time, and 'HOME VS AWAY'. Grouping by href means this keeps
    working even if the CSS classes change.
    """
    soup = BeautifulSoup(html, "lxml")
    groups = {}
    for a in soup.select('a[href*="/event/"]'):
        href = a.get("href", "").split("#")[0].split("?")[0].rstrip("/")
        txt = a.get_text(" ", strip=True)
        if href and txt:
            groups.setdefault(href, []).append(txt)

    fixtures = []
    for href, texts in groups.items():
        d = t = title = None
        for txt in texts:
            if d is None and parse_date_es(txt):
                d = parse_date_es(txt)
            elif t is None and re.fullmatch(r"\s*\d{1,2}:\d{2}\s*h?\.?\s*", txt):
                t = parse_time(txt)
            elif re.search(r"\bvs\b", txt, re.I):
                title = txt
        if not (d and title):
            continue
        parts = re.split(r"\s+vs\.?\s+", title, flags=re.I)
        if len(parts) < 2:
            continue
        home, away = tidy_name(parts[0]), tidy_name(parts[1])
        if not (is_club(home) or is_club(away)):
            continue
        fixtures.append({
            "competition": competition_for(away if is_club(home) else home),
            "date": d,
            "time": None if (t is None or t == (0, 0)) else t,   # 00:00 on the club page = TBC
            "home": home, "away": away,
            "venue": HOME_VENUE if is_club(home) else "",
            "round": "",
            "url": href + "/",
            "source": "cbcanarias.net",
        })
    fixtures.sort(key=lambda f: (f["date"], f["time"] or (0, 0)))
    return fixtures


# ------------------------------------------------------------------ 2. extras CSV

def load_extras():
    if not os.path.exists(EXTRA_CSV):
        return []
    out = []
    with open(EXTRA_CSV, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if not (row.get("date") or "").strip() or (row.get("date") or "").strip().upper() == "TBC":
                continue
            home, away = row["home"].strip(), row["away"].strip()
            out.append({
                "competition": (row.get("competition") or "Extra").strip(),
                "date": datetime.strptime(row["date"].strip(), "%Y-%m-%d").date(),
                "time": parse_time(row.get("time_canary")),
                "home": tidy_name(home) if is_club(home) else home,
                "away": tidy_name(away) if is_club(away) else away,
                "venue": (row.get("venue") or "").strip(),
                "round": (row.get("round") or "").strip(),
                "url": (row.get("url") or "").strip(),
                "source": "extra_fixtures.csv",
                "allday": (row.get("allday") or "").strip().lower() in ("1", "true", "yes", "y"),
            })
    return out


# ------------------------------------------------------------------ 3. cross-check

ACB_ALIASES = {"recoletas": "burgos", "spb": "burgos", "san pablo": "burgos",
               "badalona": "joventut", "penya": "joventut", "coruna": "coruña", "breogan": "breogán"}

# Club site and Euroleague often use different sponsor names for the same club.
EC_ALIASES = {
    "reyer": "venezia", "venezia": "venezia", "venice": "venezia",
    "cluj": "cluj",
    "siauliai": "siauliai", "siaulai": "siauliai",
    "ankara": "ankara", "telekom": "ankara",
    "london": "london",
    "roma": "roma", "maxima": "roma",
    "skyliner": "frankfurt", "frankfurt": "frankfurt", "fraport": "frankfurt",
}


def acb_keyword(name):
    """Canonical key for an ACB opponent regardless of sponsor naming (Recoletas Salud SPB == San Pablo Burgos)."""
    n = name.lower()
    for a, k in ACB_ALIASES.items():
        if a in n:
            return k.replace("ñ", "n").replace("á", "a")
    for k in ACB_KEYWORDS:
        if k in n:
            return k.replace("ñ", "n").replace("á", "a")
    return n[:8]


def ec_keyword(name):
    """Canonical key for a EuroCup opponent (Reyer Venezia == Umana Reyer)."""
    n = name.lower()
    for a, k in EC_ALIASES.items():
        if a in n:
            return k
    return n[:8]


def parse_acb_pdf_text(text):
    """
    Parse the text of acb.com's calendar PDF. Real layout (Sept 2026):
        JORNADA 1
        26 y 27 de septiembre de 2026            <- year lives here
        Domingo 27 de septiembre 13:00 La Laguna Tenerife Casademont Zaragoza
        21/22/23 de mayo Horario sin confirmar La Laguna Tenerife Recoletas Salud SPB
    Times are peninsular; converted to Canary (-1h). Returns {(date, opponent_key): (h, m) or None}.
    """
    found = {}
    years = []
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if re.search(r"\bde\s+[a-záéíóú]+\s+de\s+20\d{2}", line.lower()):
            years = sorted({int(y) for y in re.findall(r"\b(20\d{2})\b", line)})
        if "tenerife" not in line.lower() or not years:
            continue
        m = re.match(r"^(?:[A-Za-zÁ-ú]+\s+)?(\d{1,2})\s+de\s+([a-záéíóú]+)\s+(\d{1,2}):(\d{2})\s+(.*)$", line, re.I)
        if not m:
            continue
        day, mon = int(m.group(1)), MONTHS_ES.get(m.group(2).lower())
        if not mon:
            continue
        year = years[0] if mon >= 7 else years[-1]     # a jornada spanning New Year lists both years
        h, mi = int(m.group(3)) - 1, int(m.group(4))
        if h < 0:
            h += 24
        teams_txt = m.group(5)
        opp = re.sub(r"la laguna tenerife", "", teams_txt, flags=re.I).strip()
        found[(date(year, mon, day), acb_keyword(opp))] = (h, mi)
    return found


def acb_pdf_fixtures():
    """Download the ACB calendar PDF and parse it. Any failure -> {} and the check is skipped."""
    try:
        import pdfplumber
        r = fetch(acb_pdf_url())
        text = ""
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            for page in pdf.pages:
                text += (page.extract_text() or "") + "\n"
        found = parse_acb_pdf_text(text)
    except Exception as e:
        log(f"  [ACB check] skipped: {e}")
        return {}
    log(f"  [ACB check] {len(found)} Tenerife games read from ACB PDF")
    return found


def _team_name(side):
    """Euroleague feed nests team as {'club': {'name': ...}} or {'name': ...}; accept both."""
    if not isinstance(side, dict):
        return ""
    if isinstance(side.get("club"), dict):
        return side["club"].get("name") or side["club"].get("tvCode") or ""
    return side.get("name") or side.get("clubName") or ""


def parse_eurocup_games(games):
    """Returns {(date, opponent_key): (h, m)} in Canary time for Tenerife games only."""
    found = {}
    for g in games or []:
        try:
            home = _team_name(g.get("local") or g.get("home") or g.get("homeTeam"))
            away = _team_name(g.get("road") or g.get("away") or g.get("awayTeam"))
            if "tenerife" not in (home + away).lower():
                continue
            utc = g.get("utcDate") or g.get("dateUtc") or g.get("dateUTC")
            if utc:
                dt = datetime.fromisoformat(utc.replace("Z", "+00:00")).astimezone(timezone.utc)
            else:
                # 'date' is venue-local; only usable when the venue is in the Canaries (home games)
                if "tenerife" not in home.lower():
                    continue
                naive = datetime.fromisoformat(g["date"].replace("Z", ""))
                found[(naive.date(), ec_keyword(away))] = (naive.hour, naive.minute)
                continue
            local = dt + canary_offset(dt)
            opp = away if "tenerife" in home.lower() else home
            found[(local.date(), ec_keyword(opp))] = (local.hour, local.minute)
        except Exception:
            continue
    return found


def eurocup_api_fixtures():
    """Euroleague public games feed, all EuroCup games this season, filtered to Tenerife by name."""
    try:
        r = fetch(eurocup_api_url(), headers={**HEADERS, "Accept": "application/json"})
        data = r.json()
        games = data.get("data", data) if isinstance(data, dict) else data
        found = parse_eurocup_games(games)
    except Exception as e:
        log(f"  [EC check] skipped: {e}")
        return {}
    log(f"  [EC check] {len(found)} Tenerife games read from Euroleague API")
    return found


def canary_offset(dt_utc):
    """Atlantic/Canary: UTC in winter, UTC+1 from last Sunday of March to last Sunday of October."""
    y = dt_utc.year
    def last_sunday(month):
        d = date(y, month + 1, 1) - timedelta(days=1) if month < 12 else date(y, 12, 31)
        return d - timedelta(days=(d.weekday() + 1) % 7)
    start = datetime(y, 3, last_sunday(3).day, 1, tzinfo=timezone.utc)
    end = datetime(y, 10, last_sunday(10).day, 1, tzinfo=timezone.utc)
    return timedelta(hours=1) if start <= dt_utc < end else timedelta(0)


def cross_check(fixtures, today):
    """Compare upcoming fixtures with league sources. League wins. Returns list of change notes."""
    window = [f for f in fixtures if today <= f["date"] <= today + timedelta(days=CHECK_DAYS)
              and f["competition"] in ("ACB", "EC")]
    if not window:
        log("  nothing in the next 15 days to cross-check")
        return []
    notes = []
    sources = {"ACB": None, "EC": None}
    for f in window:
        comp = f["competition"]
        if sources[comp] is None:
            sources[comp] = acb_pdf_fixtures() if comp == "ACB" else eurocup_api_fixtures()
        src = sources[comp]
        if not src:
            continue
        opp = acb_keyword(opponent_of(f)) if comp == "ACB" else ec_keyword(opponent_of(f))
        # look for same opponent within +/- 1 day (a date move is exactly what we want to catch)
        hit = None
        for delta in (0, -1, 1, -2, 2):
            k = (f["date"] + timedelta(days=delta), opp)
            if k in src:
                hit = (k[0], src[k])
                break
        if not hit:
            notes.append(f"{comp} {f['date']} vs {opponent_of(f)}: not found in league source, club data kept")
            continue
        league_date, league_time = hit
        if league_time is None:
            continue   # league has no time yet, keep club's
        if league_date != f["date"] or league_time != f["time"]:
            old = f"{f['date']} {fmt_time(f['time'])}"
            f["date"], f["time"] = league_date, league_time
            f["source"] = f"{'acb.com' if comp == 'ACB' else 'euroleaguebasketball.net'} (overrides club site)"
            notes.append(f"{comp} vs {opponent_of(f)}: club site said {old}, league says "
                         f"{league_date} {fmt_time(league_time)} -> league used")
    return notes


def fmt_time(t):
    return f"{t[0]:02d}:{t[1]:02d}" if t else "TBC"


# ------------------------------------------------------------------ 4. ics read / merge / write

VTIMEZONE = """BEGIN:VTIMEZONE
TZID:Atlantic/Canary
X-LIC-LOCATION:Atlantic/Canary
BEGIN:DAYLIGHT
TZOFFSETFROM:+0000
TZOFFSETTO:+0100
TZNAME:WEST
DTSTART:19700329T010000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:+0100
TZOFFSETTO:+0000
TZNAME:WET
DTSTART:19701025T020000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE"""


def esc(s):
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def event_block(f, stamp, uid=None):
    comp = f["competition"]
    title = title_core(f)
    home_game = is_club(f["home"])
    venue = f.get("venue") or (HOME_VENUE if home_game else "Fuera de casa")
    lines = ["BEGIN:VEVENT",
             f"UID:{uid or uid_for(f)}",
             f"DTSTAMP:{stamp}",
             f"X-GAME-KEY:{esc(stable_key(f))}"]
    timed = bool(f["time"]) and not f.get("allday")
    if timed:
        start = datetime(f["date"].year, f["date"].month, f["date"].day, f["time"][0], f["time"][1])
        end = start + timedelta(hours=GAME_HOURS)
        lines += [f"DTSTART;TZID={TZID}:{start:%Y%m%dT%H%M%S}", f"DTEND;TZID={TZID}:{end:%Y%m%dT%H%M%S}"]
    else:
        if not f.get("allday"):
            title += " (hora por confirmar)"
        nxt = f["date"] + timedelta(days=1)
        lines += [f"DTSTART;VALUE=DATE:{f['date']:%Y%m%d}", f"DTEND;VALUE=DATE:{nxt:%Y%m%d}"]
    desc = f"Hora canaria. Fuente: {f.get('source', '')}"
    if f.get("url"):
        desc += f"\n{f['url']}"
    lines += [f"SUMMARY:{esc(title)}", f"LOCATION:{esc(venue)}", f"DESCRIPTION:{esc(desc)}",
              "STATUS:CONFIRMED", "TRANSP:OPAQUE"]
    if timed:
        lines += ["BEGIN:VALARM", "TRIGGER:-P1D", "ACTION:DISPLAY",
                  "DESCRIPTION:Reminder: Mañana juega el Canarias", "END:VALARM",
                  "BEGIN:VALARM", "TRIGGER:-PT1H", "ACTION:DISPLAY",
                  "DESCRIPTION:Reminder: El partido de Canarias es en 1 hora", "END:VALARM"]
    lines.append("END:VEVENT")
    return "\n".join(lines)


def read_existing_events(path):
    """Return {UID: event_block_text} from an existing .ics, preserving blocks verbatim."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        text = fh.read().replace("\r\n", "\n")
    events = {}
    for block in re.findall(r"BEGIN:VEVENT\n.*?\nEND:VEVENT", text, flags=re.S):
        m = re.search(r"^UID:(.+)$", block, flags=re.M)
        if m:
            events[m.group(1).strip()] = block
    return events


def event_meta(block):
    def field(name):
        m = re.search(rf"^{name}:(.+)$", block, flags=re.M)
        return m.group(1).strip() if m else ""
    uid = field("UID")
    key = field("X-GAME-KEY").replace("\\,", ",").replace("\\;", ";")
    summary = field("SUMMARY").replace("\\,", ",").replace("\\;", ";")
    dm = re.search(r"^DTSTART[^:]*:(\d{8})", block, flags=re.M)
    d = None
    if dm:
        s = dm.group(1)
        d = date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    sm = re.search(r"/event/([^/\\?\s]+)", block)
    return {"uid": uid, "key": key, "summary": summary, "date": d,
            "slug": sm.group(1) if sm else ""}


def find_existing_uid(f, existing):
    """Reuse the calendar UID already published for this game so clients update, not duplicate."""
    key = stable_key(f)
    slug = event_slug(f.get("url") or "")
    core = title_core(f)
    metas = [event_meta(b) for b in existing.values()]
    for ev in metas:
        if ev["key"] and ev["key"] == key:
            return ev["uid"]
    if slug:
        for ev in metas:
            if ev["slug"] == slug:
                return ev["uid"]
    for candidate in (legacy_uid_for(f), uid_for(f)):
        if candidate in existing:
            return candidate
    season = season_start_year(f["date"])
    for ev in metas:
        if ev["date"] is None or season_start_year(ev["date"]) != season:
            continue
        if not ev["summary"].startswith(core):
            continue
        if abs((ev["date"] - f["date"]).days) <= 14:
            return ev["uid"]
    return None


def normalise_block(block):
    return re.sub(r"^DTSTAMP:.*$", "", block, flags=re.M).strip()


def dtstart_of(block):
    m = re.search(r"^DTSTART[^:]*:(\d{8})", block, flags=re.M)
    return m.group(1) if m else "99999999"


def build_calendar(name, events):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    head = ["BEGIN:VCALENDAR", "VERSION:2.0",
            "PRODID:-//Roamalytics//Canarias Basketball Calendar//ES",
            "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
            f"X-WR-CALNAME:{esc(name)}", f"X-WR-TIMEZONE:{TZID}",
            "X-PUBLISHED-TTL:PT12H", "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
            "COLOR:gold", "X-APPLE-CALENDAR-COLOR:#F5C400",
            VTIMEZONE]
    ordered = sorted(events.values(), key=dtstart_of)
    text = "\n".join(head + ordered + ["END:VCALENDAR"])
    return text.replace("\n", "\r\n") + "\r\n"


def merge_into(path, name, fixtures, dry):
    """Add/update fixtures. Existing events not in `fixtures` (including last season) are kept as-is."""
    existing = read_existing_events(path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    added = updated = 0
    for f in fixtures:
        uid = find_existing_uid(f, existing) or uid_for(f)
        block = event_block(f, stamp, uid=uid)
        if uid not in existing:
            added += 1
        elif normalise_block(existing[uid]) != normalise_block(block):
            updated += 1
        else:
            continue
        existing[uid] = block
    log(f"  {os.path.basename(path)}: {len(existing)} events total, +{added} new, ~{updated} updated")
    if (added or updated or not os.path.exists(path)) and not dry:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(build_calendar(name, existing))
        return True
    return False


# ------------------------------------------------------------------ main

def main(argv):
    dry = "--dry-run" in argv
    no_check = "--no-crosscheck" in argv
    html_path = next((a.split("=", 1)[1] for a in argv if a.startswith("--html=")), None)
    today = date.today()

    log("1. Club site")
    html = open(html_path, encoding="utf-8").read() if html_path else fetch(CLUB_URL).text
    scraped = parse_club_page(html)
    n_acb = sum(f["competition"] == "ACB" for f in scraped)
    n_ec = sum(f["competition"] == "EC" for f in scraped)
    log(f"  {len(scraped)} fixtures: {n_acb} ACB, {n_ec} EC")
    if len(scraped) < MIN_EXPECTED:
        log(f"ERROR: only {len(scraped)} fixtures scraped (expected >= {MIN_EXPECTED}). "
            "Club page layout may have changed. Nothing written.")
        return 2

    log("2. Extras CSV")
    extras = load_extras()
    log(f"  {len(extras)} rows")

    log("3. Cross-check next 15 days")
    notes = [] if no_check else cross_check(scraped, today)
    for n in notes:
        log("  ! " + n)
    if notes:
        conflicts = [n for n in notes if "league used" in n]
        if conflicts and not dry:
            with open(os.path.join(BASE_DIR, "last_crosscheck.md"), "w", encoding="utf-8") as fh:
                fh.write(f"# Cross-check {today}\n\n" + "\n".join(f"- {n}" for n in notes) + "\n")

    log("4. Merge into calendars" + (" (dry run)" if dry else ""))
    by_comp = {
        "ACB": [f for f in scraped if f["competition"] == "ACB"],
        "EC": [f for f in scraped if f["competition"] == "EC"],
        "EXTRAS": extras,
        "ALL": scraped + extras,
    }
    changed = []
    for key, (fname, calname) in OUTPUTS.items():
        if merge_into(os.path.join(BASE_DIR, fname), calname, by_comp[key], dry):
            changed.append(fname)

    if not dry and changed:
        with open(os.path.join(BASE_DIR, "last_run.txt"), "w", encoding="utf-8") as fh:
            fh.write(datetime.now(timezone.utc).isoformat(timespec="seconds") + "\n")
    log("Changed: " + (", ".join(changed) if changed else "nothing"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
