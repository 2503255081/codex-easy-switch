"""A small launcher that asks only for the selected service's essentials."""
from __future__ import annotations

import dataclasses
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from urllib.parse import urlsplit

from .config import restore_last
from .diagnostics import logger, log_path, read_logs, redact
from .launch import launch
from .platforms import is_desktop_running
from .profiles import CredentialStore, ProfileStore, data_dir, new_profile, normalize_url


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
        for mode, label in (("personal", "个人账号"), ("relay", "中转站"), ("deepseek", "DeepSeek")):
            button = ttk.Button(outer, text=label, command=lambda choice=mode: self.launch_mode(choice))
            button.pack(fill="x", ipady=9, pady=5)
            self.launch_buttons.append(button)
        ttk.Label(outer, textvariable=self.status, wraplength=480).pack(anchor="w", pady=12)
        try:
            self.profiles = self.store.load()
        except Exception as exc:
            logger().error("profile loading failed: %s", exc)
            messagebox.showerror("无法读取服务设置", str(exc))

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
        window.geometry("500x310")
        window.minsize(460, 290)
        window.transient(self)
        frame = ttk.Frame(window, padding=18)
        frame.pack(fill="both", expand=True)
        choices = ttk.Frame(frame)
        choices.pack(fill="x", pady=(0, 10))
        for value, label in (("relay", "中转站"), ("deepseek", "DeepSeek")):
            ttk.Radiobutton(choices, text=label, variable=self.mode, value=value,
                            command=self.select_mode).pack(side="left", padx=(0, 22))
        self.form = ttk.Frame(frame)
        self.form.pack(fill="x")
        self.form.columnconfigure(1, weight=1)
        self.url_label = ttk.Label(self.form, text="中转站链接")
        self.url_entry = ttk.Entry(self.form, textvariable=self.url)
        self.key_label = ttk.Label(self.form, text="API 密钥")
        self.key_entry = ttk.Entry(self.form, textvariable=self.key, show="•")
        self.hint = ttk.Label(self.form, wraplength=420)
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
        self.status.set(("中转站" if profile.kind == "responses" else "DeepSeek") + "已保存，请点击对应按钮启动。")

    def existing(self, mode):
        kind = "responses" if mode == "relay" else "deepseek"
        return next((p for p in reversed(self.profiles) if p.kind == kind), None)

    def select_mode(self):
        for widget in (self.url_label, self.url_entry, self.key_label, self.key_entry, self.hint):
            widget.grid_forget()
        self.key.set("")
        mode = self.mode.get()
        prior = self.existing(mode)
        if mode == "relay":
            self.url.set(prior.base_url if prior else "")
            self.url_label.grid(row=0, column=0, sticky="w", padx=(0, 12), pady=5)
            self.url_entry.grid(row=0, column=1, sticky="ew", pady=5)
        self.key_label.grid(row=1, column=0, sticky="w", padx=(0, 12), pady=5)
        self.key_entry.grid(row=1, column=1, sticky="ew", pady=5)
        self.hint.configure(text="密钥已保存，留空可继续使用。" if prior else "密钥保存在本机系统凭据库。")
        self.hint.grid(row=2, column=1, sticky="w", pady=(2, 0))

    def prepare_profile(self):
        mode = self.mode.get()
        if mode == "personal":
            return None
        prior = self.existing(mode)
        url = self.url.get().strip() if mode == "relay" else "https://api.deepseek.com"
        # Explicit acknowledgement is needed before sending an API key over LAN HTTP.
        allow_http = False
        parsed = urlsplit(url)
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            allow_http = messagebox.askyesno("确认 HTTP 连接", "这个地址使用 HTTP，密钥会在网络中明文传输。仍要使用吗？")
            if not allow_http:
                raise ValueError("已取消 HTTP 连接。")
        url = normalize_url(url, allow_http)
        secret = self.key.get().strip()
        if prior and prior.base_url != url and not secret:
            raise ValueError("服务地址已改变，请重新填写对应的密钥。")
        if not secret and prior:
            secret = self.vault.get(prior.id) or ""
        if not secret:
            raise ValueError("请填写 API 密钥。")
        profile = new_profile("中转站" if mode == "relay" else "DeepSeek",
                              "responses" if mode == "relay" else "deepseek", url,
                              "auto" if mode == "relay" else "deepseek-chat", allow_http)
        if prior:
            profile.id = prior.id
        self.vault.put(profile.id, secret)
        updated = [p for p in self.profiles if p.id != profile.id] + [profile]
        self.store.save(updated)
        self.profiles = updated
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
                profile = dataclasses.replace(profile, model="auto" if mode == "relay" else "deepseek-chat",
                                               codex_catalog=False)
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
            self.status.set("已发出启动请求；使用 DeepSeek 期间请保持本窗口打开。")
        else:
            self.status.set("已发出启动请求。")
        logger().info("launch action completed")

    def close(self):
        if self.busy:
            messagebox.showinfo("请稍候", "正在启动，请等待检查结束。")
            return
        if self.adapters and not messagebox.askyesno("关闭 DeepSeek", "关闭启动器会停止 DeepSeek 连接，正在进行的请求会中断。继续吗？"):
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
