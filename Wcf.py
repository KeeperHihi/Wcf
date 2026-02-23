import queue
from pywinauto.application import Application
import os
import random
import time
from threading import Lock, Event, Thread
from pywinauto.controls.uiawrapper import UIAWrapper
from pywinauto import mouse
import traceback

try:
    from .utils import *
    from .WxMsg import WxMsg
    from .MxMessageParser import MxMessageParser
except ImportError:
    from utils import *
    from WxMsg import WxMsg
    from MxMessageParser import MxMessageParser

class Wcf:
    def __init__(self):
        print("Application")
        self.app = Application(backend="uia").connect(path="WeChat.exe")

        print("Window")
        self.win = self.app.window(title="微信", control_type="Window")

        print("Regular Expressions")
        self._GROUP_RE = re.compile(r"^(?P<name>.*?)(?:\s*\((?P<count>\d+)\))?$")

        print("parameters")
        self.wx_name = "hihi"
        self.default_chat_name = "文件传输助手" # TODO: 使用时建议保证只有 default_chat_name 是置顶的，不然可能会出bug
        self.listen_cnt = 5
        self.eps = 0.01
        self.memory_len = 10
        self.max_new_msg_cnt = 4
        self.listen_msg_interval = 1 # 收集新消息的时间间隔
        self.enable_image_parse = True

        print("Other compositions")
        self.chat = self.win.child_window(title="聊天", control_type="Button").wrapper_object()
        self.friend_list = self.win.child_window(title="通讯录", control_type="Button").wrapper_object()
        self.search = self.win.child_window(title="搜索", control_type="Edit").wrapper_object()
        self.message_parser = MxMessageParser()
        self.conv_list = self.win.child_window(title="会话", control_type="List")
        self.msg_list = self.win.child_window(title="消息", control_type="List")

        print("Init")
        self.stay_focus()
        self.init()


        print("Runtime elements")
        self.wx_lock = Lock()
        self.current_chat_name, self.is_room, self.room_member_cnt = self.get_current_chat_and_is_group()
        print(f'初始会话对象：{self.current_chat_name}, 是否为群聊：{self.is_room}, 有几人：{self.room_member_cnt}')
        self.msg_cache = {} # name -> [WxMsg]
        self.new_msg_queue = queue.Queue()
        self.new_msg_queue_lock = Lock()
        self.recv_stop_event = Event()
        self.recv_thread: Thread | None = None

        print("Init finished")

    def wait_a_little_while(self):
        delta = self.eps / 10
        low = max(0.0, self.eps - delta)
        high = max(low, self.eps + delta)
        time.sleep(random.uniform(low, high))

    def stay_focus(self):
        self.win.set_focus()
        self.wait_a_little_while()

    def init(self):
        self.chat.click_input()
        self.wait_a_little_while()

    def get_current_chat_and_is_group(self):
        """
        return: (chat_name, is_group, group_count_or_None)
        """

        # 1) 锚点：标题栏的“聊天信息”
        info_btn = self.win.child_window(title="聊天信息", control_type="Button")
        if not info_btn.exists(timeout=self.eps):
            return self.default_chat_name, False, None  # 只有文件传输助手才没有聊天信息
        # 2) 找到包含标题文本的那层容器（通常 parent 就够；不够就往上爬几层）
        info_btn = info_btn.wrapper_object()
        bar = info_btn.parent()

        texts = None
        for _ in range(3):
            try:
                texts = bar.descendants(control_type="Text")  # 只取直接 children，别用 descendants
                # 标题栏里一般至少有 1 个 Text（会话名）
                if texts:
                    break
                bar = bar.parent()
            except Exception as e:
                print('获取当前会话对象名称失败或者无会话对象')
                return None, False, None
        if not texts:
            return None, False, None
        title_text = texts[0].window_text()
        # title_text 可能是 "xxx (3)" 或 "xxx"
        m = self._GROUP_RE.match(title_text)
        if not m:
            return title_text, False, None
        name = (m.group("name") or "").strip()
        count = m.group("count")
        is_room = count is not None
        return name, is_room, (int(count) if count else None)

    def switch_to_sb(self, name):
        # 调用时请确保已经 stay_focus 并且 init
        name = clean_name(name)
        # if self.current_chat_name == name: # 牺牲一点效率，换来把小红点点掉
        #     return
        exist_names = self.conv_list.children(control_type="ListItem")[:self.listen_cnt]
        for exist_name in exist_names:
            cln_name, _, _ = analysis_name(exist_name.window_text())
            if cln_name == name:
                exist_name.click_input()
                self.wait_a_little_while()
                self.current_chat_name, self.is_room, self.room_member_cnt = self.get_current_chat_and_is_group()
                return
        self.search.click_input()
        self.wait_a_little_while()
        paste_text(name, with_enter=True)
        self.wait_a_little_while()
        search_result = self.win.child_window(title="@str:IDS_FAV_SEARCH_RESULT:3780", control_type="List")
        first_result = search_result.child_window(title=name, control_type="ListItem", found_index=0).wrapper_object()
        first_result.click_input()
        self.wait_a_little_while()
        self.current_chat_name, self.is_room, self.room_member_cnt = self.get_current_chat_and_is_group()

    def get_friends(self):
        with self.wx_lock:
            self.stay_focus()
            self.friend_list.click_input()
            self.wait_a_little_while()

            contacts = self.win.child_window(title="联系人", control_type="List")
            if not contacts.exists(timeout=self.eps):
                return []
            contacts = contacts.wrapper_object()

            skip_names = {
                "新的朋友",
                "公众号",
                "群聊",
                "标签",
                "企业微信联系人",
                "通讯录管理",
            }
            friends = []
            seen = set()

            last_signature = None

            try:
                items = contacts.children(control_type="ListItem")
                if not items:
                    raise RuntimeError("联系人列表为空")
                items[0].click_input()
            except Exception as e:
                traceback.print_exc()
                print("聚焦通讯录失败！！！", e)
                self.init()
                return []
            self.wait_a_little_while()
            send_keys("{HOME}", with_spaces=True)
            self.wait_a_little_while()

            while True:
                items = contacts.children(control_type="ListItem")
                visible_names = []
                for item in items:
                    try:
                        name = clean_name(item.window_text())
                    except Exception:
                        continue
                    if name:
                        visible_names.append(name)
                    if not name or name in skip_names or re.fullmatch(r"[A-Z#]", name):
                        continue
                    if name not in seen:
                        seen.add(name)
                        friends.append(name)

                signature = visible_names[-1] if visible_names else None
                if signature == last_signature:
                    break
                last_signature = signature
                send_keys("{PGDN}", with_spaces=True)
                self.wait_a_little_while()
            send_keys("{HOME}", with_spaces=True)
            self.wait_a_little_while()
            self.init()
            return friends
        

    def jump_to_top_of_chatlist(self):
        return # TODO: 被动接受消息，理论上一直会在最上面呆着，所以暂时不做处理
        self.switch_to_sb(self.default_chat_name)

    def send_text(self, text: str, receiver: str) -> int:
        with self.wx_lock:
            self.stay_focus()
            receiver = clean_name(receiver)
            try:
                self.switch_to_sb(receiver)
                paste_text(text, with_enter=True)
                self.wait_a_little_while()
                self.add_new_msg(receiver, WxMsg(
                    type=0,
                    sender=self.wx_name,
                    roomid=self.current_chat_name if self.is_room else None,
                    content=text,
                    is_meaningful=True,
                ))
                return 0
            except Exception as e:
                print(f"发送文字时报错：{e}")
                return 1

    def send_image(self, path: str, receiver: str) -> int:
        with self.wx_lock:
            self.stay_focus()
            receiver = clean_name(receiver)
            try:
                if not os.path.exists(path):
                    print('发送的图片路径不存在')
                    return 1
                self.switch_to_sb(receiver)
                paste_image(path, with_enter=True)
                self.wait_a_little_while()
                if self.enable_image_parse:
                    img_msg = self.message_parser.get_msg_from_image(None)
                    if img_msg:
                        img_msg.sender = self.wx_name
                        img_msg.roomid = self.current_chat_name if self.is_room else None
                        self.add_new_msg(receiver, img_msg)
                else:
                    self.add_new_msg(receiver, WxMsg(
                        type=1,
                        sender=self.wx_name,
                        roomid=self.current_chat_name if self.is_room else None,
                        content="这是一张图片，用户未开启图片解析功能，所以无法解析。",
                        is_meaningful=False,
                    ))

                return 0
            except Exception as e:
                print(f"发送图片时报错：{e}")
                return 1

    def get_msg(self, timeout=1.0):
        try:
            new_msg_name = self.new_msg_queue.get(timeout=timeout)
        except queue.Empty:
            return None, None
        with self.new_msg_queue_lock:
            return new_msg_name, self.msg_cache.get(new_msg_name, [None])[-1]

    def get_msg_list(self, timeout=1.0):
        try:
            new_msg_name = self.new_msg_queue.get(timeout=timeout)
        except queue.Empty:
            return None, None
        with self.new_msg_queue_lock:
            return new_msg_name, list(self.msg_cache.get(new_msg_name, []))

    def is_msg_from_me(self, msg: WxMsg) -> bool:
        if msg is None:
            return False
        return msg.sender == self.wx_name

    def parse_single_msg(self, item):
        # print_descendants(item)
        # print('\n')
        if not item.is_visible():
            return None
        btns = item.descendants(control_type="Button")
        try:
            sender = next((b.element_info.name for b in btns if b.element_info.name), "")
            if not sender:
                return None
        except Exception as e:
            return None
        if item.window_text() == "[图片]":
            if not self.enable_image_parse:
                res = WxMsg(type=1, content="这是一张图片，用户未开启图片解析功能，所以无法解析。", is_meaningful=False, sender=sender)
                return res
            if not isinstance(item, UIAWrapper):
                item = item.wrapper_object()
            # 扭曲的找图片方法
            # 1) 找所有 Button
            try:
                # 2) 筛掉有名字的头像按钮，保留 name 为空的按钮
                btn = next((b for b in btns if not b.element_info.name), None)
                if btn is None:
                    return None
            except Exception as e:
                return None
            rect = btn.rectangle()
            x = int((rect.left + rect.right) / 2)
            y = int((rect.top + rect.bottom) / 2)
            btn.click_input(button="right")
            self.wait_a_little_while()
            mouse.click(button="left", coords=(x + 10, y + 10)) # 要求复制必须是第一个选项
            self.wait_a_little_while()
        res = self.message_parser.parse_single_msg(item)
        if res is not None:
            res.sender = sender
            res.roomid = self.current_chat_name if self.is_room else None
        return res

    def get_latest_n_msg(self, n=1):
        msg_list = self.msg_list
        if not msg_list.exists(timeout=self.eps):
            return None
        msg_list = msg_list.wrapper_object()
        items = msg_list.children(control_type="ListItem")
        if not items:
            print(f"当前会话消息为空")
            return None
        msgs = []
        for it in reversed(items):
            if len(msgs) >= n:
                break
            try:
                res = self.parse_single_msg(it)
                if res:
                    msgs.append(res)
            except Exception as e:
                print(e)
                continue
        msgs.reverse()
        return msgs

    def is_new_msg(self, name, msg):
        if name not in self.msg_cache:
            self.msg_cache[name] = []
            return True
        if msg not in self.msg_cache[name]:
            return True
        return False

    def add_new_msg(self, name, msg):
        if name not in self.msg_cache:
            self.msg_cache[name] = []
        self.msg_cache[name].append(msg)

    def check_memory_len(self, name):
        if name not in self.msg_cache:
            self.msg_cache[name] = []
        while len(self.msg_cache[name]) > self.memory_len:
            self.msg_cache[name].pop(0)

    def get_latest_msg_in_cache(self, name):
        if name not in self.msg_cache:
            self.msg_cache[name] = []
        if len(self.msg_cache[name]) == 0:
            return None
        return self.msg_cache[name][-1]

    def get_new_msgs_from_person(self, new_msg_name, possible_new_msg_cnt):
        self.switch_to_sb(new_msg_name)
        possible_new_msgs = self.get_latest_n_msg(n=min(possible_new_msg_cnt, self.max_new_msg_cnt))
        if not possible_new_msgs:
            return
        is_new_msg = False
        latest_cached_msg = self.get_latest_msg_in_cache(new_msg_name)
        for possible_new_msg in possible_new_msgs:
            if possible_new_msg == None:
                continue
            # print('hahahaha')
            # if len(self.msg_cache.get(new_msg_name, [])) != 0:
            #     self.get_latest_msg_in_cache(new_msg_name).show()
            # else:
            #     print(f'empty')
            # possible_new_msg.show()
            # print('ge', self.get_latest_msg_in_cache(new_msg_name) == possible_new_msg)
            if latest_cached_msg and latest_cached_msg == possible_new_msg:
                break
            if not self.is_new_msg(new_msg_name, possible_new_msg):
                continue
            print("新消息！！！")
            possible_new_msg.show()
            self.add_new_msg(new_msg_name, possible_new_msg)
            self.check_memory_len(new_msg_name)
            latest_cached_msg = possible_new_msg
            is_new_msg = True
        latest_msg = self.get_latest_msg_in_cache(new_msg_name)
        if is_new_msg and latest_msg and not self.is_msg_from_me(latest_msg):
            with self.new_msg_queue_lock:
                print(f"{new_msg_name}传来新消息！！！")
                self.new_msg_queue.put(new_msg_name)


    def get_new_msg(self):
        '''
        获取一个未读消息的人，直接放到队列里，不返回新消息，只返回错误码
        处理这个消息需要时间，所以目前想法只能一个一个处理
        '''
        with self.wx_lock:
            try:
                self.stay_focus()
                # 处理当前聊天 TODO: 似乎没必要处理，因为当前发来也会有未读消息显示，只要不移动鼠标的话
                # self.get_new_msgs_from_person(self.current_chat_name, 1)

                # 处理其他聊天
                self.jump_to_top_of_chatlist()
                names = self.conv_list.children(control_type="ListItem")[:self.listen_cnt]
                for name in names:
                    parsed_name, _, new_msg_cnt = analysis_name(name.window_text())
                    if new_msg_cnt > 0:
                        self.get_new_msgs_from_person(parsed_name, new_msg_cnt)
                        return 1
            except Exception as e:
                traceback.print_exc()
                print(f"获取新消息出现错误：{e}")
                return -1
            return 0

    def listening_to_new_msg(self):
        while not self.recv_stop_event.is_set():
            if self.get_new_msg() == 0:
                self.switch_to_sb(self.default_chat_name)
            self.recv_stop_event.wait(self.listen_msg_interval)

    def enable_receive_msg(self):
        if self.recv_thread is not None and self.recv_thread.is_alive():
            return False
        self.recv_stop_event.clear()
        self.recv_thread = Thread(
            target=self.listening_to_new_msg,
            name="MsgReceiveThread",
            daemon=True,
        )
        self.recv_thread.start()
        return True

    def disable_receive_msg(self, timeout=5.0):
        if self.recv_thread is None:
            return False
        self.recv_stop_event.set()
        self.recv_thread.join(timeout=timeout)
        return True


if __name__ == "__main__":
    wcf = Wcf()

    friends = wcf.get_friends()
    print(friends)
    # wcf.enable_receive_msg()
    # wcf.send_text("hello, this is Wcf speaking!!!", "文件传输助手")
    #
    # msg = wcf.get_msg(timeout=30) # 随便给自己发点啥
    # print(msg)
    #
    # wcf.disable_receive_msg()
