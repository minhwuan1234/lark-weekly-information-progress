"""
sheet_parser.py
Đọc Lark Sheet, parse hàng 3 lấy ngày, quét màu nền ô để xác định task.

Logic màu:
  - Ô có màu + có text  → ô đầu của task (chứa tên, due date)
  - Ô có màu + no text  → task kéo dài từ ô trước (cùng màu, cùng hàng)
  - Ô trắng / no color  → không có task

── Lấy sheetId ────────────────────────────────────────────────────────────────
LARK_SHEET_ID trong .env là TÊN TAB (vd "Production"), KHÔNG phải sheetId thật.
sheetId thật (dạng "0b1b2c") được tự động lookup qua:
  GET /sheets/v3/spreadsheets/{token}/sheets/query
và cache lại để không gọi lại mỗi lần.

── Styles API ──────────────────────────────────────────────────────────────────
Endpoint: GET /sheets/v2/spreadsheets/{token}/styles?range=sheetId!A1:BZ60
backColor trong response là hex string (#RRGGBB).
"""

import os
import re
import requests
from datetime import date
from typing import Optional
from lark_auth import get_user_access_token

LARK_API    = "https://open.larksuite.com/open-apis"
SHEET_TOKEN = os.environ.get("LARK_SHEET_TOKEN", "EikqsZWIphkIGTtDxQIl6nSkg4f")
SHEET_NAME  = os.environ.get("LARK_SHEET_ID", "Production")   # tên tab hiển thị

# Cache sheetId thật để không gọi API lại
_sheet_id_cache: dict[str, str] = {}


# ── Auth ──────────────────────────────────────────────────────────────────────

def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {get_user_access_token()}"}


# ── Lookup sheetId thực tế từ tên tab ────────────────────────────────────────

def _resolve_sheet_id(sheet_name: str = SHEET_NAME) -> str:
    """
    Lark Sheets API v3 yêu cầu sheetId thật (vd "0b1b2c"),
    không nhận tên tab (vd "Production") trực tiếp trong range.

    Endpoint: GET /sheets/v3/spreadsheets/{token}/sheets/query
    Response: data.sheets[].{sheet_id, title, index}
    """
    if sheet_name in _sheet_id_cache:
        return _sheet_id_cache[sheet_name]

    resp = requests.get(
        f"{LARK_API}/sheets/v3/spreadsheets/{SHEET_TOKEN}/sheets/query",
        headers=_auth_headers(),
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Không lấy được danh sách sheets: {data}")

    sheets = data.get("data", {}).get("sheets", [])
    if not sheets:
        raise ValueError("Spreadsheet không có sheet nào")

    # Cache tất cả sheets trong một lần gọi
    for sheet in sheets:
        title    = sheet.get("title", "")
        sheet_id = sheet.get("sheet_id", "")
        if title and sheet_id:
            _sheet_id_cache[title] = sheet_id

    # Tìm theo tên
    if sheet_name in _sheet_id_cache:
        sid = _sheet_id_cache[sheet_name]
        print(f"[sheet] Resolved tab '{sheet_name}' → sheetId='{sid}'")
        return sid

    # Fallback: dùng sheet đầu tiên và cảnh báo
    first = sheets[0]
    sid   = first.get("sheet_id", "")
    title = first.get("title", "")
    print(f"[sheet] ⚠️  Tab '{sheet_name}' không tìm thấy "
          f"→ dùng sheet đầu tiên: '{title}' (id={sid})")
    _sheet_id_cache[sheet_name] = sid
    return sid


# ── 1. Fetch raw data từ Lark Sheets API ─────────────────────────────────────

def fetch_values(start_row: int, end_row: int) -> list[list]:
    """
    Lấy giá trị text của từng ô.
    Range dùng sheetId thật, vd: 0b1b2c!A1:BZ60
    """
    sheet_id  = _resolve_sheet_id()
    range_str = f"{sheet_id}!A{start_row}:BZ{end_row}"

    resp = requests.get(
        f"{LARK_API}/sheets/v3/spreadsheets/{SHEET_TOKEN}/values/{range_str}",
        headers=_auth_headers(),
        params={
            "valueRenderOption":    "ToString",
            "dateTimeRenderOption": "FormattedString",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Lấy values thất bại: {data}")
    return data.get("data", {}).get("valueRange", {}).get("values", [])


def fetch_styles(start_row: int, end_row: int) -> list[list]:
    """
    Lấy style (background color) qua Sheets API v2 /styles.

    Endpoint : GET /sheets/v2/spreadsheets/{token}/styles
    Param    : range = "sheetId!A{start}:BZ{end}"  (singular, không phải 'ranges')
    Response : data.valueRange.values[row][col] = {"style": {"backColor": "#FFCC00"}}

    Trả về styles_grid[row][col] = dict style (đã bóc lớp "style" ra).
    """
    sheet_id  = _resolve_sheet_id()
    range_str = f"{sheet_id}!A{start_row}:BZ{end_row}"

    resp = requests.get(
        f"{LARK_API}/sheets/v2/spreadsheets/{SHEET_TOKEN}/styles",
        headers=_auth_headers(),
        params={"range": range_str},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Lấy styles thất bại: {data}")

    raw_rows = data.get("data", {}).get("valueRange", {}).get("values", [])

    styles_grid: list[list] = []
    for row in raw_rows:
        style_row: list = []
        for cell in (row or []):
            if isinstance(cell, dict):
                style_row.append(cell.get("style") or {})
            else:
                style_row.append({})
        styles_grid.append(style_row)

    return styles_grid


# ── 2. Parse hàng 3 → map col_index → date ───────────────────────────────────

def parse_date_row(row: list) -> dict[int, date]:
    """row = list cell values từ hàng 3. Trả {col_idx: date}."""
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
    Trả hex color (#rrggbb) hoặc None nếu trắng/không màu.

    v2 format  : {"backColor": "#FFCC00", ...}
    v3 fallback: {"background_color": {"red": 0.98, "green": 0.90, "blue": 0.23}}
    """
    if not style:
        return None

    # ── v2: backColor là hex string ───────────────────────────────────────────
    back = style.get("backColor", "")
    if back:
        back = back.strip()
        if not back.startswith("#"):
            back = f"#{back}"
        # Expand shorthand #RGB → #RRGGBB
        if len(back) == 4:
            back = f"#{back[1]*2}{back[2]*2}{back[3]*2}"
        try:
            r = int(back[1:3], 16)
            g = int(back[3:5], 16)
            b = int(back[5:7], 16)
        except (ValueError, IndexError):
            return None
        if r >= 250 and g >= 250 and b >= 250:
            return None
        return f"#{r:02x}{g:02x}{b:02x}"

    # ── v3 fallback: RGBA dict ────────────────────────────────────────────────
    bg = style.get("background_color") or style.get("bg_color") or {}
    if not bg:
        return None
    r = round(bg.get("red",   1.0) * 255)
    g = round(bg.get("green", 1.0) * 255)
    b = round(bg.get("blue",  1.0) * 255)
    if r >= 250 and g >= 250 and b >= 250:
        return None
    return f"#{r:02x}{g:02x}{b:02x}"


# ── 4. Parse cell text → tên task + due date ─────────────────────────────────

def _parse_cell_text(text: str) -> tuple[str, Optional[str]]:
    """
    "CSL 6. Forests ~2'\\nDue 17/4"  →  ("CSL 6. Forests ~2'", "17/4")
    """
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    name  = lines[0] if lines else text.strip()
    due   = None
    for line in lines[1:]:
        m = re.search(r"[Dd]ue[:\s]+(\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?)", line)
        if m:
            due = m.group(1)
            break
    return name, due


# ── 5. Core: quét sheet → danh sách task trong tuần ──────────────────────────

def parse_tasks_for_week(
    week_start: date,
    week_end:   date,
    max_rows:   int = 60,
) -> list[dict]:
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
    print(f"[sheet] Đọc sheet '{SHEET_NAME}', rows 1–{max_rows}...")
    values = fetch_values(1, max_rows)
    styles = fetch_styles(1, max_rows)

    if len(values) < 3:
        raise ValueError("Sheet không đủ 3 hàng")

    date_row    = values[2]          # hàng 3 (0-indexed = 2)
    col_to_date = parse_date_row(date_row)

    if not col_to_date:
        raise ValueError("Không tìm thấy ngày ở hàng 3 — kiểm tra lại format ô")

    print(
        f"[sheet] {len(col_to_date)} cột ngày: "
        f"{min(col_to_date.values())} → {max(col_to_date.values())}"
    )

    tasks: list[dict] = []

    for row_idx in range(3, min(len(values), max_rows)):
        row_vals   = values[row_idx]
        row_styles = styles[row_idx] if row_idx < len(styles) else []

        assignee = str(row_vals[0]).strip() if row_vals else ""
        current_task: Optional[dict] = None

        for col_idx in sorted(col_to_date.keys()):
            cell_date  = col_to_date[col_idx]
            cell_text  = str(row_vals[col_idx]).strip() if col_idx < len(row_vals) else ""
            cell_style = row_styles[col_idx] if col_idx < len(row_styles) else None
            cell_color = _normalize_color(cell_style)

            if cell_color is None:
                if current_task:
                    tasks.append(current_task)
                    current_task = None
                continue

            if current_task and current_task["color"] == cell_color:
                current_task["dates"].append(cell_date)
                if cell_text and not current_task["name"]:
                    name, due = _parse_cell_text(cell_text)
                    current_task["name"] = name
                    if due:
                        current_task["due"] = due
            else:
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

    in_week = [
        t for t in tasks
        if any(week_start <= d <= week_end for d in t["dates"])
    ]

    print(
        f"[sheet] Tổng {len(tasks)} tasks, "
        f"{len(in_week)} tasks trong tuần {week_start} → {week_end}"
    )
    return in_week
