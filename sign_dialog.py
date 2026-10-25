"""Hộp thoại chọn chứng chỉ ký số và các tuỳ chọn ký (lý do, nơi ký, dấu thời gian)."""

import tkinter as tk
from tkinter import messagebox, ttk

import digital_sign
from settings_dialog import cert_label, list_certs, load_settings, save_settings


class SignDialog:
    """Hiển thị hộp thoại modal. Sau khi đóng, self.result là dict tuỳ chọn ký hoặc None."""

    def __init__(self, parent, visible):
        self.result = None
        self.settings = load_settings()
        self.certs = list_certs(self.settings)

        self.win = tk.Toplevel(parent)
        self.win.title("Ký số")
        self.win.transient(parent)
        self.win.resizable(True, False)
        self.win.minsize(640, 0)
        self._build(visible)
        self._fill_certs()

        self.win.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.win.winfo_width()) // 2
        y = parent.winfo_rooty() + 60
        self.win.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.win.grab_set()
        if self.certs:
            self.pw_entry.focus_set()
        else:
            self.btn_ok.focus_set()
        self.win.wait_window()

    def _build(self, visible):
        f = ttk.Frame(self.win, padding=12)
        f.pack(fill=tk.BOTH, expand=True)
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="Chứng chỉ ký số:").grid(row=0, column=0, sticky=tk.W, pady=3)
        self.cert_var = tk.StringVar()
        self.cert_combo = ttk.Combobox(f, textvariable=self.cert_var, state="readonly")
        self.cert_combo.grid(row=0, column=1, sticky=tk.EW, pady=3, padx=(8, 0))
        self.cert_combo.bind("<<ComboboxSelected>>", lambda e: self._sync_cert())
        self.hint = ttk.Label(f, foreground="#a33", wraplength=560, justify=tk.LEFT)
        self.hint.grid(row=1, column=1, columnspan=2, sticky=tk.W)

        ttk.Label(f, text="Mật khẩu:").grid(row=2, column=0, sticky=tk.W, pady=3)
        self.pw_var = tk.StringVar()
        self.pw_entry = ttk.Entry(f, textvariable=self.pw_var, show="•", width=24)
        self.pw_entry.grid(row=2, column=1, sticky=tk.W, pady=3, padx=(8, 0))
        self.pw_entry.bind("<Return>", lambda e: self._ok())
        self.pw_hint = ttk.Label(f, text="", style="Muted.TLabel", wraplength=560,
                                 justify=tk.LEFT)
        self.pw_hint.grid(row=3, column=1, columnspan=2, sticky=tk.W)

        ttk.Label(f, text="Lý do ký:").grid(row=4, column=0, sticky=tk.W, pady=3)
        self.reason_var = tk.StringVar(value=self.settings.get("reason", ""))
        ttk.Entry(f, textvariable=self.reason_var).grid(row=4, column=1, columnspan=2,
                                                        sticky=tk.EW, pady=3, padx=(8, 0))
        ttk.Label(f, text="Nơi ký:").grid(row=5, column=0, sticky=tk.W, pady=3)
        self.location_var = tk.StringVar(value=self.settings.get("location", ""))
        ttk.Entry(f, textvariable=self.location_var).grid(row=5, column=1, columnspan=2,
                                                          sticky=tk.EW, pady=3, padx=(8, 0))
        ttk.Label(f, text="Máy chủ dấu thời gian:").grid(row=6, column=0, sticky=tk.W, pady=3)
        self.tsa_var = tk.StringVar(value=self.settings.get("tsa_url", ""))
        ttk.Entry(f, textvariable=self.tsa_var).grid(row=6, column=1, columnspan=2,
                                                     sticky=tk.EW, pady=3, padx=(8, 0))
        ttk.Label(f, text="Không bắt buộc. Ví dụ: http://timestamp.digicert.com "
                          "(cần mạng internet).", style="Muted.TLabel").grid(
            row=7, column=1, columnspan=2, sticky=tk.W)

        self.show_text_var = tk.BooleanVar(value=self.settings.get("show_text", True))
        chk = ttk.Checkbutton(f, text="Hiện tên người ký và thời gian ký dưới hình chữ ký",
                              variable=self.show_text_var)
        chk.grid(row=8, column=0, columnspan=3, sticky=tk.W, pady=(8, 0))
        if not visible:
            chk.state(["disabled"])
            ttk.Label(f, text="Chữ ký sẽ không hiển thị trên trang.", style="Muted.TLabel").grid(
                row=9, column=0, columnspan=3, sticky=tk.W)

        bar = ttk.Frame(f)
        bar.grid(row=10, column=0, columnspan=3, sticky=tk.E, pady=(12, 0))
        self.btn_ok = ttk.Button(bar, text="Ký", command=self._ok)
        self.btn_ok.pack(side=tk.LEFT)
        ttk.Button(bar, text="Huỷ", command=self.win.destroy).pack(side=tk.LEFT, padx=(6, 0))
        self.win.bind("<Escape>", lambda e: self.win.destroy())

    # ------------------------------------------------------------ chứng chỉ
    def _fill_certs(self):
        self.cert_combo["values"] = [cert_label(c) for c in self.certs]
        if self.certs:
            self.cert_combo.current(0)
            self.btn_ok.state(["!disabled"])
        else:
            self.cert_var.set("")
            self.btn_ok.state(["disabled"])
            self.hint.config(text="Chưa có chứng chỉ ký số. Hãy vào mục Cài đặt để tạo "
                                  "chứng chỉ mới hoặc nhập file .p12 đã có.")
        self._sync_cert()

    @property
    def _current(self):
        index = self.cert_combo.current()
        if index < 0 or index >= len(self.certs):
            return None
        return self.certs[index]

    def _sync_cert(self):
        entry = self._current
        if entry is None:
            self.pw_hint.config(text="")
            return
        if entry.get("has_password"):
            self.pw_hint.config(text="Nhập mật khẩu đã đặt khi tạo hoặc nhập chứng chỉ này.")
        else:
            self.pw_hint.config(text="Chứng chỉ này không có mật khẩu — để trống là được.")
            self.pw_var.set("")
        status = digital_sign.describe_validity(entry)
        self.hint.config(text="" if status == "Còn hiệu lực"
                          else f"Chứng chỉ này {status.lower()}.")

    def _ok(self):
        entry = self._current
        if entry is None:
            messagebox.showwarning("Ký số", "Hãy chọn một chứng chỉ ký số.", parent=self.win)
            return
        status = digital_sign.describe_validity(entry)
        if status != "Còn hiệu lực" and not messagebox.askyesno(
                "Ký số", f"Chứng chỉ này {status.lower()}. Chữ ký sẽ không được công nhận.\n"
                         "Vẫn ký?", icon=messagebox.WARNING, parent=self.win):
            return
        password = self.pw_var.get()
        if entry.get("has_password") and not password:
            messagebox.showwarning("Ký số", "Hãy nhập mật khẩu của chứng chỉ.", parent=self.win)
            self.pw_entry.focus_set()
            return
        self.result = {
            "cert": entry,
            "password": password,
            "reason": self.reason_var.get().strip(),
            "location": self.location_var.get().strip(),
            "tsa_url": self.tsa_var.get().strip(),
            "show_text": self.show_text_var.get(),
        }
        self.settings.update(reason=self.result["reason"], location=self.result["location"],
                             tsa_url=self.result["tsa_url"], show_text=self.result["show_text"])
        save_settings(self.settings)  # không bao giờ lưu mật khẩu
        self.win.destroy()
