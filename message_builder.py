"""
message_builder.py
Tạo Lark Interactive Card từ danh sách tasks trong tuần.
Nhóm theo tasklist name, hiển thị status bằng icon + màu.
"""

from datetime import date


STATUS_CONFIG = {
    "done":        {"icon": "✅", "label": "Done",        "color": "#00B0A6"},
    "overdue":     {"icon": "🔴", "label": "Overdue",     "color": "#E8283C"},
    "in_progress": {"icon": "🟡", "label": "In progress", "color": "#FF8B00"},
    "todo":        {"icon": "⚪", "label": "To do",       "color": "#888888"},
}


def build_weekly_report_card(
    tasks: list[dict],
    week_start: date,
    week_end: date,
) -> dict:
    """
    Tạo Lark Interactive Card JSON.
    tasks[i] chứa: name, status, due_date, start_date, assignee,
                   tasklist_name, task_url, completed_at
    """
    total       = len(tasks)
    done_count  = sum(1 for t in tasks if t["status"] == "done")
    overdue_count = sum(1 for t in tasks if t["status"] == "overdue")
    wip_count   = sum(1 for t in tasks if t["status"] == "in_progress")
    todo_count  = sum(1 for t in tasks if t["status"] == "todo")

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
        "flex_mode": "stretch",
        "columns": [
            _stat_col("Total",       str(total),         "#2D7EFF"),
            _stat_col("✅ Done",     str(done_count),    "#00B0A6"),
            _stat_col("🔴 Overdue",  str(overdue_count), "#E8283C"),
            _stat_col("🟡 In prog",  str(wip_count),     "#FF8B00"),
            _stat_col("⚪ To do",    str(todo_count),    "#888888"),
        ],
    })

    elements.append({"tag": "hr"})

    # ── Cảnh báo nếu có task overdue ─────────────────────────────────────────
    if overdue_count > 0:
        elements.append({
            "tag": "markdown",
            "content": f"⚠️ **{overdue_count} task đã quá deadline** — cần follow up ngay!",
        })
        elements.append({"tag": "hr"})

    # ── Từng task list ────────────────────────────────────────────────────────
    for list_name, list_tasks in by_list.items():
        # Tiêu đề task list
        elements.append({
            "tag": "markdown",
            "content": f"**📁 {list_name}**  `{len(list_tasks)} task{'s' if len(list_tasks) > 1 else ''}`",
        })

        # Từng task trong list
        for t in list_tasks:
            cfg      = STATUS_CONFIG.get(t["status"], STATUS_CONFIG["todo"])
            name     = t.get("name") or "(no name)"
            if len(name) > 65:
                name = name[:62] + "..."

            # Dòng phụ: assignee + due date
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

            # Build task row: có link nếu có task_url
            task_url = t.get("task_url", "")
            if task_url:
                name_md = f"[{name}]({task_url})"
            else:
                name_md = name

            task_md = (
                f"<font color='{cfg['color']}'>{cfg['icon']} **{cfg['label']}**</font>"
                f"  {name_md}"
            )
            if meta_line:
                task_md += f"\n<font color='#999999'>{meta_line}</font>"

            elements.append({
                "tag":     "markdown",
                "content": task_md,
            })

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


def _stat_col(label: str, value: str, color: str) -> dict:
    return {
        "tag": "column",
        "elements": [{
            "tag":       "markdown",
            "content":   (
                f"<font color='{color}'>**{value}**</font>\n"
                f"<font color='#999999'>{label}</font>"
            ),
            "text_align": "center",
        }],
    }
