# Codex Provider Launcher

一个跨平台的本地 Codex 启动器，供用户在个人账号和自定义 API 服务之间切换。服务地址、模型和密钥由使用者自行提供，项目不包含预设中转站或账号。

## Windows 下载（免安装 Python）

下载后双击运行，先在电脑上安装并正常运行一次 Codex Desktop。选择中转站时只需配置自己的 API 地址和密钥；个人账号直接启动。

| 版本 | 下载 |
| --- | --- |
| x64，Intel / AMD 64 位电脑（推荐） | [下载 Windows x64 EXE](https://github.com/2503255081/codex-provider-launcher/releases/latest/download/Codex-Provider-Launcher-Windows-x64.exe) |
| x86，32 位启动器 | [下载 Windows x86 EXE](https://github.com/2503255081/codex-provider-launcher/releases/latest/download/Codex-Provider-Launcher-Windows-x86.exe) |

[全部版本、发布说明及 SHA256 校验文件](https://github.com/2503255081/codex-provider-launcher/releases)。这两个版本面向 Windows 10/11，内含 Python 和图形组件，不用执行安装命令。x86 启动器已在 64 位 Windows 的兼容环境中验证；它不会让官方 Codex Desktop 支持原本不支持的 32 位操作系统。EXE 尚未购买代码签名证书，Windows 可能显示未知发布者。

## Linux 与 macOS 下载（免安装 Python）

| 系统 | 下载 |
| --- | --- |
| Linux x64（Intel / AMD） | [下载 Linux x64](https://github.com/2503255081/codex-provider-launcher/releases/latest/download/Codex-Provider-Launcher-Linux-x64.tar.gz) |
| Linux ARM64 | [下载 Linux ARM64](https://github.com/2503255081/codex-provider-launcher/releases/latest/download/Codex-Provider-Launcher-Linux-arm64.tar.gz) |
| macOS Apple Silicon | [下载 macOS ARM64](https://github.com/2503255081/codex-provider-launcher/releases/latest/download/Codex-Provider-Launcher-macOS-arm64.zip) |
| macOS Intel | [下载 macOS x64](https://github.com/2503255081/codex-provider-launcher/releases/latest/download/Codex-Provider-Launcher-macOS-x64.zip) |

Linux：下载后解压，在解压出的文件夹中运行 `./Codex-Provider-Launcher`。启动器优先打开官方 ChatGPT Linux 桌面应用；如果没有安装桌面版，会在图形终端启动 Codex CLI。需要图形桌面、已解锁的 Secret Service 凭据库，以及桌面应用或 Codex CLI。CLI 模式暂不能可靠记住会话内的模型切换。Linux 包在 Ubuntu 22.04 上构建和测试；[官方 Linux 桌面应用](https://developers.openai.com/codex/linux/linux-app)有自己的系统要求。

macOS：解压 ZIP，将 `Codex-Provider-Launcher.app` 放入应用程序文件夹并打开。需要先安装并登录官方 Codex 或 ChatGPT 桌面应用；密钥保存在钥匙串。两个包均在 macOS 15 原生构建和进行界面自检，真实桌面登录与对话尚未在 Mac 上验证。官方 Codex 桌面版的架构支持以 OpenAI 当前说明为准；Intel 启动器的构建成功不代表官方桌面版支持 Intel。应用尚未使用 Apple 开发者证书签名或公证，macOS 可能阻止打开。

EXE 提供个人账号、中转站 Responses API、DeepSeek 原生 API 三个入口；普通 Chat Completions 的可选 LiteLLM 适配器不包含在便携包内。密钥由每位使用者保存在自己的 Windows 凭据管理器中，发行包不含开发者的密钥、域名或配置。

**目前是实验版本。** Windows 图形界面、隔离配置校验和模拟协议转换已测试；Linux、macOS 便携包已在各自系统构建并检查图形界面和路由桥接；DeepSeek 原生 Responses 的流式和同名命名空间工具调用已实测。各系统的完整桌面切换尚未全部验证。请先备份配置和聊天数据，不要把它用于无法中断的工作。

## 适用场景：个人额度用完后继续工作

当个人账号的 Codex 使用额度暂时用完，而手头的任务还没有完成时，可以通过启动器切换到自己配置的中转站或其他兼容 API 服务，继续后面的工作。等个人账号额度恢复后，再切回个人账号。

1. 点击启动器右上角的“设置”，填写中转站 API 地址和密钥；使用 DeepSeek 时只需填写密钥。
2. 需要切换时，先保存工作并正常退出 Codex Desktop，然后点击主界面的“中转站”或“DeepSeek”按钮启动。
3. 尝试打开原来的对话继续任务；如果旧对话无法继续，可以新建对话并提供必要的任务背景。
4. 个人账号额度恢复后，正常退出 Codex，在启动器中选择“个人账号”重新启动。

切换使用的是所选服务自己的账号额度或 API 计费，不会增加个人账号额度。启动器会保留现有聊天和项目配置，但目前需要重启 Codex，历史对话的跨服务继续功能仍在实验阶段，不能保证所有模型或服务都能无缝延续上下文。

## 主要功能

- 个人账号直接启动；中转站只需填写地址和密钥，DeepSeek 只需填写密钥。
- DeepSeek 按[官方 Codex 接入文档](https://api-docs.deepseek.com/zh-cn/quick_start/agent_integrations/codex)直连原生 Responses API，保留命名空间和工具调用，不再经过 LiteLLM。普通 Chat Completions 服务仍可使用本机 LiteLLM 适配。
- DeepSeek 模式提供 `deepseek-flash` 和 `deepseek-v4-pro`，支持关闭思考（none）、low、high、max 推理档位；同时设置模型目录和桌面端档位显示配置。启动默认使用 Flash，桌面中选择 Pro 后会保留选择；仅把旧对话残留的其他提供方模型替换为 Flash。Flash 支持图片，Pro 为文本模型。切回个人账号或中转站时恢复各自的模型选择。
- 若本机已登录个人 ChatGPT 账号，切换到中转站或 DeepSeek 时保留 Codex 桌面端的账号身份和右下角个人额度显示；中转请求仍使用该服务的 API 密钥。个人额度不代表中转站或 DeepSeek 的余额。
- API 密钥使用系统凭据库；非本机 HTTP 默认禁用。服务地址只保存在本机配置文件中。
- 切换前通过实际 Codex CLI 在临时目录检查配置，备份原始配置，检测并发编辑；可在启动器中恢复上次配置。聊天和账号文件不会被主动改写。
- 中转站设置可测试 `/models` 连接，选择服务推荐模型、指定模型或上次使用的模型，并设置默认推理深度；读取模型列表不代表推理请求一定成功。密钥以掩码显示，可点击修改。上次模型通过桌面桥接记录，Linux 终端模式暂不能可靠记录会话内的模型切换。服务提供 OpenCodex 原生目录时同步显示/隐藏状态并折叠重复的 `--fast` 条目。普通 Responses 服务的模型不会自动加入桌面模型选择器；如果服务无法提供模型列表，启动器会提示并停止。
- 启动前检查中转站声明的 Fast 档位是否被 Codex 识别为可选项，并在启动器日志中记录。桌面端的闪电按钮还受登录状态和配置策略影响；请求 `priority` 不保证上游实际提供加速。
- 配置校验在隔离进程中进行，并清理其子进程；连接测试会区分常见的 HTTP 错误、超时、DNS 和证书问题，不会在提示中显示密钥。
- 遇到不认识的旧 profile、provider 或自定义目录时停止；对于旧模型目录，可显式勾选隔离。不是所有 CCSwitch 配置都能自动修复。

## 使用

Windows 普通用户可直接使用上方 EXE 下载。以下是源码运行方式，适用于开发、Linux/macOS 或需要可选 LiteLLM 适配器的用户。

安装 Python 3.10 或更新版本、带 Tk 的 Python、Codex CLI 和对应平台的 Codex。Linux 还需要图形桌面、终端模拟器和已解锁的 Secret Service。macOS 需要钥匙串。Windows 的桌面入口目前面向 Microsoft Store 安装的 Codex。

1. 下载源码，在项目目录运行 `start-windows.cmd`（Windows）或 `./start.sh`（Linux/macOS）。脚本会在项目目录创建 `.venv` 并从 Python 包仓库安装依赖，首次运行需要网络。Linux 缺少 Tk 时需先用系统包管理器安装相应的 `python3-tk` 包。
2. 点击“个人账号”无需填写配置；首次使用中转站或 DeepSeek 时，打开右上角“设置”填写信息。中转站只需地址和密钥，DeepSeek 只需密钥。保存后点击对应按钮即可启动。
3. 正常退出已有 Codex Desktop，再从启动器启动。启动器会尝试记住先前的个人模型；没有记录时使用 Codex 的默认模型。历史对话路由桥接仍处于实验阶段。
4. 使用 Chat 适配器时保持启动器运行。关闭启动器会停止适配器并中断相关请求。运行结束后，请通过启动器切回个人账号或恢复上一次配置。

也可以手动安装：

```sh
python -m venv .venv
# 激活 .venv 后：
python -m pip install '.[chat]'
codex-provider-launcher
```

Linux 当前启动 CLI 而非桌面应用；macOS 当前仅尝试默认应用安装路径。Linux 无图形环境不可使用 GUI。当前没有提供签名的独立安装包。

## 配置与隐私

主界面右上角的“日志”按钮可查看、刷新和复制启动检查与报错记录。日志保存在用户配置目录的 `logs/launcher.log`，单个文件最多 1 MB，保留 3 个轮转备份。日志过滤密钥，不记录对话正文；它记录启动器的诊断信息，不包含上游完整请求日志。配置入口仍在右上角“设置”，主界面保留三个启动按钮。

配置和备份保存在各系统的用户配置目录 `codex-provider-launcher` 下；它们可能包含自定义服务地址和个人配置，请勿上传。密钥不存入 `profiles.json`、命令行或项目源码。使用 Codex 时，对话内容、工具输出和其他请求数据会发送到你选择的服务及其上游；启用模型同步时启动器也会访问所选服务。Python 依赖由各自的供应方维护，其网络行为应由使用者审查。

## 开发测试

```sh
python -m pip install -e '.[test,chat]'
python -m unittest discover -s tests -v
# 设置 TEST_LITELLM 为 litellm 可执行文件的路径，可运行模拟上游集成测试。
```

本项目独立开发，与 OpenAI、DeepSeek、OpenCodex 和 LiteLLM 没有官方隶属关系。

## 从源码打包 Windows EXE

分别使用 32 位或 64 位 Python 3.13 环境运行 `python -m pip install . -r packaging/requirements.txt`，再运行 `python packaging/build_windows.py`。程序会按当前 Python 的位数生成 x86 或 x64 EXE 和 SHA256 校验文件到 `dist/`。编译桥接程序需要 Windows 自带的 .NET Framework 4 编译器；成品已包含桥接程序。也可以在 GitHub Actions 中手动运行 `Windows portable EXE` 工作流构建两个版本。

Linux 或 macOS 使用目标系统和架构的 Python 3.12，执行 `python -m pip install . -r packaging/requirements.txt`、`python packaging/build_posix.py`。程序会生成平台对应的压缩包和 SHA256 文件，也可在 GitHub Actions 中手动运行 `Linux and macOS portable builds` 工作流。
