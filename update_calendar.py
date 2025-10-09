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

CAL_FILE = "canarias-basketball-acb-calendar.ics"
TZID = "Atlantic/Canary"

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


def get_acb_fixtures():
    """Scrape fixtures from cbcanarias.net/temporada"""
    url = "https://cbcanarias.net/temporada"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    fixtures = []
    for row in soup.select("div.match-info"):
        try:
            date_text = row.select_one("div.match-date").get_text(strip=True)
            time_text = row.select_one("div.match-hour").get_text(strip=True)
            teams = [t.get_text(strip=True) for t in row.select("div.match-team")]
            if len(teams) == 2:
                home, away = map(normalize_team, teams)
                dt = datetime.strptime(f"{date_text} {time_text}", "%d/%m/%Y %H:%M")
                fixtures.append(("ACB", home, away, dt))
        except Exception:
            continue
    return fixtures


def get_bcl_fixtures():
    """Scrape fixtures from BCL team page"""
    url = "https://www.championsleague.basketball/en/teams/la-laguna-tenerife"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    fixtures = []
    for game in soup.select("div.schedule__game"):
        try:
            date = game.select_one("div.schedule__date").get_text(strip=True)
            time = game.select_one("div.schedule__time").get_text(strip=True)
            home = normalize_team(game.select_one("div.schedule__team--home").get_text(strip=True))
            away = normalize_team(game.select_one("div.schedule__team--away").get_text(strip=True))
            dt = datetime.strptime(f"{date} {time}", "%d %b %Y %H:%M")
            fixtures.append(("BCL", home, away, dt))
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
    ics.append(f"X-WR-CALNAME:{CAL_FILE}")
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
    acb = get_acb_fixtures()
    bcl = get_bcl_fixtures()
    fixtures = sorted(acb + bcl, key=lambda x: x[3])

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
