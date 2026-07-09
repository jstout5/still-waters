"""
Still Waters — Daily Email
One email per subscriber: verse, reflection, prayer, + reading plan card (no inline text).
Outer brand: JStout Inc.  Inner content: Still Waters.

Usage: python tools/send_devotional.py
"""

import json
import os
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote_plus

import psycopg2
import psycopg2.extras
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

client       = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
GMAIL_USER   = os.getenv("GMAIL_USER")
GMAIL_PASS   = os.getenv("GMAIL_APP_PASSWORD")
APP_URL      = os.getenv("APP_URL", "https://still-waters-scripture.onrender.com")
DATABASE_URL = os.getenv("DATABASE_URL", "")

BIBLE_BOOKS = [
    ("Genesis",50),("Exodus",40),("Leviticus",27),("Numbers",36),("Deuteronomy",34),
    ("Joshua",24),("Judges",21),("Ruth",4),("1 Samuel",31),("2 Samuel",24),
    ("1 Kings",22),("2 Kings",25),("1 Chronicles",29),("2 Chronicles",36),("Ezra",10),
    ("Nehemiah",13),("Esther",10),("Job",42),("Psalms",150),("Proverbs",31),
    ("Ecclesiastes",12),("Song of Solomon",8),("Isaiah",66),("Jeremiah",52),
    ("Lamentations",5),("Ezekiel",48),("Daniel",12),("Hosea",14),("Joel",3),
    ("Amos",9),("Obadiah",1),("Jonah",4),("Micah",7),("Nahum",3),("Habakkuk",3),
    ("Zephaniah",3),("Haggai",2),("Zechariah",14),("Malachi",4),
    ("Matthew",28),("Mark",16),("Luke",24),("John",21),("Acts",28),("Romans",16),
    ("1 Corinthians",16),("2 Corinthians",13),("Galatians",6),("Ephesians",6),
    ("Philippians",4),("Colossians",4),("1 Thessalonians",5),("2 Thessalonians",3),
    ("1 Timothy",6),("2 Timothy",4),("Titus",3),("Philemon",1),("Hebrews",13),
    ("James",5),("1 Peter",5),("2 Peter",3),("1 John",5),("2 John",1),
    ("3 John",1),("Jude",1),("Revelation",22),
]
ALL_CH     = [(b, ch) for b, n in BIBLE_BOOKS for ch in range(1, n+1)]
TOTAL      = len(ALL_CH)


# ── DB ──────────────────────────────────────────────────────────────────────

def _db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def get_subscribers() -> list[dict]:
    with _db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT s.email,
                       rp.current_day,
                       rp.chapters_per_day,
                       rp.minutes_per_day,
                       rp.version,
                       rp.streak,
                       rp.active AS has_plan
                FROM subscribers s
                LEFT JOIN reading_plans rp ON rp.email=s.email AND rp.active=TRUE
                ORDER BY s.created_at
            """)
            return [dict(r) for r in cur.fetchall()]


def advance_plan(email: str, new_day: int, new_streak: int):
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE reading_plans SET current_day=%s, streak=%s, updated_at=NOW() WHERE email=%s",
                (new_day, new_streak, email),
            )
        conn.commit()


def mark_complete(email: str):
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE reading_plans SET active=FALSE WHERE email=%s", (email,))
        conn.commit()


# ── CONTENT ─────────────────────────────────────────────────────────────────

def get_devotional() -> dict:
    today = date.today().strftime("%B %d, %Y")
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=900,
        messages=[{"role": "user", "content": f"""Morning devotional for {today}. JSON only:
{{
  "verse_reference": "Book Chapter:Verse",
  "verse_text": "Full KJV verse text",
  "theme": "One word",
  "reflection": "2 sentences of pastoral reflection",
  "prayer": "One sentence morning prayer"
}}"""}],
    )
    raw = resp.content[0].text.strip()
    if "```" in raw:
        for part in raw.split("```"):
            part = part.strip().lstrip("json").strip()
            if part.startswith("{"):
                raw = part
                break
    return json.loads(raw)


def chapters_for_day(day: int, cpd: int) -> list[tuple]:
    start = (day - 1) * cpd
    if start >= TOTAL:
        return []
    return ALL_CH[start : min(day * cpd, TOTAL)]


def reading_label(chapters: list[tuple]) -> str:
    if not chapters:
        return ""
    if len(chapters) == 1:
        return f"{chapters[0][0]} {chapters[0][1]}"
    first, last = chapters[0], chapters[-1]
    if first[0] == last[0]:
        return f"{first[0]} {first[1]}–{last[1]}"
    return f"{first[0]} {first[1]} · {last[0]} {last[1]}"


# ── EMAIL ────────────────────────────────────────────────────────────────────

def build_email(sub: dict, dev: dict, reading: str, day: int, total_days: int, progress_pct: int) -> str:
    today_str  = date.today().strftime("%B %d, %Y")
    email      = sub["email"]
    unsub      = f"{APP_URL}/unsubscribe?email={quote_plus(email)}"
    plan_unsub = f"{APP_URL}/reading-plan/unsubscribe?email={quote_plus(email)}"
    has_plan   = bool(sub.get("has_plan") and reading)
    streak     = sub.get("streak") or 0
    minutes    = sub.get("minutes_per_day") or 15
    version    = sub.get("version") or "KJV"

    reading_block = ""
    if has_plan:
        streak_line = (
            f'<div style="font-size:12px;color:#E25C00;font-weight:700;margin-top:10px;">'
            f'🔥 {streak}-Day Streak — Keep Going!</div>'
        ) if streak >= 3 else ""

        reading_block = f"""
  <div style="background:#ffffff;border:1px solid #c8dff0;border-left:4px solid #5A9CBB;
    border-radius:12px;padding:24px 28px;margin-bottom:20px;">
    <div style="font-size:9px;letter-spacing:3px;text-transform:uppercase;
      color:#5A9CBB;margin-bottom:12px;">📖 &nbsp; The Daily Scroll</div>

    <div style="font-size:22px;font-style:italic;color:#1a2a3a;margin-bottom:6px;">{reading}</div>
    <div style="font-size:11px;color:#7a9ab0;letter-spacing:1px;margin-bottom:16px;">
      Day {day} of {total_days} &nbsp;·&nbsp; {minutes} min &nbsp;·&nbsp; {version}
    </div>

    <div style="background:#e8f2f8;border-radius:4px;height:5px;overflow:hidden;margin-bottom:16px;">
      <div style="background:linear-gradient(90deg,#7AABCC,#5A9CBB);height:5px;
        width:{progress_pct}%;min-width:4px;border-radius:4px;"></div>
    </div>

    {streak_line}

    <a href="{APP_URL}/read" target="_blank"
      style="display:inline-block;margin-top:16px;font-size:11px;letter-spacing:2px;
      text-transform:uppercase;color:#ffffff;background:#5A9CBB;
      text-decoration:none;padding:10px 24px;border-radius:8px;">
      Read Now &rarr;
    </a>

    <div style="margin-top:14px;font-size:10px;color:#aac0d0;">
      <a href="{plan_unsub}" style="color:#aac0d0;">Pause reading plan</a>
    </div>
  </div>"""

    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#111;font-family:Georgia,'Times New Roman',serif;">
<div style="max-width:580px;margin:0 auto;">

  <!-- JStoutInc band -->
  <div style="background:#c8102e;padding:12px 24px;border-radius:10px 10px 0 0;
    display:flex;align-items:center;justify-content:space-between;">
    <div style="font-family:Arial,sans-serif;font-size:12px;font-weight:800;
      letter-spacing:3px;text-transform:uppercase;color:#fff;">JStout Inc</div>
    <div style="font-family:Arial,sans-serif;font-size:10px;font-weight:600;
      letter-spacing:2px;text-transform:uppercase;color:rgba(255,255,255,.65);">Still Waters</div>
  </div>

  <!-- Body -->
  <div style="background:#ddeef8;padding:32px 24px 24px;">

    <!-- Header -->
    <div style="text-align:center;margin-bottom:24px;">
      <div style="font-size:18px;color:#7AABCC;letter-spacing:8px;margin-bottom:8px;">✾ ❀ ✾</div>
      <div style="font-size:26px;font-weight:bold;color:#2a4a6a;letter-spacing:4px;
        text-transform:uppercase;">Still Waters</div>
      <div style="font-size:12px;color:#5a7a9a;margin-top:4px;font-style:italic;">
        {today_str}
      </div>
    </div>

    <!-- Theme chip -->
    <div style="text-align:center;margin-bottom:20px;">
      <span style="font-size:10px;letter-spacing:3px;text-transform:uppercase;color:#fff;
        background:linear-gradient(135deg,#5A9CBB,#3A7A9A);padding:5px 18px;border-radius:20px;">
        {dev.get('theme','').upper()}
      </span>
    </div>

    <!-- Verse -->
    <div style="background:#fff;border-left:4px solid #7AABCC;border-radius:10px;
      padding:24px 28px;margin-bottom:18px;">
      <div style="font-size:9px;letter-spacing:3px;text-transform:uppercase;
        color:#5A9CBB;margin-bottom:12px;">Verse of the Day</div>
      <div style="font-size:20px;line-height:1.8;color:#1a2a3a;font-style:italic;margin-bottom:12px;">
        &ldquo;{dev.get('verse_text','')}&rdquo;
      </div>
      <div style="font-size:11px;color:#5A9CBB;letter-spacing:2px;">
        &mdash; {dev.get('verse_reference','')} &nbsp;&middot;&nbsp; KJV
      </div>
    </div>

    <!-- Reflection -->
    <div style="font-size:15px;line-height:1.8;color:#3a5a7a;font-style:italic;
      margin-bottom:18px;padding:0 4px;">
      {dev.get('reflection','')}
    </div>

    <!-- Prayer -->
    <div style="background:rgba(255,255,255,.65);border-radius:10px;
      padding:16px 20px;margin-bottom:24px;text-align:center;">
      <div style="font-size:9px;letter-spacing:3px;text-transform:uppercase;
        color:#7a9ab0;margin-bottom:8px;">Morning Prayer</div>
      <div style="font-size:14px;color:#4a6a8a;line-height:1.7;font-style:italic;">
        {dev.get('prayer','')}
      </div>
    </div>

    {reading_block}

    <!-- CTA -->
    <div style="text-align:center;margin-bottom:24px;">
      <a href="{APP_URL}" target="_blank"
        style="display:inline-block;font-size:11px;letter-spacing:2px;
        text-transform:uppercase;color:#fff;background:linear-gradient(135deg,#5A9CBB,#3A7A9A);
        text-decoration:none;padding:11px 28px;border-radius:8px;">
        Search Scripture by Mood &rarr;
      </a>
    </div>

    <!-- Footer -->
    <div style="text-align:center;padding-top:16px;border-top:1px solid rgba(100,160,200,.2);
      font-size:9px;color:#8aaabb;letter-spacing:2px;">
      STILL WATERS &nbsp;·&nbsp; KJV &nbsp;·&nbsp; HE LEADETH ME BESIDE THE STILL WATERS
      <div style="margin-top:10px;">
        <a href="{unsub}" style="color:#aac0d0;">Unsubscribe</a>
      </div>
    </div>

  </div><!-- /body -->

  <!-- JStoutInc footer band -->
  <div style="background:#1a1a1a;padding:10px 24px;border-radius:0 0 10px 10px;text-align:center;">
    <div style="font-size:9px;color:rgba(255,255,255,.25);letter-spacing:2px;text-transform:uppercase;">
      JStout Inc &nbsp;·&nbsp; Kentucky Built &amp; Based
    </div>
  </div>

</div>
</body>
</html>"""


# ── MAIN ────────────────────────────────────────────────────────────────────

def main():
    subscribers = get_subscribers()
    if not subscribers:
        print("No subscribers.")
        return

    print("Generating devotional…")
    dev = get_devotional()
    print(f"  {dev.get('verse_reference')} — {dev.get('theme')}")
    today_str = date.today().strftime("%B %d, %Y")

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.ehlo(); smtp.starttls(); smtp.login(GMAIL_USER, GMAIL_PASS)

        for sub in subscribers:
            email    = sub["email"]
            has_plan = bool(sub.get("has_plan"))
            reading  = ""
            day      = sub.get("current_day") or 1
            cpd      = sub.get("chapters_per_day") or 3
            total_days  = -(-TOTAL // cpd)
            progress_pct = min(100, round((day / total_days) * 100))

            if has_plan:
                chapters = chapters_for_day(day, cpd)
                if not chapters:
                    mark_complete(email)
                    has_plan = False
                    print(f"  {email} — Bible complete!")
                else:
                    reading = reading_label(chapters)

            html = build_email(sub, dev, reading, day, total_days, progress_pct)

            subject = f"Still Waters — {dev.get('theme','Daily Verse')} — {today_str}"

            msg = MIMEMultipart("alternative")
            msg["From"]    = f"Still Waters <{GMAIL_USER}>"
            msg["To"]      = email
            msg["Subject"] = subject
            msg.attach(MIMEText(html, "html"))
            smtp.sendmail(GMAIL_USER, [email], msg.as_string())

            if has_plan:
                advance_plan(email, day + 1, (sub.get("streak") or 0) + 1)

            print(f"  ✓ {email}" + (f" — {reading}" if reading else ""))

    print("Done.")


if __name__ == "__main__":
    main()
