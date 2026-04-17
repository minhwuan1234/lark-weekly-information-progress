"""
bot.py
Entry point của Lark Weekly Bot.
Chạy mỗi chiều thứ 6 qua GitHub Actions.
Flow:
  1. Lấy tất cả task list của user
  2. Tìm tasks có start_date hoặc due_date trong tuần này
  3. Xác định status: done / overdue / in_progress / todo
  4. Lookup open_id từ nhiều email (dùng app_access_token)
  5. Build Lark Card và gửi cho tất cả recipients

Ghi chú token:
  - Toàn bộ dùng app_access_token (tenant-level)
  - Scope cần có: contact:user.id:readonly, im:message:send_as_bot
  - TARGET_EMAIL: nhiều email cách nhau dấu phẩy (vd: a@x.com,b@x.com)
  - TARGET_USER_ID: nhiều open_id cách nhau dấu phẩy
"""

import os
import json
import requests
from datetime import date, timedelta
from lark_auth import get_app_access_token
from task_checker import get_tasks_for_week
from message_builder import build_weekly_report_card

LARK_API = "https://open.larksuite.com/open-apis"


def get_week_range() -> tuple[date, date]:
    """Thứ 2 → Thứ 6 của tuần hiện tại."""
    today  = date.today()
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=4)
    return monday, friday


def get_open_ids_by_emails(emails: list[str]) -> dict[str, str]:
    """
    Dùng /contact/v3/users/batch_get_id để lấy open_id từ danh sách email.
    Gửi batch 1 lần thay vì gọi API từng email.
    Yêu cầu scope: contact:user.id:readonly
    Trả về: {email: open_id} cho các email tìm thấy.
    """
    resp = requests.post(
        f"{LARK_API}/contact/v3/users/batch_get_id",
        headers={
            "Authorization": f"Bearer {get_app_access_token()}",
            "Content-Type": "application/json",
        },
        params={"user_id_type": "open_id"},
        json={"emails": emails},
        timeout=15,
    )
    print(f"[bot] Lookup status: {resp.status_code}")
    print(f"[bot] Lookup body: {resp.text}")
    resp.raise_for_status()
    body = resp.json()

    if body.get("code") != 0:
        raise RuntimeError(f"[bot] Lookup open_id thất bại: {body}")

    result: dict[str, str] = {}
    user_list = body.get("data", {}).get("user_list", [])
    for user in user_list:
        email   = user.get("email", "")
        user_id = user.get("user_id", "")
        if email and user_id:
            result[email] = user_id
            print(f"[bot] 🔍 Resolved {email} → open_id={user_id}")
        else:
            print(f"[bot] ⚠️  Không tìm thấy open_id cho email: {email or '(unknown)'}")

    return result


def send_message(receive_id: str, receive_id_type: str, card: dict) -> None:
    """
    Gửi Lark Interactive Card dưới danh nghĩa bot.
    Dùng app_access_token — yêu cầu scope: im:message:send_as_bot
    """
    resp = requests.post(
        f"{LARK_API}/im/v1/messages",
        headers={
            "Authorization": f"Bearer {get_app_access_token()}",
            "Content-Type": "application/json",
        },
        params={"receive_id_type": receive_id_type},
        json={
            "receive_id": receive_id,
            "msg_type":   "interactive",
            "content":    json.dumps(card),
        },
        timeout=15,
    )
    print(f"[bot] Send status: {resp.status_code}")
    print(f"[bot] Send body: {resp.text}")
    resp.raise_for_status()
    body = resp.json()

    if body.get("code") != 0:
        raise RuntimeError(f"Gửi tin nhắn thất bại: {body}")

    print(f"[bot] ✅ Đã gửi báo cáo tới {receive_id_type}={receive_id}")


def main():
    print("=" * 60)
    print("[bot] Lark Weekly Bot bắt đầu chạy...")

    week_start, week_end = get_week_range()
    print(f"[bot] Tuần: {week_start} → {week_end}")

    # Bước 1: Lấy tasks từ Lark Task API
    tasks = get_tasks_for_week(week_start, week_end)
    if not tasks:
        print("[bot] ⚠️  Không có task nào trong tuần — bỏ qua gửi tin")
        return

    # Bước 2: Build Lark Card
    card = build_weekly_report_card(tasks, week_start, week_end)

    # Bước 3: Xác định danh sách open_id để gửi tin
    target_emails   = [e.strip() for e in os.environ.get("TARGET_EMAIL", "").split(",") if e.strip()]
    target_user_ids = [u.strip() for u in os.environ.get("TARGET_USER_ID", "").split(",") if u.strip()]

    open_ids: list[str] = []

    # Ưu tiên TARGET_USER_ID nếu có
    if target_user_ids:
        open_ids.extend(target_user_ids)
        print(f"[bot] Dùng TARGET_USER_ID: {target_user_ids}")

    # Lookup open_id từ email (batch 1 lần)
    if target_emails:
        print(f"[bot] Lookup {len(target_emails)} email(s)...")
        resolved = get_open_ids_by_emails(target_emails)
        open_ids.extend(resolved.values())

        # Cảnh báo email nào không tìm thấy
        missing = [e for e in target_emails if e not in resolved]
        for e in missing:
            print(f"[bot] ⚠️  Bỏ qua — không tìm thấy open_id cho: {e}")

    if not open_ids:
        raise ValueError("Không có recipient hợp lệ — kiểm tra lại TARGET_EMAIL hoặc TARGET_USER_ID")

    # Bước 4: Gửi tin cho tất cả recipients
    print(f"[bot] Gửi tin cho {len(open_ids)} người...")
    for open_id in open_ids:
        send_message(open_id, "open_id", card)

    print("[bot] ✅ Hoàn thành!")
    print("=" * 60)


if __name__ == "__main__":
    main()
