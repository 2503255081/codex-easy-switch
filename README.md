# Codex Provider Launcher

一个跨平台的本地 Codex 启动器，供用户在个人账号和自定义 API 服务之间切换。服务地址、模型和密钥由使用者自行提供，项目不包含预设中转站或账号。

**目前是实验版本。** Windows 图形界面、隔离配置校验和模拟协议转换已测试；Linux 在 WSL 中通过核心测试；macOS 桌面启动、真实 DeepSeek 请求和完整的桌面切换尚未验证。请先备份配置和聊天数据，不要把它用于无法中断的工作。

## 主要功能

- 个人账号与 Responses 兼容服务之间切换；可手动填写服务名称、地址、模型和密钥。
- DeepSeek 等 Chat Completions 兼容服务通过本机 LiteLLM 适配；本机代理只监听 `127.0.0.1` 并设置随机访问密钥。上游必须支持所需的流式、工具调用及其他能力，不能保证所有模型和服务都兼容 Codex。
- API 密钥使用系统凭据库；非本机 HTTP 默认禁用。服务地址只保存在本机配置文件中。
- 切换前通过实际 Codex CLI 在临时目录检查配置，备份原始配置，检测并发编辑；可在启动器中恢复上次配置。聊天和账号文件不会被主动改写。
- 可选同步 OpenCodex 的原生模型目录，保留服务端的显示/隐藏状态并折叠重复的 `--fast` 条目；普通 Responses 服务需要手工填写模型 ID，桌面选择器不会自动同步它们的模型目录。
- 遇到不认识的旧 profile、provider 或自定义目录时停止；对于旧模型目录，可显式勾选隔离。不是所有 CCSwitch 配置都能自动修复。

## 使用

安装 Python 3.10 或更新版本、带 Tk 的 Python、Codex CLI 和对应平台的 Codex。Linux 还需要图形桌面、终端模拟器和已解锁的 Secret Service。macOS 需要钥匙串。Windows 的桌面入口目前面向 Microsoft Store 安装的 Codex。

1. 下载源码，在项目目录运行 `start-windows.cmd`（Windows）或 `./start.sh`（Linux/macOS）。脚本会在项目目录创建 `.venv` 并从 Python 包仓库安装依赖，首次运行需要网络。Linux 缺少 Tk 时需先用系统包管理器安装相应的 `python3-tk` 包。
2. 选择“个人账号”，或者新建服务，填写地址、模型和 API 密钥并保存。标准 Responses 中转站选择 `responses`；DeepSeek 原生 API 选择 `deepseek`，地址可填写 `https://api.deepseek.com`；其他 Chat Completions 兼容服务选择 `openai_chat`。
3. 正常退出已有 Codex Desktop，再从启动器启动。启动器会尝试记住先前的个人模型；如果没有记录，请填写个人账号支持的模型 ID。历史对话路由桥接仍处于实验阶段。
4. 使用 Chat 适配器时保持启动器运行。关闭启动器会停止适配器并中断相关请求。运行结束后，请通过启动器切回个人账号或恢复上一次配置。

也可以手动安装：

```sh
python -m venv .venv
# 激活 .venv 后：
python -m pip install '.[chat]'
codex-provider-launcher
```

Linux 当前启动 CLI 而非桌面应用；macOS 的可执行文件位置可能因安装方式不同，需要在界面中指定。Linux 无图形环境不可使用 GUI。当前没有提供签名的独立安装包。

## 配置与隐私

配置和备份保存在各系统的用户配置目录 `codex-provider-launcher` 下；它们可能包含自定义服务地址和个人配置，请勿上传。密钥不存入 `profiles.json`、命令行或项目源码。应用不会自动上传个人配置或对话；连接测试及模型同步会访问用户指定的服务。Python 依赖由各自的供应方维护，使用前请自行审查。

## 开发测试

```sh
python -m pip install -e '.[test,chat]'
python -m unittest discover -s tests -v
# 设置 TEST_LITELLM 为 litellm 可执行文件的路径，可运行模拟上游集成测试。
```

本项目独立开发，与 OpenAI、DeepSeek、OpenCodex 和 LiteLLM 没有官方隶属关系。
