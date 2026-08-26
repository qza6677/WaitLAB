# WaitLAB

WaitLAB 是一个 Windows 科研等待助手。Codex 开始执行后，它会立即启动 AI 等待计时并弹出一个微任务；Codex 完成时只提醒，不会停止正在进行的微任务计时。

![等待任务选择](artifacts/waiting-picker-native.png)

![微任务进行中](artifacts/focus-session-native.png)

## 当前 MVP

- 桌面常驻悬浮宠物和系统托盘
- 空闲时收缩为屏幕左侧小桌宠，悬停提高可见度
- Codex 开始后展开任务选择，开始微任务后变为横向迷你播放器
- 播放器直接显示任务、计时、暂停、完成和取消
- 自动读取 Codex Windows 桌面端的本机任务状态，发送后自动弹出、完成后自动提醒
- Codex 运行、完成、中断和失败状态识别
- Codex 连接状态显示：已连接、Hook 兜底和降级模式
- Codex 等待权限批准时提醒（桌面状态可用或可选 Hook 已连接时），微任务继续计时
- 设置中心：弹出方式、完成通知、提示音和 Windows 开机启动
- 固定循环任务可添加、重命名、删除、启停和排序
- “本轮跳过”会忽略当前等待轮次，但不影响下一条 Codex 指令
- AI 等待与微任务两套独立计时
- Codex 完成提醒，微任务继续计时
- 手动任务严格优先于固定任务
- 没有手动任务时，7 个固定任务依次循环
- 暂停、继续、完成和放回任务池
- SQLite 本地记录和异常退出后的进行中任务恢复
- 5 秒持久化心跳，异常退出不会累计整段离线时间
- Windows 休眠和正常退出时自动暂停微任务
- 重启时选择继续、结束并放回或保持暂停
- 全局快捷键作为自动状态源失效时的兜底
- 不保存或转发 prompt、回答、工作目录和 transcript

## 运行

环境要求：Windows、Python 3.11+。

```powershell
python -m pip install -r requirements.txt
python run_waitlab.py
```

也可以双击或运行：

```powershell
.\start_waitlab.ps1
```

数据保存在 `%LOCALAPPDATA%\WaitLAB\waitlab.db`。

### 安装版

已经构建好的文件位于 `release`：

- `WaitLAB-Setup-0.5.5.exe`：简体中文安装器，安装到当前用户目录，不需要管理员权限。
- `WaitLAB.exe`：无需安装的单文件便携版。

安装版和源码版使用同一个本地数据库，升级不会清空任务或历史计时。

### 计时语义

- 隐藏到托盘不算退出，微任务正常继续。
- 通过托盘退出时，正在运行的微任务会自动暂停。
- Windows 进入休眠前会自动暂停；恢复后不会擅自继续。
- 程序异常结束时，以最近一次 5 秒心跳作为计时终点。
- 下次启动发现未完成任务时，会询问继续、结束并放回或保持暂停。

## 接入 Codex

### Windows 桌面端（默认，无需配置）

WaitLAB 默认以只读方式检查 Codex 桌面端维护的本机任务状态库：

```text
%USERPROFILE%\.codex\thread_history_1.sqlite
```

它只查询 `thread_id`、`turn_id`、`status`、`started_at` 和 `completed_at`，不会查询消息、任务标题、工作目录、错误详情或 item JSON。连接成功后，桌宠顶部会显示“Codex · 已连接”。

这个本机数据库是桌面端的内部状态源，未来 Codex 升级若改变结构，WaitLAB 会自动进入“降级模式”，不会影响手动任务、计时数据或快捷键。

### Hook（可选增强）

Hook 不再是 Windows 桌面端的必选依赖。若你同时使用独立 Codex CLI，并希望增强权限等待事件，可运行安装器：

```powershell
.\install_hooks.ps1
```

安装器会：

1. 读取并保留已有的 `~/.codex/hooks.json`。
2. 在修改前创建带时间戳的备份。
3. 添加 `UserPromptSubmit`、`PermissionRequest`、`PostToolUse` 和 `Stop` 四个 command hook。
4. 将 hook 指向当前项目中的 `hook_bridge.py`。

注意：`/hooks` 是独立 Codex CLI 中的审核命令，不是插件，当前 Windows 桌面端没有这个入口。仅使用 Windows 桌面端时，无需安装或审核 Hook。桌面端自身也会发送任务完成和权限请求通知。

卸载：

```powershell
.\uninstall_hooks.ps1
```

卸载器只删除命令中带有 `--waitlab-hook` 标记的处理器，保留其他 hooks。

官方参考：[Codex Hooks](https://learn.chatgpt.com/docs/hooks) 和 [ChatGPT desktop app for Windows](https://learn.chatgpt.com/docs/windows/windows-app)。

## 快捷键

| 快捷键 | 功能 |
|---|---|
| `Ctrl+Alt+W` | 手动开始 AI 等待 |
| `Ctrl+Alt+D` | 手动标记 AI 完成 |
| `Ctrl+Alt+P` | 暂停或继续微任务 |

如果快捷键已被其他应用占用，WaitLAB 会继续运行，仍可使用窗口和托盘按钮。

## 任务规则

- 只要存在至少一个未完成的手动任务，推荐区只显示手动任务。
- 手动任务全部完成或删除后，才显示固定滚动任务。
- 完成固定任务后，所选任务移动到循环队尾。
- Codex 完成只结束 AI 等待会话，不会修改微任务状态。
- 在设置中可以编辑固定循环；未勾选的任务不会出现在推荐区。
- 所有固定任务都停用且没有手动任务时，选择区会提示前往设置。
- “本轮跳过”只对当前 `turn_id` 生效，程序重启后也不会重复弹出这一轮。

## 设置

点击桌宠右上角“设置”，或从托盘菜单进入：

- **弹出并置顶**：收到新指令时立即显示任务选择。
- **静默显示**：显示窗口但不主动抢占前台。
- **仅托盘提醒**：不展开窗口，通过托盘通知提示。
- 可分别开关 Codex 完成通知和提示音。
- 可启用当前用户级的 Windows 登录自启动，不需要管理员权限。

## 桌宠交互

- 空闲时左键点击桌宠可手动展开任务选择。
- 拖动桌宠可以改变垂直位置，松手后自动吸附到当前屏幕左侧。
- 右键桌宠可打开任务池、设置、隐藏和退出菜单。
- 已有微任务时，新的 Codex 指令不会重复展开任务选择。
- Codex 完成、失败或等待操作时，只改变桌宠状态并发送通知，播放器和微任务计时保持不变。

## 构建 Windows 安装包

```powershell
python -m pip install -r requirements-build.txt
.\build_release.ps1
```

脚本先通过 PyInstaller 生成单文件程序；若检测到 Inno Setup 6，还会继续生成简体中文安装器。

## 测试与界面预览

```powershell
python -m pytest -q
python scripts\render_preview.py
```

`render_preview.py` 使用 Qt 离屏模式，Windows 中文字体在离屏截图中可能显示为方框；真实 Windows 渲染可使用：

```powershell
python scripts\render_native_preview.py
```

## 代码结构

```text
waitlab/
├── app.py             应用启动、单实例、托盘和快捷键
├── ui.py              桌宠、任务选择与管理界面
├── service.py         双计时和任务状态机
├── storage.py         SQLite 数据层
├── desktop_activity.py Codex 桌面任务状态只读适配器
├── preferences.py      弹窗、通知和声音偏好
├── autostart.py        Windows 当前用户开机启动
├── ipc.py             本机 UDP hook 事件监听
├── connection.py      可选 Hook 配置检查与连接验证
├── hook_installer.py  hooks.json 安装与安全卸载
└── hotkeys.py         Windows 全局快捷键

hook_bridge.py         隐私过滤后的 Codex hook 桥接
build_release.ps1      Windows 便携版与安装器构建脚本
packaging/             图标、版本信息和 Inno Setup 配置
```
