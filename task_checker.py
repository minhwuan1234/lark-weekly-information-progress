"""
task_checker.py
Lấy tất cả task từ Lark Task API v2, lọc các task có start_date hoặc
due_date trong tuần hiện tại, và xác định trạng thái từng task.

Flow:
  1. GET /task/v2/tasklists          → danh sách tất cả task list của user
  2. GET /task/v2/tasklists/{guid}/tasks  → task trong từng list (phân trang)
  3. Lọc task có start hoặc due trong [week_start, week_end]
  4. Xác định status:
       - completed_at != null           → done
       - due < now AND not completed    → overdue  (chậm deadline)
       - due >= now AND not completed   → in_progress
       - không có due date             → todo
"""

import requests
from datetime import date, datetime, timezone
from lark_auth import get_user_access_token

LARK_API = "https://open.larksuite.com/open-apis"


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {get_user_access_token()}"}


# ── 1. Lấy danh sách tất cả task list ────────────────────────────────────────

def fetch_tasklists() -> list[dict]:
    """
    Trả list[{"guid": ..., "name": ...}].
    Dùng user_access_token nên chỉ thấy task list của user đó.
    """
    all_lists = []
    page_token = None

    while True:
        params: dict = {"page_size": 50}
        if page_token:
            params["page_token"] = page_token

        resp = requests.get(
            f"{LARK_API}/task/v2/tasklists",
            headers=_auth_headers(),
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()

        if body.get("code") != 0:
            raise RuntimeError(f"Lấy tasklists thất bại: {body}")

        data = body.get("data", {})
        all_lists.extend(data.get("items", []))

        page_token = data.get("page_token")
        if not data.get("has_more") or not page_token:
            break

    print(f"[task] Tìm thấy {len(all_lists)} task list")
    return all_lists


# ── 2. Lấy tất cả tasks trong 1 task list ────────────────────────────────────

def fetch_tasks_in_list(tasklist_guid: str) -> list[dict]:
    """
    Lấy tất cả tasks trong 1 tasklist (có phân trang).
    Lấy cả task đã completed và chưa completed.
    """
    all_tasks = []
    page_token = None

    while True:
        params: dict = {
            "page_size":       50,
            "completed":       "false",   # lấy task chưa xong trước
        }
        if page_token:
            params["page_token"] = page_token

        resp = requests.get(
            f"{LARK_API}/task/v2/tasklists/{tasklist_guid}/tasks",
            headers=_auth_headers(),
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()

        if body.get("code") != 0:
            print(f"[task] ⚠️  Bỏ qua tasklist {tasklist_guid}: {body.get('msg')}")
            return []

        data = body.get("data", {})
        all_tasks.extend(data.get("items", []))

        page_token = data.get("page_token")
        if not data.get("has_more") or not page_token:
            break

    # Lấy thêm task đã completed trong tuần
    page_token = None
    while True:
        params = {"page_size": 50, "completed": "true"}
        if page_token:
            params["page_token"] = page_token

        resp = requests.get(
            f"{LARK_API}/task/v2/tasklists/{tasklist_guid}/tasks",
            headers=_auth_headers(),
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()

        if body.get("code") != 0:
            break

        data = body.get("data", {})
        all_tasks.extend(data.get("items", []))

        page_token = data.get("page_token")
        if not data.get("has_more") or not page_token:
            break

    return all_tasks


# ── 3. Parse timestamp từ Lark Task API ──────────────────────────────────────

def _parse_ts(ts_str: str | None) -> datetime | None:
    """
    Lark Task v2 trả timestamp dạng string milliseconds, vd "1711900800000".
    Chuyển sang datetime (UTC-aware).
    """
    if not ts_str:
        return None
    try:
        ts_ms = int(ts_str)
        return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    except (ValueError, TypeError):
        return None


def _to_date(ts_str: str | None) -> date | None:
    """Chuyển timestamp string → date (theo giờ UTC)."""
    dt = _parse_ts(ts_str)
    return dt.date() if dt else None


# ── 4. Xác định status ────────────────────────────────────────────────────────

def _determine_status(task: dict, today: date) -> str:
    """
    done      → completed_at có giá trị
    overdue   → chưa xong + due_date < today
    in_progress → chưa xong + due_date >= today
    todo      → chưa xong + không có due_date
    """
    completed_at = task.get("completed_at")
    if completed_at and completed_at != "0":
        return "done"

    due_obj = task.get("due") or {}
    due_ts  = due_obj.get("timestamp")
    due_d   = _to_date(due_ts)

    if due_d:
        return "overdue" if due_d < today else "in_progress"
    return "todo"


# ── 5. Lấy tên assignee từ task members ──────────────────────────────────────

def _get_assignees(task: dict) -> str:
    """
    Lấy danh sách assignee từ members[].role == 'assignee'.
    Trả tên hiển thị nếu có, fallback về id.
    """
    members = task.get("members") or []
    names = []
    for m in members:
        if m.get("role") != "assignee":
            continue
        name = (
            m.get("name")
            or m.get("display_name")
            or m.get("id", "")
        )
        if name:
            names.append(name)
    return ", ".join(names) if names else "Unassigned"


# ── 6. Core: lấy tất cả tasks trong tuần từ mọi task list ────────────────────

def get_tasks_for_week(week_start: date, week_end: date) -> list[dict]:
    """
    Lấy tất cả tasks có start_date hoặc due_date trong [week_start, week_end].

    Trả list[dict]:
    {
      "name":          str,
      "status":        "done" | "overdue" | "in_progress" | "todo",
      "due_date":      date | None,
      "start_date":    date | None,
      "assignee":      str,
      "tasklist_name": str,
      "task_url":      str,        # link mở task trực tiếp trong Lark
      "completed_at":  date | None,
    }
    """
    today      = date.today()
    tasklists  = fetch_tasklists()
    result     = []

    for tl in tasklists:
        tl_guid = tl.get("guid") or tl.get("tasklist_guid", "")
        tl_name = tl.get("name", "Unknown List")

        if not tl_guid:
            continue

        raw_tasks = fetch_tasks_in_list(tl_guid)
        print(f"[task] '{tl_name}': {len(raw_tasks)} tasks")

        for t in raw_tasks:
            due_obj   = t.get("due") or {}
            start_obj = t.get("start") or {}

            due_d   = _to_date(due_obj.get("timestamp"))
            start_d = _to_date(start_obj.get("timestamp"))

            # Chỉ giữ task có start hoặc due trong tuần này
            in_week = (
                (due_d   and week_start <= due_d   <= week_end) or
                (start_d and week_start <= start_d <= week_end)
            )
            if not in_week:
                continue

            status       = _determine_status(t, today)
            completed_ts = t.get("completed_at")
            completed_d  = _to_date(completed_ts) if (completed_ts and completed_ts != "0") else None

            # URL task: Lark tự build từ guid
            task_guid = t.get("guid", "")
            task_url  = f"https://applink.larksuite.com/client/todo/detail?guid={task_guid}" if task_guid else ""

            result.append({
                "name":          t.get("summary", "(no name)"),
                "status":        status,
                "due_date":      due_d,
                "start_date":    start_d,
                "assignee":      _get_assignees(t),
                "tasklist_name": tl_name,
                "task_url":      task_url,
                "completed_at":  completed_d,
            })

    # Sắp xếp: overdue trước, rồi in_progress, todo, done
    order = {"overdue": 0, "in_progress": 1, "todo": 2, "done": 3}
    result.sort(key=lambda t: (order.get(t["status"], 9), t["due_date"] or date.max))

    print(
        f"\n[task] Tổng {len(result)} tasks trong tuần {week_start} → {week_end}\n"
        + f"       done={sum(1 for t in result if t['status']=='done')} | "
        + f"overdue={sum(1 for t in result if t['status']=='overdue')} | "
        + f"in_progress={sum(1 for t in result if t['status']=='in_progress')} | "
        + f"todo={sum(1 for t in result if t['status']=='todo')}"
    )
    return result
