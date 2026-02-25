# Wcf（UI 版微信控制）

该项目致敬 [WechatFerry](https://github.com/lich0821/WeChatFerry.git)，尝试用 **Windows UI 自动化** 的方式实现微信桌面端控制能力。

## 项目状态

> 🚧 当前仍处于**开发阶段**。
> 主要功能已可用，但稳定性、兼容性和边界场景还在持续完善，使用过程中可能遇到 bug。
> 欢迎试用并提交 issue / PR / 使用反馈。

## 目前已支持

- [x] 微信客户端连接与基础 UI 控制（基于 `pywinauto`）
- [x] 会话切换（会话列表命中 + 搜索兜底）
- [x] 鼠标移动（模拟人类操控）
- [x] 文本发送（逐字符模拟键入 + 随机间隔）
- [x] 可选 LLM 文本“润色”（`send_text(..., need_decorate=True)`）
- [x] 图片发送（剪贴板粘贴发送）
- [x] 新消息监听（后台轮询 + 队列消费）
- [x] 基础消息解析（文本、图片）与统一消息结构（`WxMsg`）

## TODO

- [ ] 当务之急：解决并发消息时可能会丢包的问题，主要出现在同一聊天中短时间内收到超过一条消息时
- [ ] 搞清楚当前会话对象给自己发送新消息时，在不操作的基础上，什么情况下会显示小红点
- [ ] 视频 / 表情 / 其他复杂消息类型的完整解析
- [ ] 更多微信版本与界面变化下的兼容性增强
- [ ] 异常恢复与稳定性提升（长时间运行、焦点干扰等）
- [ ] 更完善的示例与自动化测试覆盖

## 适用范围

- 当前面向 **Windows 微信 3.9.12（32 位）** 使用场景。
- 依赖微信桌面端 UI 结构，微信版本升级或界面改动可能导致失效。
- 当前控件定位依赖中文界面文案（如“微信”“聊天”“会话”“消息”）。

可参考该仓库获取对应版本微信兼容启动方式：

[https://github.com/Skyler1n/WeChat3.9-32bit-Compatibility-Launcher](https://github.com/Skyler1n/WeChat3.9-32bit-Compatibility-Launcher)

## 环境配置

```bash
pip install -r requirements.txt
```


## 快速使用

先在 `./config/config.yaml` 中设置：
- `wx_name`：你当前登录微信的昵称
- `default_chat_name`：默认会话（建议唯一置顶）

然后运行示例（已写在 `Wcf.py` 中）

```python
from Wcf import Wcf

wcf = Wcf()

friends = wcf.get_friends()
print(f'friends ({len(friends)}): ')
for friend in friends:
    print(friend)

wcf.enable_receive_msg()
wcf.send_text('你好呀！', '文件传输助手', need_decorate=True)
while True:
    name, msg = wcf.get_msg(timeout=1.0) # 内部会有日志输出
```

## 实现原理

1. 通过 `pywinauto` 连接已启动的 `WeChat.exe`。
2. 以会话列表、消息列表、搜索框等 UI 控件为锚点完成切换与读取。
3. 鼠标移动模拟人类点击
4. 文字键入模拟人类输入
5. 图片发送利用剪贴板做中介
6. 接收消息采用后台线程轮询会话未读数，解析新增消息并投递到队列。
7. 图片消息通过右键复制到剪贴板，再转为 Base64 Data URL。

## 注意事项

- 使用前请先登录微信，并保证主窗口处于打开状态（可以在后台）。
- 超参数可调，都在 `Wcf` 类的初始化函数中
- 轮询依赖会话列表前若干项（超参数 `listen_cnt` ），会漏掉范围外会话。
- 运行时请尽量避免手动抢焦点、拖动窗口、频繁切换 UI。
- UI 控件文案或结构变化后，需按实际版本调整定位逻辑。

## 作为 Python 库使用

确保目录结构如下：
```text
your_project/
	app.py
	Wcf/
		__init__.py
		Wcf.py
		...
```

然后在 `app.py` 中直接：
```python
from Wcf import Wcf
```

## 核心类

### `Wcf`

微信 UI 控制主类，负责连接客户端、发送消息和新消息监听。

主要 API：
- `init()`：进入聊天页，完成基础准备。
- `send_text(text, receiver, need_decorate=True) -> int`：发送文本；当 `need_decorate=True` 时，会先用大模型对文本做“保留原意的润色改写”再发送。
- `send_image(path, receiver) -> int`：发送图片，`0` 成功，`1` 失败。
- `enable_receive_msg() -> bool`：启动后台收消息线程。
- `disable_receive_msg(timeout=5.0) -> bool`：停止后台收消息线程。
- `get_msg(timeout=1.0)`：从队列取一条新消息，返回 `(chat_name, WxMsg)` 或 `None, None`。
- `get_msg_list(timeout=1.0)`：从队列取该用户缓存中全部消息，返回 `(chat_name, [WxMsg...])` 或 `None, None`。

## （可选）大模型润色配置

注意！强烈推荐开启润色模式，默认是开启的，因为这样可以极大避免被微信识别出异常。如果您觉得不需要，需手动将函数参数中的 `need_decorate` 默认值置为 `False`。

如果你想使用 `need_decorate=True`，请在 `./config/config.yaml` 里按 `API.py` 的结构填充：

```yaml
llm:
    provider:
        api_key: "YOUR_API_KEY"
        url: "https://api.openai.com/v1"
        model: "gpt-5.2" # 您喜欢的模型，注意最好速度较快

    model:
        name: Decorator # 无用
        # temperature: 0.7 # 可选参数
        # max_tokens: 512
```

### `WxMsgParser`

消息解析器，按 UI 文本特征识别类型并转换为 `WxMsg`。

主要 API：
- `parse_single_msg(item) -> Optional[WxMsg]`：解析单条 UI 消息项。
- `get_msg_from_text(item)`：提取文本消息。
- `get_msg_from_image(item)`：从剪贴板读取图片并转 Data URL。
- `get_msg_from_video(item)` / `get_msg_from_emoji(item)` / `get_msg_from_other(item)`：暂不支持（当前返回不可解析说明）。

### `WxMsg`

统一消息数据结构，包含：
- `type`：消息类型（0 文本，1 图片，2 视频，3 表情，-1 未知）
- `sender`：发送者显示名
- `roomid`：群名称
- `content`：消息正文（文本或 Data URL）
- `is_meaningful`：是否为可用消息
- `hash_id`：该消息的专属哈希值

## 免责声明

本项目仅用于学习、研究与个人自动化测试。使用者需自行承担全部风险与责任，包括但不限于账号风险、数据安全风险，以及因违反微信相关协议或当地法律法规导致的后果。请勿将本项目用于任何违法、滥用或侵犯他人权益的用途。