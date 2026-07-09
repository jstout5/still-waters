"""
Still Waters — Daily Email (unified)
Sends morning devotional + daily reading chapters.
Outer branding: JStout Inc.  Inner content: Still Waters.

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

import psycopg2
import psycopg2.extras
import requests as req
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

client           = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
GMAIL_USER       = os.getenv("GMAIL_USER")
GMAIL_PASS       = os.getenv("GMAIL_APP_PASSWORD")
APP_URL          = os.getenv("APP_URL", "https://still-waters-scripture.onrender.com")
DATABASE_URL     = os.getenv("DATABASE_URL", "")

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
ALL_CHAPTERS   = [(b, ch) for b, n in BIBLE_BOOKS for ch in range(1, n+1)]
TOTAL_CHAPTERS = len(ALL_CHAPTERS)


# ── DB ──────────────────────────────────────────────────────────────────────

def _db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def get_subscribers() -> list[dict]:
    with _db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT s.email,
                       rp.current_day, rp.chapters_per_day, rp.minutes_per_day,
                       rp.version, rp.streak, rp.active AS has_plan
                FROM subscribers s
                LEFT JOIN reading_plans rp ON rp.email=s.email AND rp.active=TRUE
                ORDER BY s.created_at
            """)
            return [dict(r) for r in cur.fetchall()]


def advance_plan(email: str, new_day: int, new_streak: int):
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE reading_plans
                SET current_day=%s, streak=%s, updated_at=NOW()
                WHERE email=%s""", (new_day, new_streak, email))
        conn.commit()


def mark_plan_complete(email: str):
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE reading_plans SET active=FALSE WHERE email=%s", (email,))
        conn.commit()


# ── CONTENT ─────────────────────────────────────────────────────────────────

def get_devotional() -> dict:
    today = date.today().strftime("%B %d, %Y")
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        messages=[{"role": "user", "content": f"""Generate a morning devotional for {today}.

Return JSON only:
{{
  "verse_reference": "Book Chapter:Verse",
  "verse_text": "Full KJV verse text",
  "theme": "One word theme",
  "reflection": "2-3 warm sentences of pastoral reflection",
  "prayer": "One sentence morning prayer tied to the verse"
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


def fetch_chapter(book: str, chapter: int, version: str) -> list[dict]:
    translation = "kjv" if version.upper() == "KJV" else "web"
    book_enc = book.replace(" ", "+")
    try:
        r = req.get(
            f"https://bible-api.com/{book_enc}+{chapter}?translation={translation}",
            timeout=15,
        )
        if r.status_code == 200:
            return [{"verse": v["verse"], "text": v["text"].strip()} for v in r.json().get("verses", [])]
    except Exception:
        pass
    return []


def chapters_for_day(day: int, cpd: int) -> list[tuple]:
    start = (day - 1) * cpd
    if start >= TOTAL_CHAPTERS:
        return []
    return ALL_CHAPTERS[start : min(day * cpd, TOTAL_CHAPTERS)]


# ── EMAIL BUILDER ────────────────────────────────────────────────────────────

def chapter_block(book: str, chapter: int, verses: list[dict]) -> str:
    ref = f"{book} {chapter}"
    if not verses:
        return f"""<div style="background:#f0f6fb;border:1px solid #c8dff0;border-left:3px solid #7AABCC;
          border-radius:10px;padding:20px 24px;margin-bottom:16px;">
          <div style="font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#5A9CBB;margin-bottom:8px;">{ref}</div>
          <div style="font-style:italic;color:#aac0d0;font-size:14px;">Open your Bible to {ref}.</div>
        </div>"""
    vhtml = "".join(
        f'<span style="font-size:10px;color:#7a9ab0;vertical-align:super;margin-right:3px;">{v["verse"]}</span>'
        f'{v["text"]} '
        for v in verses
    )
    return f"""<div style="background:#f0f6fb;border:1px solid #c8dff0;border-left:3px solid #7AABCC;
      border-radius:10px;padding:24px 28px;margin-bottom:16px;">
      <div style="font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#5A9CBB;margin-bottom:12px;">{ref}</div>
      <div style="font-size:15px;line-height:1.9;color:#1a2a3a;">{vhtml}</div>
    </div>"""


def build_email(sub: dict, dev: dict, chapters_data: list[dict]) -> str:
    today_str   = date.today().strftime("%B %d, %Y")
    email       = sub["email"]
    unsub_url   = f"{APP_URL}/unsubscribe?email={quote_plus(email)}"
    plan_unsub  = f"{APP_URL}/reading-plan/unsubscribe?email={quote_plus(email)}"
    has_plan    = bool(sub.get("has_plan") and chapters_data)
    day         = sub.get("current_day") or 1
    cpd         = sub.get("chapters_per_day") or 3
    minutes     = sub.get("minutes_per_day") or 15
    version     = sub.get("version") or "KJV"
    streak      = sub.get("streak") or 0
    total_days  = -(-TOTAL_CHAPTERS // cpd) if cpd else 1220
    progress    = min(100, round((day / total_days) * 100)) if has_plan else 0
    chapter_labels = ", ".join(f"{c['book']} {c['chapter']}" for c in chapters_data) if has_plan else ""
    chapters_html  = "".join(chapter_block(c["book"], c["chapter"], c["verses"]) for c in chapters_data) if has_plan else ""

    reading_section = ""
    if has_plan:
        streak_badge = (
            f'<div style="font-size:13px;color:#E25C00;font-weight:700;letter-spacing:1px;margin-top:8px;">'
            f'🔥 {streak}-Day Streak — Keep Going!</div>'
        ) if streak >= 3 else ""

        reading_section = f"""
  <!-- Reading Plan -->
  <div style="background:rgba(255,255,255,0.88);border:1px solid rgba(100,160,220,0.2);
    border-top:3px solid #5A9CBB;border-radius:12px;padding:28px 32px;margin-bottom:20px;">
    <div style="font-size:10px;letter-spacing:3px;text-transform:uppercase;color:#5A9CBB;margin-bottom:16px;">
      📖 &nbsp; The Daily Scroll &nbsp;·&nbsp; Day {day} of {total_days}
    </div>
    <div style="background:rgba(100,160,220,0.12);border-radius:4px;height:5px;overflow:hidden;margin-bottom:6px;">
      <div style="background:linear-gradient(90deg,#7AABCC,#5A9CBB);height:5px;width:{progress}%;border-radius:4px;"></div>
    </div>
    <div style="font-size:10px;color:#7a9ab0;text-align:right;margin-bottom:16px;letter-spacing:1px;">{progress}% through the Bible</div>
    <div style="font-size:11px;color:#5a7a9a;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">Tonight's Reading</div>
    <div style="font-size:17px;color:#2a4a6a;font-style:italic;margin-bottom:20px;">{chapter_labels}</div>
    {streak_badge}
  </div>
  <div style="margin-bottom:20px;">{chapters_html}</div>
  <div style="font-size:10px;color:#aac0d0;text-align:center;margin-bottom:28px;">
    <a href="{plan_unsub}" style="color:#aac0d0;">Pause my reading plan</a>
  </div>"""

    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#111111;font-family:Georgia,'Times New Roman',serif;">

  <!-- JStout Inc outer wrapper -->
  <div style="max-width:640px;margin:0 auto;">

    <!-- JStoutInc header band -->
    <div style="background:#c8102e;padding:14px 28px;display:flex;align-items:center;justify-content:space-between;border-radius:12px 12px 0 0;">
      <div style="font-family:Arial,sans-serif;font-size:13px;font-weight:800;letter-spacing:3px;text-transform:uppercase;color:#ffffff;">
        JStout Inc
      </div>
      <div style="font-family:Arial,sans-serif;font-size:10px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:rgba(255,255,255,0.7);">
        Still Waters — Daily Word
      </div>
    </div>

    <!-- Still Waters inner card -->
    <div style="background:#ddeef8;padding:36px 28px 28px;">

      <!-- Still Waters header -->
      <div style="text-align:center;background:rgba(255,255,255,0.75);border-radius:12px 12px 0 0;
        border-bottom:1px solid rgba(100,160,200,0.2);padding:28px 28px 20px;margin-bottom:0;">
        <div style="font-size:20px;color:#7AABCC;letter-spacing:8px;margin-bottom:8px;">✾ ❀ ✾</div>
        <div style="font-family:Georgia,serif;font-size:28px;font-weight:bold;
          color:#2a4a6a;letter-spacing:4px;text-transform:uppercase;">Still Waters</div>
        <div style="font-size:12px;color:#5a7a9a;margin-top:6px;font-style:italic;">
          Morning Devotional &nbsp;·&nbsp; {today_str}
        </div>
      </div>

      <!-- Theme badge -->
      <div style="text-align:center;background:rgba(255,255,255,0.6);padding:18px 28px;">
        <span style="font-size:10px;letter-spacing:3px;text-transform:uppercase;
          color:#fff;background:linear-gradient(135deg,#5A9CBB,#3A7A9A);
          padding:6px 20px;border-radius:20px;">{dev.get('theme','').upper()}</span>
      </div>

      <!-- Verse of the Day -->
      <div style="background:rgba(255,255,255,0.88);border:1px solid rgba(100,160,220,0.2);
        border-left:4px solid #7AABCC;border-radius:12px;padding:32px 36px;margin-bottom:20px;">
        <div style="font-size:10px;letter-spacing:3px;text-transform:uppercase;
          color:#5A9CBB;margin-bottom:14px;">✾ &nbsp; Verse of the Day</div>
        <div style="font-size:21px;line-height:1.8;color:#1a2a3a;font-style:italic;margin-bottom:14px;">
          &ldquo;{dev.get('verse_text','')}&rdquo;
        </div>
        <div style="font-size:12px;color:#5A9CBB;letter-spacing:2px;">
          — {dev.get('verse_reference','')} &nbsp;·&nbsp; KJV
        </div>
      </div>

      <!-- Reflection -->
      <div style="font-size:16px;line-height:1.8;color:#3a5a7a;margin-bottom:20px;
        font-style:italic;padding:0 8px;">
        {dev.get('reflection','')}
      </div>

      <!-- Morning Prayer -->
      <div style="background:rgba(255,255,255,0.7);border:1px solid rgba(100,160,220,0.2);
        border-radius:12px;padding:20px 24px;margin-bottom:28px;text-align:center;">
        <div style="font-size:10px;letter-spacing:3px;text-transform:uppercase;
          color:#7a9ab0;margin-bottom:10px;">Morning Prayer</div>
        <div style="font-size:15px;color:#4a6a8a;line-height:1.7;font-style:italic;">
          {dev.get('prayer','')}
        </div>
      </div>

      {reading_section}

      <!-- CTA -->
      <div style="text-align:center;margin-bottom:32px;">
        <div style="font-size:13px;color:#5a7a9a;margin-bottom:12px;font-style:italic;">
          Searching for a word that speaks to where you are today?
        </div>
        <a href="{APP_URL}" target="_blank"
          style="display:inline-block;font-size:11px;letter-spacing:3px;text-transform:uppercase;
          color:#fff;background:linear-gradient(135deg,#5A9CBB,#3A7A9A);
          text-decoration:none;padding:13px 32px;border-radius:10px;">
          Search by Mood &#8594;
        </a>
      </div>

      <!-- Still Waters footer -->
      <div style="text-align:center;padding-top:18px;border-top:1px solid rgba(100,160,200,0.2);
        font-size:10px;color:#8aaabb;letter-spacing:2px;">
        STILL WATERS &nbsp;✿&nbsp; KJV &amp; WEB &nbsp;✿&nbsp; HE LEADETH ME BESIDE THE STILL WATERS
        <div style="margin-top:10px;">
          <a href="{unsub_url}" style="color:#aac0d0;font-size:9px;letter-spacing:1px;">Unsubscribe from daily devotional</a>
        </div>
      </div>

    </div><!-- /Still Waters card -->

    <!-- JStoutInc footer band -->
    <div style="background:#1a1a1a;padding:12px 28px;border-radius:0 0 12px 12px;text-align:center;">
      <div style="font-family:Arial,sans-serif;font-size:10px;color:rgba(255,255,255,0.3);
        letter-spacing:2px;text-transform:uppercase;">
        JStout Inc &nbsp;·&nbsp; jstoutinc.onrender.com &nbsp;·&nbsp; Kentucky Built &amp; Based
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

    print("Generating devotional...")
    dev = get_devotional()
    print(f"  Verse: {dev.get('verse_reference')} — Theme: {dev.get('theme')}")
    today_str = date.today().strftime("%B %d, %Y")

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.ehlo(); smtp.starttls(); smtp.login(GMAIL_USER, GMAIL_PASS)

        for sub in subscribers:
            email    = sub["email"]
            has_plan = bool(sub.get("has_plan"))
            chapters_data = []

            if has_plan:
                day  = sub.get("current_day") or 1
                cpd  = sub.get("chapters_per_day") or 3
                ver  = sub.get("version") or "KJV"
                to_read = chapters_for_day(day, cpd)

                if not to_read:
                    mark_plan_complete(email)
                    has_plan = False
                    print(f"  {email} — Bible complete!")
                else:
                    for book, ch in to_read:
                        print(f"    Fetching {book} {ch}…")
                        verses = fetch_chapter(book, ch, ver)
                        chapters_data.append({"book": book, "chapter": ch, "verses": verses})

            html = build_email(sub, dev, chapters_data)
            subject = f"Still Waters — {dev.get('theme','Daily Verse')} — {today_str}"
            if has_plan and chapters_data:
                labels = ", ".join(f"{c['book']} {c['chapter']}" for c in chapters_data)
                subject = f"Still Waters — {dev.get('theme','Daily Verse')} + {labels}"

            msg = MIMEMultipart("alternative")
            msg["From"]    = f"Still Waters <{GMAIL_USER}>"
            msg["To"]      = email
            msg["Subject"] = subject
            msg.attach(MIMEText(html, "html"))
            smtp.sendmail(GMAIL_USER, [email], msg.as_string())

            if has_plan:
                day = sub.get("current_day") or 1
                advance_plan(email, day + 1, (sub.get("streak") or 0) + 1)

            print(f"  ✓ {email}")

    print("Done.")


if __name__ == "__main__":
    main()
