# Lark Weekly Bot

Bot tự động quét Lark Sheet mỗi chiều thứ 6, tổng hợp trạng thái task trong tuần và gửi báo cáo qua Lark Message.

## Cách hoạt động

1. **GitHub Actions** kích hoạt lúc 14:00 ICT mỗi thứ 6
2. **Sheet parser** đọc Lark Sheet, dùng màu nền ô để xác định task và timeline
3. **Task checker** match tên task → Lark Task API → lấy trạng thái `done / in_progress / todo`
4. **Message builder** tổng hợp thành Lark Interactive Card đẹp, nhóm theo assignee
5. Gửi card tới `TARGET_USER_ID`

## Cấu trúc repo

```
lark-weekly-bot/
├── .github/
│   └── workflows/
│       └── bot.yml          # GitHub Actions cron
├── bot.py                   # Entry point
├── lark_auth.py             # Token management + auto-refresh
├── sheet_parser.py          # Đọc sheet + parse màu ô
├── task_checker.py          # Lark Task API
├── message_builder.py       # Build Lark Card
├── get_refresh_token.py     # Script setup 1 lần (chạy local)
├── requirements.txt
└── README.md
```

## Setup

### Bước 1 — Tạo Lark App

Vào [Lark Developer Console](https://open.larksuite.com/) → tạo app mới → cấp các scopes:

| Scope | Loại token |
|---|---|
| `offline_access` | user |
| `sheets:spreadsheet:readonly` | user |
| `task:task:read` | user |
| `task:tasklist:read` | user |
| `im:message:send_as_bot` | tenant |
| `im:message` | tenant |
| `contact:user.id:readonly` | tenant |

**Security Settings → Redirect URLs**, thêm:
```
http://localhost:9999/callback
```

### Bước 2 — Lấy refresh_token (chạy 1 lần local)

```bash
git clone <repo>
cd lark-weekly-bot
pip install flask requests

export LARK_APP_ID=cli_xxxxxxxxxxxx
export LARK_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx
python get_refresh_token.py
```

Trình duyệt sẽ tự mở → authorize → copy `LARK_REFRESH_TOKEN` từ terminal.

### Bước 3 — Tạo GitHub repo và thêm Secrets

Vào **Settings → Secrets and variables → Actions**, thêm:

| Secret | Giá trị |
|---|---|
| `LARK_APP_ID` | `cli_xxxxxxxxxxxx` |
| `LARK_APP_SECRET` | secret của app |
| `LARK_REFRESH_TOKEN` | lấy từ bước 2 |
| `LARK_SHEET_TOKEN` | `EikqsZWIphkIGTtDxQIl6nSkg4f` |
| `LARK_SHEET_ID` | tên tab, vd `Production` |
| `TARGET_EMAIL` | email người nhận báo cáo, vd `minh@company.com` |
| `TARGET_USER_ID` | fallback nếu không dùng email — để trống nếu có `TARGET_EMAIL` |
| `GH_PAT` | GitHub Personal Access Token (scope: `repo`) |

### Bước 4 — Tạo GH_PAT

Vào GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens**

- Repository access: chọn repo này
- Permissions: `Secrets` → Read and write

### Bước 5 — Test thủ công

Vào **Actions tab → Lark Weekly Bot → Run workflow** để chạy thử ngay.

## Logic đọc màu sheet

| Ô | Nghĩa |
|---|---|
| Có màu + có text | Ô đầu của task — chứa tên và due date |
| Có màu + không text | Task kéo dài từ ngày trước (cùng màu, cùng hàng) |
| Trắng | Không có task |

Hàng 3 của sheet phải chứa ngày tháng theo format: `14-Apr`, `Apr 14`, `14/04`, v.v.

## Auto-refresh token

- `app_access_token`: tự renew từ `app_id` + `app_secret`, cache 2h
- `user_access_token`: refresh bằng `refresh_token` (hạn 30 ngày)
- Sau mỗi lần refresh, `LARK_REFRESH_TOKEN` mới được tự động ghi đè vào GitHub Secrets → bot chạy mãi không cần can thiệp
