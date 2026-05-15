"""send_and_dump.py — Gửi message tự động vào LINE và dump memory sau mỗi message.

Yêu cầu:
    pip install pyautogui pyperclip pywin32

Cách chạy (từ VS Code terminal hoặc bất kỳ terminal nào):
    # Bước 1: Lấy tọa độ input box của LINE
    python send_and_dump.py --calibrate

    # Bước 2: Chạy thật
    python send_and_dump.py --input ground_truth.txt --limit 10 --input-x 682 --input-y 568
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# ── Kiểm tra platform ──────────────────────────────────────────────────────────
if sys.platform != "win32":
    print("[ERROR] Script này chỉ chạy trên Windows (trong VM).")
    sys.exit(1)

try:
    import pyautogui
    import pyperclip
except ImportError:
    print("[ERROR] Thiếu thư viện. Chạy: pip install pyautogui pyperclip pygetwindow pywin32")
    sys.exit(1)

try:
    import win32gui
    import win32con
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False
    print("[WARN] pywin32 chưa cài. Focus cửa sổ sẽ kém chính xác hơn.")
    print("       Cài: pip install pywin32\n")

# ── Config ────────────────────────────────────────────────────────────────────
DUMP_DIR = Path("./dumps")
LOG_FILE = Path("./send_dump_log.txt")

WAIT_AFTER_SEND   = 3.0   # chờ sau khi gửi message (giây)
WAIT_BETWEEN_MSG  = 2.0   # delay giữa các message
WAIT_AFTER_FOCUS  = 0.5   # chờ sau khi focus cửa sổ

PROCDUMP_PATH = r".\procdump.exe"
WINPMEM_PATH  = r".\winpmem64.exe"

# ── Tìm và focus cửa sổ LINE ──────────────────────────────────────────────────

def find_line_window() -> int | None:
    """Tìm HWND của cửa sổ LINE Messenger."""
    if not HAS_WIN32:
        return None

    found = []

    def enum_callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        # LINE window title thường là tên chat hoặc "LINE"
        if "LINE" in title or "Line" in title:
            found.append((hwnd, title))

    win32gui.EnumWindows(enum_callback, None)

    if not found:
        return None

    # Ưu tiên cửa sổ có title chính xác là "LINE"
    for hwnd, title in found:
        if title == "LINE":
            return hwnd

    # Fallback: lấy cửa sổ LINE đầu tiên tìm được
    return found[0][0]


def focus_line_window(hwnd: int) -> bool:
    """Focus cửa sổ LINE."""
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.3)
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(WAIT_AFTER_FOCUS)
        return True
    except Exception:
        return False


# ── Gửi message vào LINE ──────────────────────────────────────────────────────

def send_message_to_line(
    text: str,
    input_x: int,
    input_y: int,
    hwnd: int | None,
) -> None:
    """
    Gửi text vào LINE:
    1. Focus cửa sổ LINE
    2. Click vào input box
    3. Paste text từ clipboard
    4. Nhấn Enter
    """
    # 1. Focus lại cửa sổ LINE trước mỗi lần gửi
    if hwnd and HAS_WIN32:
        focus_line_window(hwnd)

    # 2. Click vào input box để đảm bảo focus
    pyautogui.click(input_x, input_y)
    time.sleep(0.3)

    # 3. Copy text vào clipboard và paste
    pyperclip.copy(text)
    time.sleep(0.2)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.4)

    # 4. Gửi
    pyautogui.press("enter")
    time.sleep(WAIT_AFTER_SEND)

    # 5. Xóa clipboard để tránh leak dữ liệu
    pyperclip.copy("")

# ── Dump memory ───────────────────────────────────────────────────────────────

def find_line_pid() -> int:
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq LINE.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True
    )
    for line in result.stdout.strip().splitlines():
        parts = line.strip('"').split('","')
        if len(parts) >= 2:
            try:
                return int(parts[1])
            except ValueError:
                continue
    raise RuntimeError("Không tìm thấy process LINE.exe. Đảm bảo LINE đang chạy.")


def dump_process_procdump(pid: int, output_path: str) -> bool:
    import os
    
    if os.path.exists(output_path):
        try:
            os.remove(output_path)
        except OSError:
            pass
            
    cmd = [PROCDUMP_PATH, "-ma", str(pid), output_path]
    result = subprocess.run(cmd, timeout=60)
    
    if os.path.exists(output_path) and os.path.getsize(output_path) > 10 * 1024 * 1024:
        return True
        
    print(f"    [WARN] procdump báo lỗi: {result.returncode}. Không tìm thấy file dump hợp lệ.")
    return False


def dump_full_ram_winpmem(output_path: str) -> bool:
    cmd = [WINPMEM_PATH, "acquire", "--nosparse", output_path]
    subprocess.run(cmd, timeout=900)
    return os.path.isfile(output_path) and os.path.getsize(output_path) > 0

# ── Load messages ─────────────────────────────────────────────────────────────

def load_messages(input_file: str, limit: int | None = None) -> list[str]:
    path = Path(input_file)
    if not path.is_file():
        raise FileNotFoundError(f"Không tìm thấy file: {input_file}")

    content = path.read_text(encoding="utf-8", errors="ignore")

    if input_file.endswith(".csv"):
        import csv, io
        messages = []
        reader = csv.DictReader(io.StringIO(content))
        for row in reader:
            text = row.get("text") or row.get("review_body") or row.get("review") or ""
            text = text.strip()
            if text:
                messages.append(text)
    else:
        messages = [l.strip() for l in content.splitlines() if l.strip()]

    if limit:
        messages = messages[:limit]

    print(f"  Loaded {len(messages)} messages từ {input_file}")
    return messages

# ── Calibration helper ────────────────────────────────────────────────────────

def calibrate_input_box() -> tuple[int, int]:
    """
    Di chuyển chuột đến input box của LINE rồi nhấn Enter để lấy tọa độ.
    Dùng khi --calibrate được truyền vào.
    """
    print("\n=== CALIBRATION MODE ===")
    print("1. Di chuyển chuột đến INPUT BOX của LINE (ô nhập tin nhắn)")
    print("2. Giữ yên chuột")
    print("3. Nhấn Ctrl+C sau 5 giây để lưu tọa độ\n")

    try:
        for i in range(5, 0, -1):
            x, y = pyautogui.position()
            print(f"  {i}s — Vị trí chuột hiện tại: ({x}, {y})", end="\r")
            time.sleep(1)
        x, y = pyautogui.position()
        print(f"\n  → Tọa độ input box: ({x}, {y})")
        print(f"  Thêm vào lệnh: --input-x {x} --input-y {y}")
        return x, y
    except KeyboardInterrupt:
        x, y = pyautogui.position()
        print(f"\n  → Tọa độ input box: ({x}, {y})")
        print(f"  Thêm vào lệnh: --input-x {x} --input-y {y}")
        return x, y

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gửi message vào LINE và dump memory sau mỗi message"
    )
    parser.add_argument("--input",      help="File messages (.txt hoặc .csv)")
    parser.add_argument("--limit",      type=int, help="Giới hạn số message")
    parser.add_argument("--dumper",     default="winpmem",
                        choices=["procdump", "winpmem"])
    parser.add_argument("--start-from", type=int, default=1,
                        help="Bắt đầu từ message thứ N (resume support)")
    parser.add_argument("--pid",        type=int, help="PID LINE (tự tìm nếu không cung cấp)")
    parser.add_argument("--input-x",    type=int, default=635,
                        help="Tọa độ X của input box LINE (default: 635)")
    parser.add_argument("--input-y",    type=int, default=573,
                        help="Tọa độ Y của input box LINE (default: 573)")
    parser.add_argument("--calibrate",  action="store_true",
                        help="Chế độ calibration: tìm tọa độ input box rồi thoát")
    args = parser.parse_args()

    # ── Calibration mode ──────────────────────────────────────────────────────
    if args.calibrate:
        calibrate_input_box()
        return

    if not args.input:
        parser.error("--input là bắt buộc (trừ khi dùng --calibrate)")

    # ── Tạo thư mục ──────────────────────────────────────────────────────────
    DUMP_DIR.mkdir(exist_ok=True)

    # ── Tìm cửa sổ LINE ──────────────────────────────────────────────────────
    hwnd = None
    if HAS_WIN32:
        print("Tìm cửa sổ LINE...")
        hwnd = find_line_window()
        if hwnd:
            title = win32gui.GetWindowText(hwnd)
            print(f"  → Tìm thấy: HWND={hwnd}, Title='{title}'")
        else:
            print("  [WARN] Không tìm thấy cửa sổ LINE qua win32gui.")
            print("         Đảm bảo LINE đang mở và hiển thị trên màn hình.")

    # ── Tọa độ input box ─────────────────────────────────────────────────────
    input_x = args.input_x  # default: 635
    input_y = args.input_y  # default: 573
    print(f"  → Input box: ({input_x}, {input_y})")

    # ── Tìm PID ──────────────────────────────────────────────────────────────
    pid = args.pid
    if not pid:
        print("\nTìm PID LINE.exe...")
        pid = find_line_pid()
        print(f"  → PID: {pid}")

    # ── Load messages ─────────────────────────────────────────────────────────
    print(f"\nLoad messages từ {args.input}...")
    messages = load_messages(args.input, limit=args.limit)

    start_idx = args.start_from - 1
    messages  = messages[start_idx:]

    if not messages:
        print("Không có message nào để gửi.")
        return

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"Sẽ gửi   : {len(messages)} messages")
    print(f"PID LINE  : {pid}")
    print(f"Input box : ({input_x}, {input_y})")
    print(f"Dumper    : {args.dumper}")
    print(f"{'='*50}")
    print("\nĐảm bảo:")
    print("  1. LINE đang mở, chat box đang hiển thị")
    print("  2. KHÔNG di chuyển chuột hoặc click trong khi script chạy")
    print(f"  3. {'procdump.exe' if args.dumper == 'procdump' else 'winpmem64.exe'} có trong PATH")
    print("\nBắt đầu sau 5 giây... (Ctrl+C để huỷ)")
    time.sleep(5)

    # ── Vòng lặp gửi + dump ───────────────────────────────────────────────────
    log_entries   = []
    gt_lines      = []
    success_count = 0
    total         = len(messages)

    for i, msg in enumerate(messages, start=start_idx + 1):
        idx_str   = f"{i:04d}"
        ext       = "raw" if args.dumper == "winpmem" else "dmp"
        dump_name = f"msg_{idx_str}.{ext}"
        dump_path = str(DUMP_DIR / dump_name)

        print(f"\n[{i}/{start_idx + total}] ({len(msg)} chars) {msg[:70]}{'...' if len(msg) > 70 else ''}")

        # Gửi message
        try:
            send_message_to_line(msg, input_x, input_y, hwnd)
            print(f" Đã gửi")
        except Exception as e:
            print(f"  Lỗi khi gửi: {e}")
            log_entries.append(f"[{idx_str}] SEND_ERROR: {e}")
            continue

        # Dump memory
        print(f"  → Dump memory → {dump_name}...")
        dump_ok = False
        try:
            if args.dumper == "procdump":
                dump_ok = dump_process_procdump(pid, dump_path)
            else:
                dump_ok = dump_full_ram_winpmem(dump_path)
        except Exception as e:
            print(f"  Lỗi dump: {e}")
            log_entries.append(f"[{idx_str}] DUMP_ERROR: {e}")

        if dump_ok:
            size_mb = os.path.getsize(dump_path) / 1024 / 1024
            print(f" Dump xong ({size_mb:.1f} MB)")
            gt_lines.append(msg)
            log_entries.append(f"[{idx_str}] OK dump={dump_name} size={size_mb:.1f}MB")
            success_count += 1
        else:
            print(f" Dump thất bại")
            log_entries.append(f"[{idx_str}] DUMP_FAILED msg={msg[:50]}")

        if i < start_idx + total:
            time.sleep(WAIT_BETWEEN_MSG)

    # ── Lưu kết quả ──────────────────────────────────────────────────────────
    LOG_FILE.write_text("\n".join(log_entries), encoding="utf-8")

    print(f"\n{'='*50}")
    print(f"Kết quả   : {success_count}/{total} message dump thành công")
    print(f"Log          → {LOG_FILE}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()