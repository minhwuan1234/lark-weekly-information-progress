"""
sheet_parser.py
Đọc Lark Sheet, parse hàng 3 lấy ngày, quét màu nền ô để xác định task.

Logic màu:
  - Ô có màu + có text  → ô đầu của task (chứa tên, due date)
  - Ô có màu + no text  → task kéo dài từ ô trước (cùng màu, cùng hàng)
  - Ô trắng / no color  → không có task
"""

import os
import re
import requests
from datetime import date
from typing import Optional
from lark_auth import get_user_access_token

LARK_API     = "https://open.larksuite.com/open-apis"
SHEET_TOKEN  = os.environ.get("LARK_SHEET_TOKEN", "EikqsZWIphkIGTtDxQIl6nSkg4f")
SHEET_ID     = os.environ.get("LARK_SHEET_ID", "Production")  # tên tab


# ── 1. Fetch raw data từ Lark Sheets API ─────────────────────────────────────
def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {get_user_access_token()}"}


def fetch_values(start_row: int, end_row: int) -> list[list]:
    """Lấy giá trị text của từng ô, trả về list of rows."""
    range_str = f"A{start_row}:BZ{end_row}"
    resp = requests.get(
        f"{LARK_API}/sheets/v3/spreadsheets/{SHEET_TOKEN}/values/{range_str}",
        headers=_auth_headers(),
        params={"valueRenderOption": "ToString", "dateTimeRenderOption": "FormattedString"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Lấy values thất bại: {data}")
    return data.get("data", {}).get("valueRange", {}).get("values", [])


def fetch_styles(start_row: int, end_row: int) -> list[list]:
    """Lấy style (background color) của từng ô."""
    range_str = f"{SHEET_ID}!A{start_row}:BZ{end_row}"
    resp = requests.get(
        f"{LARK_API}/sheets/v3/spreadsheets/{SHEET_TOKEN}/sheets/{SHEET_ID}/styles",
        headers=_auth_headers(),
        params={"ranges": range_str},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Lấy styles thất bại: {data}")

    # Trả về grid styles[row][col]
    styles_grid: list[list] = []
    for item in data.get("data", {}).get("styles", []):
        row_idx = item.get("range", {}).get("rowIndex", 0)
        col_idx = item.get("range", {}).get("columnIndex", 0)
        while len(styles_grid) <= row_idx:
            styles_grid.append([])
        row = styles_grid[row_idx]
        while len(row) <= col_idx:
            row.append(None)
        row[col_idx] = item.get("style", {})
    return styles_grid


# ── 2. Parse hàng 3 → map col_index → date ───────────────────────────────────
def parse_date_row(row: list) -> dict[int, date]:
    """
    row = list cell values từ hàng 3.
    Trả {col_idx: date}.
    """
    col_to_date: dict[int, date] = {}
    current_year = date.today().year

    for col_idx, cell in enumerate(row):
        text = str(cell).strip() if cell else ""
        d = _parse_date(text, current_year)
        if d:
            col_to_date[col_idx] = d
    return col_to_date


def _parse_date(text: str, year: int) -> Optional[date]:
    from datetime import datetime
    # Format thường thấy trong sheet: "14-Apr", "Apr 14", "16-Apr", "17-Apr"
    formats = [
        ("%d-%b", False),
        ("%b-%d", False),
        ("%b %d", False),
        ("%d %b", False),
        ("%d/%m", False),
        ("%m/%d", False),
        ("%Y-%m-%d", True),
        ("%d-%m-%Y", True),
    ]
    for fmt, has_year in formats:
        try:
            d = datetime.strptime(text, fmt)
            if not has_year:
                d = d.replace(year=year)
            return d.date()
        except ValueError:
            continue
    return None


# ── 3. Normalize màu ô ────────────────────────────────────────────────────────
def _normalize_color(style: Optional[dict]) -> Optional[str]:
    """
    Lark trả background color dạng:
      {"background_color": {"red": 0.98, "green": 0.90, "blue": 0.23, "alpha": 1}}
    Chuyển sang hex. Trả None nếu trắng hoặc không màu.
    """
    if not style:
        return None

    bg = style.get("background_color") or style.get("bg_color") or {}
    if not bg:
        return None

    r = round(bg.get("red",   1.0) * 255)
    g = round(bg.get("green", 1.0) * 255)
    b = round(bg.get("blue",  1.0) * 255)

    # Bỏ qua trắng và gần trắng
    if r >= 250 and g >= 250 and b >= 250:
        return None
    return f"#{r:02x}{g:02x}{b:02x}"


# ── 4. Parse cell text → tên task + due date ─────────────────────────────────
def _parse_cell_text(text: str) -> tuple[str, Optional[str]]:
    """
    "CSL 6. Forests and Wood Illus ~2' (1.5)\nDue 17/4"
    → ("CSL 6. Forests and Wood Illus ~2' (1.5)", "17/4")
    """
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    name = lines[0] if lines else text.strip()
    due = None
    for line in lines[1:]:
        m = re.search(r"[Dd]ue[:\s]+(\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?)", line)
        if m:
            due = m.group(1)
            break
    return name, due


# ── 5. Core: quét sheet → danh sách task trong tuần ──────────────────────────
def parse_tasks_for_week(week_start: date, week_end: date, max_rows: int = 60) -> list[dict]:
    """
    Trả list[dict]:
    {
      "name":     str,
      "due":      str | None,
      "assignee": str,
      "color":    str hex,
      "dates":    list[date],   # các ngày task kéo dài
      "row":      int,          # số hàng trong sheet (1-indexed)
    }
    Chỉ trả task có ít nhất 1 ngày nằm trong [week_start, week_end].
    """
    print(f"[sheet] Đọc sheet rows 1–{max_rows}...")
    values = fetch_values(1, max_rows)
    styles = fetch_styles(1, max_rows)

    # Hàng 3 = index 2 (0-indexed)
    if len(values) < 3:
        raise ValueError("Sheet không đủ 3 hàng")

    date_row    = values[2]          # hàng 3, 0-indexed
    col_to_date = parse_date_row(date_row)

    if not col_to_date:
        raise ValueError("Không tìm thấy ngày ở hàng 3 — kiểm tra lại format ô")

    print(f"[sheet] Tìm thấy {len(col_to_date)} cột ngày: "
          f"{min(col_to_date.values())} → {max(col_to_date.values())}")

    tasks: list[dict] = []

    # Quét từ hàng 4 trở đi (index 3)
    for row_idx in range(3, min(len(values), max_rows)):
        row_vals   = values[row_idx]
        row_styles = styles[row_idx] if row_idx < len(styles) else []

        # Cột A (index 0) = tên assignee
        assignee = str(row_vals[0]).strip() if row_vals else ""

        current_task: Optional[dict] = None

        for col_idx in sorted(col_to_date.keys()):
            cell_date  = col_to_date[col_idx]
            cell_text  = str(row_vals[col_idx]).strip() if col_idx < len(row_vals) else ""

            cell_style = row_styles[col_idx] if col_idx < len(row_styles) else None
            cell_color = _normalize_color(cell_style)

            if cell_color is None:
                # Ô trắng → đóng task hiện tại
                if current_task:
                    tasks.append(current_task)
                    current_task = None
                continue

            if current_task and current_task["color"] == cell_color:
                # Cùng màu → kéo dài task
                current_task["dates"].append(cell_date)
                # Nếu ô này có text mà task chưa có tên → lấy tên từ đây
                if cell_text and not current_task["name"]:
                    name, due = _parse_cell_text(cell_text)
                    current_task["name"] = name
                    if due:
                        current_task["due"] = due
            else:
                # Màu khác hoặc task mới → lưu task cũ, mở task mới
                if current_task:
                    tasks.append(current_task)

                name, due = _parse_cell_text(cell_text) if cell_text else ("", None)
                current_task = {
                    "name":     name,
                    "due":      due,
                    "assignee": assignee,
                    "color":    cell_color,
                    "dates":    [cell_date],
                    "row":      row_idx + 1,
                }

        if current_task:
            tasks.append(current_task)

    # Lọc chỉ giữ task có ngày trong tuần cần báo cáo
    in_week = [
        t for t in tasks
        if any(week_start <= d <= week_end for d in t["dates"])
    ]

    print(f"[sheet] Tổng {len(tasks)} tasks, {len(in_week)} tasks trong tuần "
          f"{week_start} → {week_end}")
    return in_week
