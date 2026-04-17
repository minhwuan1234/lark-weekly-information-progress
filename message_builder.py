"""
message_builder.py
Tạo Lark Interactive Card từ danh sách tasks trong tuần.
Nhóm theo tasklist name, hiển thị status bằng icon + màu.

Lark Card 2.0 schema — dùng tag "div" thay vì "markdown",
dùng "lark_md" cho text có format, tránh <font color>.
"""

from datetime import date


STATUS_CONFIG = {
    "done":        {"icon": "✅", "label": "Done",        "tag": "[Done]"},
    "overdue":     {"icon": "🔴", "label": "Overdue",     "tag": "[Overdue]"},
    "in_progress": {"icon": "🟡", "label": "In progress", "tag": "[In progress]"},
    "todo":        {"icon": "⚪", "label": "To do",       "tag": "[To do]"},
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
    total         = len(tasks)
    done_count    = sum(1 for t in tasks if t["status"] == "done")
    overdue_count = sum(1 for t in tasks if t["status"] == "overdue")
    wip_count     = sum(1 for t in tasks if t["status"] == "in_progress")
    todo_count    = sum(1 for t in tasks if t["status"] == "todo")

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

    # ── Summary stats (dùng column_set hợp lệ) ───────────────────────────────
    elements.append({
        "tag": "column_set",
        "flex_mode": "none",
        "background_style": "default",
        "columns": [
            _stat_col("Total",        str(total),         "blue"),
            _stat_col("✅ Done",      str(done_count),    "green"),
            _stat_col("🔴 Overdue",   str(overdue_count), "red"),
            _stat_col("🟡 In prog",   str(wip_count),     "yellow"),
            _stat_col("⚪ To do",     str(todo_count),    "grey"),
        ],
    })

    elements.append({"tag": "hr"})

    # ── Cảnh báo nếu có task overdue ─────────────────────────────────────────
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
        # Tiêu đề task list
        elements.append({
            "tag": "div",
            "text": {
                "tag":     "lark_md",
                "content": f"**📁 {list_name}**  `{len(list_tasks)} task{'s' if len(list_tasks) > 1 else ''}`",
            },
        })

        # Từng task trong list
        for t in list_tasks:
            cfg  = STATUS_CONFIG.get(t["status"], STATUS_CONFIG["todo"])
            name = t.get("name") or "(no name)"
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

            # Build task row
            task_url = t.get("task_url", "")
            if task_url:
                name_md = f"[{name}]({task_url})"
            else:
                name_md = name

            content = f"{cfg['icon']} **{cfg['label']}**  {name_md}"
            if meta_line:
                content += f"\n{meta_line}"

            elements.append({
                "tag": "div",
                "text": {
                    "tag":     "lark_md",
                    "content": content,
                },
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
    """
    color nhận: "blue" | "green" | "red" | "yellow" | "grey"
    Dùng Lark column background thay vì font color tag.
    """
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
