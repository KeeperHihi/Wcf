import hashlib
from utils import zip_text

class WxMsg:
    def __init__(
            self,
            type = 5,
            sender = "",
            roomid = "",
            content = "",
            is_meaningful = True
    ) -> None:
        self.type = type       # 0->文本; 1->图片; 2->视频; 3->表情; -1->链接等其他，TODO: 暂时只支持文本和图片
        self.sender = sender
        self.roomid = roomid
        self.content = content
        self.is_meaningful = is_meaningful
        self.hash_id = self._build_hash_id()

    def _signature(self):
        return (
            self.type,
            self.sender,
            self.roomid,
            self.content,
            self.is_meaningful,
        )

    def _build_hash_id(self) -> str:
        raw = "|".join(str(part) for part in self._signature())
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def __eq__(self, other):
        if isinstance(other, WxMsg):
            return self._signature() == other._signature()
        return False

    def __hash__(self):
        return hash(self._signature())

    def from_group(self) -> bool:
        raise NotImplementedError()

    def is_at(self, wxid: str) -> bool:
        raise NotImplementedError()

    def show(self):
        print(f'type: {self.type} | sender: {self.sender} | roomid: {self.roomid} | content: {zip_text(self.content)} | hash_id: {self.hash_id}')