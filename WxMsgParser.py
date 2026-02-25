import base64
import io
import os
import re
from dataclasses import dataclass
from typing import Optional, List
from PIL import Image, ImageGrab

try:
    from .WxMsg import WxMsg
except ImportError:
    from WxMsg import WxMsg


class WxMsgParser:
    """
    类型：
      0 = 文本
      1 = 图片
      2 = 视频 (ignored)
      3 = 表情/动态表情
      -1 = 其他
      -2 = 假消息
    """

    def __init__(self):
        self.BRACKET = re.compile(r"^\[[^\]]+\]$")
        self.TIME_ONLY = re.compile(r"^\d{1,2}:\d{2}$")
        self.DATE_ONLY = re.compile(
            r"^(?:\d{2,4}[/-]\d{1,2}[/-]\d{1,2}|\d{2,4}\.\d{1,2}\.\d{1,2}|\d{1,2}月\d{1,2}日)$"
        )
        self.DATE_LABELS = {"昨天", "前天", "今天", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"}

    def parse_single_msg(self, item) -> Optional[WxMsg]:
        try:
            raw_title = self._safe_text(item)
            msg_type = self._detect_type(raw_title)
            if msg_type == -2:
                return None
            if msg_type == 0:
                return self.get_msg_from_text(item)
            if msg_type == 1:
                return self.get_msg_from_image(item)
            if msg_type == 2:
                return self.get_msg_from_video(item)  # None
            if msg_type == 3:
                return self.get_msg_from_emoji(item)
            return self.get_msg_from_other(item)

        except Exception as e:
            print(f"[消息解析失败]：{e}")
            return None

    def _is_date_separator_text(self, text: str) -> bool:
        t = (text or "").strip()
        if not t:
            return False
        if t in self.DATE_LABELS:
            return True
        if self.TIME_ONLY.fullmatch(t):
            return True
        if self.DATE_ONLY.fullmatch(t):
            return True
        # 例如：昨天 18:15 / 今天 09:01
        if re.fullmatch(r"^(昨天|前天|今天)\s+\d{1,2}:\d{2}$", t):
            return True
        return False

    def _detect_type(self, text: str) -> int:
        t = (text or "").strip()
        if self._is_date_separator_text(t):
            return -2
        if t == "[图片]":
            return 1
        if t == "[视频]":
            return 2
        if t in ("[动画表情]", "[表情]"):
            return 3
        if self.BRACKET.fullmatch(t):
            return -1
        return 0

    def get_msg_from_text(self, item) -> Optional[WxMsg]:
        text = self._safe_text(item)
        if not text:
            print("[消息解析失败] get_msg_from_text：空消息")
            return None
        return WxMsg(
            type=0,
            content=text
        )

    def get_msg_from_image(self, item) -> Optional[WxMsg]:
        data_url = self._image_from_clipboard_to_data_url()
        if not data_url:
            print("[消息解析失败] get_msg_from_image：图片不在剪切板")
            return None
        return WxMsg(
            type=1,
            content=data_url
        )

    def get_msg_from_video(self, item) -> Optional[WxMsg]:
        # TODO: 暂时忽略视频
        return WxMsg(
            type=2,
            content="这是一个视频，暂时无法解析",
            is_meaningful=False
        )

    def get_msg_from_emoji(self, item) -> Optional[WxMsg]:
        # TODO: 暂时忽略表情
        return WxMsg(
            type=3,
            content="这是一个表情，暂时无法解析",
            is_meaningful=False
        )

    def get_msg_from_other(self, item) -> Optional[WxMsg]:
        # TODO: 暂时忽略其他类型
        return WxMsg(
            type=-1,
            content="这是一个未知数据，暂时无法解析",
            is_meaningful=False
        )

    def _safe_text(self, ctrl) -> str:
        try:
            return (ctrl.window_text() or "").strip()
        except Exception:
            return ""

    def _extract_all_texts(self, item) -> List[str]:
        out = []
        own = self._safe_text(item)
        if own:
            out.append(own)

        descendants = []
        try:
            descendants = item.descendants()
        except Exception:
            pass

        for c in descendants:
            t = self._safe_text(c)
            if t:
                out.append(t)

        seen = set()
        uniq = []
        for t in out:
            if t not in seen:
                seen.add(t)
                uniq.append(t)
        return uniq

    def _join_meaningful(self, texts: List[str]) -> str:
        if not texts:
            return ""

        time_re = re.compile(r"^\d{1,2}:\d{2}$|^\d{2}/\d{1,2}/\d{1,2}$")
        filtered = []
        for t in texts:
            x = t.replace("\ufeff", " ").strip()
            if not x:
                continue
            if time_re.match(x):
                continue
            if x in ("[图片]", "[视频]", "[表情]", "[动画表情]"):
                continue
            filtered.append(x)

        return "\n".join(filtered).strip()

    def _image_from_clipboard_to_data_url(self) -> Optional[str]:
        if ImageGrab is None:
            return None
        try:
            clip = ImageGrab.grabclipboard()
            if clip is None:
                return None
            # ~/Documents/WeChat Files/wxid_xxx/FileStorage/Temp/abcd.jpg
            if isinstance(clip, list):
                if not clip:
                    return None
                path = clip[0]
                if not isinstance(path, str) or not os.path.isfile(path):
                    return None
                im = Image.open(path)
                im.load()
            else:
                im = clip
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return f"data:image/png;base64,{b64}"
        except Exception:
            return None
