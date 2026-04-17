"""
task_checker.py
Lấy tất cả task từ Lark Task API v2, lọc các task có start_date hoặc
due_date trong tuần hiện tại, và xác định trạng thái từng task.

Flow:
  1. GET /task/v2/tasklists                    → danh sách tất cả task list
  2. GET /task/v2/tasklists/{guid}/tasks       → task trong từng list (phân trang)
  3. GET /task/v2/tasks/{guid}/subtasks        → sub-task của từng task
  4. Collect tất cả open_id → batch lookup tên 1 lần
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

# Cache tên user: {open_id: name}
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


# ── 4. Preload tên tất cả user bằng batch API ────────────────────────────────

def preload_user_names(open_ids: set[str]) -> None:
    """
    Gọi /contact/v3/users/{open_id} từng người bằng user_access_token.
    user_access_token đã có quyền đọc thông tin user trong workspace.
    Cache kết quả để mỗi user chỉ gọi 1 lần.
    """
    ids_to_fetch = [uid for uid in open_ids if uid and uid not in _user_name_cache]
    if not ids_to_fetch:
        return

    print(f"[user] Preloading {len(ids_to_fetch)} user names...")
    token = get_user_access_token()

    for uid in ids_to_fetch:
        try:
            resp = requests.get(
                f"{LARK_API}/contact/v3/users/{uid}",
                headers={"Authorization": f"Bearer {token}"},
                params={"user_id_type": "open_id"},
                timeout=10,
            )
            body = resp.json()
            if body.get("code") == 0:
                u    = body.get("data", {}).get("user", {})
                name = u.get("name") or u.get("en_name") or u.get("nickname") or uid
                _user_name_cache[uid] = name
                print(f"[user] ✅ {uid} → {name}")
            else:
                print(f"[user] ⚠️  {uid}: code={body.get('code')} msg={body.get('msg')}")
                _user_name_cache[uid] = uid
        except Exception as e:
            print(f"[user] ❌ {uid}: {e}")
            _user_name_cache[uid] = uid


# ── 5. Collect tất cả open_id từ raw tasks ───────────────────────────────────

def _collect_open_ids(raw_tasks: list[dict]) -> set[str]:
    ids = set()
    for t in raw_tasks:
        for m in (t.get("members") or []):
            uid = m.get("id", "")
            if uid:
                ids.add(uid)
    return ids


# ── 6. Lấy assignee từ cache ──────────────────────────────────────────────────

def _get_assignees(task: dict) -> str:
    members = task.get("members") or []
    names = []
    for m in members:
        if m.get("role") != "assignee":
            continue
        uid  = m.get("id", "")
        name = _user_name_cache.get(uid, uid) if uid else ""
        if name:
            names.append(name)
    return ", ".join(filter(None, names)) or "Unassigned"


# ── 7. Parse timestamp ────────────────────────────────────────────────────────

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


# ── 8. Xác định status ────────────────────────────────────────────────────────

def _determine_status(task: dict, today: date) -> str:
    completed_at = task.get("completed_at")
    if completed_at and completed_at != "0":
        return "done"

    due_d = _to_date((task.get("due") or {}).get("timestamp"))
    if due_d:
        return "overdue" if due_d < today else "in_progress"
    return "todo"


# ── 9. Parse 1 task/sub-task thành dict chuẩn ────────────────────────────────

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


# ── 10. Core: lấy tất cả tasks + sub-tasks trong tuần ────────────────────────

def get_tasks_for_week(week_start: date, week_end: date) -> list[dict]:
    """
    Lấy tất cả tasks VÀ sub-tasks có start_date hoặc due_date
    trong [week_start, week_end].
    """
    today     = date.today()
    tasklists = fetch_tasklists()

    # Pass 1: thu thập tất cả raw tasks + subtasks
    all_raw: list[tuple[dict, str, str | None, date | None, date | None]] = []
    # (task, tl_name, parent_name, fallback_due, fallback_start)

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
            task_name      = t.get("summary", "(no name)")
            task_guid      = t.get("guid", "")
            due_d          = _to_date((t.get("due") or {}).get("timestamp"))
            start_d        = _to_date((t.get("start") or {}).get("timestamp"))
            parent_in_week = _in_week(due_d) or _in_week(start_d)

            if parent_in_week:
                all_raw.append((t, tl_name, None, None, None))

            if task_guid:
                subtasks = fetch_subtasks(task_guid)
                for st in subtasks:
                    st_due     = _to_date((st.get("due") or {}).get("timestamp"))
                    st_start   = _to_date((st.get("start") or {}).get("timestamp"))
                    st_in_week = _in_week(st_due) or _in_week(st_start)
                    if st_in_week or parent_in_week:
                        all_raw.append((st, tl_name, task_name, due_d, start_d))

    # Pass 2: collect tất cả open_id → preload tên 1 lần
    all_ids: set[str] = set()
    for (t, *_) in all_raw:
        all_ids |= _collect_open_ids([t])

    preload_user_names(all_ids)

    # Pass 3: parse thành dict chuẩn (dùng cache đã có)
    result = []
    for (t, tl_name, parent_name, fallback_due, fallback_start) in all_raw:
        parsed = _parse_task(t, today, tl_name, parent_name, fallback_due, fallback_start)
        result.append(parsed)
        if parent_name:
            print(f"[task]   └─ Sub-task: {t.get('summary', '')} | status={parsed['status']}")

    # Sắp xếp: overdue → in_progress → todo → done
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
