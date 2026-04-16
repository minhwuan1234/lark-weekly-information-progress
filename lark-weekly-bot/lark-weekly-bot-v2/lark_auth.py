"""
lark_auth.py
Xử lý app_access_token và user_access_token cho Lark API.
user_access_token hết hạn sau 2h → tự refresh bằng refresh_token.
refresh_token mới được ghi đè vào GitHub Secrets sau mỗi lần refresh.
"""

import os
import time
import base64
import requests

LARK_API = "https://open.larksuite.com/open-apis"

# ── Cache in-memory (tránh gọi thừa trong cùng 1 lần chạy) ──────────────────
_app_token_cache: dict = {"token": None, "expires_at": 0.0}
_user_token_cache: dict = {"token": None, "expires_at": 0.0}


# ── App Access Token (tenant-level) ─────────────────────────────────────────
def get_app_access_token() -> str:
    """
    Lấy app_access_token từ app_id + app_secret.
    Không cần refresh_token — gọi lại API là xong.
    Cache 2h, tự renew khi còn 60s.
    """
    now = time.time()
    if _app_token_cache["token"] and now < _app_token_cache["expires_at"] - 60:
        return _app_token_cache["token"]

    resp = requests.post(
        f"{LARK_API}/auth/v3/app_access_token/internal",
        json={
            "app_id": os.environ["LARK_APP_ID"],
            "app_secret": os.environ["LARK_APP_SECRET"],
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Lấy app_access_token thất bại: {data}")

    _app_token_cache["token"] = data["app_access_token"]
    _app_token_cache["expires_at"] = now + int(data.get("expire", 7200))
    print("[auth] ✅ app_access_token OK")
    return _app_token_cache["token"]


# ── User Access Token (user-level) ───────────────────────────────────────────
def get_user_access_token() -> str:
    """
    Lấy user_access_token bằng refresh_token.
    refresh_token hết hạn sau 30 ngày.
    Sau mỗi lần refresh → Lark trả refresh_token MỚI → lưu vào GitHub Secrets.
    """
    now = time.time()
    if _user_token_cache["token"] and now < _user_token_cache["expires_at"] - 60:
        return _user_token_cache["token"]

    refresh_token = os.environ["LARK_REFRESH_TOKEN"]

    resp = requests.post(
        f"{LARK_API}/authen/v1/refresh_access_token",
        headers={"Authorization": f"Bearer {get_app_access_token()}"},
        json={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=10,
    )
    resp.raise_for_status()
    body = resp.json()

    data = body.get("data", {})
    if not data.get("access_token"):
        raise RuntimeError(f"Refresh user_access_token thất bại: {body}")

    new_user_token = data["access_token"]
    new_refresh_token = data["refresh_token"]   # Lark invalidate token cũ ngay!
    expires_in = int(data.get("expires_in", 7200))

    _user_token_cache["token"] = new_user_token
    _user_token_cache["expires_at"] = now + expires_in

    print("[auth] ✅ user_access_token refreshed OK")
    _rotate_refresh_token_in_github(new_refresh_token)
    return new_user_token


# ── Rotate refresh_token vào GitHub Secrets ──────────────────────────────────
def _rotate_refresh_token_in_github(new_token: str) -> None:
    """
    Encrypt và ghi LARK_REFRESH_TOKEN mới vào GitHub Secrets.
    Cần: GH_PAT (Personal Access Token với scope repo) và GITHUB_REPOSITORY.
    GitHub Actions tự inject GITHUB_REPOSITORY; GH_PAT cần thêm thủ công vào Secrets.
    """
    try:
        from nacl import encoding, public as nacl_public
    except ImportError:
        print("[auth] ⚠️  PyNaCl chưa cài — bỏ qua rotate refresh_token")
        return

    gh_pat = os.environ.get("GH_PAT")
    repo = os.environ.get("GITHUB_REPOSITORY")  # vd: "minhanhs/lark-weekly-bot"

    if not gh_pat or not repo:
        print("[auth] ⚠️  Thiếu GH_PAT hoặc GITHUB_REPOSITORY — bỏ qua rotate")
        return

    headers = {
        "Authorization": f"Bearer {gh_pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Bước 1: lấy public key của repo để encrypt
    key_resp = requests.get(
        f"https://api.github.com/repos/{repo}/actions/secrets/public-key",
        headers=headers,
        timeout=10,
    )
    key_resp.raise_for_status()
    key_data = key_resp.json()

    # Bước 2: encrypt giá trị mới bằng NaCl SealedBox
    pub_key = nacl_public.PublicKey(key_data["key"].encode(), encoding.Base64Encoder)
    sealed = nacl_public.SealedBox(pub_key).encrypt(new_token.encode())
    encrypted = base64.b64encode(sealed).decode()

    # Bước 3: PUT secret mới
    put_resp = requests.put(
        f"https://api.github.com/repos/{repo}/actions/secrets/LARK_REFRESH_TOKEN",
        headers=headers,
        json={"encrypted_value": encrypted, "key_id": key_data["key_id"]},
        timeout=10,
    )
    put_resp.raise_for_status()
    print("[auth] ✅ LARK_REFRESH_TOKEN đã được rotate trong GitHub Secrets")
