"""
bot.py
Entry point của Lark Weekly Bot.
Chạy mỗi chiều thứ 6 qua GitHub Actions.

Flow mới (không còn dùng sheet):
  1. Lấy tất cả task list của user
  2. Tìm tasks có start_date hoặc due_date trong tuần này
  3. Xác định status: done / overdue / in_progress / todo
  4. Build Lark Card và gửi
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


def get_user_id_by_email(email: str) -> str:
    resp = requests.post(
        f"{LARK_API}/contact/v3/users/batch_get_id",
        headers={
            "Authorization": f"Bearer {get_app_access_token()}",
            "Content-Type": "application/json",
        },
        params={"user_id_type": "open_id"},
        json={"emails": [email]},
        timeout=10,
    )
    resp.raise_for_status()
    body = resp.json()

    if body.get("code") != 0:
        raise RuntimeError(f"Lookup user by email thất bại: {body}")

    user_list = body.get("data", {}).get("user_list", [])
    if not user_list:
        raise ValueError(f"Không tìm thấy user với email: {email}")

    user_id = user_list[0].get("user_id")
    if not user_id:
        raise ValueError(f"Email {email} chưa được liên kết với tài khoản Lark nào.")

    print(f"[bot] Tìm thấy user_id={user_id} cho email={email}")
    return user_id


def send_message(user_id: str, card: dict) -> None:
    resp = requests.post(
        f"{LARK_API}/im/v1/messages",
        headers={
            "Authorization": f"Bearer {get_app_access_token()}",
            "Content-Type": "application/json",
        },
        params={"receive_id_type": "open_id"},
        json={
            "receive_id": user_id,
            "msg_type":   "interactive",
            "content":    json.dumps(card),
        },
        timeout=15,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 0:
        raise RuntimeError(f"Gửi tin nhắn thất bại: {body}")
    print(f"[bot] ✅ Đã gửi báo cáo tới user_id={user_id}")


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

    # Bước 3: Resolve user_id rồi gửi
    target_email = os.environ.get("TARGET_EMAIL")
    if target_email:
        target_user_id = get_user_id_by_email(target_email)
    else:
        target_user_id = os.environ["TARGET_USER_ID"]

    send_message(target_user_id, card)

    print("[bot] ✅ Hoàn thành!")
    print("=" * 60)


if __name__ == "__main__":
    main()
