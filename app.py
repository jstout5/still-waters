"""
Scripture by Mood — Flask app powered by Claude.
Returns relevant Bible passages based on the user's emotional state or life issue.
"""

import os
import json
import re
from pathlib import Path
from flask import Flask, render_template, request, jsonify
from anthropic import Anthropic
from dotenv import load_dotenv

SUBSCRIBERS_FILE = Path(__file__).parent / "subscribers.json"

def load_subscribers() -> list:
    if SUBSCRIBERS_FILE.exists():
        return json.loads(SUBSCRIBERS_FILE.read_text(encoding="utf-8")).get("subscribers", [])
    return []

def save_subscribers(subs: list):
    SUBSCRIBERS_FILE.write_text(json.dumps({"subscribers": subs}, indent=2), encoding="utf-8")

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


def get_verses(mood: str, version: str) -> dict:
    prompt = f"""The person is feeling or experiencing: "{mood}"

Bible version requested: {version}

Return a JSON object with this structure:
{{
  "mood_reflection": "One warm sentence acknowledging what they are going through",
  "verses": [
    {{
      "reference": "Book Chapter:Verse",
      "text": "The exact verse text in {version}",
      "reflection": "1-2 sentences on why this verse speaks to this moment"
    }}
  ],
  "books": [
    {{
      "title": "Book Title",
      "author": "Author Name",
      "description": "One sentence on why this book helps with this topic",
      "amazon_search": "search terms to find it on Amazon"
    }}
  ]
}}

Return 4-5 verses and 3 real, well-known Christian books directly relevant to this mood or topic.
Books should be widely available, respected Christian titles (e.g. C.S. Lewis, Max Lucado, Timothy Keller, etc.).
Return only the JSON, no other text."""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    if "```" in raw:
        for part in raw.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                raw = part
                break

    return json.loads(raw)


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


@app.route("/search", methods=["POST"])
def search():
    data = request.get_json()
    mood = data.get("mood", "").strip()
    version = data.get("version", "KJV")

    if not mood:
        return jsonify({"error": "Please describe what you are feeling."}), 400

    try:
        result = get_verses(mood, version)
        theme = result.get("verses", [{}])[0].get("reference", mood)
        result["sermons"] = get_sermons(mood)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/subscribe", methods=["POST"])
def subscribe():
    data = request.get_json()
    email = (data.get("email", "") or "").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({"error": "Please enter a valid email address."}), 400
    subs = load_subscribers()
    if email in subs:
        return jsonify({"status": "already_subscribed"})
    subs.append(email)
    save_subscribers(subs)
    return jsonify({"status": "subscribed"})


@app.route("/unsubscribe", methods=["GET"])
def unsubscribe():
    email = (request.args.get("email", "") or "").strip().lower()
    if email:
        subs = load_subscribers()
        subs = [s for s in subs if s != email]
        save_subscribers(subs)
    return "<html><body style='background:#1a1208;font-family:Georgia,serif;color:#c9a84c;text-align:center;padding:80px;'><h2>✦ You have been unsubscribed.</h2><p style='color:#7a6040;margin-top:16px;'>You will no longer receive daily devotionals.</p></body></html>"


if __name__ == "__main__":
    app.run(debug=True, port=5050)
