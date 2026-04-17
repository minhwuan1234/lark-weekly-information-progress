"""
task_checker.py
Lấy tất cả task từ Lark Task API v2, lọc các task có start_date hoặc
due_date trong tuần hiện tại, và xác định trạng thái từng task.

Flow:
  1. GET /task/v2/tasklists                    → danh sách tất cả task list
  2. GET /task/v2/tasklists/{guid}/tasks       → task trong từng list (phân trang)
  3. GET /task/v2/tasks/{guid}/subtasks        → sub-task của từng task
  4. Lọc task/sub-task có start hoặc due trong [week_start, week_end]
  5. Xác định status:
       - completed_at != null           → done
       - due < today AND not completed  → overdue
       - due >= today AND not completed → in_progress
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
    all_tasks = []

    for completed in ("false", "true"):
        page_token = None
        while True:
            params: dict = {"page_size": 50, "completed": completed}
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
                break

            data = body.get("data", {})
            all_tasks.extend(data.get("items", []))

            page_token = data.get("page_token")
            if not data.get("has_more") or not page_token:
                break

    return all_tasks


# ── 3. Lấy sub-tasks của 1 task ───────────────────────────────────────────────

def fetch_subtasks(task_guid: str) -> list[dict]:
    """
    GET /task/v2/tasks/{task_guid}/subtasks
    Trả về list sub-task (có thể rỗng nếu task không có sub-task).
    """
    all_subtasks = []
    page_token = None

    while True:
        params: dict = {"page_size": 50}
        if page_token:
            params["page_token"] = page_token

        resp = requests.get(
            f"{LARK_API}/task/v2/tasks/{task_guid}/subtasks",
            headers=_auth_headers(),
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()

        if body.get("code") != 0:
            # Không có sub-task hoặc lỗi → bỏ qua
            break

        data = body.get("data", {})
        all_subtasks.extend(data.get("items", []))

        page_token = data.get("page_token")
        if not data.get("has_more") or not page_token:
            break

    return all_subtasks


# ── 4. Parse timestamp ────────────────────────────────────────────────────────

def _parse_ts(ts_str: str | None) -> datetime | None:
    if not ts_str:
        return None
    try:
        return datetime.fromtimestamp(int(ts_str) / 1000, tz=timezone.utc)
    except (ValueError, TypeError):
        return None


def _to_date(ts_str: str | None) -> date | None:
    dt = _parse_ts(ts_str)
    return dt.date() if dt else None


# ── 5. Xác định status ────────────────────────────────────────────────────────

def _determine_status(task: dict, today: date) -> str:
    completed_at = task.get("completed_at")
    if completed_at and completed_at != "0":
        return "done"

    due_d = _to_date((task.get("due") or {}).get("timestamp"))
    if due_d:
        return "overdue" if due_d < today else "in_progress"
    return "todo"


# ── 6. Lấy assignee ───────────────────────────────────────────────────────────

def _get_assignees(task: dict) -> str:
    members = task.get("members") or []
    names = [
        m.get("name") or m.get("display_name") or m.get("id", "")
        for m in members
        if m.get("role") == "assignee"
    ]
    return ", ".join(filter(None, names)) or "Unassigned"


# ── 7. Parse 1 task/sub-task thành dict chuẩn ────────────────────────────────

def _parse_task(
    t: dict,
    today: date,
    tasklist_name: str,
    parent_name: str | None = None,
) -> dict:
    """
    Chuyển raw task API response thành dict chuẩn.
    parent_name: tên task cha nếu đây là sub-task, None nếu là task thường.
    """
    due_d   = _to_date((t.get("due") or {}).get("timestamp"))
    start_d = _to_date((t.get("start") or {}).get("timestamp"))

    completed_ts = t.get("completed_at")
    completed_d  = _to_date(completed_ts) if (completed_ts and completed_ts != "0") else None

    task_guid = t.get("guid", "")
    task_url  = (
        f"https://applink.larksuite.com/client/todo/detail?guid={task_guid}"
        if task_guid else ""
    )

    return {
        "name":          t.get("summary", "(no name)"),
        "status":        _determine_status(t, today),
        "due_date":      due_d,
        "start_date":    start_d,
        "assignee":      _get_assignees(t),
        "tasklist_name": tasklist_name,
        "task_url":      task_url,
        "completed_at":  completed_d,
        "is_subtask":    parent_name is not None,
        "parent_name":   parent_name,
    }


# ── 8. Core: lấy tất cả tasks + sub-tasks trong tuần ─────────────────────────

def get_tasks_for_week(week_start: date, week_end: date) -> list[dict]:
    """
    Lấy tất cả tasks VÀ sub-tasks có start_date hoặc due_date
    trong [week_start, week_end].

    Mỗi item trả về:
    {
      "name":          str,
      "status":        "done" | "overdue" | "in_progress" | "todo",
      "due_date":      date | None,
      "start_date":    date | None,
      "assignee":      str,
      "tasklist_name": str,
      "task_url":      str,
      "completed_at":  date | None,
      "is_subtask":    bool,
      "parent_name":   str | None,   # tên task cha nếu là sub-task
    }
    """
    today     = date.today()
    tasklists = fetch_tasklists()
    result    = []

    def _in_week(t: dict) -> bool:
        due_d   = _to_date((t.get("due") or {}).get("timestamp"))
        start_d = _to_date((t.get("start") or {}).get("timestamp"))
        return (
            (due_d   and week_start <= due_d   <= week_end) or
            (start_d and week_start <= start_d <= week_end)
        )

    for tl in tasklists:
        tl_guid = tl.get("guid") or tl.get("tasklist_guid", "")
        tl_name = tl.get("name", "Unknown List")
        if not tl_guid:
            continue

        raw_tasks = fetch_tasks_in_list(tl_guid)
        print(f"[task] '{tl_name}': {len(raw_tasks)} tasks")

        for t in raw_tasks:
            task_name = t.get("summary", "(no name)")
            task_guid = t.get("guid", "")

            # ── Task cha: add nếu trong tuần ──────────────────────────────
            if _in_week(t):
                result.append(_parse_task(t, today, tl_name, parent_name=None))

            # ── Sub-tasks: luôn fetch, add nếu sub-task trong tuần ────────
            if task_guid:
                subtasks = fetch_subtasks(task_guid)
                for st in subtasks:
                    if _in_week(st):
                        result.append(_parse_task(st, today, tl_name, parent_name=task_name))
                        print(f"[task]   └─ Sub-task: {st.get('summary', '')} (parent: {task_name})")

    # Sắp xếp: overdue trước, rồi in_progress, todo, done
    order = {"overdue": 0, "in_progress": 1, "todo": 2, "done": 3}
    result.sort(key=lambda t: (order.get(t["status"], 9), t["due_date"] or date.max))

    done_c = sum(1 for t in result if t["status"] == "done")
    over_c = sum(1 for t in result if t["status"] == "overdue")
    wip_c  = sum(1 for t in result if t["status"] == "in_progress")
    todo_c = sum(1 for t in result if t["status"] == "todo")
    sub_c  = sum(1 for t in result if t["is_subtask"])

    print(
        f"\n[task] Tổng {len(result)} tasks trong tuần {week_start} → {week_end}\n"
        f"       done={done_c} | overdue={over_c} | in_progress={wip_c} | todo={todo_c}\n"
        f"       (trong đó {sub_c} sub-tasks)"
    )
    return result
