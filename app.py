"""
Scripture by Mood — Flask app powered by Claude.
Returns relevant Bible passages based on the user's emotional state or life issue.
"""

import os
import json
import re
import hashlib
from datetime import date
from pathlib import Path
from flask import Flask, render_template, request, jsonify, Response, stream_with_context, send_file
from anthropic import Anthropic
from dotenv import load_dotenv

# Simple in-memory cache for verse lookups (mood → streamed result)
_cache = {}   # {hash: full_response_text}
CACHE_MAX = 200  # keep last 200 unique searches

PRAYERS_FILE       = Path(__file__).parent / "prayers.json"
VOTD_FILE          = Path(__file__).parent / ".tmp" / "verse_of_day.json"

import psycopg2, psycopg2.extras
DATABASE_URL = os.getenv("DATABASE_URL", "")

def _db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def _init_subscribers():
    if not DATABASE_URL:
        return
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS subscribers (
                id         SERIAL PRIMARY KEY,
                email      TEXT UNIQUE NOT NULL,
                phone      TEXT,
                source     TEXT DEFAULT 'bible',
                created_at TIMESTAMPTZ DEFAULT NOW()
            )""")
        conn.commit()

_init_subscribers()

def load_subscribers() -> list:
    if not DATABASE_URL:
        return []
    with _db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT email, phone FROM subscribers ORDER BY created_at DESC")
            return [dict(r) for r in cur.fetchall()]


def sb_add_subscriber(email: str, phone: str = "") -> str:
    if not DATABASE_URL:
        return "error"
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM subscribers WHERE email=%s", (email,))
            if cur.fetchone():
                return "already_subscribed"
            cur.execute("INSERT INTO subscribers (email, phone, source) VALUES (%s, %s, 'bible')",
                        (email, phone or None))
        conn.commit()
    return "subscribed"


def sb_remove_subscriber(email: str):
    if not DATABASE_URL:
        return
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM subscribers WHERE email=%s", (email,))
        conn.commit()


def save_subscribers(subs: list):
    pass  # no-op — Neon is source of truth


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
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 31536000  # 1 year cache for static files
client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# Gzip all responses >= 1KB
try:
    from flask_compress import Compress
    Compress(app)
except ImportError:
    pass

@app.after_request
def add_cache_headers(response):
    # Cache static assets for 1 year
    if request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    # Never cache SSE or API responses
    elif request.path.startswith("/stream") or request.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store"
    return response

SYSTEM_PROMPT = """You are a wise and compassionate biblical scholar with deep knowledge of the
King James Version (KJV), New International Version (NIV), English Standard Version (ESV),
New Living Translation (NLT), New King James Version (NKJV), and World English Bible (WEB).
You help people find comfort, guidance, and truth in Scripture based on what they are feeling or going through.

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


@app.route("/read")
def read_bible():
    return render_template("bible.html")


@app.route("/sw.js")
def service_worker():
    resp = app.send_static_file("sw.js")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


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

    cache_key = hashlib.md5(f"{mood.lower()[:120]}|{version}".encode()).hexdigest()

    # Serve cached result if available (replay SSE events)
    if cache_key in _cache:
        cached_lines = _cache[cache_key]
        def replay():
            for line in cached_lines:
                yield line
            yield "data: {\"type\":\"done\"}\n\n"
        return Response(replay(), mimetype="text/event-stream",
                        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})

    # Kick off SermonAudio in background thread
    from concurrent.futures import ThreadPoolExecutor
    executor = ThreadPoolExecutor(max_workers=1)
    sermons_fut = executor.submit(get_sermons, mood)

    @stream_with_context
    def generate():
        collected = []
        for chunk in stream_verses(mood, version):
            collected.append(chunk)
            yield chunk
        try:
            sermons = sermons_fut.result(timeout=10)
            if sermons:
                s_chunk = f"data: {json.dumps({'type':'sermons','items':sermons})}\n\n"
                collected.append(s_chunk)
                yield s_chunk
        except Exception:
            pass
        executor.shutdown(wait=False)
        # Store in cache (evict oldest if full)
        if len(_cache) >= CACHE_MAX:
            _cache.pop(next(iter(_cache)))
        _cache[cache_key] = collected

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
    phone = (data.get("phone", "") or "").strip()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({"error": "Please enter a valid email address."}), 400
    # normalize phone: digits only, prepend +1 if 10 digits
    phone_digits = re.sub(r"\D", "", phone)
    if phone_digits and len(phone_digits) == 10:
        phone = "+1" + phone_digits
    elif phone_digits and len(phone_digits) == 11 and phone_digits.startswith("1"):
        phone = "+" + phone_digits
    else:
        phone = ""
    try:
        status = sb_add_subscriber(email, phone)
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


# ── PRAYER WALL ───────────────────────────────────────────────────────────────

def load_prayers():
    if PRAYERS_FILE.exists():
        return json.loads(PRAYERS_FILE.read_text(encoding="utf-8")).get("prayers", [])
    return []

def save_prayers(prayers):
    PRAYERS_FILE.write_text(json.dumps({"prayers": prayers}, indent=2), encoding="utf-8")


@app.route("/prayers", methods=["GET"])
def get_prayers():
    return jsonify({"prayers": load_prayers()})


@app.route("/prayers", methods=["POST"])
def add_prayer():
    import uuid
    data = request.get_json()
    name    = (data.get("name")    or "Anonymous").strip()[:40]
    request_ = (data.get("request") or "").strip()[:280]
    if not request_:
        return jsonify({"error": "Prayer request is required"}), 400
    prayers = load_prayers()
    prayer = {
        "id":      str(uuid.uuid4())[:8],
        "name":    name,
        "request": request_,
        "count":   0,
        "date":    date.today().isoformat(),
    }
    prayers.insert(0, prayer)
    save_prayers(prayers[:100])   # keep last 100
    return jsonify({"status": "added", "prayer": prayer})


@app.route("/prayers/<pid>/pray", methods=["POST"])
def pray_for(pid):
    prayers = load_prayers()
    for p in prayers:
        if p["id"] == pid:
            p["count"] = p.get("count", 0) + 1
            save_prayers(prayers)
            return jsonify({"count": p["count"]})
    return jsonify({"error": "Not found"}), 404


# ── VERSE OF THE DAY ──────────────────────────────────────────────────────────

@app.route("/verse-of-day")
def verse_of_day():
    from datetime import date as dt
    today = dt.today().isoformat()
    VOTD_FILE.parent.mkdir(exist_ok=True)

    # Serve cached version if it's from today
    if VOTD_FILE.exists():
        cached = json.loads(VOTD_FILE.read_text(encoding="utf-8"))
        if cached.get("date") == today:
            return jsonify(cached)

    # Generate fresh verse of the day
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content":
                f"Today is {today}. Return a single uplifting Bible verse for the day. "
                "JSON only: {\"reference\":\"Book Ch:V\",\"text\":\"KJV text\",\"theme\":\"one word\"}"
            }],
        )
        raw = resp.content[0].text.strip()
        if "```" in raw:
            raw = re.sub(r"```(?:json)?", "", raw).strip().strip("```").strip()
        data = json.loads(raw)
        data["date"] = today
        VOTD_FILE.write_text(json.dumps(data), encoding="utf-8")
        return jsonify(data)
    except Exception as e:
        return jsonify({"reference": "Psalm 23:1", "text": "The Lord is my shepherd; I shall not want.", "theme": "Peace", "date": today})


@app.route("/verse-card", methods=["POST"])
def verse_card():
    """Render a beautiful shareable verse card as a PNG (1080x1080)."""
    import io, base64, tempfile
    data        = request.get_json()
    verse_text  = (data.get("text")      or "").strip()
    reference   = (data.get("reference") or "").strip()
    reflection  = (data.get("reflection") or "").strip()
    version     = (data.get("version")   or "KJV").strip()

    if not verse_text or not reference:
        return jsonify({"error": "verse text and reference required"}), 400

    # Build the card HTML
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=EB+Garamond:ital,wght@0,400;0,600;1,400&family=Cinzel:wght@400;600;700&display=swap" rel="stylesheet">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    width:1080px; height:1080px; overflow:hidden;
    background: linear-gradient(160deg, #0d2a4a 0%, #0a1f38 35%, #071528 70%, #040e1c 100%);
    display:flex; align-items:center; justify-content:center;
    font-family:'EB Garamond', Georgia, serif;
    position:relative;
  }}
  .stars {{
    position:absolute; inset:0;
    background-image:
      radial-gradient(1px 1px at 15% 20%, rgba(255,255,255,0.5) 0%, transparent 100%),
      radial-gradient(1px 1px at 42% 8%,  rgba(255,255,255,0.35) 0%, transparent 100%),
      radial-gradient(1px 1px at 70% 15%, rgba(255,255,255,0.4) 0%, transparent 100%),
      radial-gradient(1px 1px at 85% 30%, rgba(255,255,255,0.3) 0%, transparent 100%),
      radial-gradient(1px 1px at 28% 45%, rgba(255,255,255,0.25) 0%, transparent 100%),
      radial-gradient(1px 1px at 55% 35%, rgba(255,255,255,0.4) 0%, transparent 100%),
      radial-gradient(1px 1px at 92% 55%, rgba(255,255,255,0.3) 0%, transparent 100%),
      radial-gradient(1px 1px at 10% 70%, rgba(255,255,255,0.2) 0%, transparent 100%),
      radial-gradient(1px 1px at 78% 72%, rgba(255,255,255,0.35) 0%, transparent 100%),
      radial-gradient(1px 1px at 35% 88%, rgba(255,255,255,0.2) 0%, transparent 100%);
  }}
  .glow {{
    position:absolute; top:50%; left:50%;
    transform:translate(-50%, -50%);
    width:700px; height:700px; border-radius:50%;
    background: radial-gradient(ellipse, rgba(122,171,204,0.08) 0%, transparent 70%);
  }}
  .card {{
    position:relative; z-index:2;
    width:860px;
    text-align:center;
    padding:0 40px;
  }}
  .ornament {{
    font-size:22px; color:#7AABCC; letter-spacing:14px;
    margin-bottom:36px; opacity:0.8;
  }}
  .open-quote {{
    font-size:130px; color:rgba(122,171,204,0.15);
    font-family:Georgia,serif; line-height:0.6;
    margin-bottom:16px; display:block;
  }}
  .verse {{
    font-size:clamp(28px,3.2vw,44px);
    line-height:1.65;
    color:#e8f4fc;
    font-style:italic;
    font-weight:400;
    letter-spacing:0.3px;
    margin-bottom:32px;
    padding:0 20px;
  }}
  .reference {{
    font-family:'Cinzel',serif;
    font-size:18px; font-weight:600;
    color:#7AABCC; letter-spacing:4px;
    text-transform:uppercase;
    margin-bottom:28px;
  }}
  .divider {{
    width:80px; height:1px;
    background:linear-gradient(90deg,transparent,rgba(122,171,204,0.5),transparent);
    margin:0 auto 28px;
  }}
  .reflection {{
    font-size:17px; color:rgba(200,220,240,0.6);
    line-height:1.7; font-style:italic;
    max-width:680px; margin:0 auto 40px;
  }}
  .brand {{
    font-family:'Cinzel',serif;
    font-size:13px; font-weight:600;
    color:rgba(122,171,204,0.45);
    letter-spacing:5px; text-transform:uppercase;
  }}
  .version-tag {{
    display:inline-block;
    background:rgba(122,171,204,0.12);
    border:1px solid rgba(122,171,204,0.2);
    border-radius:20px;
    padding:4px 16px;
    font-family:'Cinzel',serif;
    font-size:10px; letter-spacing:3px;
    color:rgba(122,171,204,0.5);
    margin-bottom:12px;
  }}
</style>
</head>
<body>
  <div class="stars"></div>
  <div class="glow"></div>
  <div class="card">
    <div class="ornament">✾ ❀ ✾</div>
    <span class="open-quote">"</span>
    <div class="verse">{verse_text}</div>
    <div class="reference">— {reference}</div>
    <div class="divider"></div>
    {'<div class="reflection">' + reflection + '</div>' if reflection else ''}
    <div class="version-tag">{version}</div>
    <div class="brand">Still Waters</div>
  </div>
</body>
</html>"""

    try:
        from playwright.sync_api import sync_playwright
        tmp = Path(tempfile.mkdtemp()) / "verse_card.png"
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1080, "height": 1080})
            page.set_content(html, wait_until="networkidle")
            page.wait_for_timeout(1200)   # let fonts load
            page.screenshot(path=str(tmp), full_page=False)
            browser.close()
        safe_ref = re.sub(r"[^\w\s-]", "", reference).strip().replace(" ", "_")
        filename = f"StillWaters_{safe_ref}.png"
        return send_file(str(tmp), mimetype="image/png",
                         as_attachment=True, download_name=filename)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/sitemap.xml")
def sitemap():
    from flask import Response
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://still-waters-scripture.onrender.com/</loc>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>"""
    return Response(xml, mimetype="application/xml")


@app.route("/robots.txt")
def robots():
    from flask import Response
    txt = """User-agent: *
Allow: /
Sitemap: https://still-waters-scripture.onrender.com/sitemap.xml"""
    return Response(txt, mimetype="text/plain")


if __name__ == "__main__":
    app.run(debug=True, port=5050)
