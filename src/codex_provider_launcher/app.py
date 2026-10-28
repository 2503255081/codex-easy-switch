"""Small cross-platform desktop launcher."""
from __future__ import annotations

import sys
import os
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from pathlib import Path

from .config import restore_last
from .launch import launch
from .platforms import is_desktop_running
from .profiles import CredentialStore, ProfileStore, data_dir, new_profile


class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Codex Provider Launcher")
        self.geometry("660x690")
        self.minsize(600, 650)
        self.store = ProfileStore()
        self.vault = CredentialStore()
        self.adapters = []
        self.profiles = []
        self.profile_map = {}
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.selection = tk.StringVar()
        self.name_var = tk.StringVar()
        self.kind = tk.StringVar(value="responses")
        self.url = tk.StringVar()
        self.model = tk.StringVar()
        self.key = tk.StringVar()
        self.personal_model = tk.StringVar()
        self.cli_path = tk.StringVar()
        self.desktop_path = tk.StringVar()
        self.allow_http = tk.BooleanVar()
        self.codex_catalog = tk.BooleanVar()
        self.isolate_catalog = tk.BooleanVar()
        self.status = tk.StringVar(value="选择个人账号，或添加一个 API 服务。")
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Codex 启动器", font=("TkDefaultFont", 16, "bold")).pack(anchor="w")
        ttk.Label(outer, text="切换个人账号或兼容接口；原有聊天和项目配置会保留。").pack(anchor="w", pady=(2, 10))
        row = ttk.Frame(outer)
        row.pack(fill="x")
        ttk.Label(row, text="启动方式").pack(side="left")
        self.choices = ttk.Combobox(row, textvariable=self.selection, state="readonly")
        self.choices.pack(side="left", fill="x", expand=True, padx=8)
        self.choices.bind("<<ComboboxSelected>>", self.select_profile)
        ttk.Button(row, text="新建", command=self.new).pack(side="left")
        form = ttk.LabelFrame(outer, text="API 服务设置", padding=10)
        form.pack(fill="x", pady=10)
        self._field(form, "名称", self.name_var, 0)
        ttk.Label(form, text="接口类型").grid(row=1, column=0, sticky="w", pady=3)
        kinds = ttk.Combobox(form, textvariable=self.kind, values=("responses", "deepseek", "openai_chat"), state="readonly")
        kinds.grid(row=1, column=1, sticky="ew", pady=3)
        kinds.bind("<<ComboboxSelected>>", self.select_kind)
        self._field(form, "API 地址", self.url, 2)
        self._field(form, "模型 ID", self.model, 3)
        self._field(form, "API 密钥", self.key, 4, show="•")
        ttk.Checkbutton(form, text="允许非本机 HTTP（密钥会明文传输）", variable=self.allow_http).grid(row=5, column=1, sticky="w")
        ttk.Checkbutton(form, text="同步 OpenCodex 原生模型目录", variable=self.codex_catalog).grid(row=6, column=1, sticky="w")
        buttons = ttk.Frame(form)
        buttons.grid(row=7, column=1, sticky="w", pady=(7, 0))
        ttk.Button(buttons, text="保存服务", command=self.save).pack(side="left")
        ttk.Button(buttons, text="删除服务", command=self.delete).pack(side="left", padx=6)
        form.columnconfigure(1, weight=1)
        options = ttk.LabelFrame(outer, text="启动选项", padding=10)
        options.pack(fill="x")
        self._field(options, "个人账号模型", self.personal_model, 0)
        ttk.Checkbutton(options, text="隔离旧模型目录配置（原文件自动备份）", variable=self.isolate_catalog).grid(row=3, column=1, sticky="w")
        if sys.platform != "win32":
            self._field(options, "Codex CLI 路径", self.cli_path, 1)
        if sys.platform == "darwin":
            self._field(options, "桌面程序路径", self.desktop_path, 2)
        options.columnconfigure(1, weight=1)
        ttk.Label(outer, text="密钥保存在系统凭据库。Chat 接口需要安装 LiteLLM 可选依赖；使用期间请保持启动器打开。", wraplength=600).pack(anchor="w", pady=8)
        self.launch_button = ttk.Button(outer, text="启动 Codex", command=self.start)
        self.launch_button.pack(fill="x")
        ttk.Button(outer, text="恢复上一次切换前的配置", command=self.restore).pack(fill="x", pady=(5, 0))
        ttk.Label(outer, textvariable=self.status, wraplength=600).pack(anchor="w", pady=7)
        self.reload()

    @staticmethod
    def _field(parent, title, variable, row, show=None):
        ttk.Label(parent, text=title).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=3)
        ttk.Entry(parent, textvariable=variable, show=show).grid(row=row, column=1, sticky="ew", pady=3)

    def reload(self, select="个人账号"):
        try:
            self.profiles = self.store.load()
            self.profile_map = {f"{p.name} [{p.id[:8]}]": p for p in self.profiles}
            self.choices["values"] = ["个人账号", *self.profile_map]
            self.selection.set(select if select in self.choices["values"] else "个人账号")
        except Exception as exc:
            messagebox.showerror("无法读取服务设置", str(exc))

    def new(self):
        self.selection.set("个人账号")
        self.name_var.set("")
        self.kind.set("responses")
        self.url.set("")
        self.model.set("")
        self.key.set("")
        self.allow_http.set(False)
        self.codex_catalog.set(False)
        self.status.set("填写服务设置后点击保存。DeepSeek 官方地址可填写 https://api.deepseek.com。")

    def select_profile(self, _event=None):
        p = self.profile_map.get(self.selection.get())
        if not p:
            return
        self.name_var.set(p.name)
        self.kind.set(p.kind)
        self.url.set(p.base_url)
        self.model.set(p.model)
        self.allow_http.set(p.allow_http)
        self.codex_catalog.set(p.codex_catalog)
        self.key.set("")
        self.status.set("如不修改密钥，请留空；当前密钥不会显示。")

    def select_kind(self, _event=None):
        if self.kind.get() == "deepseek":
            if not self.url.get().strip():
                self.url.set("https://api.deepseek.com")
            if not self.model.get().strip():
                self.model.set("deepseek-chat")
            self.status.set("DeepSeek 官方地址与模型可以自行修改；请填写自己的 API 密钥。")

    def save(self):
        try:
            prior = self.profile_map.get(self.selection.get())
            profile = new_profile(self.name_var.get(), self.kind.get(), self.url.get(), self.model.get(), self.allow_http.get(), self.codex_catalog.get())
            if prior:
                profile.id = prior.id
            secret = self.key.get()
            if secret:
                self.vault.put(profile.id, secret)
            elif not self.vault.get(profile.id):
                raise ValueError("请填写密钥。")
            profiles = [p for p in self.profiles if p.id != profile.id] + [profile]
            self.store.save(profiles)
            self.key.set("")
            self.reload(f"{profile.name} [{profile.id[:8]}]")
            self.status.set("服务已保存；密钥保存在系统凭据库。")
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))

    def delete(self):
        p = self.profile_map.get(self.selection.get())
        if p and messagebox.askyesno("删除服务", f"删除“{p.name}”及其保存的密钥？"):
            try:
                self.vault.delete(p.id)
                self.store.save([item for item in self.profiles if item.id != p.id])
                self.reload()
                self.new()
            except Exception as exc:
                messagebox.showerror("删除失败", str(exc))

    def start(self):
        p = self.profile_map.get(self.selection.get())
        personal, cli, desktop, isolate = self.personal_model.get(), self.cli_path.get(), self.desktop_path.get(), self.isolate_catalog.get()
        self.launch_button.configure(state="disabled")
        self.status.set("正在验证配置并启动，请稍候……")
        def worker():
            try:
                child, adapter = launch(p, personal, cli, desktop, isolate)
            except Exception as exc:
                message = str(exc)
                self.after(0, lambda: self.finish(None, message))
            else:
                self.after(0, lambda: self.finish(adapter, None))
        threading.Thread(target=worker, daemon=True).start()

    def finish(self, adapter, error):
        self.launch_button.configure(state="normal")
        if error:
            self.status.set("启动失败；请检查提示。")
            messagebox.showerror("启动失败", error)
        else:
            if adapter:
                self.adapters.append(adapter)
            self.status.set("已发出启动请求。使用 Chat 适配器期间，请保持此窗口打开。")

    def close(self):
        if self.adapters and not messagebox.askyesno("关闭适配器", "关闭启动器会停止本地 Chat 适配器，正在使用它的 Codex 请求会中断。继续吗？"):
            return
        for adapter in self.adapters:
            adapter.__exit__(None, None, None)
        self.destroy()

    def restore(self):
        if not messagebox.askyesno("恢复配置", "恢复上次切换前的配置；该配置可能包含旧插件或失效的模型目录。继续吗？"):
            return
        try:
            if is_desktop_running():
                raise RuntimeError("请先正常退出 Codex Desktop，避免覆盖它正在使用的配置。")
            home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
            restore_last(home / "config.toml", data_dir() / "backups")
            self.status.set("已恢复上次切换前的配置。")
        except Exception as exc:
            messagebox.showerror("恢复失败", str(exc))


def main():
    Launcher().mainloop()


if __name__ == "__main__":
    main()
