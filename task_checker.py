"""
task_checker.py
Lấy tất cả task từ Lark Task API v2, lọc các task có start_date hoặc
due_date trong tuần hiện tại, và xác định trạng thái từng task.

Flow:
  1. GET /task/v2/tasklists                    → danh sách tất cả task list
  2. GET /task/v2/tasklists/{guid}/tasks       → task trong từng list (phân trang)
  3. GET /task/v2/tasks/{guid}/subtasks        → sub-task của từng task
  4. GET /contact/v3/users/{user_id}           → tên thật của assignee
  5. Lọc task/sub-task có start hoặc due trong [week_start, week_end]
     Sub-task: nếu không có date riêng thì kế thừa date từ task cha
  6. Xác định status:
       - completed_at != null           → done
       - due < today AND not completed  → overdue
       - due >= today AND not completed → in_progress
       - không có due date             → todo
"""

import requests
from datetime import date, datetime, timezone
from lark_auth import get_user_access_token, get_app_access_token

LARK_API = "https://open.larksuite.com/open-apis"

# Cache tên user để tránh gọi API lặp lại
_user_name_cache: dict[str, str] = {}


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
            break

        data = body.get("data", {})
        all_subtasks.extend(data.get("items", []))

        page_token = data.get("page_token")
        if not data.get("has_more") or not page_token:
            break

    return all_subtasks


# ── 4. Lookup tên user từ open_id ─────────────────────────────────────────────

def _get_user_name(user_id: str) -> str:
    """
    Gọi /contact/v3/users/{user_id} để lấy tên thật.
    Cache kết quả để tránh gọi lặp.
    """
    if not user_id:
        return "Unassigned"
    if user_id in _user_name_cache:
        return _user_name_cache[user_id]

    try:
        resp = requests.get(
            f"{LARK_API}/contact/v3/users/{user_id}",
            headers={"Authorization": f"Bearer {get_app_access_token()}"},
            params={"user_id_type": "open_id"},
            timeout=10,
        )
        resp.raise_for_status()
        print(f"[user] Lookup {user_id}: {resp.status_code} | {resp.text}")
        body = resp.json()
        if body.get("code") == 0:
            user_data = body.get("data", {}).get("user", {})
            name = (
                user_data.get("name")
                or user_data.get("en_name")
                or user_data.get("nickname")
                or user_id
            )
            _user_name_cache[user_id] = name
            return name
    except Exception:
        pass

    _user_name_cache[user_id] = user_id  # fallback về id
    return user_id


# ── 5. Lấy assignee (tên thật) ────────────────────────────────────────────────

def _get_assignees(task: dict) -> str:
    members = task.get("members") or []
    names = []
    for m in members:
        if m.get("role") != "assignee":
            continue
        user_id = m.get("id", "")
        if user_id:
            # Luôn lookup qua API để đảm bảo có tên thật
            name = _get_user_name(user_id)
        else:
            name = m.get("name") or m.get("display_name") or ""
        if name:
            names.append(name)
    return ", ".join(filter(None, names)) or "Unassigned"


# ── 6. Parse timestamp ────────────────────────────────────────────────────────

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


# ── 7. Xác định status ────────────────────────────────────────────────────────

def _determine_status(task: dict, today: date) -> str:
    completed_at = task.get("completed_at")
    if completed_at and completed_at != "0":
        return "done"

    due_d = _to_date((task.get("due") or {}).get("timestamp"))
    if due_d:
        return "overdue" if due_d < today else "in_progress"
    return "todo"


# ── 8. Parse 1 task/sub-task thành dict chuẩn ────────────────────────────────

def _parse_task(
    t: dict,
    today: date,
    tasklist_name: str,
    parent_name: str | None = None,
    fallback_due: date | None = None,
    fallback_start: date | None = None,
) -> dict:
    due_d   = _to_date((t.get("due") or {}).get("timestamp")) or fallback_due
    start_d = _to_date((t.get("start") or {}).get("timestamp")) or fallback_start

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


# ── 9. Core: lấy tất cả tasks + sub-tasks trong tuần ─────────────────────────

def get_tasks_for_week(week_start: date, week_end: date) -> list[dict]:
    """
    Lấy tất cả tasks VÀ sub-tasks có start_date hoặc due_date
    trong [week_start, week_end].

    Sub-task không có date riêng sẽ kế thừa date từ task cha
    → luôn hiện cùng task cha nếu task cha trong tuần.
    """
    today     = date.today()
    tasklists = fetch_tasklists()
    result    = []

    def _in_week(d: date | None) -> bool:
        return bool(d and week_start <= d <= week_end)

    for tl in tasklists:
        tl_guid = tl.get("guid") or tl.get("tasklist_guid", "")
        tl_name = tl.get("name", "Unknown List")
        if not tl_guid:
            continue

        raw_tasks = fetch_tasks_in_list(tl_guid)
        print(f"[task] '{tl_name}': {len(raw_tasks)} tasks")

        for t in raw_tasks:
            task_name  = t.get("summary", "(no name)")
            task_guid  = t.get("guid", "")
            due_d      = _to_date((t.get("due") or {}).get("timestamp"))
            start_d    = _to_date((t.get("start") or {}).get("timestamp"))
            parent_in_week = _in_week(due_d) or _in_week(start_d)

            # ── Task cha ──────────────────────────────────────────────────
            if parent_in_week:
                result.append(_parse_task(t, today, tl_name, parent_name=None))

            # ── Sub-tasks ─────────────────────────────────────────────────
            if task_guid:
                subtasks = fetch_subtasks(task_guid)
                for st in subtasks:
                    st_due   = _to_date((st.get("due") or {}).get("timestamp"))
                    st_start = _to_date((st.get("start") or {}).get("timestamp"))
                    st_in_week = _in_week(st_due) or _in_week(st_start)

                    # Hiện sub-task nếu:
                    # (a) sub-task có date riêng trong tuần, HOẶC
                    # (b) task cha trong tuần (sub-task kế thừa context cha)
                    if st_in_week or parent_in_week:
                        parsed = _parse_task(
                            st, today, tl_name,
                            parent_name=task_name,
                            fallback_due=due_d,       # kế thừa due cha nếu không có
                            fallback_start=start_d,   # kế thừa start cha nếu không có
                        )
                        result.append(parsed)
                        print(f"[task]   └─ Sub-task: {st.get('summary', '')} | status={parsed['status']}")

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
