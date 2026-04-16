"""
get_refresh_token.py
Chạy script này MỘT LẦN DUY NHẤT trên máy local để lấy refresh_token.
Sau đó lưu refresh_token vào GitHub Secrets → bot tự chạy không cần can thiệp thêm.

Yêu cầu:
  - pip install flask requests
  - Đã thêm redirect URL vào Lark App Security Settings:
    http://localhost:9999/callback

Cách chạy:
  export LARK_APP_ID=cli_xxxxxxxxxxxx
  export LARK_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  python get_refresh_token.py
  → Mở trình duyệt theo link hiện ra → authorize → copy token vào GitHub Secrets
"""

import os
import threading
import webbrowser
import requests
from urllib.parse import urlencode, parse_qs, urlparse

APP_ID     = os.environ["LARK_APP_ID"]
APP_SECRET = os.environ["LARK_APP_SECRET"]
REDIRECT   = "http://localhost:9999/callback"
LARK_API   = "https://open.larksuite.com/open-apis"

# Scopes cần authorize
SCOPES = " ".join([
    "offline_access",
    "sheets:spreadsheet:readonly",
    "task:task:read",
    "task:tasklist:read",
])


def get_app_access_token() -> str:
    resp = requests.post(
        f"{LARK_API}/auth/v3/app_access_token/internal",
        json={"app_id": APP_ID, "app_secret": APP_SECRET},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["app_access_token"]


def exchange_code_for_tokens(code: str, app_token: str) -> dict:
    resp = requests.post(
        f"{LARK_API}/authen/v1/access_token",
        headers={"Authorization": f"Bearer {app_token}"},
        json={
            "grant_type":   "authorization_code",
            "code":         code,
            "redirect_uri": REDIRECT,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("data", {})


def main():
    from flask import Flask, request as flask_request

    app     = Flask(__name__)
    result  = {}
    done    = threading.Event()

    @app.route("/callback")
    def callback():
        code = flask_request.args.get("code")
        if not code:
            return "❌ Không có code trong callback URL", 400

        print(f"\n[oauth] Nhận code: {code[:12]}...")
        app_token = get_app_access_token()
        tokens    = exchange_code_for_tokens(code, app_token)

        result["access_token"]  = tokens.get("access_token")
        result["refresh_token"] = tokens.get("refresh_token")
        result["expires_in"]    = tokens.get("expires_in")

        done.set()
        return """
        <h2>✅ Authorized thành công!</h2>
        <p>Quay lại terminal để lấy token.</p>
        <script>window.close()</script>
        """

    # Build OAuth URL
    params = {
        "app_id":        APP_ID,
        "redirect_uri":  REDIRECT,
        "response_type": "code",
        "scope":         SCOPES,
        "state":         "lark_bot_setup",
    }
    auth_url = f"https://open.larksuite.com/open-apis/authen/v1/authorize?{urlencode(params)}"

    print("=" * 60)
    print("Lark OAuth — Lấy refresh_token lần đầu")
    print("=" * 60)
    print(f"\nMở URL này trong trình duyệt:\n\n{auth_url}\n")
    print("(Đang tự động mở trình duyệt...)")

    threading.Timer(1.5, lambda: webbrowser.open(auth_url)).start()

    # Chạy Flask server tạm
    import logging
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)
    threading.Thread(
        target=lambda: app.run(port=9999, debug=False),
        daemon=True,
    ).start()

    done.wait(timeout=120)

    if not result.get("refresh_token"):
        print("❌ Timeout hoặc lỗi — chưa lấy được token")
        return

    print("\n" + "=" * 60)
    print("✅ Lấy token thành công!\n")
    print(f"access_token  (2h): {result['access_token'][:40]}...")
    print(f"refresh_token (30d): {result['refresh_token'][:40]}...")
    print("\n" + "=" * 60)
    print("📌 Lưu các giá trị sau vào GitHub Secrets:")
    print(f"\n  LARK_REFRESH_TOKEN  = {result['refresh_token']}")
    print("\n(access_token không cần lưu — bot tự refresh từ refresh_token)")
    print("=" * 60)


if __name__ == "__main__":
    main()
