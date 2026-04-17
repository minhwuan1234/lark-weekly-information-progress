"""
bot.py
Entry point của Lark Weekly Bot.
Chạy mỗi chiều thứ 6 qua GitHub Actions.

Flow:
  1. Lấy tất cả task list của user
  2. Tìm tasks có start_date hoặc due_date trong tuần này
  3. Xác định status: done / overdue / in_progress / todo
  4. Build Lark Card và gửi thẳng bằng email (không cần lookup open_id)

Ghi chú gửi tin:
  - Dùng receive_id_type="email" → gửi thẳng bằng email, không cần contact API
  - Fallback: nếu có TARGET_USER_ID (open_id) thì dùng open_id
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


def send_message(receive_id: str, receive_id_type: str, card: dict) -> None:
    """
    Gửi Lark Interactive Card.
    receive_id_type: "email" | "open_id" | "user_id" | "union_id" | "chat_id"
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

    # Bước 3: Gửi tin — ưu tiên email (không cần lookup open_id)
    target_email   = os.environ.get("TARGET_EMAIL", "").strip()
    target_user_id = os.environ.get("TARGET_USER_ID", "").strip()

    if target_email:
        # Gửi thẳng bằng email — Lark hỗ trợ receive_id_type=email
        send_message(target_email, "email", card)
    elif target_user_id:
        send_message(target_user_id, "open_id", card)
    else:
        raise ValueError("Cần set TARGET_EMAIL hoặc TARGET_USER_ID trong GitHub Secrets")

    print("[bot] ✅ Hoàn thành!")
    print("=" * 60)


if __name__ == "__main__":
    main()
