# Wcf (UI 版微信控制)
该仓库致敬伟大的 WechatFerry，试图用 UI 控制的方式复活 Wcf。
基于 Windows UI 自动化（`pywinauto`）实现的微信桌面端消息能力封装：
- 发送文本、发送图片
- 轮询会话列表并收集新消息
- 解析文本/图片消息（图片以 Data URL 返回）

## 前情提要
过去使用 WechatFerry 库可以通过 dll 注入直接控制微信，但随着微信禁止旧版本客户端的登录，该方法已经基本失效。
在尝试复活 Wcf 的过程中，我发现 32 位某些旧版本的微信还可以继续登录，但是碍于 Wcf 的 32 位版本并不支持这些微信版本，
加上逆向的成本和风险过高，所以试图用更加稳定安全的 UI 控制方式进行微信接管。

## 免责声明

本项目仅用于学习、研究与个人自动化测试。使用者需自行承担全部风险与责任，包括但不限于账号风险、数据安全风险、以及因违反微信相关协议或当地法律法规导致的后果。请勿将本项目用于任何违法、滥用或侵犯他人权益的用途。

## 适用范围

- 目前必须为 Windows 上的微信 3.9.12，还必须得是 32 位的才可以登录，获取方式见这个[伟大的仓库](https://github.com/Skyler1n/WeChat3.9-32bit-Compatibility-Launcher)
- 依赖微信 Windows 客户端界面结构（UI 变化可能导致失效）
- 当前项目以中文界面控件标题为定位依据（例如“微信”“聊天”“会话”“消息”）

## 配置环境

use conda or uv or anying you want（
```bash
pip install -r requirements.txt
```

### 大佬勿看，windows 端无脑配置环境
```bash
irm https://astral.sh/uv/install.ps1 | iex
uv python install 3.10.16
uv venv wcf --python 3.10.16
uv pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
./wcf/Scripts/Activate.ps1
```

## 快速使用

跑起来之前先在 `./config/config.yaml` 里设置 `wx_name` 为你登陆微信的昵称，然后 python Wcf.py 即可，或者跑下面的代码：
```python
from Wcf import Wcf

wcf = Wcf()

wcf.enable_receive_msg()
wcf.send_text("hello", "文件传输助手")

# 可选：发送前让大模型润色，防止重复话术被微信检测为机器人（需要在 ./config/config.yaml 配好 api/providers）
wcf.send_text("你好", "文件传输助手", need_decorate=True)

msg = wcf.get_msg(timeout=5)
print(msg)

wcf.disable_receive_msg()
```

## 实现原理（简述）

1. 通过 `pywinauto` 连接已启动的 `WeChat.exe`。
2. 以会话列表、消息列表、搜索框等 UI 控件为锚点完成切换与读取。
3. 发送消息时使用剪贴板 + `Ctrl+V` 粘贴（文本/图片）。
4. 接收消息采用后台线程轮询会话未读数，解析新增消息并投递到队列。
5. 图片消息通过右键复制到剪贴板，再转为 Base64 Data URL。

## 注意事项

- 使用前请先登录微信，并保证主窗口处于打开状态（可以在后台）。
- 超参数可调，都在 `Wcf` 类的初始化函数中
- 轮询依赖会话列表前若干项（超参数 `listen_cnt` ），会漏掉范围外会话。
- 运行时请尽量避免手动抢焦点、拖动窗口、频繁切换 UI。
- UI 控件文案或结构变化后，需按实际版本调整定位逻辑。

## 作为 Python 库使用

### 方式一：同级目录直接导入（无需安装）
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

### 方式二：pip 安装
在你的项目中执行：
```bash
pip install -e ../Wcf
```

安装后，无论你的代码文件放在哪，都可以：
```python
from Wcf import Wcf
```

## 核心类

### `Wcf`

微信 UI 自动化主类，负责连接客户端、收发消息和新消息监听。

主要 API：
- `init()`：进入聊天页，完成基础准备。
- `send_text(text, receiver, need_decorate=True) -> int`：发送文本；当 `need_decorate=True` 时，会先用大模型对文本做“保留原意的润色改写”再发送。
- `send_image(path, receiver) -> int`：发送图片，`0` 成功，`1` 失败。
- `enable_receive_msg() -> bool`：启动后台收消息线程。
- `disable_receive_msg(timeout=5.0) -> bool`：停止后台收消息线程。
- `get_msg(timeout=None)`：从队列取一条新消息，返回 `(chat_name, [WxMsg...])` 或 `None`。

## （可选）大模型润色配置

注意！强烈推荐开启润色模式，默认是开启的，因为这样可以极大避免被微信识别出异常。如果您觉得不需要，需手动将函数参数中的 `need_decorate` 置为 `False`。

如果你想使用 `need_decorate=True`，请在 `./config/config.yaml` 里按 `API.py` 的结构填充：

```yaml
llm:
    provider:
        api_key: "YOUR_API_KEY"
        url: "https://api.openai.com/v1"
        model: "gpt-5.2"

    # 这些字段会在请求时透传给 chat.completions.create（可选）
    model:
        name: Decorator
        # temperature: 0.7
        # max_tokens: 512
```

### `MxMessageParser`

消息解析器，按 UI 文本特征识别类型并转换为 `WxMsg`。

主要 API：
- `parse_single_msg(item) -> Optional[WxMsg]`：解析单条 UI 消息项。
- `get_msg_from_text(item)`：提取文本消息。
- `get_msg_from_image(item)`：从剪贴板读取图片并转 Data URL。
- `get_msg_from_video(item)` / `get_msg_from_emoji(item)` / `get_msg_from_other(item)`：占位解析（当前返回不可解析说明）。

### `WxMsg`

统一消息数据结构，包含：
- `type`：消息类型（0 文本，1 图片，2 视频，3 表情，-1 未知）
- `sender`：发送者显示名
- `roomid`：群名称
- `content`：消息正文（文本或 Data URL）
- `is_meaningful`：是否为可用消息
- `hash_id`：该消息的专属哈希值
