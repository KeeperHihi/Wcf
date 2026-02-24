from pywinauto.keyboard import send_keys
import win32clipboard
import win32con
from PIL import Image
import io
import re
import random
import time


def _escape_send_keys_char(ch: str) -> str:
    if ch == "\r":
        return ""
    if ch == "\n":
        return "^{ENTER}"  # Ctrl+Enter: 换行但不发送
    if ch == "\t":
        return "{TAB}"
    if ch == "{":
        return "{{}"
    if ch == "}":
        return "{}}"
    if ch in "+^%~()":
        return "{" + ch + "}"
    return ch


def type_text_humanlike(
    text: str,
    *,
    with_enter: bool = False,
    min_interval: float = 0.02,
    max_interval: float = 0.12,
):
    """模拟人类键入：逐字符输入 + 随机间隔，不使用剪贴板。"""
    if not text:
        if with_enter:
            send_keys("{ENTER}", pause=0, with_spaces=True)
        return

    low = max(0.0, float(min_interval))
    high = max(low, float(max_interval))
    for ch in str(text):
        seq = _escape_send_keys_char(ch)
        if seq:
            send_keys(seq, pause=0, with_spaces=True)
        time.sleep(random.uniform(low, high))

    if with_enter:
        time.sleep(random.uniform(low, high))
        send_keys("{ENTER}", pause=0, with_spaces=True)

def set_clipboard_text(text: str) -> None:
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    except Exception as e:
        print(f'设置文字进入剪贴板时发生异常：{e}')
    finally:
        win32clipboard.CloseClipboard()

def set_clipboard_image(image_path: str) -> None:
    image = Image.open(image_path)
    if image.mode != "RGB":
        image = image.convert("RGB")
    output = io.BytesIO()
    image.save(output, "BMP")
    data = output.getvalue()[14:]
    output.close()
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_DIB, data)
    except Exception as e:
        print(f'设置图片进入剪贴板时发生异常：{e}')
    finally:
        win32clipboard.CloseClipboard()

def paste_text(text, with_enter=False, pause=0):
    set_clipboard_text(text)
    if with_enter:
        send_keys("^v{ENTER}", pause=pause, with_spaces=True)
    else:
        send_keys("^v", pause=pause, with_spaces=True)

def paste_image(image_path, with_enter=False, pause=0):
    set_clipboard_image(image_path)
    if with_enter:
        send_keys("^v{ENTER}", pause=pause, with_spaces=True)
    else:
        send_keys("^v", pause=pause, with_spaces=True)

def zip_text(text: str, max_len: int=40) -> str:
    s = "".join(c for c in text if c != "\n")
    return s if len(s) < max_len else f"“{s[:10]}......{s[-10:]}”"

def clean_name(text: str) -> str:
    text = (text or "").replace("已置顶", "").strip()
    return re.sub(r"\d+条新消息$", "", text).strip()

def analysis_name(text: str) -> tuple[str, bool, int]:
    s = (text or "").strip()
    # 1) 是否置顶
    is_pinned = "已置顶" in s
    s = s.replace("已置顶", "").strip()
    # 2) 新消息数量
    m = re.search(r"(\d+)条新消息$", s)
    new_msg_cnt = int(m.group(1)) if m else 0
    s = re.sub(r"\d+条新消息$", "", s).strip()
    # 3) 本名
    name = s
    return name, is_pinned, new_msg_cnt

def ZIP(content: str) -> str:
    s = content.replace('\n', '')
    if len(s) < 40:
        return content
    return f'“{s[:10]}......{s[-10:]}”'


def print_descendants(item):
    for d in item.descendants():
        ei = d.element_info
        print(ei.control_type, repr(ei.name), repr(ei.automation_id), ei.rectangle)
    print()


def print_rect(rect):
    print(f"RECT: [{rect.left}, {rect.top}, {rect.right}, {rect.bottom}]")


def flash_rect(rect, color=0x0000FF, width=3, times=3, on_ms=120, off_ms=80):
    """
    Draw an XOR rectangle on the screen to visualize where `rect` is.
    rect: pywinauto rectangle (has left/top/right/bottom)
    color: 0x00BBGGRR (Win32 COLORREF). 0x0000FF = red.
    """
    import time
    import win32con
    import win32gui
    l, t, r, b = int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    hdc = win32gui.GetDC(0)  # desktop DC
    try:
        pen = win32gui.CreatePen(win32con.PS_SOLID, width, color)
        brush = win32gui.GetStockObject(win32con.NULL_BRUSH)
        win32gui.SetROP2(hdc, win32con.R2_NOTXORPEN)  # XOR so drawing twice erases
        old_pen = win32gui.SelectObject(hdc, pen)
        old_brush = win32gui.SelectObject(hdc, brush)
        try:
            for _ in range(times):
                win32gui.Rectangle(hdc, l, t, r, b)  # draw
                time.sleep(on_ms / 1000)
                win32gui.Rectangle(hdc, l, t, r, b)  # erase
                time.sleep(off_ms / 1000)
        finally:
            win32gui.SelectObject(hdc, old_pen)
            win32gui.SelectObject(hdc, old_brush)
            win32gui.DeleteObject(pen)
    finally:
        win32gui.ReleaseDC(0, hdc)