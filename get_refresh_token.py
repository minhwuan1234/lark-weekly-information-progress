"""
get_refresh_token.py
Chạy script này khi cần lấy hoặc RE-AUTHORIZE refresh_token mới.

Dùng khi:
  - Lần đầu setup
  - Vừa thêm scope mới vào Lark App → cần user authorize lại
  - refresh_token hết hạn 30 ngày (hiếm, vì bot tự rotate)

Yêu cầu:
  - pip install flask requests
  - Trong Lark Developer Console, thêm đủ các scope bên dưới
  - Đã thêm redirect URL: http://localhost:9999/callback
    (Security Settings → Redirect URLs)

Cách chạy:
  export LARK_APP_ID=cli_xxxxxxxxxxxx
  export LARK_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  python get_refresh_token.py
  → Trình duyệt tự mở → đăng nhập → authorize → copy token vào GitHub Secrets
"""

import os
import threading
import webbrowser
import requests
from urllib.parse import urlencode

APP_ID     = os.environ["LARK_APP_ID"]
APP_SECRET = os.environ["LARK_APP_SECRET"]
REDIRECT   = "http://localhost:9999/callback"
LARK_API   = "https://open.larksuite.com/open-apis"

# ── Scopes cần authorize ──────────────────────────────────────────────────────
# Sau khi thêm scope mới vào Lark App, chạy lại script này để re-authorize.
SCOPES = " ".join([
    "offline_access",           # bắt buộc để nhận refresh_token
    # Task API
    "task:task:read",
    "task:tasklist:read",
    # Contact API — để hiển thị tên user trong báo cáo
    "contact:contact.base:readonly",
    "contact:user.base:readonly",
])


def get_app_access_token() -> str:
    resp = requests.post(
        f"{LARK_API}/auth/v3/app_access_token/internal",
        json={"app_id": APP_ID, "app_secret": APP_SECRET},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0 or "app_access_token" not in data:
        raise RuntimeError(f"❌ Lấy app_access_token thất bại: {data.get('msg', data)}")
    return data["app_access_token"]


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

    app    = Flask(__name__)
    result = {}
    done   = threading.Event()

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

        done.set()
        return """
        <h2>✅ Authorized thành công!</h2>
        <p>Quay lại terminal để copy token.</p>
        <script>setTimeout(() => window.close(), 2000)</script>
        """

    # Build OAuth URL
    params = {
        "app_id":        APP_ID,
        "redirect_uri":  REDIRECT,
        "response_type": "code",
        "scope":         SCOPES,
        "state":         "lark_bot_reauth",
    }
    auth_url = f"{LARK_API}/authen/v1/authorize?{urlencode(params)}"

    print("=" * 60)
    print("Lark OAuth — Re-authorization")
    print("=" * 60)
    print("\nScopes sẽ được authorize:")
    for s in SCOPES.split():
        print(f"  • {s}")
    print(f"\nURL:\n{auth_url}\n")
    print("(Đang tự động mở trình duyệt...)")

    threading.Timer(1.5, lambda: webbrowser.open(auth_url)).start()

    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    threading.Thread(
        target=lambda: app.run(port=9999, debug=False),
        daemon=True,
    ).start()

    done.wait(timeout=120)

    if not result.get("refresh_token"):
        print("❌ Timeout hoặc lỗi — chưa lấy được token")
        return

    print("\n" + "=" * 60)
    print("✅ Re-authorization thành công!\n")
    print(f"LARK_REFRESH_TOKEN = {result['refresh_token']}")
    print("\n" + "=" * 60)
    print("📌 Cập nhật vào GitHub Secrets:")
    print("   Settings → Secrets → Actions → LARK_REFRESH_TOKEN → Update")
    print("=" * 60)


if __name__ == "__main__":
    main()
