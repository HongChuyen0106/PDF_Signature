"""Hộp thoại chọn USB token, nhập PIN và chọn chứng thư số để ký."""

import json
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import token_sign

SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".pdf_sign.json")


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


class TokenSignDialog:
    """Hiển thị hộp thoại modal. Sau khi đóng, self.result là dict tuỳ chọn ký hoặc None."""

    def __init__(self, parent, visible):
        self.result = None
        self.certs = []
        self.settings = load_settings()

        self.win = tk.Toplevel(parent)
        self.win.title("Ký số bằng USB token")
        self.win.transient(parent)
        self.win.resizable(True, False)
        self.win.minsize(640, 0)
        self._build(visible)
        self._refresh_libs()

        self.win.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.win.winfo_width()) // 2
        y = parent.winfo_rooty() + 60
        self.win.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.win.grab_set()
        self.pin_entry.focus_set()
        self.win.wait_window()

    def _build(self, visible):
        f = ttk.Frame(self.win, padding=12)
        f.pack(fill=tk.BOTH, expand=True)
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="Driver token (PKCS#11):").grid(row=0, column=0, sticky=tk.W, pady=3)
        self.lib_var = tk.StringVar()
        self.lib_combo = ttk.Combobox(f, textvariable=self.lib_var)
        self.lib_combo.grid(row=0, column=1, sticky=tk.EW, pady=3)
        btns = ttk.Frame(f)
        btns.grid(row=0, column=2, sticky=tk.W, padx=(6, 0))
        ttk.Button(btns, text="Chọn file...", command=self._browse_lib).pack(side=tk.LEFT)
        ttk.Button(btns, text="Dò lại", command=self._refresh_libs).pack(side=tk.LEFT, padx=(4, 0))
        self.lib_hint = ttk.Label(f, foreground="#a33", wraplength=560, justify=tk.LEFT)
        self.lib_hint.grid(row=1, column=1, columnspan=2, sticky=tk.W)

        ttk.Label(f, text="Mã PIN:").grid(row=2, column=0, sticky=tk.W, pady=3)
        self.pin_var = tk.StringVar()
        self.pin_entry = ttk.Entry(f, textvariable=self.pin_var, show="•", width=20)
        self.pin_entry.grid(row=2, column=1, sticky=tk.W, pady=3)
        self.pin_entry.bind("<Return>", lambda e: self._read_certs())
        self.btn_read = ttk.Button(f, text="Đọc chứng thư", command=self._read_certs)
        self.btn_read.grid(row=2, column=2, sticky=tk.W, padx=(6, 0))

        cols = ("subject", "issuer", "expires", "status")
        self.tree = ttk.Treeview(f, columns=cols, show="headings", height=5, selectmode="browse")
        for col, title, width in zip(cols, ("Người sở hữu", "Nhà cung cấp", "Hiệu lực đến", "Trạng thái"),
                                     (230, 200, 110, 100)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, stretch=col in ("subject", "issuer"))
        self.tree.grid(row=3, column=0, columnspan=3, sticky=tk.EW, pady=(8, 8))
        self.tree.bind("<Double-1>", lambda e: self._ok())

        ttk.Label(f, text="Lý do ký:").grid(row=4, column=0, sticky=tk.W, pady=3)
        self.reason_var = tk.StringVar(value=self.settings.get("reason", ""))
        ttk.Entry(f, textvariable=self.reason_var).grid(row=4, column=1, columnspan=2, sticky=tk.EW)
        ttk.Label(f, text="Nơi ký:").grid(row=5, column=0, sticky=tk.W, pady=3)
        self.location_var = tk.StringVar(value=self.settings.get("location", ""))
        ttk.Entry(f, textvariable=self.location_var).grid(row=5, column=1, columnspan=2, sticky=tk.EW)
        ttk.Label(f, text="Máy chủ dấu thời gian:").grid(row=6, column=0, sticky=tk.W, pady=3)
        self.tsa_var = tk.StringVar(value=self.settings.get("tsa_url", ""))
        ttk.Entry(f, textvariable=self.tsa_var).grid(row=6, column=1, columnspan=2, sticky=tk.EW)
        ttk.Label(f, text="Không bắt buộc. Ví dụ: http://timestamp.digicert.com (cần mạng internet).",
                  foreground="#666").grid(row=7, column=1, columnspan=2, sticky=tk.W)

        self.show_text_var = tk.BooleanVar(value=self.settings.get("show_text", True))
        chk = ttk.Checkbutton(f, text="Hiện tên người ký và thời gian ký dưới hình chữ ký",
                              variable=self.show_text_var)
        chk.grid(row=8, column=0, columnspan=3, sticky=tk.W, pady=(8, 0))
        if not visible:
            chk.state(["disabled"])
            ttk.Label(f, text="Chữ ký sẽ không hiển thị trên trang.", foreground="#666").grid(
                row=9, column=0, columnspan=3, sticky=tk.W)

        bar = ttk.Frame(f)
        bar.grid(row=10, column=0, columnspan=3, sticky=tk.E, pady=(12, 0))
        self.btn_ok = ttk.Button(bar, text="Ký", command=self._ok)
        self.btn_ok.pack(side=tk.LEFT)
        ttk.Button(bar, text="Huỷ", command=self.win.destroy).pack(side=tk.LEFT, padx=(6, 0))
        self.win.bind("<Escape>", lambda e: self.win.destroy())

    # ---------------------------------------------------------------- driver
    def _refresh_libs(self):
        self.libs = token_sign.find_pkcs11_libs()
        values = [f"{name}  —  {path}" for name, path in self.libs]
        saved = self.settings.get("lib_path")
        if saved and os.path.exists(saved) and not any(
                os.path.normcase(saved) == os.path.normcase(p) for _, p in self.libs):
            values.insert(0, saved)
        self.lib_combo["values"] = values
        current = self.lib_var.get().strip()
        if saved and os.path.exists(saved):
            match = next((v for v in values if v.endswith(saved) or v == saved), saved)
            self.lib_var.set(match)
        elif values and not current:
            self.lib_var.set(values[0])
        if values:
            self.lib_hint.config(text="")
        else:
            self.lib_hint.config(text="Không tìm thấy driver token trên máy. Hãy cắm token và cài "
                                      "phần mềm của nhà cung cấp (VNPT, Viettel, FPT, BKAV...), "
                                      "hoặc bấm \"Chọn file...\" để chọn file .dll PKCS#11.")

    def _browse_lib(self):
        path = filedialog.askopenfilename(parent=self.win, title="Chọn thư viện PKCS#11 của token",
                                          initialdir=token_sign.SYSTEM_DIR,
                                          filetypes=[("Thư viện DLL", "*.dll"), ("Tất cả", "*.*")])
        if path:
            self.lib_var.set(os.path.normpath(path))

    def _lib_path(self):
        value = self.lib_var.get().strip()
        if "  —  " in value:
            value = value.split("  —  ", 1)[1]
        return value

    # ------------------------------------------------------------ chứng thư
    def _read_certs(self):
        lib_path, pin = self._lib_path(), self.pin_var.get()
        if not lib_path or not os.path.exists(lib_path):
            messagebox.showwarning("Ký số", "Hãy chọn driver token (file .dll PKCS#11).", parent=self.win)
            return
        if not pin:
            messagebox.showwarning("Ký số", "Hãy nhập mã PIN của token.", parent=self.win)
            self.pin_entry.focus_set()
            return
        self.win.config(cursor="watch")
        self.win.update_idletasks()
        try:
            self.certs = token_sign.list_certificates(lib_path, pin)
        except Exception as ex:
            self.certs = []
            messagebox.showerror("Ký số", str(ex), parent=self.win)
        finally:
            self.win.config(cursor="")
        self.tree.delete(*self.tree.get_children())
        for i, c in enumerate(self.certs):
            self.tree.insert("", tk.END, iid=str(i), values=(
                c.subject, c.issuer, c.not_after.astimezone().strftime("%d/%m/%Y"),
                token_sign.describe_validity(c)))
        if self.certs:
            # ưu tiên chứng thư còn hiệu lực
            best = next((i for i, c in enumerate(self.certs)
                         if token_sign.describe_validity(c) == "Còn hiệu lực"), 0)
            self.tree.selection_set(str(best))
            self.tree.focus(str(best))
            self.btn_ok.focus_set()

    def _ok(self):
        if not self.certs:
            self._read_certs()
            if not self.certs:
                return
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Ký số", "Hãy chọn một chứng thư số.", parent=self.win)
            return
        cert = self.certs[int(sel[0])]
        validity = token_sign.describe_validity(cert)
        if validity != "Còn hiệu lực" and not messagebox.askyesno(
                "Ký số", f"Chứng thư này {validity.lower()}. Chữ ký sẽ không được công nhận.\n"
                         "Vẫn ký?", icon=messagebox.WARNING, parent=self.win):
            return
        if not cert.has_key:
            messagebox.showerror("Ký số", "Không tìm thấy khoá bí mật đi kèm chứng thư này trên token.",
                                 parent=self.win)
            return
        self.result = {
            "cert": cert,
            "pin": self.pin_var.get(),
            "reason": self.reason_var.get().strip(),
            "location": self.location_var.get().strip(),
            "tsa_url": self.tsa_var.get().strip(),
            "show_text": self.show_text_var.get(),
        }
        self.settings.update(lib_path=self._lib_path(), reason=self.result["reason"],
                             location=self.result["location"], tsa_url=self.result["tsa_url"],
                             show_text=self.result["show_text"])
        save_settings(self.settings)  # không bao giờ lưu mã PIN
        self.win.destroy()
