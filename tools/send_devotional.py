"""
Sends the daily Still Waters devotional email.
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
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    supabase_key = os.getenv("SUPABASE_KEY", "")
    if supabase_url and supabase_key:
        try:
            import requests as req
            r = req.get(
                f"{supabase_url}/rest/v1/subscribers?select=email",
                headers={"apikey": supabase_key,
                         "Authorization": f"Bearer {supabase_key}"},
                timeout=8,
            )
            if r.status_code == 200:
                emails = [row["email"] for row in r.json()]
                if emails:
                    return emails
        except Exception as e:
            print(f"  Supabase fetch failed: {e} — falling back to local file")
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
          background:#f0f6fb;border:1px solid #c8dff0;border-left:3px solid #7AABCC;
          border-radius:10px;padding:16px 20px;margin-bottom:10px;">
          <div style="font-family:Georgia,serif;font-size:13px;color:#3a6a8a;margin-bottom:3px;">{b['title']}</div>
          <div style="font-size:11px;color:#7a9ab0;margin-bottom:6px;">{b['author']}</div>
          <div style="font-size:13px;color:#4a6a8a;line-height:1.6;">{b['description']}</div>
          <div style="font-size:10px;color:#aac0d0;margin-top:6px;letter-spacing:1px;">FIND ON AMAZON &#8599;</div>
        </a>"""

    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#ddeef8;font-family:Georgia,'Times New Roman',serif;">
<div style="max-width:620px;margin:0 auto;padding:40px 20px;">

  <!-- Header -->
  <div style="text-align:center;background:rgba(255,255,255,0.7);border-radius:16px 16px 0 0;
    border-bottom:1px solid rgba(100,160,200,0.2);padding:32px 28px 24px;margin-bottom:0;">
    <div style="font-size:22px;color:#7AABCC;letter-spacing:8px;margin-bottom:10px;">✾ ❀ ✾</div>
    <div style="font-family:Georgia,serif;font-size:32px;font-weight:bold;
      color:#2a4a6a;letter-spacing:4px;text-transform:uppercase;">Still Waters</div>
    <div style="font-size:13px;color:#5a7a9a;margin-top:8px;font-style:italic;">
      Morning Devotional &nbsp;·&nbsp; {today}
    </div>
  </div>

  <!-- Theme badge -->
  <div style="text-align:center;background:rgba(255,255,255,0.6);padding:20px 28px;">
    <span style="font-size:10px;letter-spacing:3px;text-transform:uppercase;
      color:#ffffff;background:linear-gradient(135deg,#5A9CBB,#3A7A9A);
      padding:6px 20px;border-radius:20px;">
      {d.get('theme','').upper()}
    </span>
  </div>

  <!-- Verse of the Day -->
  <div style="background:rgba(255,255,255,0.88);border:1px solid rgba(100,160,220,0.2);
    border-left:4px solid #7AABCC;border-radius:12px;padding:32px 36px;margin-bottom:20px;">
    <div style="font-size:10px;letter-spacing:3px;text-transform:uppercase;
      color:#5A9CBB;margin-bottom:16px;">✾ &nbsp; Verse of the Day</div>
    <div style="font-size:22px;line-height:1.8;color:#1a2a3a;font-style:italic;margin-bottom:16px;">
      &ldquo;{d.get('verse_text','')}&rdquo;
    </div>
    <div style="font-size:12px;color:#5A9CBB;letter-spacing:2px;">
      — {d.get('verse_reference','')} &nbsp;·&nbsp; KJV
    </div>
  </div>

  <!-- Reflection -->
  <div style="font-size:16px;line-height:1.8;color:#3a5a7a;margin-bottom:20px;
    font-style:italic;padding:0 8px;">
    {d.get('reflection','')}
  </div>

  <!-- Prayer -->
  <div style="background:rgba(255,255,255,0.7);border:1px solid rgba(100,160,220,0.2);
    border-radius:12px;padding:20px 24px;margin-bottom:32px;text-align:center;">
    <div style="font-size:10px;letter-spacing:3px;text-transform:uppercase;
      color:#7a9ab0;margin-bottom:10px;">Morning Prayer</div>
    <div style="font-size:15px;color:#4a6a8a;line-height:1.7;font-style:italic;">
      {d.get('prayer','')}
    </div>
  </div>

  <!-- CTA -->
  <div style="text-align:center;margin-bottom:36px;">
    <div style="font-size:13px;color:#5a7a9a;margin-bottom:14px;font-style:italic;">
      Searching for a word that speaks to where you are today?
    </div>
    <a href="{APP_URL}" target="_blank"
      style="display:inline-block;font-size:12px;letter-spacing:3px;text-transform:uppercase;
      color:#ffffff;background:linear-gradient(135deg,#5A9CBB,#3A7A9A);
      text-decoration:none;padding:14px 36px;border-radius:10px;">
      Search by Mood &#8594;
    </a>
  </div>

  <!-- Divider -->
  <div style="text-align:center;color:rgba(100,160,200,0.5);font-size:18px;
    letter-spacing:10px;margin-bottom:28px;">✿ ✿ ✿</div>

  <!-- Books -->
  <div style="font-size:10px;letter-spacing:3px;text-transform:uppercase;
    color:#7a9ab0;margin-bottom:16px;text-align:center;">Further Reading</div>
  {books_html}

  <!-- Footer -->
  <div style="text-align:center;margin-top:36px;padding-top:20px;
    border-top:1px solid rgba(100,160,200,0.2);font-size:10px;color:#8aaabb;letter-spacing:2px;">
    STILL WATERS &nbsp;✿&nbsp; KJV &amp; WEB &nbsp;✿&nbsp; HE LEADETH ME BESIDE THE STILL WATERS
    <div style="margin-top:12px;">
      <a href="{APP_URL}/unsubscribe?email={{EMAIL}}" style="color:#aac0d0;font-size:9px;letter-spacing:1px;">Unsubscribe</a>
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
            msg["Subject"] = f"Still Waters — {d.get('theme', 'Daily Verse')} — {today}"
            msg.attach(MIMEText(html, "html"))
            s.sendmail(GMAIL_USER, [email], msg.as_string())
            print(f"  sent: {email}")

    print("Devotional sent.")


if __name__ == "__main__":
    main()
