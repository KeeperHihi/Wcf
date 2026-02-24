import queue
from pywinauto.application import Application
import os
import random
import time
from pathlib import Path
from threading import Lock, Event, Thread
from pywinauto.controls.uiawrapper import UIAWrapper
from pywinauto import mouse
import traceback
import yaml


try:
    from .API import API
except Exception:
    from API import API

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
        self.load_parameters_from_yaml()
        if not self.wx_name or not str(self.wx_name).strip():
            print('错误：请在 ./config/config.yaml 中设置非空的 wx_name（你当前登录微信的昵称）。')
            raise SystemExit(1)

        print("Application")
        self.app = Application(backend="uia").connect(path="WeChat.exe")

        print("Window")
        self.win = self.app.window(title="微信", control_type="Window")

        print("Regular Expressions")
        self._GROUP_RE = re.compile(r"^(?P<name>.*?)(?:\s*\((?P<count>\d+)\))?$")

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

    def load_parameters_from_yaml(self):
        cfg_path = Path(__file__).resolve().parent / 'config' / 'config.yaml'
        if not cfg_path.exists():
            print(f'错误：未找到配置文件：{cfg_path}（期望路径为 ./config/config.yaml）')
            raise SystemExit(1)

        with cfg_path.open('r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}

        try:
            self.wx_name = cfg['wx_name']
            self.default_chat_name = cfg['default_chat_name']
            self.listen_cnt = int(cfg['listen_cnt'])
            self.eps = float(cfg['eps'])
            self.memory_len = int(cfg['memory_len'])
            self.max_new_msg_cnt = int(cfg['max_new_msg_cnt'])
            self.listen_msg_interval = float(cfg['listen_msg_interval'])
            self.type_min_interval = float(cfg['type_min_interval'])
            self.type_max_interval = float(cfg['type_max_interval'])
            self.enable_image_parse = bool(cfg['enable_image_parse'])
            self.llm = dict(cfg['llm'])
            self.api = API(config=self.llm)
        except KeyError as e:
            print(f'错误：配置缺少字段 {e}，请检查 ./config/config.yaml')
            raise SystemExit(1)

    def decorate_text(self, text: str) -> str:
        if text is None:
            return None

        system_prompt = '''
你是一位精通社交媒体沟通的语言风格大师。你的核心任务是接收一段由程序提供的固定文本，并对其进行润色和改写，使其看起来更像是真人在微信聊天中发出的消息，而不是由机器人自动生成的。最终目标是帮助微信机器人规避因发送重复、模式化消息而被平台限制的风险。

在执行任务时，请严格遵守以下核心原则：

1. 保持原意（Highest Priority）
这是最高优先级的要求。无论你如何调整句式、语气或用词，改写后的文本必须完整、准确地传达原始文本的全部核心信息。绝不能增加、删减或扭曲原文的意图。

2. 风格自然口语化

模拟真人对话：使用自然、流畅的口语，就像朋友之间聊天一样。
避免书面语：避免使用过于正式、僵硬或充满“程序感”的词汇和句式。
语气友好：除非原文带有特殊情绪，否则整体基调应保持友好、礼貌和乐于助人。
3. 创造表达多样性

拒绝模板化：这是你的关键价值所在。对于同一个输入，你的每一次输出都应该力求不同。请主动变换句式结构、使用同义词、调整语序。
随机性：在保持自然的前提下，引入一定的随机性，让每次生成的结果都有细微差别。
4. 恰当使用辅助元素

Emoji 表情：可以根据文本内容和语气，在句末或句中恰当地加入 1-2 个通用且符合情境的 Emoji，这能极大地提升消息的“真人感”。请注意不要过度使用或使用不恰当的表情。
标点符号：可以灵活使用标点，例如用“～”代替“。”来表达更轻松的语气，或适当使用感叹号“！”来加强情绪。
5. 简洁清晰
在追求口语化和自然风格的同时，确保信息传达的清晰度。改写后的句子应言简意赅、易于理解，避免使用过于复杂或生僻的词汇。

6. 注意表情必须使用微信的表情代码，把对应的代码嵌入你的回答中，发送后将会自动表现为表情。列表如下：
[Aaagh!]
[Angry]
[Awesome]
[Awkward]
[Bah！R]
[Bah！L]
[Beckon]
[Beer]
[Blessing]
[Blush]
[Bomb]
[Boring]
[Broken]
[BrokenHeart]
[Bye]
[Cake]
[Chuckle]
[Clap]
[Cleaver]
[Coffee]
[Commando]
[Concerned]
[CoolGuy]
[Cry]
[Determined]
[Dizzy]
[Doge]
[Drool]
[Drowsy]
[Duh]
[Emm]
[Facepalm]
[Fireworks]
[Fist]
[Flushed]
[Frown]
[Gift]
[GoForIt]
[Grimace]
[Grin]
[Hammer]
[Happy]
[Heart]
[Hey]
[Hug]
[Hurt]
[Joyful]
[KeepFighting]
[Kiss]
[Laugh]
[Let Down]
[LetMeSee]
[Lips]
[Lol]
[Moon]
[MyBad]
[NoProb]
[NosePick]
[OK]
[OMG]
[Onlooker]
[Packet]
[Panic]
[Party]
[Peace]
[Pig]
[Pooh-pooh]
[Poop]
[Puke]
[Respect]
[Rose]
[Salute]
[Scold]
[Scowl]
[Scream]
[Shake]
[Shhh]
[Shocked]
[Shrunken]
[Shy]
[Sick]
[Sigh]
[Silent]
[Skull]
[Sleep]
[Slight]
[Sly]
[Smart]
[Smile]
[Smirk]
[Smug]
[Sob]
[Speechless]
[Sun]
[Surprise]
[Sweat]
[Sweats]
[TearingUp]
[Terror]
[ThumbsDown]
[ThumbsUp]
[Toasted]
[Tongue]
[Tremble]
[Trick]
[Twirl]
[Watermelon]
[Waddle]
[Whimper]
[Wilt]
[Worship]
[Wow]
[Yawn]
[Yeah!]
输出要求：你的回答必须且仅能包含润色后的文本内容。

不要包含任何解释、分析、或前缀，例如“好的，这是改写后的版本：”、“这里有几个选项：”等。直接输出最终结果即可。
'''
        msgs = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': str(text)},
        ]
        try:
            res = self.api.get_response(msgs)
            print(f'润色前: {text}')
            print(f'润色后: {res}')
        except Exception as e:
            print(f'润色文本时报错：{e}')
            return None
        if not res or not str(res).strip():
            return None
        return str(res).strip()

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
        # 2) 找到包含标题文本的那层容器
        info_btn = info_btn.wrapper_object()
        bar = info_btn.parent()

        texts = None
        for _ in range(3): # 亲测 3 层就够了
            try:
                texts = bar.descendants(control_type="Text")  # 只取直接 children，别用 descendants
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
        type_text_humanlike(
            name,
            with_enter=True,
            min_interval=self.type_min_interval,
            max_interval=self.type_max_interval
        )
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

    def send_text(self, text: str, receiver: str, need_decorate: bool = False) -> int:
        with self.wx_lock:
            self.stay_focus()
            receiver = clean_name(receiver)
            try:
                if need_decorate:
                    decorated = self.decorate_text(text)
                    if decorated is not None:
                        text = decorated
                self.switch_to_sb(receiver)
                type_text_humanlike(
                    text, 
                    with_enter=True, 
                    min_interval=self.type_min_interval, 
                    max_interval=self.type_max_interval
                )
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
                if self.current_chat_name != self.default_chat_name:
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

    wcf.send_text('有一件很奇怪的事情不知道你发现了没有，我觉得我是个sb，今天放学我又没主动跟她说话', '金天', need_decorate=True)

    # wcf.enable_receive_msg()
    # wcf.send_text("hello, this is Wcf speaking!!!", "文件传输助手")
    #
    # msg = wcf.get_msg(timeout=30) # 随便给自己发点啥
    # print(msg)
    #
    # wcf.disable_receive_msg()
