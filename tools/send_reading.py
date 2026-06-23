"""
The Daily Scroll — nightly Bible reading delivery.
Sends each subscriber their day's chapters (full text) based on their reading plan.

Usage: python tools/send_reading.py
"""

import json
import os
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote_plus

import requests
from dotenv import load_dotenv

load_dotenv()

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
APP_URL = os.getenv("APP_URL", "http://localhost:5050")
READING_PLANS_FILE = Path(__file__).parent.parent / "reading_plans.json"
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")


def _sb_headers():
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json", "Prefer": "return=minimal"}


def _sb_available():
    return bool(SUPABASE_URL and SUPABASE_KEY)


def load_plans() -> list:
    if _sb_available():
        try:
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/reading_plans?active=eq.true&select=*",
                headers=_sb_headers(), timeout=8,
            )
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            print(f"  Supabase load failed: {e} — falling back to local file")
    if READING_PLANS_FILE.exists():
        plans = json.loads(READING_PLANS_FILE.read_text(encoding="utf-8")).get("plans", [])
        return [p for p in plans if p.get("active")]
    return []


def update_plan(plan: dict):
    if _sb_available():
        try:
            requests.patch(
                f"{SUPABASE_URL}/rest/v1/reading_plans?email=eq.{plan['email']}",
                headers=_sb_headers(),
                json={k: v for k, v in plan.items() if k != "email"},
                timeout=8,
            )
            return
        except Exception as e:
            print(f"  Supabase update failed for {plan['email']}: {e}")

BIBLE_BOOKS = [
    ("Genesis", 50), ("Exodus", 40), ("Leviticus", 27), ("Numbers", 36),
    ("Deuteronomy", 34), ("Joshua", 24), ("Judges", 21), ("Ruth", 4),
    ("1 Samuel", 31), ("2 Samuel", 24), ("1 Kings", 22), ("2 Kings", 25),
    ("1 Chronicles", 29), ("2 Chronicles", 36), ("Ezra", 10), ("Nehemiah", 13),
    ("Esther", 10), ("Job", 42), ("Psalms", 150), ("Proverbs", 31),
    ("Ecclesiastes", 12), ("Song of Solomon", 8), ("Isaiah", 66), ("Jeremiah", 52),
    ("Lamentations", 5), ("Ezekiel", 48), ("Daniel", 12), ("Hosea", 14),
    ("Joel", 3), ("Amos", 9), ("Obadiah", 1), ("Jonah", 4), ("Micah", 7),
    ("Nahum", 3), ("Habakkuk", 3), ("Zephaniah", 3), ("Haggai", 2),
    ("Zechariah", 14), ("Malachi", 4),
    ("Matthew", 28), ("Mark", 16), ("Luke", 24), ("John", 21), ("Acts", 28),
    ("Romans", 16), ("1 Corinthians", 16), ("2 Corinthians", 13), ("Galatians", 6),
    ("Ephesians", 6), ("Philippians", 4), ("Colossians", 4), ("1 Thessalonians", 5),
    ("2 Thessalonians", 3), ("1 Timothy", 6), ("2 Timothy", 4), ("Titus", 3),
    ("Philemon", 1), ("Hebrews", 13), ("James", 5), ("1 Peter", 5), ("2 Peter", 3),
    ("1 John", 5), ("2 John", 1), ("3 John", 1), ("Jude", 1), ("Revelation", 22),
]

ALL_CHAPTERS = [(book, ch) for book, count in BIBLE_BOOKS for ch in range(1, count + 1)]
TOTAL_CHAPTERS = len(ALL_CHAPTERS)


def chapters_for_day(day: int, chapters_per_day: int) -> list:
    start = (day - 1) * chapters_per_day
    if start >= TOTAL_CHAPTERS:
        return []
    end = min(day * chapters_per_day, TOTAL_CHAPTERS)
    return ALL_CHAPTERS[start:end]


def fetch_chapter(book: str, chapter: int, version: str) -> list[dict]:
    """Returns list of {verse, text} dicts from bible-api.com."""
    translation = "kjv" if version == "KJV" else "web"
    book_enc = book.replace(" ", "+")
    try:
        r = requests.get(
            f"https://bible-api.com/{book_enc}+{chapter}?translation={translation}",
            timeout=15,
        )
        if r.status_code == 200:
            return [{"verse": v["verse"], "text": v["text"].strip()} for v in r.json().get("verses", [])]
    except Exception:
        pass
    return []


def chapter_html(book: str, chapter: int, verses: list[dict]) -> str:
    ref = f"{book} {chapter}"
    if not verses:
        return f"""
        <div style="background:#f0f6fb;border:1px solid #c8dff0;border-left:3px solid #7AABCC;
          border-radius:10px;padding:20px 24px;margin-bottom:20px;">
          <div style="font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#5A9CBB;margin-bottom:10px;">{ref}</div>
          <div style="font-style:italic;color:#aac0d0;font-size:14px;">Chapter text unavailable. Please open your Bible to {ref}.</div>
        </div>"""

    verses_html = "".join(
        f'<span style="font-size:10px;color:#7a9ab0;vertical-align:super;margin-right:3px;">{v["verse"]}</span>'
        f'{v["text"]} '
        for v in verses
    )
    return f"""
    <div style="background:#f0f6fb;border:1px solid #c8dff0;border-left:3px solid #7AABCC;
      border-radius:10px;padding:24px 28px;margin-bottom:20px;">
      <div style="font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#5A9CBB;margin-bottom:14px;">{ref}</div>
      <div style="font-size:16px;line-height:1.9;color:#1a2a3a;">{verses_html}</div>
    </div>"""


def build_email(plan: dict, chapters_data: list[dict], day: int) -> str:
    today_str = date.today().strftime("%B %d, %Y")
    version = plan.get("version", "KJV")
    minutes = plan.get("minutes_per_day", 15)
    cpd = plan.get("chapters_per_day", 3)
    total_days = -(-TOTAL_CHAPTERS // cpd)
    email = plan["email"]
    unsub_url = f"{APP_URL}/reading-plan/unsubscribe?email={quote_plus(email)}"

    chapters_html = "".join(
        chapter_html(c["book"], c["chapter"], c["verses"])
        for c in chapters_data
    )
    chapter_labels = ", ".join(f"{c['book']} {c['chapter']}" for c in chapters_data)
    progress_pct = min(100, round((day / total_days) * 100))

    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#ddeef8;font-family:Georgia,'Times New Roman',serif;">
<div style="max-width:640px;margin:0 auto;padding:40px 20px;">

  <!-- Header -->
  <div style="text-align:center;background:rgba(255,255,255,0.7);border-radius:16px 16px 0 0;
    border-bottom:1px solid rgba(100,160,200,0.2);padding:28px 28px 20px;">
    <div style="font-size:20px;color:#7AABCC;letter-spacing:8px;margin-bottom:8px;">✦ ✾ ✦</div>
    <div style="font-family:Georgia,serif;font-size:26px;font-weight:bold;color:#2a4a6a;letter-spacing:3px;text-transform:uppercase;">The Daily Scroll</div>
    <div style="font-size:12px;color:#5a7a9a;margin-top:6px;font-style:italic;">Still Waters &nbsp;·&nbsp; {today_str}</div>
  </div>

  <!-- Day badge -->
  <div style="text-align:center;background:rgba(255,255,255,0.6);padding:16px 28px 0;">
    <span style="font-size:10px;letter-spacing:3px;text-transform:uppercase;
      color:#fff;background:linear-gradient(135deg,#5A9CBB,#3A7A9A);
      padding:5px 18px;border-radius:20px;">Day {day} of {total_days} &nbsp;·&nbsp; {minutes} min plan &nbsp;·&nbsp; {version}</span>
    {f'<div style="margin-top:10px;font-size:14px;color:#E25C00;font-weight:700;letter-spacing:1px;">🔥 {plan.get("streak",1)}-Day Streak — Keep Going!</div>' if plan.get("streak", 1) >= 3 else ''}
  </div>

  <!-- Progress bar -->
  <div style="background:rgba(255,255,255,0.6);padding:14px 28px 20px;">
    <div style="background:rgba(100,160,220,0.15);border-radius:4px;height:5px;overflow:hidden;">
      <div style="background:linear-gradient(90deg,#7AABCC,#5A9CBB);height:5px;width:{progress_pct}%;border-radius:4px;"></div>
    </div>
    <div style="font-size:10px;color:#7a9ab0;text-align:right;margin-top:5px;letter-spacing:1px;">{progress_pct}% through the Bible</div>
  </div>

  <!-- Today's reading label -->
  <div style="background:rgba(255,255,255,0.88);border-radius:0;padding:16px 28px;
    border-top:1px solid rgba(100,160,220,0.15);border-bottom:1px solid rgba(100,160,220,0.15);">
    <div style="font-size:10px;letter-spacing:2px;text-transform:uppercase;color:#5a7a9a;margin-bottom:4px;">Tonight's Reading</div>
    <div style="font-size:17px;color:#2a4a6a;font-style:italic;">{chapter_labels}</div>
  </div>

  <!-- Chapter text -->
  <div style="padding:24px 0 8px;">
    {chapters_html}
  </div>

  <!-- CTA -->
  <div style="text-align:center;margin:20px 0 32px;">
    <div style="font-size:13px;color:#5a7a9a;margin-bottom:12px;font-style:italic;">
      Searching for a word that speaks to where you are today?
    </div>
    <a href="{APP_URL}" target="_blank"
      style="display:inline-block;font-size:11px;letter-spacing:3px;text-transform:uppercase;
      color:#fff;background:linear-gradient(135deg,#5A9CBB,#3A7A9A);
      text-decoration:none;padding:12px 32px;border-radius:10px;">
      Search by Mood &#8594;
    </a>
  </div>

  <!-- Footer -->
  <div style="text-align:center;margin-top:24px;padding-top:18px;
    border-top:1px solid rgba(100,160,200,0.2);font-size:10px;color:#8aaabb;letter-spacing:2px;">
    STILL WATERS &nbsp;✿&nbsp; THE DAILY SCROLL &nbsp;✿&nbsp; THY WORD IS A LAMP
    <div style="margin-top:10px;">
      <a href="{unsub_url}" style="color:#aac0d0;font-size:9px;letter-spacing:1px;">Pause my reading plan</a>
    </div>
  </div>

</div>
</body>
</html>"""


def build_completion_email(plan: dict) -> str:
    today_str = date.today().strftime("%B %d, %Y")
    email = plan["email"]
    unsub_url = f"{APP_URL}/reading-plan/unsubscribe?email={quote_plus(email)}"
    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#ddeef8;font-family:Georgia,'Times New Roman',serif;">
<div style="max-width:640px;margin:0 auto;padding:60px 20px;text-align:center;">
  <div style="font-size:28px;color:#7AABCC;letter-spacing:8px;margin-bottom:16px;">✦ ✾ ✦</div>
  <div style="font-family:Georgia,serif;font-size:28px;font-weight:bold;color:#2a4a6a;letter-spacing:3px;text-transform:uppercase;">You Have Read the Whole Bible</div>
  <div style="font-size:15px;color:#5a7a9a;margin-top:16px;font-style:italic;line-height:1.8;">
    From Genesis to Revelation — the entire Word of God.<br>
    Well done, good and faithful reader. &nbsp;— {today_str}
  </div>
  <div style="margin-top:32px;font-size:18px;color:#3a5a7a;font-style:italic;line-height:1.8;">
    &ldquo;Blessed is he that readeth, and they that hear the words of this prophecy.&rdquo;<br>
    <span style="font-size:12px;color:#7a9ab0;letter-spacing:2px;">— Revelation 1:3 &nbsp;·&nbsp; KJV</span>
  </div>
  <div style="margin-top:36px;">
    <a href="{APP_URL}" target="_blank"
      style="display:inline-block;font-size:11px;letter-spacing:3px;text-transform:uppercase;
      color:#fff;background:linear-gradient(135deg,#5A9CBB,#3A7A9A);
      text-decoration:none;padding:14px 36px;border-radius:10px;">
      Begin Again &#8594;
    </a>
  </div>
  <div style="margin-top:28px;font-size:10px;color:#aac0d0;">
    <a href="{unsub_url}" style="color:#aac0d0;">Unsubscribe from The Daily Scroll</a>
  </div>
</div>
</body>
</html>"""


def main():
    active = load_plans()

    if not active:
        print("No active reading plans.")
        return

    print(f"Processing {len(active)} active reading plan(s)…")

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.ehlo(); smtp.starttls(); smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)

        for plan in active:
            email = plan["email"]
            day = plan.get("current_day", 1)
            cpd = plan.get("chapters_per_day", 3)
            version = plan.get("version", "KJV")

            to_read = chapters_for_day(day, cpd)

            if not to_read:
                html = build_completion_email(plan)
                msg = MIMEMultipart("alternative")
                msg["From"] = GMAIL_USER
                msg["To"] = email
                msg["Subject"] = "Still Waters — You Have Read the Whole Bible"
                msg.attach(MIMEText(html, "html"))
                smtp.sendmail(GMAIL_USER, [email], msg.as_string())
                plan["active"] = False
                update_plan(plan)
                print(f"  ✓ {email} — COMPLETED the Bible!")
                continue

            chapters_data = []
            for book, ch in to_read:
                print(f"    Fetching {book} {ch}…")
                verses = fetch_chapter(book, ch, version)
                chapters_data.append({"book": book, "chapter": ch, "verses": verses})

            chapter_labels = ", ".join(f"{c['book']} {c['chapter']}" for c in chapters_data)
            html = build_email(plan, chapters_data, day)

            msg = MIMEMultipart("alternative")
            msg["From"] = GMAIL_USER
            msg["To"] = email
            streak = plan.get("streak", 1)
            streak_str = f" {streak} day streak" if streak >= 3 else ""
            msg["Subject"] = f"Daily Scroll — Day {day}{streak_str} — {chapter_labels}"
            msg.attach(MIMEText(html, "html"))
            smtp.sendmail(GMAIL_USER, [email], msg.as_string())
            plan["current_day"] = day + 1
            plan["streak"] = plan.get("streak", 0) + 1
            update_plan(plan)
            print(f"  sent: {email} — Day {day} (streak {plan['streak']}): {chapter_labels}")

    print("Done.")


if __name__ == "__main__":
    main()
