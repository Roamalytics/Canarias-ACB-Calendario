"""
Auto-update Canarias Basketball calendar (ACB + BCL)
Author: Roamalytics
Description:
  - Fetches fixture data from cbcanarias.net and championsleague.basketball
  - Normalizes team names to "Canarias"
  - Compares to current ICS file
  - Regenerates 'canarias-basketball-acb-calendar.ics' if differences exist
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import uuid
import os
import difflib

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CAL_FILE = os.path.join(BASE_DIR, "canarias-basketball-acb-calendar.ics")
TZID = "Atlantic/Canary"
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

# Normalize Tenerife name variants
NAME_MAP = {
    "lenovo tenerife": "Canarias",
    "la laguna tenerife": "Canarias",
    "iberostar tenerife": "Canarias",
    "cb canarias": "Canarias",
}

def normalize_team(name: str) -> str:
    n = name.strip()
    for k, v in NAME_MAP.items():
        if k in n.lower():
            return v
    return n


def get_fixtures():
    """Scrape ACB/BCL fixtures from cbcanarias.net/temporada."""
    url = "https://cbcanarias.net/temporada/"
    r = requests.get(url, headers=REQUEST_HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    fixtures = []
    for row in soup.select("tr.sp-row.sp-post"):
        try:
            date_node = row.select_one("time.sp-event-date")
            title_node = row.select_one("h4.sp-event-title a")
            if not date_node or not title_node:
                continue

            dt_raw = (date_node.get("datetime") or "").strip()
            if not dt_raw:
                continue

            dt = datetime.strptime(dt_raw[:16], "%Y-%m-%d %H:%M")
            teams = [s.get("title", "").strip() for s in row.select("span.team-logo")]
            if len(teams) != 2 or not teams[0] or not teams[1]:
                continue

            home, away = map(normalize_team, teams)
            title = title_node.get_text(strip=True).upper()
            comp = "BCL" if "BCL" in title else "ACB"
            fixtures.append((comp, home, away, dt))
        except Exception:
            continue
    return fixtures


def generate_ics(fixtures):
    """Generate an ICS string from fixture list"""
    ics = []
    ics.append("BEGIN:VCALENDAR")
    ics.append("VERSION:2.0")
    ics.append("PRODID:-//Canarias Basketball Calendar//EN")
    ics.append("CALSCALE:GREGORIAN")
    ics.append("METHOD:PUBLISH")
    ics.append("X-WR-CALNAME:canarias-basketball-acb-calendar.ics")
    ics.append(f"X-WR-TIMEZONE:{TZID}")
    ics.append("CATEGORIES:Banana")

    for comp, home, away, dt in fixtures:
        uid = f"{uuid.uuid4()}@canarias-calendar"
        start = dt.strftime("%Y%m%dT%H%M%S")
        end = (dt + timedelta(hours=2)).strftime("%Y%m%dT%H%M%S")

        ics.append("BEGIN:VEVENT")
        ics.append(f"UID:{uid}")
        ics.append(f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}")
        ics.append(f"DTSTART;TZID={TZID}:{start}")
        ics.append(f"DTEND;TZID={TZID}:{end}")
        ics.append(f"SUMMARY:{comp}: {home} - {away}")
        ics.append("LOCATION:Por confirmar")
        ics.append("STATUS:CONFIRMED")

        # Reminders
        ics.append("BEGIN:VALARM")
        ics.append("TRIGGER:-P1D")
        ics.append("ACTION:DISPLAY")
        ics.append("DESCRIPTION:Reminder: Mañana juega el Canarias")
        ics.append("END:VALARM")

        ics.append("BEGIN:VALARM")
        ics.append("TRIGGER:-PT1H")
        ics.append("ACTION:DISPLAY")
        ics.append("DESCRIPTION:Reminder: El partido de Canarias es en 1 hora")
        ics.append("END:VALARM")

        ics.append("END:VEVENT")

    ics.append("END:VCALENDAR")
    return "\n".join(ics)


def summarize_changes(old_text, new_text):
    diff = difflib.unified_diff(old_text.splitlines(), new_text.splitlines(), lineterm="")
    changes = [line for line in diff if line.startswith(("+", "-")) and not line.startswith(("++", "--"))]
    summary = "\n".join(changes[:20])  # limit for brevity
    return summary if summary else "No visible text differences."


def main():
    print("🔄 Checking for fixture updates...")
    fixtures = sorted(get_fixtures(), key=lambda x: x[3])

    new_ics = generate_ics(fixtures)
    old_ics = ""
    if os.path.exists(CAL_FILE):
        with open(CAL_FILE, "r", encoding="utf-8") as f:
            old_ics = f.read()

    if new_ics.strip() != old_ics.strip():
        with open(CAL_FILE, "w", encoding="utf-8") as f:
            f.write(new_ics)
        print("✅ Calendar updated successfully.")
        print("📝 Change summary:")
        print(summarize_changes(old_ics, new_ics))
    else:
        print("✅ No updates found – calendar unchanged.")


if __name__ == "__main__":
    main()
