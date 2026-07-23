"""WorkBuddy Cookies 抓取工具（GUI 版）。

避免在终端里手动 input() 交互：弹出一个桌面窗口，用户在窗口里点按钮即可控制
抓取流程，浏览器登录仍然需要用户本人完成（手机验证码 / 微信扫码）。

流程：
  1. 选择账号 -> 点击「打开浏览器」
  2. 在弹出的 Chromium 里手动登录该账号
  3. 确认浏览器已进入登录后界面后，点击「抓取 Cookies」
  4. 工具自动抓取、备份旧文件、保存新文件、并验证登录态是否完整

关键校验：cookies 必须包含 KEYCLOAK_IDENTITY / KEYCLOAK_SESSION / session，
否则视为登录未完成（与上次 acc1 缺 KEYCLOAK_IDENTITY 的问题对应）。
"""
import json
import os
import shutil
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk

# 把项目根目录加入模块搜索路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.config import Config
from common.logger import setup_logger


# ============ 主题配色（深色 + 高对比，符合用户偏好） ============
BG = "#1f2329"            # 主背景
BG_PANEL = "#2a2f37"      # 面板背景
BG_INPUT = "#353b45"      # 输入/按钮 hover
FG = "#e6e6e6"            # 主文字
FG_DIM = "#9aa0a8"        # 次要文字
FG_TITLE = "#4fc3f7"      # 标题蓝
FG_OK = "#9ccc65"         # 成功绿
FG_WARN = "#ffb74d"       # 警告橙
FG_ERR = "#ef5350"        # 错误红
FG_ACCENT = "#4fc3f7"     # 强调
BORDER = "#3f4751"


class CaptureApp:
    def __init__(self, root):
        self.root = root
        self.root.title("WorkBuddy Cookies 抓取工具")
        self.root.geometry("720x640")
        self.root.minsize(680, 560)
        self.root.configure(bg=BG)

        # 运行态
        self.account_var = tk.StringVar(value="acc1")
        self.is_capturing = False
        self.finish_event = threading.Event()
        self.cancel_event = threading.Event()
        self.capture_thread = None

        self._setup_style()
        self._build_ui()
        self._load_accounts()

    # ---------------- 样式 ----------------
    def _setup_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=BG_PANEL)
        style.configure("TLabel", background=BG, foreground=FG,
                        font=("Microsoft YaHei UI", 10))
        style.configure("Dim.TLabel", background=BG, foreground=FG_DIM,
                        font=("Microsoft YaHei UI", 9))
        style.configure("Title.TLabel", background=BG, foreground=FG_TITLE,
                        font=("Microsoft YaHei UI", 15, "bold"))
        style.configure("PanelTitle.TLabel", background=BG_PANEL, foreground=FG_ACCENT,
                        font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Status.TLabel", background=BG_PANEL, foreground=FG_WARN,
                        font=("Microsoft YaHei UI", 10))

        style.configure("TButton", font=("Microsoft YaHei UI", 10),
                        padding=(12, 6))
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"),
                        padding=(14, 7), foreground="#ffffff")
        style.map("Accent.TButton",
                  background=[("active", BG_INPUT), ("disabled", "#3a3f47")],
                  foreground=[("disabled", "#6a6f77")])
        style.map("TButton",
                  background=[("active", BG_INPUT), ("disabled", "#2a2f37")],
                  foreground=[("disabled", "#6a6f77")])

        style.configure("TLabelframe", background=BG_PANEL, foreground=FG_ACCENT,
                        bordercolor=BORDER, relief="solid", borderwidth=1,
                        font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TLabelframe.Label", background=BG_PANEL, foreground=FG_ACCENT)

        style.configure("TCombobox", fieldbackground=BG_INPUT, background=BG_PANEL,
                        foreground=FG, bordercolor=BORDER, arrowcolor=FG,
                        font=("Microsoft YaHei UI", 10), padding=(6, 3))
        style.map("TCombobox",
                  fieldbackground=[("readonly", BG_INPUT)],
                  foreground=[("readonly", FG)],
                  selectbackground=[("readonly", BG_INPUT)],
                  selectforeground=[("readonly", FG)])

    # ---------------- 界面 ----------------
    def _build_ui(self):
        # 顶部标题区
        header = ttk.Frame(self.root)
        header.pack(fill="x", padx=20, pady=(16, 12))
        ttk.Label(header, text="WorkBuddy Cookies 抓取工具", style="Title.TLabel"
                  ).pack(anchor="w")
        ttk.Label(header,
                  text="本地登录态抓取 · 自动保存到 store/wb_cookies_<账号>.json",
                  style="Dim.TLabel").pack(anchor="w", pady=(2, 0))

        # 账号选择 + 按钮
        ctrl = ttk.Frame(self.root)
        ctrl.pack(fill="x", padx=20, pady=(0, 12))
        ttk.Label(ctrl, text="账号").pack(side="left", padx=(0, 8))
        self.account_combo = ttk.Combobox(ctrl, textvariable=self.account_var,
                                          state="readonly", width=18)
        self.account_combo.pack(side="left", padx=(0, 16))
        self.open_btn = ttk.Button(ctrl, text="打开浏览器", style="Accent.TButton",
                                   command=self.start_capture)
        self.open_btn.pack(side="left", padx=(0, 8))
        self.finish_btn = ttk.Button(ctrl, text="抓取 Cookies",
                                     command=self.finish_capture, state="disabled")
        self.finish_btn.pack(side="left", padx=(0, 8))
        self.cancel_btn = ttk.Button(ctrl, text="取消",
                                     command=self.cancel_capture, state="disabled")
        self.cancel_btn.pack(side="left")

        # 状态条
        status_frame = ttk.Frame(self.root)
        status_frame.pack(fill="x", padx=20, pady=(0, 12))
        status_inner = ttk.Frame(status_frame, style="Panel.TFrame")
        status_inner.pack(fill="x")
        self.status_label = ttk.Label(status_inner, text="就绪 · 选择账号后点击「打开浏览器」",
                                      style="Status.TLabel")
        self.status_label.pack(anchor="w", padx=12, pady=8)

        # 步骤说明
        steps_frame = ttk.LabelFrame(self.root, text="操作步骤", padding=12)
        steps_frame.pack(fill="x", padx=20, pady=(0, 12))
        steps = [
            "1. 选择要抓取的账号",
            "2. 点击「打开浏览器」，会弹出 Chromium 窗口并打开 codebuddy.cn",
            "3. 在弹出的浏览器里手动登录该账号（手机验证码 / 微信扫码）",
            "4. 确认浏览器已显示登录后的用户界面（能看到头像 / 菜单）",
            "5. 回到本工具，点击「抓取 Cookies」",
            "6. 工具自动保存并验证登录态是否完整",
        ]
        for s in steps:
            ttk.Label(steps_frame, text=s, style="Dim.TLabel").pack(anchor="w", pady=1)

        # 结果区
        result_frame = ttk.LabelFrame(self.root, text="抓取结果", padding=8)
        result_frame.pack(fill="both", expand=True, padx=20, pady=(0, 16))
        self.result_text = tk.Text(
            result_frame, bg="#15171c", fg=FG_OK,
            font=("Consolas", 10), wrap="word",
            relief="flat", bd=0, padx=8, pady=8,
            insertbackground=FG, selectbackground=BG_INPUT,
        )
        self.result_text.pack(fill="both", expand=True)
        self.result_text.configure(state="disabled")

    # ---------------- 数据加载 ----------------
    def _load_accounts(self):
        try:
            logger = setup_logger("capture_gui")
            cfg = Config.load("config.yaml", logger)
            wb_sites = [s for s in cfg.sites(require_credentials=False)
                        if s.type == "workbuddy"]
            accounts = []
            for site in wb_sites:
                for acc in site.accounts:
                    accounts.append(acc.name)
            if accounts:
                self.account_combo["values"] = accounts
                self.account_var.set(accounts[0])
        except Exception as e:
            self._append_result(f"读取 config.yaml 失败: {e}", "err")

    # ---------------- 线程安全的 UI 更新 ----------------
    def _set_status(self, msg, color=None):
        def do():
            self.status_label.configure(text=msg)
            if color:
                style = ttk.Style()
                style.configure("Status.TLabel", foreground=color)
        self.root.after(0, do)

    def _append_result(self, msg, color=None):
        # 预注册 tag 颜色（幂等，多次调用无副作用）
        for tag, c in [("ok", FG_OK), ("warn", FG_WARN), ("err", FG_ERR),
                       ("dim", FG_DIM), ("accent", FG_ACCENT)]:
            self.result_text.tag_config(tag, foreground=c)

        def do():
            self.result_text.configure(state="normal")
            if color:
                self.result_text.insert("end", msg + "\n", (color,))
            else:
                self.result_text.insert("end", msg + "\n")
            self.result_text.see("end")
            self.result_text.configure(state="disabled")
        self.root.after(0, do)

    def _set_buttons(self, open_state, finish_state, cancel_state):
        def do():
            self.open_btn.configure(state=open_state)
            self.finish_btn.configure(state=finish_state)
            self.cancel_btn.configure(state=cancel_state)
        self.root.after(0, do)

    # ---------------- 流程控制 ----------------
    def start_capture(self):
        if self.is_capturing:
            return
        self.is_capturing = True
        self.finish_event.clear()
        self.cancel_event.clear()
        self._set_buttons("disabled", "disabled", "normal")
        self._set_status("正在启动浏览器...", FG_WARN)
        self.capture_thread = threading.Thread(target=self._capture_worker, daemon=True)
        self.capture_thread.start()

    def finish_capture(self):
        self.finish_event.set()
        self._set_buttons("disabled", "disabled", "disabled")
        self._set_status("正在抓取 Cookies...", FG_WARN)

    def cancel_capture(self):
        self.cancel_event.set()
        self.finish_event.set()  # 同时唤醒 worker 避免卡住
        self._set_buttons("disabled", "disabled", "disabled")
        self._set_status("正在取消...", FG_WARN)

    def _capture_worker(self):
        """子线程：打开浏览器 -> 等待用户登录 -> 抓取 cookies -> 保存验证"""
        from playwright.sync_api import sync_playwright

        account = self.account_var.get()
        base = "https://www.codebuddy.cn"
        pw = None
        browser = None
        try:
            self._set_status(f"正在打开 {base} ...", FG_WARN)
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=False)
            ctx = browser.new_context()
            page = ctx.new_page()
            page.goto(base, wait_until="domcontentloaded", timeout=60000)

            if self.cancel_event.is_set():
                self._set_status("已取消", FG_DIM)
                return

            self._set_status(
                f"浏览器已打开。请在浏览器中登录账号 [{account}]，"
                f"完成登录后回到本工具点击「抓取 Cookies」",
                FG_ACCENT,
            )
            self._set_buttons("disabled", "normal", "normal")

            # 等待用户操作
            self.finish_event.wait()

            if self.cancel_event.is_set():
                self._set_status("已取消", FG_DIM)
                return

            self._set_status("正在抓取 Cookies...", FG_WARN)
            cookies = ctx.cookies()

            # 保存路径
            store_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "store",
            )
            os.makedirs(store_dir, exist_ok=True)
            cookie_file = os.path.join(store_dir, f"wb_cookies_{account}.json")

            # 备份旧文件
            if os.path.exists(cookie_file):
                bak = cookie_file + ".bak"
                shutil.copy2(cookie_file, bak)
                self._append_result(f"已备份旧文件 -> {os.path.basename(bak)}", "dim")

            # 写入新文件
            with open(cookie_file, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)

            # 校验
            names = sorted(c.get("name", "") for c in cookies)
            has_kc_identity = "KEYCLOAK_IDENTITY" in names
            has_kc_session = "KEYCLOAK_SESSION" in names
            has_session = "session" in names
            all_ok = has_kc_identity and has_kc_session and has_session

            self._append_result("=" * 50, "dim")
            self._append_result(f"账号: {account}", "accent")
            self._append_result(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "dim")
            self._append_result(f"Cookie 数量: {len(cookies)}", "ok")
            self._append_result(
                f"KEYCLOAK_IDENTITY: {'包含' if has_kc_identity else '缺失'}",
                "ok" if has_kc_identity else "err",
            )
            self._append_result(
                f"KEYCLOAK_SESSION : {'包含' if has_kc_session else '缺失'}",
                "ok" if has_kc_session else "err",
            )
            self._append_result(
                f"session          : {'包含' if has_session else '缺失'}",
                "ok" if has_session else "err",
            )
            self._append_result(f"文件: {cookie_file}", "dim")

            if all_ok:
                self._append_result("结果: 登录态完整，可用", "ok")
                self._set_status(f"账号 [{account}] 抓取成功，登录态完整", FG_OK)
            else:
                self._append_result(
                    "结果: 登录态不完整！请确认浏览器中已显示登录后界面，再重试",
                    "err",
                )
                self._set_status(
                    f"账号 [{account}] 抓取完成，但登录态不完整", FG_ERR,
                )
            self._append_result("", "dim")

        except Exception as e:
            self._set_status(f"出错: {e}", FG_ERR)
            self._append_result(f"错误: {e}", "err")
        finally:
            try:
                if browser:
                    browser.close()
                if pw:
                    pw.stop()
            except Exception:
                pass
            self.is_capturing = False
            self._set_buttons("normal", "disabled", "disabled")


def main():
    root = tk.Tk()
    CaptureApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
