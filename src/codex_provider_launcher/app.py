"""A small launcher that asks only for the selected service's essentials."""
from __future__ import annotations

import dataclasses
import os
import re
import threading
import webbrowser
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from urllib.parse import urlsplit

from .config import restore_last
from .diagnostics import logger, log_path, read_logs, redact
from .launch import launch
from .platforms import is_desktop_running, windows_runtime, find_posix_cli, find_linux_desktop, find_mac_desktop
from .catalog import list_models, list_bailian_authorized, test_responses, reasoning_levels
from .profiles import CredentialStore, ProfileStore, data_dir, new_profile, normalize_url


# Public documentation examples, not a claim of account entitlement.
DOUBAO_ON_DEMAND = ("doubao-seed-2-1-pro-260915", "doubao-seed-2-1-lite-260915", "doubao-seed-2-0-lite-260428", "doubao-seed-2-0-mini-260428")
DOUBAO_CODING = ("ark-code-latest", "doubao-seed-evolving", "doubao-seed-2.1-pro", "doubao-seed-2.1-lite", "doubao-seed-2.0-mini", "glm-5.3", "glm-5.3-flash", "deepseek-v4.1-flash", "deepseek-v4.1-flash-economy", "deepseek-v4-flash", "deepseek-v4-pro", "kimi-k2.8-preview", "kimi-k3", "kimi-k3.1-pro")
QIANWEN_TEXT_MODELS = ("qwen3.8-max", "qwen3.8-flash", "qwen3.7-max", "qwen3.7-plus", "qwen3.6-flash")
BAILIAN_TEXT_MODELS = ("qwen3.8-max", "qwen3.8-flash", "qwen3.7-max", "qwen3.7-plus", "qwen3.7-flash", "qwen3.6-plus", "qwen3.6-flash", "qwen3.5-plus", "qwen3.5-flash", "deepseek-v4.1-flash", "deepseek-v4-pro", "glm-5.3", "glm-5.2", "kimi-k3")
PROVIDER_KEY_URLS = {
    "deepseek": "https://platform.deepseek.com/api_keys",
    "doubao": "https://ark.volcengine.com/region:cn-beijing/apiKey",
    "qwen": "https://bailian.console.aliyun.com/cn-beijing/model/settings/api-key",
    "qianwen": "https://platform.qianwenai.com/home/api-keys",
}



def doubao_migration_models(profile):
    """Upgrade legacy single-model settings without changing saved credentials."""
    if profile.models:
        return list(profile.models)
    if profile.base_url.endswith("/api/coding/v3"):
        suggested = DOUBAO_CODING
    elif profile.base_url.endswith("/api/v3"):
        suggested = DOUBAO_ON_DEMAND
    else:
        suggested = ()
    return list(dict.fromkeys([profile.model, *suggested]))


class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Codex 启动器")
        self.geometry("500x390")
        self.minsize(460, 360)
        self.store = ProfileStore()
        self.vault = CredentialStore()
        self.adapters = []
        self.profiles = []
        self.busy = False
        self.mode = tk.StringVar(value="relay")
        self.url = tk.StringVar()
        self.key = tk.StringVar()
        self.model_choice = tk.StringVar(value="自动（服务推荐）")
        self.effort_choice = tk.StringVar(value="跟随当前设置")
        self.connection_status = tk.StringVar()
        self.visible = tk.BooleanVar(value=True)
        self.plan = tk.StringVar(value="按量 API")
        self.qianwen_plan = tk.StringVar(value="按量 API")
        self.more_models = tk.StringVar()
        self.display_name = tk.StringVar()
        self.custom_selected_id = None
        self.status = tk.StringVar(value="请选择一种方式启动 Codex。")
        self.settings_window = None
        self.active_profile = None
        self.log_window = None
        logger().info("launcher opened")
        self.protocol("WM_DELETE_WINDOW", self.close)

        outer = ttk.Frame(self, padding=20)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="Codex 启动器", font=("TkDefaultFont", 17, "bold")).pack(side="left")
        ttk.Button(header, text="⚙ 设置", command=self.open_settings).pack(side="right")
        ttk.Button(header, text="日志", command=self.open_logs).pack(side="right", padx=(0, 6))
        ttk.Label(outer, text="选择使用方式，然后启动 Codex。", wraplength=480).pack(anchor="w", pady=(3, 14))
        self.launch_buttons = []
        self.button_area = ttk.Frame(outer)
        self.button_area.pack(fill="both", expand=True)
        ttk.Label(outer, textvariable=self.status, wraplength=480).pack(anchor="w", pady=12)
        try:
            self.profiles = self.store.load()
        except Exception as exc:
            logger().error("profile loading failed: %s", exc)
            messagebox.showerror("无法读取服务设置", str(exc))
        self.refresh_launch_buttons()

    def refresh_launch_buttons(self):
        for widget in self.button_area.winfo_children():
            widget.destroy()
        self.launch_buttons = []
        buttons = [("个人账号", lambda: self.launch_mode("personal"))]
        for profile in self.profiles:
            if profile.visible:
                buttons.append((profile.name, lambda p=profile: self.launch_saved(p)))
        for label, command in buttons:
            button = ttk.Button(self.button_area, text=label, command=command)
            button.pack(fill="x", ipady=6, pady=3)
            self.launch_buttons.append(button)

    def launch_saved(self, profile):
        if self.busy:
            return
        try:
            if not self.vault.get(profile.id):
                raise ValueError("没有保存密钥，请打开设置填写。")
            if profile.kind == "deepseek":
                model = profile.model if profile.model in ("deepseek-flash", "deepseek-v4-pro") else "deepseek-flash"
                profile = dataclasses.replace(profile, model=model, codex_catalog=False)
            elif profile.provider == "doubao" and profile.kind == "fixed_responses" and not profile.models:
                profile = dataclasses.replace(profile, models=doubao_migration_models(profile))
            elif profile.provider == "qwen" and profile.kind == "fixed_responses" and not profile.models:
                profile = dataclasses.replace(profile, models=list(dict.fromkeys([profile.model, *BAILIAN_TEXT_MODELS])))
            self.start(profile)
        except Exception as exc:
            messagebox.showerror("无法启动", str(exc))

    def open_logs(self):
        if self.log_window is not None and self.log_window.winfo_exists():
            self.log_window.lift()
            return
        window = tk.Toplevel(self)
        self.log_window = window
        window.title("启动器日志")
        window.geometry("820x500")
        outer = ttk.Frame(window, padding=12)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="启动检查和报错记录（密钥已过滤）").pack(anchor="w")
        output = ScrolledText(outer, wrap="word", font=("TkFixedFont", 10))
        output.pack(fill="both", expand=True, pady=8)
        def refresh():
            try:
                value = read_logs()
            except OSError as exc:
                value = "读取日志失败：" + redact(str(exc))
            output.configure(state="normal")
            output.delete("1.0", "end")
            output.insert("1.0", value)
            output.configure(state="disabled")
            output.see("end")
        def copy():
            self.clipboard_clear()
            self.clipboard_append(output.get("1.0", "end-1c"))
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="刷新", command=refresh).pack(side="left")
        ttk.Button(buttons, text="复制日志", command=copy).pack(side="left", padx=6)
        ttk.Label(outer, text=str(log_path()), wraplength=780).pack(anchor="w", pady=(8, 0))
        refresh()

    def report_callback_exception(self, exc_type, value, tb):
        import traceback
        logger().error("GUI callback failed: %s", "".join(traceback.format_exception(exc_type, value, tb)))
        messagebox.showerror("操作失败", "发生错误，请点击右上角“日志”查看详细记录。")

    def open_settings(self, mode="relay"):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        self.mode.set(mode)
        window = tk.Toplevel(self)
        self.settings_window = window
        window.title("设置")
        window.geometry("760x690")
        window.minsize(660, 590)
        window.transient(self)
        frame = ttk.Frame(window, padding=18)
        frame.pack(fill="both", expand=True)
        choices = ttk.Frame(frame)
        choices.pack(fill="x", pady=(0, 10))
        providers = (("deepseek", "DeepSeek"), ("doubao", "豆包 / 火山方舟"), ("qianwen", "千问 AI 平台"), ("qwen", "阿里云百炼"), ("relay", "自定义模型"))
        for index, (value, label) in enumerate(providers):
            ttk.Radiobutton(choices, text=label, variable=self.mode, value=value,
                            command=self.select_mode).grid(row=index // 3, column=index % 3, sticky="w", padx=(0, 18), pady=3)
        self.custom_frame = ttk.Frame(frame)
        ttk.Label(self.custom_frame, text="已有配置").pack(side="left", padx=(0, 8))
        self.custom_picker = ttk.Combobox(self.custom_frame, state="readonly")
        self.custom_picker.pack(side="left", fill="x", expand=True)
        self.custom_picker.bind("<<ComboboxSelected>>", self.select_custom_profile)
        self.form = ttk.Frame(frame)
        self.form.pack(fill="x")
        self.form.columnconfigure(1, weight=1)
        self.url_label = ttk.Label(self.form, text="中转站链接")
        self.url_entry = ttk.Entry(self.form, textvariable=self.url)
        self.plan_label = ttk.Label(self.form, text="账号类型")
        self.plan_entry = ttk.Combobox(self.form, textvariable=self.plan, state="readonly", values=("按量 API", "Coding Plan", "Agent Plan"))
        self.plan_entry.bind("<<ComboboxSelected>>", self.select_plan)
        self.qianwen_plan_entry = ttk.Combobox(self.form, textvariable=self.qianwen_plan, state="readonly", values=("按量 API", "Token Plan"))
        self.key_link = ttk.Button(self.form, text="申请 API Key ↗", command=self.open_key_link)
        self.name_label = ttk.Label(self.form, text="按钮名称")
        self.name_entry = ttk.Entry(self.form, textvariable=self.display_name)
        self.more_label = ttk.Label(self.form, text="其他模型 ID")
        self.more_entry = ttk.Entry(self.form, textvariable=self.more_models)
        self.fetch_models_button = ttk.Button(self.form, text="读取模型列表", command=self.fetch_provider_models)
        self.key_label = ttk.Label(self.form, text="API 密钥")
        self.key_frame = ttk.Frame(self.form)
        self.key_frame.columnconfigure(0, weight=1)
        self.key_entry = ttk.Entry(self.key_frame, textvariable=self.key, show="•")
        self.key_entry.grid(row=0, column=0, sticky="ew")
        self.key_button = ttk.Button(self.key_frame, text="修改", command=self.edit_key)
        self.key_button.grid(row=0, column=1, padx=(6, 0))
        self.hint = ttk.Label(self.form, wraplength=420)
        self.test_button = ttk.Button(self.form, text="测试连接并获取模型", command=self.test_connection)
        self.test_status = ttk.Label(self.form, textvariable=self.connection_status, wraplength=420)
        self.model_label = ttk.Label(self.form, text="启动默认模型")
        self.model_entry = ttk.Combobox(self.form, textvariable=self.model_choice, values=("自动（服务推荐）", "上次使用（无记录时自动）"))
        self.model_entry.bind("<<ComboboxSelected>>", lambda _event: self.update_efforts())
        self.model_entry.bind("<KeyRelease>", lambda _event: self.update_efforts())
        self.model_details = {}
        self.effort_label = ttk.Label(self.form, text="默认推理深度")
        self.effort_entry = ttk.Combobox(self.form, textvariable=self.effort_choice, state="readonly", values=("跟随当前设置", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"))
        self.visible_check = ttk.Checkbutton(frame, text="在启动页显示此提供商", variable=self.visible)
        self.visible_check.pack(anchor="w", pady=(10, 0))
        ttk.Button(frame, text="保存设置", command=self.save_settings).pack(fill="x", pady=(12, 0))
        ttk.Button(frame, text="恢复上一次切换前的配置", command=self.restore).pack(fill="x", pady=(7, 0))
        self.select_mode()
        window.grab_set()

    def save_settings(self):
        try:
            profile = self.prepare_profile()
        except Exception as exc:
            logger().error("settings save failed: %s", exc)
            messagebox.showerror("保存失败", str(exc), parent=self.settings_window)
            return
        self.settings_window.destroy()
        self.settings_window = None
        self.refresh_launch_buttons()
        self.status.set(profile.name + "已保存。")

    def existing(self, mode):
        if mode == "relay" and getattr(self, "custom_selected_id", None) is not None:
            return next((p for p in self.profiles if p.id == self.custom_selected_id), None)
        def matches(p):
            if p.provider:
                return p.provider == ("custom" if mode == "relay" else mode)
            return (mode == "relay" and p.kind == "responses") or (mode == "deepseek" and p.kind == "deepseek")
        return next((p for p in reversed(self.profiles) if matches(p)), None)

    def select_mode(self):
        for widget in (self.url_label, self.url_entry, self.plan_label, self.plan_entry, self.key_label, self.key_frame, self.hint, self.test_button, self.test_status, self.model_label, self.model_entry, self.effort_label, self.effort_entry, self.name_label, self.name_entry, self.more_label, self.more_entry, self.fetch_models_button, self.qianwen_plan_entry, self.key_link):
            widget.grid_forget()
        mode = self.mode.get()
        if mode == "relay":
            self.custom_frame.pack(fill="x", pady=(0, 8), before=self.form)
            choices = [p for p in self.profiles if p.provider == "custom" or (not p.provider and p.kind == "responses")]
            self.custom_ids = [""] + [p.id for p in choices]
            self.custom_picker.configure(values=["＋ 新建自定义模型"] + [p.name for p in choices])
            if self.custom_selected_id is None:
                self.custom_selected_id = choices[-1].id if choices else ""
            if self.custom_selected_id not in self.custom_ids:
                self.custom_selected_id = ""
            self.custom_picker.current(self.custom_ids.index(self.custom_selected_id))
        else:
            self.custom_frame.pack_forget()
        prior = self.existing(mode)
        self.visible.set(prior.visible if prior else True)
        self.key.set("")
        self.connection_status.set("")
        self.model_details = {}
        saved_key = bool(prior and self.vault.get(prior.id))
        self.key_editing = not saved_key
        self.key.set("已保存的密钥" if saved_key else "")
        self.key_entry.configure(state="readonly" if saved_key else "normal")
        self.key_button.configure(state="normal" if saved_key else "disabled")
        if mode in ("relay", "qwen"):
            self.url_label.configure(text="API 地址" if mode == "relay" else "工作空间 ID（可选）")
            value = prior.base_url if prior else ""
            if mode == "qwen" and value:
                match = re.fullmatch(r"https://([a-zA-Z0-9-]+)\.cn-beijing\.maas\.aliyuncs\.com/compatible-mode/v1", value)
                value = match.group(1) if match else value
            self.url.set(value)
            if mode == "relay":
                self.display_name.set(prior.name if prior else "")
                self.name_label.grid(row=7, column=0, sticky="w", pady=5)
                self.name_entry.grid(row=7, column=1, sticky="ew", pady=5)
            self.url_label.grid(row=0, column=0, sticky="w", padx=(0, 12), pady=5)
            self.url_entry.grid(row=0, column=1, sticky="ew", pady=5)
        if mode == "doubao":
            choices = {"https://ark.cn-beijing.volces.com/api/coding/v3": "Coding Plan", "https://ark.cn-beijing.volces.com/api/plan/v3": "Agent Plan"}
            self.plan.set(choices.get(prior.base_url, "按量 API") if prior else "按量 API")
            self.plan_label.grid(row=0, column=0, sticky="w", padx=(0, 12), pady=5)
            self.plan_entry.grid(row=0, column=1, sticky="ew", pady=5)
        if mode == "qianwen":
            self.qianwen_plan.set("Token Plan" if prior and prior.base_url.startswith("https://token-plan.") else "按量 API")
            self.plan_label.grid(row=0, column=0, sticky="w", padx=(0, 12), pady=5)
            self.qianwen_plan_entry.grid(row=0, column=1, sticky="ew", pady=5)
        if mode in PROVIDER_KEY_URLS:
            titles = {"deepseek": "DeepSeek 密钥页 ↗", "doubao": "方舟密钥页 ↗", "qwen": "百炼密钥页 ↗", "qianwen": "千问 AI 密钥页 ↗"}
            self.key_link.configure(text=titles[mode])
            self.key_link.grid(row=1, column=2, sticky="w", padx=(6, 0))
        self.key_label.grid(row=1, column=0, sticky="w", padx=(0, 12), pady=5)
        self.key_frame.grid(row=1, column=1, sticky="ew", pady=5)
        hint = "密钥保存在本机系统凭据库。"
        if mode == "qwen":
            hint += " 候选模型可增删；查询已授权模型需填写北京业务空间 ID。"
        elif mode == "doubao":
            hint += " 列表为文档候选；权限以方舟控制台为准，可增删其他模型。"
        elif mode == "qianwen":
            hint += " 使用千问 AI 平台密钥；按量与 Token Plan 的密钥和地址不可混用。"
        elif mode == "relay":
            hint += " 需支持 OpenAI Responses API。"
        if mode in ("doubao", "qwen", "qianwen"):
            hint += " 未确认档位的模型暂只显示 low；具体能力以平台为准。"
        self.hint.configure(text=hint)
        self.hint.grid(row=2, column=1, sticky="w", pady=(2, 0))
        self.test_button.configure(text="测试连接并获取模型" if mode == "relay" else "测试模型（可能产生费用）")
        self.test_button.grid(row=3, column=1, sticky="w", pady=(8, 0))
        self.test_status.grid(row=4, column=1, sticky="w")
        self.model_entry.configure(values=("自动（服务推荐）", "上次使用（无记录时自动）") if mode == "relay" else ())
        if mode in ("doubao", "qianwen", "qwen"):
            if mode in ("doubao", "qwen"):
                self.fetch_models_button.configure(text="读取授权模型" if mode == "qwen" else "读取模型列表")
                self.fetch_models_button.grid(row=3, column=0, sticky="w", pady=(8, 0))
            suggested = BAILIAN_TEXT_MODELS if mode == "qwen" else (QIANWEN_TEXT_MODELS if mode == "qianwen" else (DOUBAO_CODING if self.plan.get() == "Coding Plan" else (DOUBAO_ON_DEMAND if self.plan.get() == "按量 API" else ())))
            models = prior.models if prior and prior.models else list(dict.fromkeys(([prior.model] if prior else []) + list(suggested)))
            self.more_models.set(", ".join(m for m in models if not prior or m != prior.model) if prior else ", ".join(models[1:]))
            self.more_label.grid(row=6, column=0, sticky="w", pady=5)
            self.more_entry.grid(row=6, column=1, sticky="ew", pady=5)
            self.model_entry.configure(values=models)
        if mode == "deepseek":
            self.fetch_models_button.configure(text="读取模型列表")
            self.fetch_models_button.grid(row=3, column=0, sticky="w", pady=(8, 0))
            selected = prior.model if prior and prior.model in ("deepseek-flash", "deepseek-v4-pro") else "deepseek-flash"
            self.model_choice.set(selected)
            self.model_label.configure(text="启动默认模型")
            self.model_label.grid(row=5, column=0, sticky="w", pady=5)
            self.model_entry.configure(state="readonly", values=("deepseek-flash", "deepseek-v4-pro"))
            self.model_entry.grid(row=5, column=1, sticky="ew", pady=5)
        else:
            self.model_entry.configure(state="normal")
            selected = prior.model if prior else {"relay": "auto", "doubao": "doubao-seed-2-1-pro-260915", "qwen": "qwen3.8-max", "qianwen": "qwen3.8-max"}.get(mode, "")
            self.model_choice.set({"auto": "自动（服务推荐）", "last": "上次使用（无记录时自动）"}.get(selected, selected))
            self.model_label.configure(text="模型名称 / ID")
            self.model_label.grid(row=5, column=0, sticky="w", pady=5)
            self.model_entry.grid(row=5, column=1, sticky="ew", pady=5)
            if mode == "relay":
                self.effort_choice.set(prior.reasoning_effort if prior and prior.reasoning_effort else "跟随当前设置")
                self.effort_label.grid(row=6, column=0, sticky="w", pady=5)
                self.effort_entry.grid(row=6, column=1, sticky="ew", pady=5)
            elif mode in ("doubao", "qwen", "qianwen"):
                self.effort_choice.set(prior.reasoning_effort if prior and prior.reasoning_effort else "跟随当前设置")
                self.effort_label.grid(row=7, column=0, sticky="w", pady=5)
                self.effort_entry.grid(row=7, column=1, sticky="ew", pady=5)
                self.update_efforts()

    def open_key_link(self):
        url = PROVIDER_KEY_URLS.get(self.mode.get())
        if url:
            webbrowser.open(url)

    def select_custom_profile(self, _event=None):
        index = self.custom_picker.current()
        if 0 <= index < len(self.custom_ids):
            self.custom_selected_id = self.custom_ids[index]
            self.select_mode()

    def select_plan(self, _event=None):
        if self.plan.get() == "Coding Plan":
            models = DOUBAO_CODING
        elif self.plan.get() == "按量 API":
            models = DOUBAO_ON_DEMAND
        else:
            # Agent Plan access is account-specific: start with no assumptions.
            models = ()
        self.model_choice.set(models[0] if models else "")
        self.more_models.set(", ".join(models[1:]))
        self.model_entry.configure(values=models)
        self.update_efforts()
        self.connection_status.set("候选模型以账号权限为准；套餐变更后请使用对应密钥。")

    def fetch_provider_models(self):
        try:
            mode = self.mode.get()
            url, secret, allow_http = self.connection_details()
            profile = new_profile(mode, "responses", url, "auto", allow_http)
        except Exception as exc:
            messagebox.showerror("无法读取模型", str(exc), parent=self.settings_window)
            return
        plan = self.plan.get()
        workspace_input = self.url.get().strip() if mode == "qwen" else ""
        window = self.settings_window
        self.fetch_models_button.configure(state="disabled")
        self.connection_status.set("正在尝试读取服务返回的模型列表……")
        def worker():
            try:
                if mode == "qwen":
                    names = list_bailian_authorized(profile.base_url, secret, BAILIAN_TEXT_MODELS)
                else:
                    models, _ = list_models(profile, secret, None, opencodex_header=False)
                    names = [name for name, _ in models]
                error = None
            except Exception as exc:
                names, error = [], redact(str(exc))
            def done():
                if not window.winfo_exists(): return
                self.fetch_models_button.configure(state="normal")
                if self.mode.get() != mode or (mode == "doubao" and self.plan.get() != plan) or (mode == "qwen" and self.url.get().strip() != workspace_input):
                    self.connection_status.set("配置已改变，请重新读取。")
                elif error:
                    self.connection_status.set("未获取列表，可按官方控制台手动填写：" + error)
                else:
                    selected = self.model_choice.get().strip()
                    if mode == "qwen":
                        if not names:
                            self.connection_status.set("未查到已授权的内置 Responses 候选；保留手动列表。")
                            return
                        if selected not in names:
                            selected = names[0]
                            self.model_choice.set(selected)
                        self.model_entry.configure(values=names)
                        self.more_models.set(", ".join(name for name in names if name != selected))
                        self.connection_status.set(f"读取到 {len(names)} 个已授权的内置 Responses 候选；请保存设置。")
                    elif mode == "deepseek":
                        supported = [name for name in names if name in ("deepseek-flash", "deepseek-v4-pro")]
                        if not supported:
                            self.connection_status.set("返回列表未包含当前支持的 Responses 模型；保留内置列表。")
                            return
                        self.model_entry.configure(values=supported)
                        if selected not in supported:
                            self.model_choice.set(supported[0])
                        self.connection_status.set(f"读取到 {len(supported)} 个支持 Responses 的模型。")
                    else:
                        names = list(dict.fromkeys(([selected] if selected else []) + names))[:100]
                        self.model_entry.configure(values=names)
                        self.more_models.set(", ".join(name for name in names if name != selected))
                        self.connection_status.set(f"显示 {len(names)} 个模型 ID（最多 100 个）；权限以实际调用结果为准。")
            self.after(0, done)
        threading.Thread(target=worker, daemon=True).start()

    def edit_key(self):
        self.key_editing = True
        self.key_entry.configure(state="normal")
        self.key.set("")
        self.key_entry.focus_set()

    def connection_details(self):
        prior = self.existing(self.mode.get())
        mode = self.mode.get()
        if mode == "relay":
            url = self.url.get().strip()
        elif mode == "qwen":
            workspace = self.url.get().strip()
            if not workspace:
                url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            elif re.fullmatch(r"[A-Za-z0-9-]+", workspace):
                url = f"https://{workspace}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
            else:
                url = workspace
        elif mode == "qianwen":
            url = {"按量 API": "https://maas.qianwenaiapi.com/compatible-mode/v1", "Token Plan": "https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1"}[self.qianwen_plan.get()]
        elif mode == "doubao":
            plan = self.plan.get()
            url = {"按量 API": "https://ark.cn-beijing.volces.com/api/v3", "Coding Plan": "https://ark.cn-beijing.volces.com/api/coding/v3", "Agent Plan": "https://ark.cn-beijing.volces.com/api/plan/v3"}[plan]
        else:
            url = "https://api.deepseek.com"
        parsed = urlsplit(url)
        allow_http = False
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            allow_http = bool(prior and prior.base_url == url and prior.allow_http)
            if not allow_http:
                allow_http = messagebox.askyesno("确认 HTTP 连接", "这个地址使用 HTTP，密钥会在网络中明文传输。仍要使用吗？", parent=self.settings_window)
                if not allow_http:
                    raise ValueError("已取消 HTTP 连接。")
        url = normalize_url(url, allow_http)
        secret = self.key.get().strip() if getattr(self, "key_editing", True) else ""
        if prior and prior.base_url != url and not secret:
            raise ValueError("服务地址已改变，请点击修改并重新填写对应的密钥。")
        if not secret and prior and not getattr(self, "key_editing", True):
            secret = self.vault.get(prior.id) or ""
        if not secret:
            raise ValueError("请填写 API 密钥。")
        if mode == "qianwen":
            is_plan = self.qianwen_plan.get() == "Token Plan"
            if secret.startswith("sk-sp-") and not is_plan:
                raise ValueError("此密钥是 Token Plan 密钥，请选择 Token Plan，避免使用错误计费通道。")
            if secret.startswith("sk-ws-") and is_plan:
                raise ValueError("此密钥是按量 API 密钥，请选择按量 API。")
        return url, secret, allow_http

    def test_connection(self):
        if self.mode.get() != "relay":
            self.test_direct_connection()
            return
        try:
            url, secret, allow_http = self.connection_details()
            profile = new_profile("中转站", "responses", url, "auto", allow_http)
        except Exception as exc:
            messagebox.showerror("连接测试失败", str(exc), parent=self.settings_window)
            return
        self.test_button.configure(state="disabled")
        self.connection_status.set("正在连接并读取模型列表……")
        window = self.settings_window
        def worker():
            try:
                desktop = find_mac_desktop() if sys.platform == "darwin" else (find_linux_desktop() if sys.platform.startswith("linux") else None)
                cli = windows_runtime()[1] if os.name == "nt" else find_posix_cli(desktop)
                if not cli:
                    raise RuntimeError("找不到 Codex CLI，请先安装 Codex CLI。")
                models, _ = list_models(profile, secret, cli)
                names = [name for name, _ in models]
                error = None
            except Exception as exc:
                names, error = [], redact(str(exc))
            def done():
                if not window.winfo_exists():
                    return
                self.test_button.configure(state="normal")
                if self.mode.get() != "relay" or self.url.get().strip().rstrip("/") != url:
                    self.connection_status.set("设置已更改，请重新测试。")
                    return
                if error:
                    logger().warning("relay connection test failed: %s", error)
                    self.connection_status.set("连接失败：" + error)
                else:
                    logger().info("relay connection test succeeded models=%s", len(names))
                    self.model_details = dict(models)
                    self.model_entry.configure(values=("自动（服务推荐）", "上次使用（无记录时自动）", *names))
                    self.update_efforts()
                    self.connection_status.set(f"连接成功，读取到 {len(names)} 个可用模型。此测试不发送对话请求。")
            self.after(0, done)
        threading.Thread(target=worker, daemon=True).start()

    def test_direct_connection(self):
        try:
            mode = self.mode.get()
            url, secret, allow_http = self.connection_details()
            model = self.model_choice.get().strip()
            profile = new_profile(mode, "fixed_responses", url, model, allow_http)
        except Exception as exc:
            messagebox.showerror("模型测试失败", str(exc), parent=self.settings_window)
            return
        self.test_button.configure(state="disabled")
        self.connection_status.set("正在发送简短测试请求，可能产生费用……")
        window = self.settings_window
        def worker():
            try:
                test_responses(profile, secret)
                error = None
            except Exception as exc:
                error = redact(str(exc))
            def done():
                if not window.winfo_exists():
                    return
                self.test_button.configure(state="normal")
                if self.mode.get() != mode:
                    self.connection_status.set("提供商已更改，请重新测试。")
                elif error:
                    logger().warning("provider model test failed: %s", error)
                    self.connection_status.set("测试失败：" + error)
                else:
                    self.connection_status.set("测试成功：模型已响应；工具调用兼容性需另行验证。")
            self.after(0, done)
        threading.Thread(target=worker, daemon=True).start()

    def update_efforts(self):
        mode = self.mode.get()
        if mode in ("doubao", "qwen", "qianwen"):
            choices = reasoning_levels(mode, self.model_choice.get().strip())
            self.effort_entry.configure(values=("跟随当前设置", *choices))
            if self.effort_choice.get() not in ("跟随当前设置", *choices):
                self.effort_choice.set("跟随当前设置")
            return
        details = self.model_details.get(self.model_choice.get(), {})
        levels = details.get("supported_reasoning_levels") or details.get("supportedReasoningEfforts") or []
        allowed = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
        available = [e.get("effort") or e.get("reasoningEffort") for e in levels if isinstance(e, dict)]
        choices = [e for e in allowed if not available or e in available]
        self.effort_entry.configure(values=("跟随当前设置", *choices))
        if self.effort_choice.get() not in ("跟随当前设置", *choices):
            self.effort_choice.set("跟随当前设置")

    def prepare_profile(self):
        mode = self.mode.get()
        if mode == "personal":
            return None
        prior = self.existing(mode)
        url, secret, allow_http = Launcher.connection_details(self)
        selected = self.model_choice.get().strip() if mode != "deepseek" or self.model_choice.get().strip() in ("deepseek-flash", "deepseek-v4-pro") else "deepseek-flash"
        selected = {"自动（服务推荐）": "auto", "上次使用（无记录时自动）": "last"}.get(selected, selected)
        if mode in ("doubao", "qwen", "qianwen") and selected in ("", "auto", "last"):
            raise ValueError("请填写提供商控制台中已开通的模型 ID。")
        names = {"relay": "自定义模型", "deepseek": "DeepSeek", "doubao": "豆包 / 火山方舟", "qwen": "阿里云百炼", "qianwen": "千问 AI 平台"}
        discovered = getattr(self, "model_details", {})
        use_catalog = mode == "relay" and (selected in ("auto", "last") or selected in discovered or bool(prior and prior.kind == "responses"))
        kind = "deepseek" if mode == "deepseek" else ("responses" if use_catalog else "fixed_responses")
        if mode == "relay":
            name = self.display_name.get().strip() if hasattr(self, "display_name") else (selected if selected not in ("auto", "last") else "自定义模型")
            if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
                raise ValueError("请填写显示在启动页的按钮名称。")
        else:
            name = names[mode]
        profile = new_profile(name, kind, url, selected, allow_http)
        if mode in ("doubao", "qianwen", "qwen"):
            extra = getattr(self, "more_models", None)
            values = re.split(r"[\s,，;；]+", extra.get().strip()) if extra and extra.get().strip() else []
            profile.models = list(dict.fromkeys([selected, *values]))
        profile.provider = "custom" if mode == "relay" else mode
        profile.visible = self.visible.get() if hasattr(self, "visible") else True
        if (mode == "relay" and kind == "responses") or mode in ("doubao", "qwen", "qianwen"):
            effort = self.effort_choice.get()
            profile.reasoning_effort = "" if effort == "跟随当前设置" else effort
            if mode in ("doubao", "qwen", "qianwen") and profile.reasoning_effort and profile.reasoning_effort not in reasoning_levels(mode, selected):
                raise ValueError("该模型不支持所选推理深度，请重新选择。")
        profile.validate()
        if prior:
            profile.id = prior.id
        self.vault.put(profile.id, secret)
        updated = [p for p in self.profiles if p.id != profile.id] + [profile]
        self.store.save(updated)
        self.profiles = updated
        if mode == "relay" and hasattr(self, "custom_selected_id"):
            self.custom_selected_id = profile.id
        logger().info("settings saved kind=%s", profile.kind)
        self.key.set("")
        return profile

    def launch_mode(self, mode):
        if self.busy:
            return
        try:
            profile = None if mode == "personal" else self.existing(mode)
            if mode != "personal":
                if profile is None or (mode == "deepseek" and profile.base_url != "https://api.deepseek.com") or not self.vault.get(profile.id):
                    self.status.set("请先填写并保存" + ("中转站" if mode == "relay" else "DeepSeek") + "设置。")
                    self.open_settings(mode)
                    return
                if mode == "deepseek":
                    model = profile.model if profile.model in ("deepseek-flash", "deepseek-v4-pro") else "deepseek-flash"
                    profile = dataclasses.replace(profile, model=model, codex_catalog=False)
        except Exception as exc:
            logger().error("launch preparation failed: %s", exc)
            messagebox.showerror("无法启动", str(exc))
            return
        self.start(profile)

    def start(self, profile, isolate=False):
        if self.busy:
            return
        self.busy = True
        self.active_profile = profile
        for button in self.launch_buttons:
            button.configure(state="disabled")
        self.status.set("正在检查配置并启动，请稍候……")
        def worker():
            try:
                _, adapter = launch(profile, "", "", "", isolate)
            except Exception as exc:
                import traceback
                logger().error("launch failed: %s", traceback.format_exc())
                message = str(exc)
                self.after(0, lambda: self.finish(None, message))
            else:
                self.after(0, lambda: self.finish(adapter, None))
        threading.Thread(target=worker, daemon=True).start()

    def finish(self, adapter, error):
        self.busy = False
        for button in self.launch_buttons:
            button.configure(state="normal")
        if error:
            self.status.set("启动失败，原有 Codex 配置未被覆盖。")
            if "model_catalog_json" in error and messagebox.askyesno("发现旧模型目录", "旧配置中有自定义模型目录，可能与当前版本冲突。是否先备份原配置并隔离此目录后重试？"):
                self.start(self.active_profile, isolate=True)
                return
            messagebox.showerror("启动失败", error)
            return
        if adapter:
            self.adapters.append(adapter)
            self.status.set("已发出启动请求；使用本地协议适配器期间请保持本窗口打开。")
        else:
            self.status.set("已发出启动请求。")
        logger().info("launch action completed")

    def close(self):
        if self.busy:
            messagebox.showinfo("请稍候", "正在启动，请等待检查结束。")
            return
        if self.adapters and not messagebox.askyesno("关闭本地适配器", "关闭启动器会停止本地适配连接，正在进行的请求会中断。继续吗？"):
            return
        for adapter in self.adapters:
            adapter.__exit__(None, None, None)
        logger().info("launcher closed")
        self.destroy()

    def restore(self):
        if not messagebox.askyesno("恢复配置", "恢复上次切换前的配置；该配置可能包含旧插件或失效的模型目录。继续吗？"):
            return
        try:
            if is_desktop_running():
                raise RuntimeError("请先正常退出 Codex Desktop，避免覆盖它正在使用的配置。")
            home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
            restore_last(home / "config.toml", data_dir() / "backups")
            logger().info("previous configuration restored")
            self.status.set("已恢复上次切换前的配置。")
        except Exception as exc:
            logger().error("configuration restore failed: %s", exc)
            messagebox.showerror("恢复失败", str(exc))


def main():
    Launcher().mainloop()


if __name__ == "__main__":
    main()
