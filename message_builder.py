"""
message_builder.py
Tạo Lark Interactive Card từ danh sách tasks trong tuần.
Nhóm theo tasklist name, hiển thị status bằng icon.
Sub-tasks được indent vào dưới task cha.
"""

from datetime import date


STATUS_CONFIG = {
    "done":        {"icon": "✅", "label": "Done"},
    "overdue":     {"icon": "🔴", "label": "Overdue"},
    "in_progress": {"icon": "🟡", "label": "In progress"},
    "todo":        {"icon": "⚪", "label": "To do"},
}


def build_weekly_report_card(
    tasks: list[dict],
    week_start: date,
    week_end: date,
) -> dict:
    total         = len(tasks)
    done_count    = sum(1 for t in tasks if t["status"] == "done")
    overdue_count = sum(1 for t in tasks if t["status"] == "overdue")
    wip_count     = sum(1 for t in tasks if t["status"] == "in_progress")
    todo_count    = sum(1 for t in tasks if t["status"] == "todo")
    sub_count     = sum(1 for t in tasks if t.get("is_subtask"))

    # Nhóm theo tasklist
    by_list: dict[str, list[dict]] = {}
    for t in tasks:
        key = t.get("tasklist_name") or "Other"
        by_list.setdefault(key, []).append(t)

    # ── Header ───────────────────────────────────────────────────────────────
    header = {
        "template": "blue",
        "title": {
            "tag":     "plain_text",
            "content": f"📋 Weekly Task Review  {week_start.strftime('%d/%m')} – {week_end.strftime('%d/%m/%Y')}",
        },
    }

    elements: list[dict] = []

    # ── Summary stats ─────────────────────────────────────────────────────────
    elements.append({
        "tag": "column_set",
        "flex_mode": "none",
        "background_style": "default",
        "columns": [
            _stat_col("Total",       str(total),         ),
            _stat_col("✅ Done",     str(done_count),    ),
            _stat_col("🔴 Overdue",  str(overdue_count), ),
            _stat_col("🟡 In prog",  str(wip_count),     ),
            _stat_col("⚪ To do",    str(todo_count),    ),
        ],
    })

    if sub_count:
        elements.append({
            "tag": "div",
            "text": {
                "tag":     "lark_md",
                "content": f"_Bao gồm {sub_count} sub-task_",
            },
        })

    elements.append({"tag": "hr"})

    # ── Cảnh báo overdue ──────────────────────────────────────────────────────
    if overdue_count > 0:
        elements.append({
            "tag": "div",
            "text": {
                "tag":     "lark_md",
                "content": f"⚠️ **{overdue_count} task đã quá deadline** — cần follow up ngay!",
            },
        })
        elements.append({"tag": "hr"})

    # ── Từng task list ────────────────────────────────────────────────────────
    for list_name, list_tasks in by_list.items():
        elements.append({
            "tag": "div",
            "text": {
                "tag":     "lark_md",
                "content": f"**📁 {list_name}**  `{len(list_tasks)} task{'s' if len(list_tasks) > 1 else ''}`",
            },
        })

        # Tách task cha và sub-task
        parent_tasks = [t for t in list_tasks if not t.get("is_subtask")]
        subtask_map: dict[str, list[dict]] = {}
        for st in list_tasks:
            if st.get("is_subtask") and st.get("parent_name"):
                subtask_map.setdefault(st["parent_name"], []).append(st)

        for t in parent_tasks:
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": _task_md(t)}})

            # Sub-tasks của task này (indent bằng dấu └─)
            for st in subtask_map.get(t["name"], []):
                elements.append({"tag": "div", "text": {"tag": "lark_md", "content": _task_md(st, indent=True)}})

        # Sub-tasks mà không match được task cha nào (edge case)
        orphan_subtasks = [
            st for st in list_tasks
            if st.get("is_subtask") and st.get("parent_name") not in [t["name"] for t in parent_tasks]
        ]
        for st in orphan_subtasks:
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": _task_md(st, indent=True)}})

        elements.append({"tag": "hr"})

    # Xoá hr cuối
    if elements and elements[-1].get("tag") == "hr":
        elements.pop()

    # ── Footer ────────────────────────────────────────────────────────────────
    elements.append({
        "tag": "note",
        "elements": [{
            "tag":     "plain_text",
            "content": f"Lark Weekly Bot · {date.today().strftime('%A, %d %b %Y')}",
        }],
    })

    return {
        "header":   header,
        "elements": elements,
    }


def _task_md(t: dict, indent: bool = False) -> str:
    """Build markdown string cho 1 task hoặc sub-task."""
    cfg    = STATUS_CONFIG.get(t["status"], STATUS_CONFIG["todo"])
    name   = t.get("name") or "(no name)"
    if len(name) > 65:
        name = name[:62] + "..."

    task_url = t.get("task_url", "")
    name_md  = f"[{name}]({task_url})" if task_url else name

    prefix = "　└─ " if indent else ""  # indent bằng ký tự full-width space

    meta_parts = []
    if t.get("assignee") and t["assignee"] != "Unassigned":
        meta_parts.append(f"👤 {t['assignee']}")
    if t.get("due_date"):
        due_str = t["due_date"].strftime("%d/%m")
        if t["status"] == "overdue":
            meta_parts.append(f"⏰ Due {due_str} (quá hạn)")
        else:
            meta_parts.append(f"📅 Due {due_str}")
    if t.get("completed_at"):
        meta_parts.append(f"✓ Completed {t['completed_at'].strftime('%d/%m')}")

    meta_line = "  ·  ".join(meta_parts)
    content   = f"{prefix}{cfg['icon']} **{cfg['label']}**  {name_md}"
    if meta_line:
        content += f"\n{prefix}{meta_line}"

    return content


def _stat_col(label: str, value: str) -> dict:
    return {
        "tag":              "column",
        "width":            "weighted",
        "weight":           1,
        "background_style": "default",
        "elements": [{
            "tag": "div",
            "text": {
                "tag":     "lark_md",
                "content": f"**{value}**\n{label}",
            },
        }],
    }
