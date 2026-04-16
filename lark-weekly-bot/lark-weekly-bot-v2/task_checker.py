"""
task_checker.py
Nhận danh sách task từ sheet_parser, tìm task tương ứng trong Lark Task API
và lấy trạng thái thực tế: done / in_progress / todo.
"""

import requests
from lark_auth import get_user_access_token
from difflib import SequenceMatcher

LARK_API = "https://open.larksuite.com/open-apis"


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {get_user_access_token()}"}


# ── 1. Lấy danh sách tasks từ Lark Task API ───────────────────────────────────
def fetch_all_tasks(page_size: int = 50) -> list[dict]:
    """
    Lấy tất cả tasks (có thể phân trang).
    Trả list[dict] raw từ API.
    """
    all_tasks = []
    page_token = None

    while True:
        params: dict = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token

        resp = requests.get(
            f"{LARK_API}/task/v1/tasks",
            headers=_auth_headers(),
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()

        if body.get("code") != 0:
            raise RuntimeError(f"Lấy task list thất bại: {body}")

        data = body.get("data", {})
        all_tasks.extend(data.get("items", []))

        page_token = data.get("page_token")
        if not data.get("has_more") or not page_token:
            break

    print(f"[task] Tìm thấy {len(all_tasks)} tasks từ Lark Task API")
    return all_tasks


# ── 2. Map trạng thái từ task API ─────────────────────────────────────────────
def _map_status(task: dict) -> str:
    """
    Lark Task v1:
      - is_completed = True          → done
      - is_completed = False + có due → in_progress
      - is_completed = False + no due → todo
    """
    if task.get("is_completed"):
        return "done"
    if task.get("due") and task["due"].get("time"):
        return "in_progress"
    return "todo"


# ── 3. Match task sheet → task API bằng tên ───────────────────────────────────
def _similarity(a: str, b: str) -> float:
    """Tỉ lệ giống nhau giữa 2 chuỗi, 0.0 → 1.0."""
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _find_best_match(name: str, api_tasks: list[dict], threshold: float = 0.6) -> dict | None:
    """
    Tìm task API có tên gần giống nhất với tên từ sheet.
    Trả None nếu không tìm thấy match đủ tốt.
    """
    best_score = 0.0
    best_task  = None

    for t in api_tasks:
        api_name = t.get("summary", "")
        score = _similarity(name, api_name)
        if score > best_score:
            best_score = score
            best_task  = t

    if best_score >= threshold:
        return best_task
    return None


# ── 4. Enrich: gán status vào từng task từ sheet ─────────────────────────────
def enrich_tasks_with_status(sheet_tasks: list[dict]) -> list[dict]:
    """
    Nhận list task từ sheet_parser, trả về list đã thêm field:
      - "status": "done" | "in_progress" | "todo" | "not_found"
      - "api_task": dict raw từ API (hoặc None)
    """
    api_tasks = fetch_all_tasks()

    for task in sheet_tasks:
        name = task.get("name", "")
        if not name:
            task["status"]   = "todo"
            task["api_task"] = None
            continue

        match = _find_best_match(name, api_tasks)
        if match:
            task["status"]   = _map_status(match)
            task["api_task"] = match
            print(f"[task] '{name[:40]}' → {task['status']} "
                  f"(match: '{match.get('summary','')[:40]}')")
        else:
            # Không tìm thấy trong Task API → đánh là todo
            task["status"]   = "todo"
            task["api_task"] = None
            print(f"[task] '{name[:40]}' → không tìm thấy match trong Task API")

    return sheet_tasks
