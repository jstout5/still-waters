"""
Scripture by Mood — Flask app powered by Claude.
Returns relevant Bible passages based on the user's emotional state or life issue.
"""

import os
import json
import re
from pathlib import Path
from flask import Flask, render_template, request, jsonify, Response, stream_with_context
from anthropic import Anthropic
from dotenv import load_dotenv

SUBSCRIBERS_FILE = Path(__file__).parent / "subscribers.json"
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")


def _sb_headers():
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json", "Prefer": "return=minimal"}


def _sb_available():
    return bool(SUPABASE_URL and SUPABASE_KEY)


def load_subscribers() -> list:
    if _sb_available():
        try:
            import requests as req
            r = req.get(f"{SUPABASE_URL}/rest/v1/subscribers?select=email",
                        headers=_sb_headers(), timeout=8)
            if r.status_code == 200:
                return [row["email"] for row in r.json()]
        except Exception:
            pass
    if SUBSCRIBERS_FILE.exists():
        return json.loads(SUBSCRIBERS_FILE.read_text(encoding="utf-8")).get("subscribers", [])
    return []


def sb_add_subscriber(email: str) -> str:
    """Returns 'subscribed', 'already_subscribed', or raises."""
    if _sb_available():
        import requests as req
        r = req.post(f"{SUPABASE_URL}/rest/v1/subscribers",
                     headers={**_sb_headers(), "Prefer": "return=minimal"},
                     json={"email": email}, timeout=8)
        if r.status_code in (200, 201):
            return "subscribed"
        if r.status_code == 409:
            return "already_subscribed"
        r.raise_for_status()
    # fallback to local file
    subs = load_subscribers()
    if email in subs:
        return "already_subscribed"
    subs.append(email)
    SUBSCRIBERS_FILE.write_text(json.dumps({"subscribers": subs}, indent=2), encoding="utf-8")
    return "subscribed"


def sb_remove_subscriber(email: str):
    if _sb_available():
        import requests as req
        req.delete(f"{SUPABASE_URL}/rest/v1/subscribers?email=eq.{email}",
                   headers=_sb_headers(), timeout=8)
    else:
        subs = [s for s in load_subscribers() if s != email]
        SUBSCRIBERS_FILE.write_text(json.dumps({"subscribers": subs}, indent=2), encoding="utf-8")


def save_subscribers(subs: list):
    SUBSCRIBERS_FILE.write_text(json.dumps({"subscribers": subs}, indent=2), encoding="utf-8")


READING_PLANS_FILE = Path(__file__).parent / "reading_plans.json"

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
CHAPTERS_PER_DAY_MAP = {5: 1, 10: 2, 15: 3, 30: 5}


def load_reading_plans() -> list:
    if READING_PLANS_FILE.exists():
        return json.loads(READING_PLANS_FILE.read_text(encoding="utf-8")).get("plans", [])
    return []


def save_reading_plans(plans: list):
    READING_PLANS_FILE.write_text(json.dumps({"plans": plans}, indent=2), encoding="utf-8")


load_dotenv()

app = Flask(__name__)
client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """You are a wise and compassionate biblical scholar with deep knowledge of both the
King James Version (KJV) and the World English Bible (WEB). You help people find comfort,
guidance, and truth in Scripture based on what they are feeling or going through.

When given a mood, emotion, or life situation, you:
1. Identify the emotional and spiritual core of what the person is experiencing
2. Select 4-5 of the most meaningful, directly relevant Bible passages
3. Return the exact verse text for the requested version
4. Offer a brief, warm, pastoral explanation of why each passage speaks to this moment

Be spiritually sensitive, theologically grounded, and deeply human in your response.
Never be preachy — speak as a trusted guide sharing ancient wisdom."""


STREAM_PROMPT = """The person is feeling or experiencing: "{mood}"

Bible version requested: {version}

Output ONLY newline-delimited JSON — one object per line, no other text, no markdown.

Line 1 — reflection:
{{"type":"reflection","text":"One warm sentence acknowledging what they are going through"}}

Lines 2-5 — one verse per line:
{{"type":"verse","reference":"Book Chapter:Verse","text":"Exact verse text in {version}","reflection":"1-2 sentences why this speaks to this moment"}}

Final line — books:
{{"type":"books","items":[{{"title":"...","author":"...","description":"...","amazon_search":"..."}}]}}

Return 4 verses and 3 books. Output each line immediately as you generate it."""


def stream_verses(mood: str, version: str):
    """Generator that yields SSE events, one per verse/reflection/books."""
    prompt = STREAM_PROMPT.format(mood=mood, version=version)
    buffer = ""
    with client.messages.stream(
        model="claude-haiku-4-5-20251001",
        max_tokens=1400,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for chunk in stream.text_stream:
            buffer += chunk
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    yield f"data: {json.dumps(obj)}\n\n"
                except json.JSONDecodeError:
                    pass
    # flush any remaining buffer
    if buffer.strip():
        try:
            obj = json.loads(buffer.strip())
            yield f"data: {json.dumps(obj)}\n\n"
        except json.JSONDecodeError:
            pass
    yield "data: {\"type\":\"done\"}\n\n"


@app.route("/")
def index():
    return render_template("index.html")


def get_sermons(theme: str) -> list[dict]:
    """Search SermonAudio for free conservative sermons on a topic."""
    try:
        import requests as req
        resp = req.get(
            "https://api.sermonaudio.com/v2/node/sermons",
            params={"query": theme, "pageSize": 3, "sortBy": "downloads"},
            timeout=8,
        )
        if resp.status_code != 200:
            return []
        items = resp.json().get("results", {}).get("nodes", [])
        sermons = []
        for s in items[:3]:
            sermons.append({
                "title":   s.get("fullTitle", ""),
                "speaker": s.get("speaker", {}).get("displayName", ""),
                "church":  s.get("broadcaster", {}).get("displayName", ""),
                "date":    s.get("preachDate", ""),
                "url":     f"https://www.sermonaudio.com/sermoninfo.asp?SID={s.get('sermonID','')}",
            })
        return sermons
    except Exception:
        return []


@app.route("/stream", methods=["POST"])
def stream():
    data = request.get_json()
    mood = (data.get("mood", "") or "").strip()
    version = data.get("version", "KJV")
    if not mood:
        return jsonify({"error": "Please describe what you are feeling."}), 400

    # Kick off SermonAudio in background thread
    from concurrent.futures import ThreadPoolExecutor
    executor = ThreadPoolExecutor(max_workers=1)
    sermons_fut = executor.submit(get_sermons, mood)

    @stream_with_context
    def generate():
        yield from stream_verses(mood, version)
        # Append sermons once Claude is done
        try:
            sermons = sermons_fut.result(timeout=10)
            if sermons:
                yield f"data: {json.dumps({'type':'sermons','items':sermons})}\n\n"
        except Exception:
            pass
        executor.shutdown(wait=False)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


@app.route("/search", methods=["POST"])
def search():
    """Fallback non-streaming endpoint."""
    data = request.get_json()
    mood = data.get("mood", "").strip()
    version = data.get("version", "KJV")
    if not mood:
        return jsonify({"error": "Please describe what you are feeling."}), 400
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as ex:
            sermons_fut = ex.submit(get_sermons, mood)
        # Re-assemble from stream
        verses, books, reflection = [], [], ""
        for line in "".join(
            c for c in stream_verses(mood, version)
            if not c.startswith("data: {\"type\":\"done\"}")
        ).split("data: "):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if obj["type"] == "reflection": reflection = obj["text"]
                elif obj["type"] == "verse":     verses.append(obj)
                elif obj["type"] == "books":     books = obj.get("items", [])
            except Exception:
                pass
        return jsonify({"mood_reflection": reflection, "verses": verses,
                        "books": books, "sermons": sermons_fut.result()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/subscribe", methods=["POST"])
def subscribe():
    data = request.get_json()
    email = (data.get("email", "") or "").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({"error": "Please enter a valid email address."}), 400
    try:
        status = sb_add_subscriber(email)
        return jsonify({"status": status})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/unsubscribe", methods=["GET"])
def unsubscribe():
    email = (request.args.get("email", "") or "").strip().lower()
    if email:
        sb_remove_subscriber(email)
    return "<html><body style='background:#ddeef8;font-family:Georgia,serif;color:#2a4a6a;text-align:center;padding:80px;'><h2>You have been unsubscribed.</h2><p style='color:#5a7a9a;margin-top:16px;'>You will no longer receive daily verses.</p></body></html>"


@app.route("/reading-plan", methods=["POST"])
def create_reading_plan():
    from datetime import date as dt
    data = request.get_json()
    email = (data.get("email", "") or "").strip().lower()
    minutes = int(data.get("minutes_per_day", 15))
    version = data.get("version", "KJV")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({"error": "Please enter a valid email address."}), 400
    if minutes not in CHAPTERS_PER_DAY_MAP:
        return jsonify({"error": "Invalid reading plan selected."}), 400
    plans = load_reading_plans()
    existing = next((p for p in plans if p["email"] == email), None)
    plan_data = {
        "email": email,
        "minutes_per_day": minutes,
        "chapters_per_day": CHAPTERS_PER_DAY_MAP[minutes],
        "version": version,
        "current_day": 1,
        "start_date": dt.today().isoformat(),
        "active": True,
    }
    if existing:
        existing.update(plan_data)
        save_reading_plans(plans)
        return jsonify({"status": "updated"})
    plans.append(plan_data)
    save_reading_plans(plans)
    return jsonify({"status": "created"})


@app.route("/reading-plan/unsubscribe", methods=["GET"])
def cancel_reading_plan():
    email = (request.args.get("email", "") or "").strip().lower()
    if email:
        plans = load_reading_plans()
        for p in plans:
            if p["email"] == email:
                p["active"] = False
        save_reading_plans(plans)
    return (
        "<html><body style='background:#ddeef8;font-family:Georgia,serif;"
        "color:#2a4a6a;text-align:center;padding:80px;'>"
        "<h2>Your Daily Scroll has been paused.</h2>"
        "<p style='color:#5a7a9a;margin-top:16px;'>Your place is saved. "
        "You may return and begin again at any time.</p></body></html>"
    )


if __name__ == "__main__":
    app.run(debug=True, port=5050)
