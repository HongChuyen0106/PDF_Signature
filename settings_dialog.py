"""Cài đặt: quản lý chứng chỉ ký số (tạo mới, nhập file .p12/.pfx, xoá)."""

import json
import os
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, simpledialog, ttk

import digital_sign

# Các lựa chọn ký được nhớ ở %USERPROFILE%\.pdf_sign.json (mật khẩu thì không bao giờ lưu)
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


def list_certs(settings=None):
    """Chứng chỉ đang có trên máy (bỏ qua mục mà file của nó đã bị xoá)."""
    settings = load_settings() if settings is None else settings
    return [c for c in settings.get("certs", [])
            if isinstance(c, dict) and c.get("path") and os.path.exists(c["path"])]


def add_cert(entry):
    """Thêm (hoặc thay) một chứng chỉ vào danh sách theo đường dẫn file."""
    settings = load_settings()
    certs = settings.setdefault("certs", [])
    key = os.path.normcase(entry["path"])
    certs[:] = [c for c in certs if os.path.normcase(c.get("path", "")) != key]
    certs.append(entry)
    save_settings(settings)
    return settings


def remove_cert(cert_id):
    settings = load_settings()
    settings["certs"] = [c for c in settings.get("certs", []) if c.get("id") != cert_id]
    save_settings(settings)
    return settings


def valid_until(entry):
    """Ngày hết hiệu lực dạng dd/mm/yyyy, rỗng nếu không đọc được."""
    try:
        return datetime.fromisoformat(entry.get("not_after", "")).astimezone().strftime("%d/%m/%Y")
    except ValueError:
        return ""


def cert_label(entry):
    """Tên hiển thị của chứng chỉ trong danh sách chọn khi ký."""
    until = valid_until(entry)
    return f"{entry['name']}  —  hiệu lực đến {until}" if until else entry["name"]


class SettingsDialog:
    """Hộp thoại Cài đặt: danh sách chứng chỉ ký số và các nút tạo / nhập / xoá."""

    def __init__(self, parent):
        self.win = tk.Toplevel(parent)
        self.win.title("Cài đặt — Chứng chỉ ký số")
        self.win.transient(parent)
        self.win.resizable(True, False)
        self.win.minsize(700, 0)
        self._build()
        self.refresh()

        self.win.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.win.winfo_width()) // 2
        y = parent.winfo_rooty() + 60
        self.win.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.win.grab_set()
        self.win.wait_window()

    def _build(self):
        f = ttk.Frame(self.win, padding=12)
        f.pack(fill=tk.BOTH, expand=True)
        f.columnconfigure(0, weight=1)

        ttk.Label(f, text="Chứng chỉ ký số", font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, sticky=tk.W)
        ttk.Label(f, text="Ký số dùng khoá bí mật nằm trong file .p12/.pfx. Có thể tự tạo "
                          "chứng chỉ mới hoặc nhập file đã có.",
                  style="Muted.TLabel", wraplength=640, justify=tk.LEFT).grid(
            row=1, column=0, sticky=tk.W, pady=(2, 8))

        cols = ("name", "until", "status", "origin", "file")
        head = ("Người sở hữu", "Hiệu lực đến", "Trạng thái", "Nguồn", "File")
        widths = (210, 100, 110, 90, 170)
        self.tree = ttk.Treeview(f, columns=cols, show="headings", height=7, selectmode="browse")
        for c, t, w in zip(cols, head, widths):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, stretch=c in ("name", "file"))
        self.tree.grid(row=2, column=0, sticky=tk.EW)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._sync_delete())

        bar = ttk.Frame(f)
        bar.grid(row=3, column=0, sticky=tk.E, pady=(12, 0))
        self.btn_create = ttk.Button(bar, text="Tạo chứng chỉ mới...", command=self._create)
        self.btn_create.pack(side=tk.LEFT)
        self.btn_import = ttk.Button(bar, text="Nhập file .p12...", command=self._import)
        self.btn_import.pack(side=tk.LEFT, padx=(6, 0))
        self.btn_delete = ttk.Button(bar, text="Xoá", command=self._delete)
        self.btn_delete.pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(bar, text="Đóng", command=self.win.destroy).pack(side=tk.LEFT, padx=(18, 0))
        self._sync_delete()

    # ------------------------------------------------------------ danh sách
    def refresh(self, select=None):
        self.tree.delete(*self.tree.get_children())
        for entry in list_certs():
            self.tree.insert("", tk.END, iid=entry["id"], values=(
                entry["name"], valid_until(entry), digital_sign.describe_validity(entry),
                entry.get("origin", ""), os.path.basename(entry["path"])))
        if select and self.tree.exists(select):
            self.tree.selection_set(select)
            self.tree.focus(select)
        self._sync_delete()

    def _selected(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return next((c for c in list_certs() if c.get("id") == sel[0]), None)

    def _sync_delete(self):
        self.btn_delete.state(["!disabled"] if self._selected() else ["disabled"])

    # ---------------------------------------------------------- tạo mới
    def _create(self):
        dialog = CreateCertDialog(self.win)
        self.win.grab_set()          # lấy lại quyền chặn hộp thoại sau khi hộp con đóng
        entry = dialog.entry
        if not entry:
            return
        add_cert(entry)
        self.refresh(select=entry["id"])
        messagebox.showinfo(
            "Cài đặt",
            f"Đã tạo chứng chỉ ký số:\n{entry['name']}\n\n"
            f"File khoá (dùng để ký): {entry['path']}\n"
            f"File chứng chỉ (gửi cho người kiểm tra): {entry['cert_path']}\n\n"
            "Hãy ghi nhớ mật khẩu của file .p12: chương trình không lưu mật khẩu này.",
            parent=self.win)

    # ------------------------------------------------------------ nhập file
    def _import(self):
        path = filedialog.askopenfilename(
            parent=self.win, title="Chọn file chứng chỉ số",
            filetypes=[("Chứng chỉ PKCS#12", "*.p12 *.pfx"), ("Tất cả", "*.*")])
        if not path:
            return
        password = simpledialog.askstring(
            "Mật khẩu", "Nhập mật khẩu của file chứng chỉ.\n"
                        "(Để trống nếu file không có mật khẩu.)",
            show="*", parent=self.win)
        if password is None:
            self.win.grab_set()
            return
        self.win.grab_set()
        try:
            entry = digital_sign.import_certificate(path, password)
        except digital_sign.SignError as ex:
            messagebox.showerror("Cài đặt", str(ex), parent=self.win)
            return
        add_cert(entry)
        self.refresh(select=entry["id"])
        messagebox.showinfo("Cài đặt", f"Đã thêm chứng chỉ:\n{entry['name']}", parent=self.win)

    # --------------------------------------------------------------- xoá
    def _delete(self):
        entry = self._selected()
        if not entry:
            return
        if not messagebox.askyesno(
                "Cài đặt", f"Xoá chứng chỉ \"{entry['name']}\" khỏi danh sách?\n\n"
                           "Chữ ký đã ký trước đó không bị ảnh hưởng.",
                icon=messagebox.WARNING, parent=self.win):
            return
        owned = os.path.normcase(os.path.dirname(entry["path"])) == \
            os.path.normcase(digital_sign.CERTS_DIR)
        if owned and messagebox.askyesno(
                "Cài đặt", "Xoá luôn file chứng chỉ trên ổ đĩa?\n\n"
                           f"{entry['path']}\n\n"
                           "Nếu bạn vẫn còn bản sao ở nơi khác thì chọn \"Không\".",
                icon=messagebox.WARNING, parent=self.win):
            for path in (entry["path"], entry.get("cert_path", "")):
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError as ex:
                        messagebox.showerror("Cài đặt", f"Không xoá được file:\n{ex}",
                                             parent=self.win)
        remove_cert(entry["id"])
        self.refresh()


class CreateCertDialog:
    """Hộp thoại tạo chứng chỉ ký số tự ký. self.entry là chứng chỉ vừa tạo hoặc None."""

    def __init__(self, parent):
        self.entry = None
        self.win = tk.Toplevel(parent)
        self.win.title("Tạo chứng chỉ ký số mới")
        self.win.transient(parent)
        self.win.resizable(False, False)
        self._build()

        self.win.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.win.winfo_width()) // 2
        y = parent.winfo_rooty() + 80
        self.win.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.win.grab_set()
        self.cn_entry.focus_set()
        self.win.wait_window()

    def _build(self):
        f = ttk.Frame(self.win, padding=12)
        f.pack(fill=tk.BOTH, expand=True)
        f.columnconfigure(1, weight=1)

        self.cn_var = tk.StringVar()
        self.org_var = tk.StringVar()
        self.unit_var = tk.StringVar()
        self.country_var = tk.StringVar()
        self.email_var = tk.StringVar()
        self.years_var = tk.StringVar(value="1")
        self.pw_var = tk.StringVar()
        self.pw2_var = tk.StringVar()

        rows = (
            ("Họ và tên (người ký) *:", self.cn_var, 0),
            ("Tổ chức:", self.org_var, 1),
            ("Phòng ban / Đơn vị:", self.unit_var, 2),
            ("Quốc gia (2 chữ, vd VN):", self.country_var, 3),
            ("Email:", self.email_var, 4),
        )
        for text, var, row in rows:
            ttk.Label(f, text=text).grid(row=row, column=0, sticky=tk.W, pady=3)
            entry = ttk.Entry(f, textvariable=var, width=34)
            entry.grid(row=row, column=1, sticky=tk.EW, pady=3, padx=(8, 0))
            if var is self.cn_var:
                self.cn_entry = entry

        ttk.Label(f, text="Thời hạn (năm):").grid(row=5, column=0, sticky=tk.W, pady=3)
        years = ttk.Combobox(f, textvariable=self.years_var, values=("1", "2", "3", "5", "10"),
                             width=8, state="readonly")
        years.grid(row=5, column=1, sticky=tk.W, pady=3, padx=(8, 0))

        ttk.Label(f, text="Mật khẩu file .p12:").grid(row=6, column=0, sticky=tk.W, pady=3)
        self.pw_entry = ttk.Entry(f, textvariable=self.pw_var, show="•", width=34)
        self.pw_entry.grid(row=6, column=1, sticky=tk.EW, pady=3, padx=(8, 0))
        ttk.Label(f, text="Nhập lại mật khẩu:").grid(row=7, column=0, sticky=tk.W, pady=3)
        self.pw2_entry = ttk.Entry(f, textvariable=self.pw2_var, show="•", width=34)
        self.pw2_entry.grid(row=7, column=1, sticky=tk.EW, pady=3, padx=(8, 0))

        ttk.Label(f, text="Mật khẩu này dùng để mở khoá bí mật khi ký và chỉ hỏi lại lúc ký; "
                          "chương trình không lưu nó. Để trống nếu không muốn đặt mật khẩu.",
                  style="Muted.TLabel", wraplength=440, justify=tk.LEFT).grid(
            row=8, column=0, columnspan=2, sticky=tk.W, pady=(6, 0))
        ttk.Label(f, text="Chứng chỉ tự ký: người khác mở file đã ký sẽ thấy chữ ký hợp lệ về "
                          "kỹ thuật nhưng cần tự tin chứng chỉ này (hoặc dùng chứng chỉ do CA cấp).",
                  style="Muted.TLabel", wraplength=440, justify=tk.LEFT).grid(
            row=9, column=0, columnspan=2, sticky=tk.W, pady=(4, 0))

        bar = ttk.Frame(f)
        bar.grid(row=10, column=0, columnspan=2, sticky=tk.E, pady=(12, 0))
        ttk.Button(bar, text="Tạo", command=self._ok).pack(side=tk.LEFT)
        ttk.Button(bar, text="Huỷ", command=self.win.destroy).pack(side=tk.LEFT, padx=(6, 0))
        self.win.bind("<Return>", lambda e: self._ok())
        self.win.bind("<Escape>", lambda e: self.win.destroy())

    def _ok(self):
        password = self.pw_var.get()
        if password != self.pw2_var.get():
            messagebox.showerror("Tạo chứng chỉ", "Hai ô mật khẩu không khớp nhau.",
                                 parent=self.win)
            self.pw2_entry.focus_set()
            return
        if not self.cn_var.get().strip():
            messagebox.showerror("Tạo chứng chỉ", "Hãy nhập họ và tên của người ký.",
                                 parent=self.win)
            self.cn_entry.focus_set()
            return
        self.win.config(cursor="watch")
        self.win.update_idletasks()
        try:
            self.entry = digital_sign.create_certificate(
                self.cn_var.get(), self.org_var.get(), self.unit_var.get(),
                self.country_var.get(), self.email_var.get(), self.years_var.get(), password)
        except digital_sign.SignError as ex:
            messagebox.showerror("Tạo chứng chỉ", str(ex), parent=self.win)
            return
        except Exception as ex:  # lỗi sinh khoá / ghi file
            messagebox.showerror("Tạo chứng chỉ", f"Không tạo được chứng chỉ:\n{ex}",
                                 parent=self.win)
            return
        finally:
            self.win.config(cursor="")
        self.win.destroy()
