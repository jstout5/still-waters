"""
Sends the daily Scripture & Soul devotional email.
Generates a Verse of the Day + mood search link + 3 book recommendations.

Usage: python tools/send_devotional.py
"""

import json
import os
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote_plus

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
APP_URL = os.getenv("APP_URL", "http://localhost:5050")
SUBSCRIBERS_FILE = Path(__file__).parent.parent / "subscribers.json"


def get_recipients() -> list:
    if SUBSCRIBERS_FILE.exists():
        subs = json.loads(SUBSCRIBERS_FILE.read_text(encoding="utf-8")).get("subscribers", [])
        if subs:
            return subs
    return ["frostbytehero@gmail.com"]


def get_devotional() -> dict:
    today = date.today().strftime("%B %d, %Y")
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": f"""Generate a morning devotional for {today}.

Return JSON only:
{{
  "verse_reference": "Book Chapter:Verse",
  "verse_text": "Full KJV verse text",
  "theme": "One word theme (e.g. Hope, Courage, Peace, Grace)",
  "reflection": "2-3 warm sentences of pastoral reflection on this verse for the morning",
  "prayer": "One sentence morning prayer tied to the verse",
  "books": [
    {{
      "title": "Book Title",
      "author": "Author Name",
      "description": "One sentence on why this book is worth reading",
      "amazon_search": "search terms"
    }}
  ]
}}

Choose a verse that is timely, comforting, and universally relevant. Return 3 books."""}],
    )
    raw = resp.content[0].text.strip()
    if "```" in raw:
        for part in raw.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                raw = part
                break
    return json.loads(raw)


def build_email(d: dict) -> str:
    today = date.today().strftime("%B %d, %Y")
    books_html = ""
    for b in d.get("books", []):
        q = b.get("amazon_search", f"{b['title']} {b['author']}")
        url = f"https://www.amazon.com/s?k={q.replace(' ', '+')}&i=stripbooks"
        books_html += f"""
        <a href="{url}" target="_blank" style="display:block;text-decoration:none;
          background:#1a1208;border:1px solid #2a1e0e;border-left:3px solid #8b6914;
          border-radius:2px;padding:16px 20px;margin-bottom:10px;">
          <div style="font-family:Georgia,serif;font-size:13px;color:#c9a84c;margin-bottom:3px;">{b['title']}</div>
          <div style="font-size:11px;color:#5a4530;margin-bottom:6px;">{b['author']}</div>
          <div style="font-size:13px;color:#7a6040;line-height:1.6;">{b['description']}</div>
          <div style="font-size:10px;color:#4a3820;margin-top:6px;letter-spacing:1px;">FIND ON AMAZON &#8599;</div>
        </a>"""

    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#0f0b04;font-family:Georgia,'Times New Roman',serif;">
<div style="max-width:620px;margin:0 auto;padding:40px 20px;">

  <!-- Header -->
  <div style="text-align:center;border-bottom:1px solid #2a1e0e;padding-bottom:28px;margin-bottom:36px;">
    <div style="font-size:22px;color:#8b6914;letter-spacing:6px;margin-bottom:10px;">☩ ✦ ☩</div>
    <div style="font-family:'Georgia',serif;font-size:30px;font-weight:bold;
      color:#c9a84c;letter-spacing:4px;text-transform:uppercase;">Scripture &amp; Soul</div>
    <div style="font-size:13px;color:#5a4530;margin-top:8px;font-style:italic;">
      Morning Devotional &nbsp;·&nbsp; {today}
    </div>
  </div>

  <!-- Theme badge -->
  <div style="text-align:center;margin-bottom:28px;">
    <span style="font-size:10px;letter-spacing:3px;text-transform:uppercase;
      color:#0f0b04;background:#c9a84c;padding:5px 18px;border-radius:2px;">
      {d.get('theme','').upper()}
    </span>
  </div>

  <!-- Verse of the Day -->
  <div style="background:#1a1208;border:1px solid #2a1e0e;border-top:3px solid #8b6914;
    border-radius:2px;padding:32px 36px;margin-bottom:28px;">
    <div style="font-size:10px;letter-spacing:3px;text-transform:uppercase;
      color:#8b6914;margin-bottom:16px;">✦ &nbsp; Verse of the Day</div>
    <div style="font-size:22px;line-height:1.8;color:#e8d5b8;font-style:italic;margin-bottom:16px;">
      &ldquo;{d.get('verse_text','')}&rdquo;
    </div>
    <div style="font-size:12px;color:#8b6914;letter-spacing:2px;">
      — {d.get('verse_reference','')} &nbsp;·&nbsp; KJV
    </div>
  </div>

  <!-- Reflection -->
  <div style="font-size:16px;line-height:1.8;color:#a08060;margin-bottom:24px;
    font-style:italic;padding:0 8px;">
    {d.get('reflection','')}
  </div>

  <!-- Prayer -->
  <div style="background:#111008;border:1px solid #1e1608;border-radius:2px;
    padding:20px 24px;margin-bottom:36px;text-align:center;">
    <div style="font-size:10px;letter-spacing:3px;text-transform:uppercase;
      color:#5a4530;margin-bottom:10px;">Morning Prayer</div>
    <div style="font-size:15px;color:#7a6040;line-height:1.7;font-style:italic;">
      {d.get('prayer','')}
    </div>
  </div>

  <!-- Mood search CTA -->
  <div style="text-align:center;margin-bottom:40px;">
    <div style="font-size:13px;color:#5a4530;margin-bottom:14px;font-style:italic;">
      Searching for a word that speaks to where you are today?
    </div>
    <a href="{APP_URL}" target="_blank"
      style="display:inline-block;font-size:12px;letter-spacing:3px;text-transform:uppercase;
      color:#0f0b04;background:linear-gradient(135deg,#c9a84c,#a8862e);
      text-decoration:none;padding:14px 36px;border-radius:2px;">
      Search by Mood &#8594;
    </a>
  </div>

  <!-- Divider -->
  <div style="text-align:center;color:#2a1e0e;font-size:16px;
    letter-spacing:10px;margin-bottom:32px;">✦ ✦ ✦</div>

  <!-- Books -->
  <div style="font-size:10px;letter-spacing:3px;text-transform:uppercase;
    color:#5a4530;margin-bottom:16px;text-align:center;">Further Reading</div>
  {books_html}

  <!-- Footer -->
  <div style="text-align:center;margin-top:40px;padding-top:24px;
    border-top:1px solid #1e1508;font-size:10px;color:#2a1e0e;letter-spacing:2px;">
    SCRIPTURE &amp; SOUL &nbsp;✦&nbsp; KJV &amp; WEB &nbsp;✦&nbsp; SEEK AND YE SHALL FIND
    <div style="margin-top:12px;">
      <a href="{APP_URL}/unsubscribe?email={{EMAIL}}" style="color:#2a1e0e;font-size:9px;letter-spacing:1px;">Unsubscribe</a>
    </div>
  </div>

</div>
</body>
</html>"""


def main():
    recipients = get_recipients()
    if not recipients:
        print("No subscribers — nothing to send.")
        return

    print("Generating today's devotional...")
    d = get_devotional()
    print(f"  Verse: {d.get('verse_reference')} — Theme: {d.get('theme')}")
    today = date.today().strftime("%B %d, %Y")

    print(f"Sending to {len(recipients)} subscriber(s)...")
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.ehlo(); s.starttls(); s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        for email in recipients:
            html = build_email(d).replace("{EMAIL}", quote_plus(email))
            msg = MIMEMultipart("alternative")
            msg["From"] = GMAIL_USER
            msg["To"] = email
            msg["Subject"] = f"Scripture & Soul — {d.get('theme', 'Daily Verse')} — {today}"
            msg.attach(MIMEText(html, "html"))
            s.sendmail(GMAIL_USER, [email], msg.as_string())
            print(f"  ✓ {email}")

    print("Devotional sent.")


if __name__ == "__main__":
    main()
