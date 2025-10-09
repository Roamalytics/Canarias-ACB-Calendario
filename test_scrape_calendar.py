"""
Test scraper for Canarias Basketball fixtures (ACB + BCL)
Safe version: prints results and writes test_canarias_calendar.ics
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import uuid
import os

CAL_FILE = "test_canarias_calendar.ics"
TZID = "Atlantic/Canary"

NAME_MAP = {
    "lenovo tenerife": "Canarias",
    "la laguna tenerife": "Canarias",
    "iberostar tenerife": "Canarias",
    "cb canarias": "Canarias",
}

def normalize_team(name: str) -> str:
    for k, v in NAME_MAP.items():
        if k in name.lower():
            return v
    return name.strip()

def get_acb_fixtures():
    url = "https://cbcanarias.net/temporada"
    print(f"🔗 Fetching ACB fixtures from {url}")
    r = requests.get(url, timeout=20)
    print(f"HTTP {r.status_code}, {len(r.text)} bytes")

    soup = BeautifulSoup(r.text, "lxml")

    # Try multiple possible class names in case the site changed
    selectors = ["div.match-info", "div.match-item", "div.elementor-post"]
    fixtures = []

    for sel in selectors:
        rows = soup.select(sel)
        print(f"Trying selector '{sel}', found {len(rows)} matches")
        for row in rows:
            try:
                date_text = row.get_text()
                if any(keyword in date_text.lower() for keyword in ["2025", "2026"]):
                    print("➡️ Example snippet:", date_text[:100])
                    # Example placeholder, parse later once confirmed
            except Exception as e:
                print("Parse error:", e)

    return fixtures


def get_bcl_fixtures():
    url = "https://www.championsleague.basketball/en/teams/la-laguna-tenerife"
    print(f"🔗 Fetching BCL fixtures from {url}")
    r = requests.get(url, timeout=20)
    print(f"HTTP {r.status_code}, {len(r.text)} bytes")

    soup = BeautifulSoup(r.text, "lxml")
    fixtures = []

    for sel in ["div.schedule__game", "div.GameCard", "div.game"]:
        rows = soup.select(sel)
        print(f"Trying selector '{sel}', found {len(rows)} matches")

    return fixtures


def generate_ics(fixtures):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Canarias Test Calendar//EN",
        f"X-WR-TIMEZONE:{TZID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]

    for comp, home, away, dt in fixtures:
        uid = f"{uuid.uuid4()}@test-canarias"
        start = dt.strftime("%Y%m%dT%H%M%S")
        end = (dt + timedelta(hours=2)).strftime("%Y%m%dT%H%M%S")

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART;TZID={TZID}:{start}",
            f"DTEND;TZID={TZID}:{end}",
            f"SUMMARY:{comp}: {home} - {away}",
            "END:VEVENT",
        ])

    lines.append("END:VCALENDAR")
    return "\n".join(lines)


def main():
    acb = get_acb_fixtures()
    bcl = get_bcl_fixtures()

    fixtures = sorted(acb + bcl, key=lambda x: x[3]) if acb or bcl else []

    print(f"\n✅ Total fixtures collected: {len(fixtures)}")
    if fixtures:
        ics = generate_ics(fixtures)
        with open(CAL_FILE, "w", encoding="utf-8") as f:
            f.write(ics)
        print(f"📁 Test calendar saved to {CAL_FILE}")
    else:
        print("⚠️ No fixtures parsed — check selector output above.")


if __name__ == "__main__":
    main()
