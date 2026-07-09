"""
Still Waters — Daily Email
Sends the actual Bible reading for the day + a short devotional.
Outer brand: JStout Inc.  Inner content: Still Waters.

Usage: python tools/send_devotional.py
"""

import json
import os
import smtplib
import time
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote_plus

import psycopg2
import psycopg2.extras
import requests
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
ALL_CH = [(b, ch) for b, n in BIBLE_BOOKS for ch in range(1, n+1)]
TOTAL  = len(ALL_CH)


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


# ── BIBLE TEXT ───────────────────────────────────────────────────────────────

def fetch_chapter_html(book: str, chapter: int, version: str = "kjv") -> str:
    slug = book.lower().replace(" ", "+")
    url  = f"https://bible-api.com/{slug}+{chapter}?translation={version.lower()}"
    try:
        r    = requests.get(url, timeout=12)
        data = r.json()
        verses = data.get("verses", [])
        if not verses:
            return f"<p><em>{book} {chapter} — text unavailable</em></p>"
        lines = []
        for v in verses:
            num  = v.get("verse", "")
            text = v.get("text", "").strip()
            lines.append(
                f'<sup style="font-size:9px;color:#9ab0c0;margin-right:2px;">{num}</sup>'
                f'<span style="line-height:1.9;">{text} </span>'
            )
        return (
            f'<div style="font-size:15px;color:#1a2a3a;font-family:Georgia,serif;'
            f'margin-bottom:8px;font-weight:700;color:#2a4a6a;">'
            f'{book} {chapter}</div>'
            f'<p style="font-size:15px;line-height:1.9;color:#1a2a3a;margin:0 0 20px;">{"".join(lines)}</p>'
        )
    except Exception as e:
        return f"<p><em>{book} {chapter} — could not load ({e})</em></p>"


# ── DEVOTIONAL ───────────────────────────────────────────────────────────────

def get_devotional() -> dict:
    today = date.today().strftime("%B %d, %Y")
    resp  = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        messages=[{"role": "user", "content": f"""Morning devotional for {today}. JSON only:
{{
  "verse_reference": "Book Chapter:Verse",
  "verse_text": "Full KJV verse text",
  "theme": "One word",
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


# ── EMAIL ─────────────────────────────────────────────────────────────────────

def build_email(sub: dict, dev: dict, chapters: list[tuple], day: int,
                total_days: int, progress_pct: int, chapter_html: str) -> str:
    today_str  = date.today().strftime("%B %d, %Y")
    email      = sub["email"]
    unsub      = f"{APP_URL}/unsubscribe?email={quote_plus(email)}"
    plan_unsub = f"{APP_URL}/reading-plan/unsubscribe?email={quote_plus(email)}"
    has_plan   = bool(sub.get("has_plan") and chapters)
    streak     = sub.get("streak") or 0
    minutes    = sub.get("minutes_per_day") or 15
    version    = sub.get("version") or "KJV"
    label      = reading_label(chapters)

    streak_line = ""
    if streak >= 3:
        streak_line = (
            f'<div style="font-size:12px;color:#E25C00;font-weight:700;margin:10px 0 0;">'
            f'🔥 {streak}-Day Streak — Keep it up!</div>'
        )

    reading_section = ""
    if has_plan:
        reading_section = f"""
  <!-- Reading -->
  <div style="background:#fff;border:1px solid #c8dff0;border-left:4px solid #5A9CBB;
    border-radius:12px;padding:24px 28px;margin-bottom:20px;">

    <div style="font-size:9px;letter-spacing:3px;text-transform:uppercase;
      color:#5A9CBB;margin-bottom:6px;">📖 &nbsp; Today's Reading</div>
    <div style="font-size:20px;font-weight:700;color:#1a2a3a;margin-bottom:4px;">{label}</div>
    <div style="font-size:11px;color:#7a9ab0;letter-spacing:1px;margin-bottom:14px;">
      Day {day} of {total_days} &nbsp;·&nbsp; {minutes} min &nbsp;·&nbsp; {version}
    </div>

    <!-- Progress bar -->
    <div style="background:#e8f2f8;border-radius:4px;height:6px;overflow:hidden;margin-bottom:6px;">
      <div style="background:linear-gradient(90deg,#7AABCC,#5A9CBB);height:6px;
        width:{progress_pct}%;min-width:4px;border-radius:4px;"></div>
    </div>
    <div style="font-size:10px;color:#aac0d0;margin-bottom:18px;">{progress_pct}% through the Bible</div>

    {streak_line}

    <!-- Chapter text -->
    <div style="border-top:1px solid #e0eef8;margin-top:16px;padding-top:16px;">
      {chapter_html}
    </div>

    <div style="margin-top:16px;font-size:10px;color:#aac0d0;">
      <a href="{plan_unsub}" style="color:#aac0d0;">Pause reading plan</a>
    </div>
  </div>"""

    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#111;font-family:Georgia,'Times New Roman',serif;">
<div style="max-width:600px;margin:0 auto;">

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

    <div style="text-align:center;margin-bottom:24px;">
      <div style="font-size:26px;font-weight:bold;color:#2a4a6a;letter-spacing:4px;
        text-transform:uppercase;">Still Waters</div>
      <div style="font-size:12px;color:#5a7a9a;margin-top:4px;font-style:italic;">{today_str}</div>
    </div>

    {reading_section}

    <!-- Devotional verse -->
    <div style="background:#fff;border-left:4px solid #7AABCC;border-radius:10px;
      padding:20px 24px;margin-bottom:16px;">
      <div style="font-size:9px;letter-spacing:3px;text-transform:uppercase;
        color:#5A9CBB;margin-bottom:10px;">{dev.get('theme','').upper()}</div>
      <div style="font-size:17px;line-height:1.8;color:#1a2a3a;font-style:italic;margin-bottom:10px;">
        &ldquo;{dev.get('verse_text','')}&rdquo;
      </div>
      <div style="font-size:11px;color:#5A9CBB;letter-spacing:1px;">
        &mdash; {dev.get('verse_reference','')}
      </div>
    </div>

    <!-- Prayer -->
    <div style="background:rgba(255,255,255,.6);border-radius:10px;
      padding:14px 20px;margin-bottom:20px;text-align:center;">
      <div style="font-size:9px;letter-spacing:3px;text-transform:uppercase;
        color:#7a9ab0;margin-bottom:6px;">Morning Prayer</div>
      <div style="font-size:14px;color:#4a6a8a;line-height:1.7;font-style:italic;">
        {dev.get('prayer','')}
      </div>
    </div>

    <div style="text-align:center;padding-top:14px;border-top:1px solid rgba(100,160,200,.2);
      font-size:9px;color:#8aaabb;letter-spacing:2px;">
      STILL WATERS &nbsp;·&nbsp; HE LEADETH ME BESIDE THE STILL WATERS
      <div style="margin-top:10px;">
        <a href="{unsub}" style="color:#aac0d0;">Unsubscribe</a>
      </div>
    </div>

  </div>

  <!-- JStoutInc footer -->
  <div style="background:#1a1a1a;padding:10px 24px;border-radius:0 0 10px 10px;text-align:center;">
    <div style="font-size:9px;color:rgba(255,255,255,.25);letter-spacing:2px;text-transform:uppercase;">
      JStout Inc &nbsp;·&nbsp; Kentucky Built &amp; Based
    </div>
  </div>

</div>
</body>
</html>"""


# ── MAIN ─────────────────────────────────────────────────────────────────────

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
            day      = sub.get("current_day") or 1
            cpd      = sub.get("chapters_per_day") or 3
            total_days   = -(-TOTAL // cpd)
            progress_pct = min(100, round((day / total_days) * 100))

            chapters    = []
            chapter_html = ""

            if has_plan:
                chapters = chapters_for_day(day, cpd)
                if not chapters:
                    mark_complete(email)
                    has_plan = False
                    print(f"  {email} — Bible complete!")
                else:
                    parts = []
                    for book, ch in chapters:
                        parts.append(fetch_chapter_html(book, ch, sub.get("version") or "kjv"))
                        time.sleep(0.3)
                    chapter_html = "".join(parts)

            html = build_email(sub, dev, chapters, day, total_days, progress_pct, chapter_html)
            subject = f"Still Waters — {reading_label(chapters) or dev.get('theme','Daily Verse')} — {today_str}"

            msg = MIMEMultipart("alternative")
            msg["From"]    = f"Still Waters <{GMAIL_USER}>"
            msg["To"]      = email
            msg["Subject"] = subject
            msg.attach(MIMEText(html, "html"))
            smtp.sendmail(GMAIL_USER, [email], msg.as_string())

            if has_plan:
                advance_plan(email, day + 1, (sub.get("streak") or 0) + 1)

            label = reading_label(chapters)
            print(f"  ✓ {email}" + (f" — {label}" if label else ""))

    print("Done.")


if __name__ == "__main__":
    main()
