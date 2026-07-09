"""
Manage Still Waters subscribers from the command line.

Usage:
  python tools/subscribers.py list
  python tools/subscribers.py add someone@email.com
  python tools/subscribers.py remove someone@email.com
"""

import os, sys, requests
from dotenv import load_dotenv

load_dotenv()
URL = os.getenv("SUPABASE_URL", "").rstrip("/")
KEY = os.getenv("SUPABASE_KEY", "")
HEADERS = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}


def list_subs():
    r = requests.get(f"{URL}/rest/v1/subscribers?select=email,created_at&order=created_at.asc", headers=HEADERS, timeout=8)
    rows = r.json()
    if not rows:
        print("No subscribers yet.")
        return
    print(f"\n{len(rows)} subscriber(s):")
    for row in rows:
        print(f"  {row['email']}  (joined {row.get('created_at','')[:10]})")
    print()


def add_sub(email):
    r = requests.post(f"{URL}/rest/v1/subscribers", headers={**HEADERS, "Prefer": "return=minimal"},
                      json={"email": email.strip().lower()}, timeout=8)
    if r.status_code in (200, 201):
        print(f"Added: {email}")
    elif r.status_code == 409:
        print(f"Already subscribed: {email}")
    else:
        print(f"Error {r.status_code}: {r.text}")


def remove_sub(email):
    r = requests.delete(f"{URL}/rest/v1/subscribers?email=eq.{email.strip().lower()}",
                        headers=HEADERS, timeout=8)
    if r.status_code in (200, 204):
        print(f"Removed: {email}")
    else:
        print(f"Error {r.status_code}: {r.text}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1].lower()
    if cmd == "list":
        list_subs()
    elif cmd == "add" and len(sys.argv) == 3:
        add_sub(sys.argv[2])
    elif cmd == "remove" and len(sys.argv) == 3:
        remove_sub(sys.argv[2])
    else:
        print(__doc__)
