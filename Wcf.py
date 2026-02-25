import queue
from pywinauto.application import Application
import os
import random
import time
import math
from pathlib import Path
from threading import Lock, Event, Thread
from pywinauto.controls.uiawrapper import UIAWrapper
from pywinauto import mouse
import win32api
import win32con
import traceback
import yaml
from typing import Any


try:
    from .API import API
except Exception:
    from API import API

try:
    from .utils import *
    from .WxMsg import WxMsg
    from .WxMsgParser import WxMsgParser
except ImportError:
    from utils import *
    from WxMsg import WxMsg
    from WxMsgParser import WxMsgParser

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
        self.message_parser = WxMsgParser()
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
            self.EPS = float(cfg['EPS'])
            self.square_eps = float(cfg['square_eps'])
            self.mouse_move_speed = float(cfg['mouse_move_speed'])
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

    def get_cursor_pos(self) -> tuple[int, int]:
        x, y = win32api.GetCursorPos()
        return int(x), int(y)

    def set_cursor_pos(self, x: int, y: int) -> None:
        win32api.SetCursorPos((int(x), int(y)))

    def mouse_move(self, target_xy: tuple[int, int], *, speed: float | None = None) -> None:
        """模拟人的鼠标移动：从当前位置出发，用随机、不平滑但整体朝向正确的曲线逐步移动到目标点。

        - 禁止瞬间跳到目标点：不会在一步内 SetCursorPos 到终点（除非起终点极近）。
        - speed: 像素/秒，越大越快；None 时使用配置 mouse_move_speed。
        """
        if not isinstance(target_xy, (tuple, list)) or len(target_xy) != 2:
            raise TypeError(f'expected (x, y) tuple, got: {target_xy!r}')

        tx, ty = int(target_xy[0]), int(target_xy[1])
        sx, sy = self.get_cursor_pos()

        dx = tx - sx
        dy = ty - sy
        dist = math.hypot(dx, dy)
        if dist <= 0.5:
            return

        use_speed = self.mouse_move_speed if speed is None else speed
        try:
            use_speed = float(use_speed)
        except Exception:
            use_speed = 1200.0
        if use_speed <= 0:
            use_speed = 1200.0

        # 总时长由距离和速度决定；每步 sleep = duration / steps
        duration = max(0.04, dist / use_speed)

        # 生成两段随机的“弯曲”控制：沿垂直方向偏移，形成整体正确的曲线
        # 幅度随距离增长，但有上下限，避免太夸张或太直。
        if dist > 1:
            perp_len = dist
            px = -dy / perp_len
            py = dx / perp_len
        else:
            px, py = 0.0, 0.0

        amp_base = min(28.0, max(3.0, dist * 0.12))
        amp1 = random.uniform(-amp_base, amp_base)
        amp2 = random.uniform(-amp_base, amp_base)

        c1x = sx + dx * 0.33 + px * amp1
        c1y = sy + dy * 0.33 + py * amp1
        c2x = sx + dx * 0.72 + px * amp2
        c2y = sy + dy * 0.72 + py * amp2

        # 步数：以 90Hz 左右为目标，但限制上限防止极端距离过慢造成卡顿
        target_hz = 90.0
        steps = int(max(12, min(900, math.ceil(duration * target_hz))))
        dt = duration / steps

        def bezier(t: float) -> tuple[float, float]:
            u = 1.0 - t
            x = (u * u * u) * sx + 3 * (u * u) * t * c1x + 3 * u * (t * t) * c2x + (t * t * t) * tx
            y = (u * u * u) * sy + 3 * (u * u) * t * c1y + 3 * u * (t * t) * c2y + (t * t * t) * ty
            return x, y

        last_x, last_y = sx, sy
        for i in range(1, steps + 1):
            t = i / steps

            bx, by = bezier(t)

            # “不平滑”：加入逐渐衰减的抖动（沿方向+垂直方向）
            jitter_scale = (1.0 - t)
            j_perp = random.gauss(0.0, amp_base * 0.18) * jitter_scale
            j_along = random.gauss(0.0, 1.5) * jitter_scale

            nx = bx + px * j_perp + (dx / dist) * j_along
            ny = by + py * j_perp + (dy / dist) * j_along

            ix, iy = int(round(nx)), int(round(ny))

            # 避免重复设置同一位置，减少抖动时的无意义调用
            if ix != last_x or iy != last_y:
                self.set_cursor_pos(ix, iy)
                last_x, last_y = ix, iy

            # 轻微的随机加减速（保持总体 duration 不变的同时让节奏更“人”）
            sleep_dt = max(0.001, dt * random.uniform(0.7, 1.35))
            time.sleep(sleep_dt)

            # 偶尔出现极短暂停（更像手在微调）
            if i in (int(steps * 0.35), int(steps * 0.62)) and random.random() < 0.18:
                self.wait_a_little_while()

        # 最后对齐到终点（此时距离极小，不会形成“瞬移到目标点”的观感）
        self.set_cursor_pos(tx, ty)

    def mouse_click_current_pos(self, *, button: str = 'left') -> None:
        if button == 'left':
            down, up = win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP
        elif button == 'right':
            down, up = win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP
        elif button == 'middle':
            down, up = win32con.MOUSEEVENTF_MIDDLEDOWN, win32con.MOUSEEVENTF_MIDDLEUP
        else:
            raise ValueError(f'unsupported mouse button: {button!r}')

        win32api.mouse_event(down, 0, 0, 0, 0)
        self.wait_a_little_while()
        win32api.mouse_event(up, 0, 0, 0, 0)

    def resolve_click_center(self, target: Any) -> tuple[int, int]:
        if target is None:
            raise ValueError('click target is None')

        if isinstance(target, (tuple, list)) and len(target) == 2:
            x, y = target
            return int(x), int(y)

        if not isinstance(target, UIAWrapper) and hasattr(target, 'wrapper_object'):
            target = target.wrapper_object()

        if not hasattr(target, 'rectangle'):
            raise TypeError(f'unsupported click target type: {type(target)!r}')

        rect = target.rectangle()
        x = int((rect.left + rect.right) / 2)
        y = int((rect.top + rect.bottom) / 2)
        return x, y

    def click(self, target: Any, *, button: str = 'left', square_eps: float | None = None):
        """
        统一点击入口：
        - target 可以是 (x, y) 坐标，或可点击控件（会取控件中心坐标）
        - 在中心点附近边长为 2*square_eps 的正方形内随机取点点击
        """
        x, y = self.resolve_click_center(target)
        eps = self.square_eps if square_eps is None else square_eps
        try:
            eps_val = float(eps)
        except Exception:
            eps_val = 0.0

        if eps_val > 0:
            x += int(round(random.uniform(-eps_val, eps_val)))
            y += int(round(random.uniform(-eps_val, eps_val)))

        # 禁止瞬移：先用人类风格移动到目标点，再在当前位置点击
        self.mouse_move((int(x), int(y)))
        self.mouse_click_current_pos(button=button)

    def decorate_text(self, text: str) -> str:
        if text is None:
            return None

        system_prompt = '''
你是一位精通社交媒体沟通的语言风格大师。你的核心任务是接收一段由程序提供的固定文本，并对其进行润色和改写，使其看起来更像是真人在微信聊天中发出的消息，而不是由机器人自动生成的。最终目标是帮助微信机器人规避因发送重复、模式化消息而被平台限制的风险。

在执行任务时，请严格遵守以下核心原则：

1. 保持原意（Highest Priority）
这是最高优先级的要求。无论你如何调整句式、语气或用词，改写后的文本必须尽可能准确地传达原始文本的全部核心信息。绝不能增加、删减或扭曲原文的意图。

2. 字数限制（Highest Priority）
改写后的文本必须严格控制在 100 字以内，且必须有且仅有一个自然段。如果原文超过这个长度，请务必进行必要的删减和浓缩，但前提是不能改变原意。

3. 风格自然口语化

拒绝列表式的表达，总结成连贯的表达。
拒绝华丽的修辞，拒绝 AI 式的给出选项，拒绝过于正式或书面化的语言。改写后的文本应该听起来像是一个普通人在微信聊天中会说的话，具有自然、流畅的口语风格。

模拟真人对话：使用自然、流畅的口语，就像朋友之间聊天一样。
避免书面语：避免使用过于正式、僵硬或充满“程序感”的词汇和句式。
语气友好：除非原文带有特殊情绪，否则整体基调应保持友好、礼貌和乐于助人。
4. 创造表达多样性

拒绝模板化：对于同一个输入，你的每一次输出都应该力求不同。请主动变换句式结构、使用同义词、调整语序。
随机性：在保持自然的前提下，引入一定的随机性，让每次生成的结果都有细微差别。
5. 恰当使用辅助元素

Emoji 表情：可以根据文本内容和语气，在句末或句中恰当地加入 1-2 个通用且符合情境的 Emoji，这能极大地提升消息的“真人感”。请注意不要过度使用或使用不恰当的表情。
6. 简洁清晰
在追求口语化和自然风格的同时，确保信息传达的清晰度。改写后的句子应言简意赅、易于理解，避免使用过于复杂或生僻的词汇。

7. 注意表情必须使用微信的表情代码，把对应的代码嵌入你的回答中，发送后将会自动表现为表情。列表如下：
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

8. 尽可能简短
在保持信息完整和清晰的前提下，尽量使改写后的文本简洁明了。避免冗长的句子和不必要的修饰词。
因为微信上很少出现大段的文字，过长的消息反而会显得不自然。简洁才是微信聊天的常态。

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
            print(f'请查看 README，将 LLM 配置信息填好，或取消 need_decorate 来跳过润色步骤。')
            return None
        if not res or not str(res).strip():
            return None
        return str(res).strip()

    def wait_a_little_while(self):
        delta = self.eps / 2
        low = max(0.0, self.eps - delta)
        high = max(low, self.eps + delta)
        time.sleep(random.uniform(low, high))

    def wait_a_large_while(self):
        delta = self.EPS / 2
        low = max(0.0, self.EPS - delta)
        high = max(low, self.EPS + delta)
        time.sleep(random.uniform(low, high))

    def stay_focus(self):
        self.win.set_focus()
        self.wait_a_little_while()

    def init(self):
        self.click(self.chat)
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
                self.click(exist_name)
                self.wait_a_little_while()
                self.current_chat_name, self.is_room, self.room_member_cnt = self.get_current_chat_and_is_group()
                return
        self.click(self.search)
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
        self.click(first_result)
        self.wait_a_little_while()
        self.current_chat_name, self.is_room, self.room_member_cnt = self.get_current_chat_and_is_group()

    def get_friends(self):
        with self.wx_lock:
            self.stay_focus()
            self.click(self.friend_list)
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
                self.click(items[0])
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
                self.wait_a_large_while()
            send_keys("{HOME}", with_spaces=True)
            self.wait_a_large_while()
            self.init()
            return friends
        

    def jump_to_top_of_chatlist(self):
        return # TODO: 被动接受消息时，理论上一直会在最上面呆着，所以暂时不做处理，如果您不放心，就设置好唯一置顶，并 switch 过去
        self.switch_to_sb(self.default_chat_name)

    def send_text(self, text: str, receiver: str, need_decorate: bool = True) -> int:
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
        '''获取来信者的最新一条消息'''
        try:
            new_msg_name = self.new_msg_queue.get(timeout=timeout)
        except queue.Empty:
            return None, None
        with self.new_msg_queue_lock:
            return new_msg_name, self.msg_cache.get(new_msg_name, [None])[-1]

    def get_msg_list(self, timeout=1.0):
        '''获取与来信者的最新 memory_len 条聊天记录，不区分哪些是新消息'''
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
            self.click(btn, button='right')
            self.wait_a_little_while()
            self.click((x + 10, y + 10))  # 要求复制必须是第一个选项
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
        TODO: 处理这个消息需要时间，所以目前想法只能一个一个处理，不然可能会读取过时的消息列表？
        '''
        with self.wx_lock:
            try:
                # 当前聊天似乎没必要特殊处理，因为当前发来也会有未读消息显示，只要不移动鼠标的话
                self.stay_focus()
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
            res = self.get_new_msg()
            # if res == 0:
            #     if self.current_chat_name != self.default_chat_name:
            #         self.switch_to_sb(self.default_chat_name)
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
    print(f'friends ({len(friends)}): ')
    for friend in friends:
        print(friend)

    wcf.enable_receive_msg()
    wcf.send_text('你好呀！', '文件传输助手', need_decorate=True)
    while True:
        name, msg = wcf.get_msg(timeout=1.0) # 内部会有日志输出