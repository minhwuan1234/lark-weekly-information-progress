"""
bot.py
Entry point của Lark Weekly Bot.
Chạy mỗi chiều thứ 6 qua GitHub Actions.
Flow:
  1. Lấy tất cả task list của user
  2. Tìm tasks có start_date hoặc due_date trong tuần này
  3. Xác định status: done / overdue / in_progress / todo
  4. Lookup open_id từ email (dùng contact/v3/users/batch_get_id)
  5. Build Lark Card và gửi bằng open_id

Ghi chú gửi tin:
  - Dùng receive_id_type="open_id" → cần lookup open_id từ email trước
  - Fallback: nếu có TARGET_USER_ID (open_id) thì dùng thẳng, bỏ qua bước lookup
  - Yêu cầu scope Lark App: contact:user.id:readonly
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


def get_open_id_by_email(email: str) -> str:
    """
    Dùng /contact/v3/users/batch_get_id để lấy open_id từ email.
    Yêu cầu scope Lark App: contact:user.id:readonly
    """
    resp = requests.post(
        f"{LARK_API}/contact/v3/users/batch_get_id",
        headers={
            "Authorization": f"Bearer {get_app_access_token()}",
            "Content-Type": "application/json",
        },
        params={"user_id_type": "open_id"},
        json={"emails": [email]},
        timeout=15,
    )
    resp.raise_for_status()
    body = resp.json()

    if body.get("code") != 0:
        raise RuntimeError(f"[bot] Lookup open_id thất bại: {body}")

    user_list = body.get("data", {}).get("user_list", [])
    if not user_list or not user_list[0].get("user_id"):
        raise ValueError(f"[bot] Không tìm thấy open_id cho email: {email}")

    open_id = user_list[0]["user_id"]
    print(f"[bot] 🔍 Resolved {email} → open_id={open_id}")
    return open_id


def send_message(receive_id: str, receive_id_type: str, card: dict) -> None:
    """
    Gửi Lark Interactive Card.
    receive_id_type: "open_id" | "user_id" | "union_id" | "chat_id"
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

    # Bước 3: Xác định open_id để gửi tin
    target_email   = os.environ.get("TARGET_EMAIL", "").strip()
    target_user_id = os.environ.get("TARGET_USER_ID", "").strip()

    if target_user_id:
        # Ưu tiên dùng open_id có sẵn — không cần lookup
        open_id = target_user_id
        print(f"[bot] Dùng TARGET_USER_ID trực tiếp: {open_id}")
    elif target_email:
        # Lookup open_id từ email — cần scope: contact:user.id:readonly
        open_id = get_open_id_by_email(target_email)
    else:
        raise ValueError("Cần set TARGET_EMAIL hoặc TARGET_USER_ID trong GitHub Secrets")

    # Bước 4: Gửi tin
    send_message(open_id, "open_id", card)

    print("[bot] ✅ Hoàn thành!")
    print("=" * 60)


if __name__ == "__main__":
    main()
