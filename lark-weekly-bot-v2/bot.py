"""
bot.py
Entry point của Lark Weekly Bot.
Chạy mỗi chiều thứ 6 qua GitHub Actions.
"""

import os
import json
import requests
from datetime import date, timedelta

from lark_auth import get_app_access_token
from sheet_parser import parse_tasks_for_week
from task_checker import enrich_tasks_with_status
from message_builder import build_weekly_report_card

LARK_API = "https://open.larksuite.com/open-apis"


# ── Lookup user_id từ email ───────────────────────────────────────────────────
def get_user_id_by_email(email: str) -> str:
    """
    Tìm open_id của user từ email.
    Dùng app_access_token (tenant-level) — không cần user token.
    Trả về open_id dạng "ou_xxxxxxxx".
    """
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
        raise ValueError(
            f"Email {email} chưa được liên kết với tài khoản Lark nào "
            f"trong workspace này."
        )

    print(f"[bot] Tìm thấy user_id={user_id} cho email={email}")
    return user_id


def get_week_range() -> tuple[date, date]:
    """Thứ 2 → Thứ 6 của tuần hiện tại."""
    today  = date.today()
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=4)
    return monday, friday


def send_message(user_id: str, card: dict) -> None:
    """
    Gửi Lark Interactive Card tới user_id.
    Dùng app_access_token (tenant-level) vì đây là hành động bot gửi tin.
    """
    resp = requests.post(
        f"{LARK_API}/im/v1/messages",
        headers={
            "Authorization": f"Bearer {get_app_access_token()}",
            "Content-Type": "application/json",
        },
        params={"receive_id_type": "user_id"},
        json={
            "receive_id": user_id,
            "msg_type":   card["msg_type"],
            "content":    json.dumps(card["card"]),
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

    # Bước 1: Đọc sheet + parse tasks theo màu
    tasks = parse_tasks_for_week(week_start, week_end)
    if not tasks:
        print("[bot] ⚠️  Không tìm thấy task nào trong tuần — bỏ qua gửi tin")
        return

    # Bước 2: Kiểm tra trạng thái từng task qua Lark Task API
    tasks = enrich_tasks_with_status(tasks)

    # Bước 3: Build Lark Card
    card = build_weekly_report_card(tasks, week_start, week_end)

    # Bước 4: Resolve user_id từ email rồi gửi
    # Ưu tiên TARGET_EMAIL nếu có, fallback về TARGET_USER_ID
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
