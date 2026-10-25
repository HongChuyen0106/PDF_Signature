"""UD Trucks PDF Signature - trình xem PDF đơn giản: đặt chữ ký (ảnh PNG) lên trang và ký số bằng chứng chỉ số (.p12)."""

import bisect
import gc
import math
import os
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont, ImageTk

try:  # kéo thả file từ Explorer (tuỳ chọn)
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    TkinterDnD = None

ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 0.25, 5.0, 1.2
PAD = 20                  # khoảng đệm quanh trang trên canvas (px)
HANDLE = 10               # kích thước nút kéo giãn chữ ký (px)
DEFAULT_SIG_WIDTH = 150   # chiều rộng mặc định khi đặt chữ ký (point)
MIN_SIG_WIDTH = 15        # chiều rộng tối thiểu (point)
APP_NAME = "UD Trucks PDF Signature"
PREVIEW_MAX = 1200        # cạnh lớn nhất của ảnh xem trước (px)

# Bảng màu theo phong cách Windows 11 (khớp với theme Sun Valley)
THEME_COLORS = {
    "light": {"accent": "#005fb8", "on_accent": "#ffffff", "disabled": "#c5c5c5",
              "muted": "#5f5f5f", "canvas": "#e6e6e6", "shadow": "#c4c4c4",
              "drop_border": "#9a9a9a", "drop_hover": "#dde8f5"},
    "dark": {"accent": "#57c8ff", "on_accent": "#000000", "disabled": "#5c5c5c",
             "muted": "#a8a8a8", "canvas": "#1b1b1b", "shadow": "#0b0b0b",
             "drop_border": "#6a6a6a", "drop_hover": "#1f2b35"},
}
DROP_MARGIN = 24          # khoảng cách từ vùng kéo thả tới mép canvas (px, trước khi nhân DPI)
DROP_RADIUS = 12          # bán kính bo góc vùng kéo thả (px)
DROP_DASH = (8, 6)        # độ dài nét / khoảng trống của viền nét đứt (px)


def _load_font(names, size):
    fonts_dir = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Fonts")
    for name in names:
        try:
            return ImageFont.truetype(os.path.join(fonts_dir, name), size)
        except OSError:
            pass
    return ImageFont.load_default(size)


def windows_dark_mode():
    """True nếu Windows đang đặt chế độ tối cho ứng dụng."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def apply_theme(root):
    """Áp dụng theme Windows 11 (Sun Valley) theo chế độ sáng/tối của hệ thống."""
    dark = windows_dark_mode()
    try:
        import sv_ttk
        sv_ttk.set_theme("dark" if dark else "light", root)
    except ImportError:
        dark = False  # không có sv-ttk: dùng giao diện ttk mặc định
    try:  # thanh tiêu đề tối (Windows 10 20H1+ / Windows 11)
        from ctypes import byref, c_int, sizeof, windll
        root.update_idletasks()
        hwnd = windll.user32.GetParent(root.winfo_id())
        value = c_int(1 if dark else 0)
        windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, byref(value), sizeof(value))
    except Exception:
        pass
    return dark


class Signature:
    """Ảnh chữ ký đã nhập: dữ liệu gốc để nhúng vào PDF và bản thu nhỏ để hiển thị."""

    def __init__(self, path):
        with open(path, "rb") as f:
            self.data = f.read()
        img = Image.open(path)
        img.load()
        img = img.convert("RGBA")
        img.thumbnail((PREVIEW_MAX, PREVIEW_MAX))
        self.preview = img
        self.name = os.path.basename(path)

    @property
    def aspect(self):
        return self.preview.height / self.preview.width


class Placement:
    """Một chữ ký đã đặt trên trang. rect tính bằng point, theo hệ toạ độ trang đang hiển thị."""

    def __init__(self, page, rect, signature):
        self.page = page
        self.rect = rect
        self.signature = signature
        self.tk_img = None


def apply_signatures(src_path, out_path, placements, password=None):
    """Chèn các chữ ký vào bản sao của file PDF gốc và lưu ra out_path (None: trả về bytes)."""
    doc = fitz.open(src_path)
    try:
        if doc.needs_pass and not doc.authenticate(password or ""):
            raise ValueError("Sai mật khẩu PDF.")
        xrefs = {}  # mỗi ảnh chỉ nhúng một lần, các lần sau dùng lại xref
        for p in placements:
            page = doc[p.page]
            # rect đang ở hệ toạ độ đã xoay; chuyển về hệ toạ độ gốc của trang
            target = p.rect * page.derotation_matrix
            key = id(p.signature)
            if key in xrefs:
                page.insert_image(target, xref=xrefs[key], rotate=page.rotation,
                                  keep_proportion=False)
            else:
                xrefs[key] = page.insert_image(target, stream=p.signature.data,
                                               rotate=page.rotation, keep_proportion=False)
        if out_path is None:
            return doc.tobytes(garbage=3, deflate=True)
        doc.save(out_path, garbage=3, deflate=True)
    finally:
        doc.close()


def to_pdf_box(page, rect):
    """Đổi rect (point, hệ toạ độ trang đang hiển thị) sang hệ toạ độ PDF gốc dưới-trái."""
    r = rect * page.derotation_matrix * ~page.transformation_matrix
    r.normalize()
    return (r.x0, r.y0, r.x1, r.y1)


def rounded_rect_points(x0, y0, x1, y1, r, steps=8):
    """Các điểm (khép kín) của hình chữ nhật bo góc, đi theo chiều kim đồng hồ."""
    r = max(0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    pts = []
    for cx, cy, start in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0),
                          (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        for i in range(steps + 1):
            a = math.radians(start + 90 * i / steps)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    pts.append(pts[0])
    return pts


def dash_polyline(points, dash, gap):
    """Chia đường gấp khúc khép kín thành các nét đứt đều nhau; trả về danh sách nét (list điểm)."""
    cum = [0.0]
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        cum.append(cum[-1] + math.hypot(bx - ax, by - ay))
    total = cum[-1]
    n = max(1, round(total / (dash + gap)))  # co giãn nhẹ để các nét khép kín đều
    period = total / n
    on = period * dash / (dash + gap)

    def point_at(s):
        i = max(1, min(len(cum) - 1, bisect.bisect_left(cum, s)))
        (ax, ay), (bx, by) = points[i - 1], points[i]
        seg = cum[i] - cum[i - 1]
        t = (s - cum[i - 1]) / seg if seg else 0
        return ax + (bx - ax) * t, ay + (by - ay) * t

    dashes = []
    for k in range(n):
        s, e = k * period, k * period + on
        dashes.append([point_at(s)] + [points[i] for i, c in enumerate(cum) if s < c < e]
                      + [point_at(e)])
    return dashes


def run_in_background(root, func, on_done):
    """Chạy func trong luồng riêng, gọi on_done(kết quả, lỗi) trên luồng giao diện."""
    result = {}

    def worker():
        try:
            result["value"] = func()
        except Exception as ex:  # chuyển lỗi về luồng giao diện
            result["error"] = ex

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    def poll():
        if thread.is_alive():
            root.after(100, poll)
        else:
            on_done(result.get("value"), result.get("error"))
    root.after(100, poll)


class AutoScrollbar(ttk.Scrollbar):
    """Thanh cuộn tự ẩn khi toàn bộ nội dung đã hiển thị vừa khung."""

    def set(self, first, last):
        if float(first) <= 0 and float(last) >= 1:
            self.grid_remove()
        else:
            self.grid()
        super().set(first, last)


class PdfSignApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("1100x850")
        self.root.minsize(700, 400)

        self.doc = None
        self.pdf_path = None
        self.password = None
        self.page_index = 0
        self.zoom = 1.0
        self.page_tk = None
        self.ox = self.oy = PAD   # vị trí góc trên-trái của trang trên canvas

        self.signature = None
        self.placing = False
        self.placements = []
        self.selected = None
        self.drag = None          # (chế độ, x bắt đầu, y bắt đầu, rect ban đầu)
        self.dirty = False
        self._resize_job = None
        self._zoom_job = None
        self.fit_mode = False      # đang ở chế độ vừa khung
        self._last_flip = 0.0
        self._syncing_slider = False
        self.thumb_tk = None
        self.drop_zone = None      # (x0, y0, x1, y1) của vùng kéo thả khi chưa mở file
        self.drop_hover = False

        self.dark = apply_theme(root)
        self._build_ui()
        self._bind_keys()
        self._update_controls()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        self.ui_scale = self.root.winfo_fpixels("1i") / 96
        colors = THEME_COLORS["dark" if self.dark else "light"]
        self.colors = colors
        style = ttk.Style()
        style.configure("Muted.TLabel", foreground=colors["muted"])
        style.configure("Chevron.TLabel", foreground=colors["muted"], font=("Segoe UI", 14))
        self._make_badges()

        # ---- thanh các bước (trên)
        header = ttk.Frame(self.root, padding=(14, 10))
        header.pack(side=tk.TOP, fill=tk.X)
        steps = (
            ("Mở file", self.open_pdf),
            ("Nhập chữ ký PNG", self.import_signature),
            ("Đặt chữ ký", lambda: self._set_placing(True)),
            ("Ký số", self.sign_digital),
            ("Lưu file đã ký", self.save_pdf),
        )
        self.step_buttons = []
        for i, (text, command) in enumerate(steps, 1):
            if i > 1:
                ttk.Label(header, text="›", style="Chevron.TLabel").pack(side=tk.LEFT, padx=6)
            btn = ttk.Button(header, text=f"  {text}", compound=tk.LEFT, command=command,
                             padding=(10, 6))
            btn.pack(side=tk.LEFT)
            self.step_buttons.append(btn)
            if i == 2:
                card = ttk.Frame(header, style="Card.TFrame", padding=4)
                card.pack(side=tk.LEFT, padx=(8, 0))
                self.thumb = ttk.Label(card, text="Chưa có chữ ký", style="Muted.TLabel",
                                       anchor=tk.CENTER, padding=(6, 0))
                self.thumb.pack()
        self.btn_settings = ttk.Button(header, text="Cài đặt", style="Toolbutton",
                                       command=self.open_settings, padding=(10, 6))
        self.btn_settings.pack(side=tk.RIGHT)
        ttk.Separator(self.root).pack(side=tk.TOP, fill=tk.X)

        # ---- thanh trạng thái, chuyển trang và zoom (dưới)
        bottom = ttk.Frame(self.root, padding=(14, 6))
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Separator(self.root).pack(side=tk.BOTTOM, fill=tk.X)
        bottom.columnconfigure(0, weight=1)

        self.status = tk.StringVar(value="Bắt đầu với bước 1: mở file PDF (Ctrl+O).")
        ttk.Label(bottom, textvariable=self.status, style="Muted.TLabel",
                  anchor=tk.W).grid(row=0, column=0, sticky=tk.EW, padx=(0, 12))

        nav = ttk.Frame(bottom)
        nav.grid(row=0, column=1)
        self.btn_prev = ttk.Button(nav, text="‹", width=3, style="Toolbutton", command=self.prev_page)
        self.btn_prev.pack(side=tk.LEFT)
        self.page_var = tk.StringVar(value="0")
        self.page_entry = ttk.Entry(nav, textvariable=self.page_var, width=4, justify=tk.CENTER)
        self.page_entry.pack(side=tk.LEFT, padx=(4, 4))
        self.page_entry.bind("<Return>", self._goto_entered_page)
        self.page_total = ttk.Label(nav, text="/ 0", style="Muted.TLabel")
        self.page_total.pack(side=tk.LEFT, padx=(0, 4))
        self.btn_next = ttk.Button(nav, text="›", width=3, style="Toolbutton", command=self.next_page)
        self.btn_next.pack(side=tk.LEFT)

        ttk.Separator(bottom, orient=tk.VERTICAL).grid(row=0, column=2, sticky=tk.NS, padx=12)

        zoom = ttk.Frame(bottom)
        zoom.grid(row=0, column=3)
        self.btn_zoom_out = ttk.Button(zoom, text="−", width=3, style="Toolbutton",
                                       command=self.zoom_out)
        self.btn_zoom_out.pack(side=tk.LEFT)
        self.zoom_scale = ttk.Scale(zoom, from_=math.log(ZOOM_MIN), to=math.log(ZOOM_MAX),
                                    length=round(140 * self.ui_scale), command=self._on_zoom_slider)
        self.zoom_scale.pack(side=tk.LEFT, padx=4)
        self.btn_zoom_in = ttk.Button(zoom, text="+", width=3, style="Toolbutton",
                                      command=self.zoom_in)
        self.btn_zoom_in.pack(side=tk.LEFT)
        self.zoom_label = ttk.Label(zoom, text="100%", width=5, anchor=tk.E)
        self.zoom_label.pack(side=tk.LEFT, padx=(4, 8))
        self.btn_fit = ttk.Button(zoom, text="Vừa khung", style="Toolbutton", command=self.fit_width)
        self.btn_fit.pack(side=tk.LEFT)

        # ---- vùng xem trang
        area = ttk.Frame(self.root)
        area.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(area, bg=colors["canvas"], highlightthickness=0)
        vbar = AutoScrollbar(area, orient=tk.VERTICAL, command=self.canvas.yview)
        hbar = AutoScrollbar(area, orient=tk.HORIZONTAL, command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")
        area.rowconfigure(0, weight=1)
        area.columnconfigure(0, weight=1)

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_right_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Shift-MouseWheel>", self._on_shift_wheel)
        self.canvas.bind("<Control-MouseWheel>", self._on_ctrl_wheel)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<Leave>", lambda e: self._set_drop_hover(False))
        if self._enable_dnd():
            self.canvas.drop_target_register(DND_FILES)
            self.canvas.dnd_bind("<<DropEnter>>", self._on_drop_enter)
            self.canvas.dnd_bind("<<DropLeave>>", self._on_drop_leave)
            self.canvas.dnd_bind("<<Drop>>", self._on_drop)

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Xoá chữ ký", command=self.delete_selected)

    def _make_badges(self):
        """Vẽ huy hiệu số thứ tự hình tròn cho các nút bước (thường / nổi bật / mờ)."""
        c = self.colors
        size = round(20 * self.ui_scale)
        big = size * 4  # vẽ lớn rồi thu nhỏ để viền tròn mịn
        font = _load_font(("segoeuib.ttf", "arialbd.ttf"), round(big * 0.58))
        self.badges = {}
        for n in range(1, 6):
            variants = []
            for fill, ink in ((c["accent"], c["on_accent"]), (c["on_accent"], c["accent"]),
                              (c["disabled"], c["on_accent"])):
                img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
                d = ImageDraw.Draw(img)
                d.ellipse((0, 0, big - 1, big - 1), fill=fill)
                d.text((big / 2, big / 2 + big * 0.02), str(n), font=font, fill=ink, anchor="mm")
                variants.append(ImageTk.PhotoImage(img.resize((size, size), Image.LANCZOS)))
            self.badges[n] = variants

    def _update_thumb(self):
        if self.signature is None:
            self.thumb.config(image="", text="Chưa có chữ ký")
            return
        w, h = round(120 * self.ui_scale), round(34 * self.ui_scale)
        img = self.signature.preview.copy()
        img.thumbnail((w, h), Image.LANCZOS)
        # nền trắng để chữ ký mực tối vẫn nhìn rõ ở giao diện tối
        bg = Image.new("RGBA", (w, h), (255, 255, 255, 255))
        bg.alpha_composite(img, ((w - img.width) // 2, (h - img.height) // 2))
        self.thumb_tk = ImageTk.PhotoImage(bg)
        self.thumb.config(image=self.thumb_tk, text="")

    def _bind_keys(self):
        r = self.root
        r.bind("<Control-o>", lambda e: self.open_pdf())
        r.bind("<Control-s>", lambda e: self.save_pdf())
        r.bind("<Control-plus>", lambda e: self.zoom_in())
        r.bind("<Control-equal>", lambda e: self.zoom_in())
        r.bind("<Control-minus>", lambda e: self.zoom_out())
        r.bind("<Control-0>", lambda e: self.fit_width())
        r.bind("<Control-comma>", lambda e: self.open_settings())
        r.bind("<Prior>", lambda e: self.prev_page())
        r.bind("<Next>", lambda e: self.next_page())
        r.bind("<Left>", self._key_nav(self.prev_page))
        r.bind("<Right>", self._key_nav(self.next_page))
        r.bind("<Delete>", self._key_nav(self.delete_selected))
        r.bind("<Escape>", lambda e: self._cancel())

    def _key_nav(self, action):
        def handler(event):
            if not isinstance(event.widget, (tk.Entry, ttk.Entry)):
                action()
        return handler

    def _next_step(self):
        """Bước nên làm tiếp theo (1-5), dùng để làm nổi bật nút tương ứng."""
        if not self.doc:
            return 1
        if not self.signature:
            return 2
        if not self.placements:
            return 3
        return 4

    def _update_controls(self):
        has_doc = self.doc is not None
        n = len(self.doc) if has_doc else 0

        def state(widget, enabled):
            widget.state(["!disabled"] if enabled else ["disabled"])

        enabled_steps = (True, True, has_doc and self.signature is not None,
                         has_doc and bool(self.placements), has_doc and bool(self.placements))
        nxt = self._next_step()
        for i, (btn, enabled) in enumerate(zip(self.step_buttons, enabled_steps), 1):
            normal, accent, disabled = self.badges[i]
            btn.config(style="Accent.TButton" if i == nxt else "TButton",
                       image=(accent if i == nxt else normal, "disabled", disabled))
            state(btn, enabled)

        state(self.btn_prev, has_doc and self.page_index > 0)
        state(self.btn_next, has_doc and self.page_index < n - 1)
        state(self.page_entry, has_doc)
        for w in (self.btn_zoom_in, self.btn_zoom_out, self.btn_fit, self.zoom_scale):
            state(w, has_doc)
        self.page_var.set(str(self.page_index + 1 if has_doc else 0))
        self.page_total.config(text=f"/ {n}")
        self.zoom_label.config(text=f"{round(self.zoom * 100)}%")
        self._syncing_slider = True
        self.zoom_scale.set(math.log(self.zoom))
        self._syncing_slider = False
        title = APP_NAME
        if self.pdf_path:
            title = f"{'*' if self.dirty else ''}{os.path.basename(self.pdf_path)} - {APP_NAME}"
        self.root.title(title)

    def _on_zoom_slider(self, value):
        if self._syncing_slider or not self.doc:
            return
        if self._zoom_job:
            self.root.after_cancel(self._zoom_job)
        self._zoom_job = self.root.after(60, lambda: self.set_zoom(math.exp(float(value))))

    # ------------------------------------------------------------- file
    def open_pdf(self):
        if not self._confirm_discard():
            return
        path = filedialog.askopenfilename(title="Mở file PDF",
                                          filetypes=[("PDF", "*.pdf"), ("Tất cả", "*.*")])
        if path:
            self.load_pdf(path)

    def load_pdf(self, path):
        try:
            doc = fitz.open(path)
        except Exception as ex:
            messagebox.showerror("Lỗi", f"Không mở được file:\n{ex}")
            return
        password = None
        if doc.needs_pass:
            password = simpledialog.askstring("Mật khẩu", "File PDF được bảo vệ. Nhập mật khẩu:",
                                              show="*", parent=self.root)
            if password is None or not doc.authenticate(password):
                doc.close()
                if password is not None:
                    messagebox.showerror("Lỗi", "Sai mật khẩu.")
                return
        if len(doc) == 0:
            doc.close()
            messagebox.showerror("Lỗi", "File PDF không có trang nào.")
            return

        if self.doc:
            self.doc.close()
        self.doc, self.pdf_path, self.password = doc, path, password
        self.page_index = 0
        self.placements.clear()
        self.selected = None
        self.dirty = False
        self.fit_width()
        self.status.set(f"Đã mở {os.path.basename(path)} ({len(doc)} trang)."
                        + ("" if self.signature else " Tiếp theo: nhập chữ ký PNG (bước 2)."))

    def save_pdf(self):
        if not self.doc:
            return
        if not self.placements:
            messagebox.showinfo(APP_NAME, "Chưa có chữ ký nào được đặt trên trang.")
            return
        warning = self._multi_image_message()
        if warning and not messagebox.askyesno(APP_NAME, warning + "\n\nVẫn lưu tất cả các ảnh?",
                                               icon=messagebox.WARNING, default=messagebox.NO):
            return
        if self._has_digital_signatures() and not messagebox.askyesno(
                APP_NAME, "File này đã có chữ ký số. Chèn ảnh vào trang sẽ làm các chữ ký số "
                            "hiện có mất hiệu lực.\n\nVẫn tiếp tục?", icon=messagebox.WARNING):
            return
        folder, name = os.path.split(self.pdf_path)
        out = filedialog.asksaveasfilename(title="Lưu PDF đã ký", initialdir=folder,
                                           initialfile=f"{os.path.splitext(name)[0]}_signed.pdf",
                                           defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
        if not out or self._same_as_source(out):
            return
        try:
            apply_signatures(self.pdf_path, out, self.placements, self.password)
        except Exception as ex:
            messagebox.showerror("Lỗi", f"Không lưu được file:\n{ex}")
            return
        self.dirty = False
        self._update_controls()
        self.status.set(f"Đã lưu: {out}")

    def _multi_image_message(self):
        """Thông báo khi tài liệu có từ 2 ảnh chữ ký trở lên, None nếu chỉ có 0-1 ảnh."""
        if len(self.placements) < 2:
            return None
        pages = sorted({p.page + 1 for p in self.placements})
        return (f"Đã có {len(self.placements)} ảnh chữ ký được chèn lên tài liệu "
                f"(trang {', '.join(map(str, pages))}).\n\n"
                "Hãy kiểm tra lại và xoá bớt (chọn ảnh rồi bấm Delete, hoặc chuột phải → Xoá chữ ký).")

    def _same_as_source(self, out):
        if os.path.normcase(os.path.abspath(out)) == os.path.normcase(os.path.abspath(self.pdf_path)):
            messagebox.showerror("Lỗi", "Hãy lưu với tên khác để không ghi đè file gốc.")
            return True
        return False

    def _has_digital_signatures(self):
        # SigFlags bit 1 (SignaturesExist) được bật khi file có ít nhất một chữ ký số; -1: không có
        flags = self.doc.get_sigflags()
        return flags > 0 and bool(flags & 1)

    # ---------------------------------------------------------- ký số
    def open_settings(self):
        """Mở mục Cài đặt: tạo chứng chỉ ký số mới hoặc nhập file .p12 đã có."""
        try:
            from settings_dialog import SettingsDialog, list_certs
        except ImportError as ex:
            messagebox.showerror("Thiếu thư viện", f"Không mở được mục Cài đặt:\n{ex}")
            return
        SettingsDialog(self.root)
        n = len(list_certs())
        self.status.set(f"Cài đặt: {n} chứng chỉ ký số." if n else
                        "Cài đặt: chưa có chứng chỉ ký số.")

    def sign_digital(self):
        if not self.doc:
            return
        try:
            import digital_sign
            from settings_dialog import SettingsDialog, list_certs
            from sign_dialog import SignDialog
        except ImportError as ex:
            messagebox.showerror("Thiếu thư viện", "Cần cài thư viện ký số:\n"
                                 f"pip install -r requirements.txt\n\n{ex}")
            return

        # Vị trí ký số là hình chữ ký duy nhất đã đặt trên tài liệu
        if not self.placements:
            messagebox.showinfo("Ký số", "Hãy đặt chữ ký lên trang (bước 2 và 3) trước khi ký số.")
            return
        warning = self._multi_image_message()
        if warning:
            messagebox.showwarning("Ký số", warning + "\n\nKý số cần đúng 1 ảnh chữ ký trên tài liệu.")
            return
        visible_p = self.placements[0]
        self.go_to(visible_p.page)
        self.selected = visible_p
        self._draw_placements()
        if self.doc[visible_p.page].rotation and not messagebox.askyesno(
                "Ký số", "Trang này đang bị xoay, hình chữ ký có thể hiển thị bị xoay theo.\n"
                         "Vẫn tiếp tục?"):
            return

        # Chưa có chứng chỉ số: mở Cài đặt để tạo mới hoặc nhập file .p12
        if not list_certs():
            if not messagebox.askyesno(
                    "Ký số", "Chưa có chứng chỉ ký số.\n\n"
                             "Bấm \"Có\" để mở mục Cài đặt và tạo chứng chỉ mới "
                             "(hoặc nhập file .p12 đã có).",
                    icon=messagebox.WARNING):
                return
            SettingsDialog(self.root)
            if not list_certs():
                self.status.set("Ký số huỷ: chưa có chứng chỉ ký số.")
                return

        with open(self.pdf_path, "rb") as f:
            pdf_bytes = f.read()
        page = self.doc[visible_p.page]
        visible = digital_sign.VisibleSignature(visible_p.page, to_pdf_box(page, visible_p.rect),
                                              visible_p.signature.preview)

        opts = SignDialog(self.root, True).result
        # dọn các biến Tk của hộp thoại ngay trên luồng giao diện, tránh bị dọn trong luồng ký
        gc.collect()
        if not opts:
            return
        visible.show_text = opts["show_text"]

        folder, name = os.path.split(self.pdf_path)
        out = filedialog.asksaveasfilename(title="Lưu file đã ký số", initialdir=folder,
                                           initialfile=f"{os.path.splitext(name)[0]}_signed.pdf",
                                           defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
        if not out or self._same_as_source(out):
            return
        password = self.password

        def work():
            signer = digital_sign.load_signer(opts["cert"]["path"], opts["password"])
            digital_sign.sign_with_signer(pdf_bytes, out, signer, visible, opts["reason"],
                                          opts["location"], opts["tsa_url"], password)

        busy = BusyWindow(self.root, "Đang ký số, vui lòng chờ...")

        def done(_value, error):
            busy.close()
            if error:
                messagebox.showerror("Ký số thất bại", str(error) or type(error).__name__)
                self.status.set("Ký số thất bại.")
                return
            self.dirty = False
            self._update_controls()
            self.status.set(f"Đã ký số: {out}")
            if messagebox.askyesno("Ký số", f"Đã ký số thành công:\n{out}\n\nMở file đã ký?"):
                self.load_pdf(out)

        run_in_background(self.root, work, done)

    def import_signature(self):
        path = filedialog.askopenfilename(title="Chọn ảnh chữ ký",
                                          filetypes=[("Ảnh PNG", "*.png"), ("Tất cả", "*.*")])
        if not path:
            return
        try:
            self.signature = Signature(path)
        except Exception as ex:
            messagebox.showerror("Lỗi", f"Không đọc được ảnh:\n{ex}")
            return
        self._update_thumb()
        if self.doc:
            self.status.set(f"Đã nhập chữ ký {self.signature.name}. "
                            "Bấm bước 3 \"Đặt chữ ký\" rồi bấm vào vị trí trên trang.")
        else:
            self.status.set(f"Đã nhập chữ ký {self.signature.name}. Hãy mở file PDF (bước 1).")
        self._update_controls()

    def _confirm_discard(self):
        if not self.dirty:
            return True
        return messagebox.askyesno(APP_NAME, "Các chữ ký đã đặt chưa được lưu. Bỏ qua và tiếp tục?")

    def on_close(self):
        if self._confirm_discard():
            self.root.destroy()

    # ------------------------------------------------------- navigation
    def go_to(self, index):
        if not self.doc:
            return
        index = max(0, min(index, len(self.doc) - 1))
        if index != self.page_index:
            self.page_index = index
            self.selected = None
            if self.fit_mode:  # các trang có thể khác kích thước
                self.fit_width()
            else:
                self.render()
            self.canvas.yview_moveto(0)
        else:
            self._update_controls()

    def prev_page(self):
        self.go_to(self.page_index - 1)

    def next_page(self):
        self.go_to(self.page_index + 1)

    def _goto_entered_page(self, _event):
        try:
            self.go_to(int(self.page_var.get()) - 1)
        except ValueError:
            self._update_controls()
        self.canvas.focus_set()

    def set_zoom(self, zoom, fit=False):
        if not self.doc:
            return
        self.fit_mode = fit
        self.zoom = max(ZOOM_MIN, min(zoom, ZOOM_MAX))
        self.render()

    def zoom_in(self):
        self.set_zoom(self.zoom * ZOOM_STEP)

    def zoom_out(self):
        self.set_zoom(self.zoom / ZOOM_STEP)

    def fit_width(self):
        if not self.doc:
            return
        self.root.update_idletasks()
        page = self.doc[self.page_index]
        avail_w = max(100, self.canvas.winfo_width() - 2 * PAD)
        avail_h = max(100, self.canvas.winfo_height() - 2 * PAD)
        self.set_zoom(min(avail_w / page.rect.width, avail_h / page.rect.height), fit=True)

    # ----------------------------------------------------------- render
    def render(self):
        self.canvas.delete("all")
        if not self.doc:
            self._draw_drop_zone()
            self._update_controls()
            return
        self.drop_zone = None
        self.drop_hover = False
        page = self.doc[self.page_index]
        pix = page.get_pixmap(matrix=fitz.Matrix(self.zoom, self.zoom), alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        self.page_tk = ImageTk.PhotoImage(img)

        self.ox = max(PAD, (self.canvas.winfo_width() - pix.width) // 2)
        self.oy = PAD
        self.canvas.create_rectangle(self.ox + 3, self.oy + 3, self.ox + pix.width + 3,
                                     self.oy + pix.height + 3, fill=self.colors["shadow"], outline="")
        self.canvas.create_image(self.ox, self.oy, image=self.page_tk, anchor=tk.NW)
        self.canvas.configure(scrollregion=(0, 0, self.ox + pix.width + PAD,
                                            self.oy + pix.height + PAD))
        self._draw_placements()
        self._update_controls()

    def _draw_placements(self):
        self.canvas.delete("sig")
        for p in self.placements:
            if p.page != self.page_index:
                continue
            x0, y0, x1, y1 = self._canvas_rect(p.rect)
            size = (max(1, round(x1 - x0)), max(1, round(y1 - y0)))
            resample = Image.BILINEAR if self.drag else Image.LANCZOS
            p.tk_img = ImageTk.PhotoImage(p.signature.preview.resize(size, resample))
            self.canvas.create_image(x0, y0, image=p.tk_img, anchor=tk.NW, tags="sig")
            if p is self.selected:
                self.canvas.create_rectangle(x0, y0, x1, y1, outline="#1e6fff", width=2,
                                             dash=(4, 2), tags="sig")
                self.canvas.create_rectangle(x1 - HANDLE, y1 - HANDLE, x1, y1, fill="#1e6fff",
                                             outline="white", tags="sig")

    def _on_canvas_resize(self, _event):
        if self.doc:
            if self._resize_job:
                self.root.after_cancel(self._resize_job)
            self._resize_job = self.root.after(120, self.fit_width if self.fit_mode else self.render)
        else:
            self._draw_drop_zone()

    # -------------------------------------------------------- kéo thả
    def _enable_dnd(self):
        """Nạp thư viện tkdnd; False nếu không có (vẫn mở file bằng cách bấm vào vùng giữa)."""
        if TkinterDnD is None:
            return False
        try:
            TkinterDnD._require(self.root)
            return True
        except RuntimeError:
            return False

    def _draw_drop_zone(self):
        """Vùng giữa khi chưa mở file: viền nét đứt, kéo thả PDF vào hoặc bấm để mở."""
        c, s = self.colors, self.ui_scale
        cv = self.canvas
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        cv.configure(scrollregion=(0, 0, w, h))
        cv.xview_moveto(0)
        cv.yview_moveto(0)
        m = round(DROP_MARGIN * s)
        x0, y0, x1, y1 = m, m, w - m, h - m
        if x1 - x0 < 40 or y1 - y0 < 40:
            self.drop_zone = None
            return
        self.drop_zone = (x0, y0, x1, y1)
        hover = self.drop_hover
        line = c["accent"] if hover else c["drop_border"]
        outline = rounded_rect_points(x0, y0, x1, y1, DROP_RADIUS * s)
        if hover:
            cv.create_polygon(outline, fill=c["drop_hover"], outline="")
        for dash in dash_polyline(outline, DROP_DASH[0] * s, DROP_DASH[1] * s):
            cv.create_line(dash, fill=line, width=max(1, round(1.5 * s)), capstyle=tk.ROUND)

        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        cv.create_text(cx, cy - 18 * s, text="", font=("Segoe MDL2 Assets", 32),
                       fill=c["accent"] if hover else c["muted"], anchor=tk.S)
        font = ("Segoe UI", 12)
        lead = cv.create_text(0, cy + 6 * s, text="Kéo thả vào đây hoặc ", font=font,
                              fill=c["muted"], anchor=tk.W)
        link = cv.create_text(0, cy + 6 * s, text="mở file", font=font + ("underline",),
                              fill=c["accent"], anchor=tk.W)
        lw = cv.bbox(lead)[2] - cv.bbox(lead)[0]
        tw = lw + cv.bbox(link)[2] - cv.bbox(link)[0]
        cv.coords(lead, cx - tw / 2, cy + 6 * s)
        cv.coords(link, cx - tw / 2 + lw, cy + 6 * s)

    def _in_drop_zone(self, cx, cy):
        z = self.drop_zone
        return z is not None and z[0] <= cx <= z[2] and z[1] <= cy <= z[3]

    def _set_drop_hover(self, on):
        if self.doc or on == self.drop_hover:
            return
        self.drop_hover = on
        self._draw_drop_zone()

    def _on_drop_enter(self, event):
        self._set_drop_hover(True)
        return event.action

    def _on_drop_leave(self, event):
        self._set_drop_hover(False)
        return event.action

    def _on_drop(self, event):
        self._set_drop_hover(False)
        files = self.root.tk.splitlist(event.data)
        pdfs = [f for f in files if f.lower().endswith(".pdf")]
        if not pdfs:
            messagebox.showerror("Lỗi", "Chỉ hỗ trợ kéo thả file PDF.")
        elif self._confirm_discard():
            self.load_pdf(pdfs[0])
        return event.action

    # ------------------------------------------------------ coordinates
    def _canvas_rect(self, r):
        z = self.zoom
        return (self.ox + r.x0 * z, self.oy + r.y0 * z, self.ox + r.x1 * z, self.oy + r.y1 * z)

    def _to_pdf(self, cx, cy):
        return (cx - self.ox) / self.zoom, (cy - self.oy) / self.zoom

    def _event_xy(self, e):
        return self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)

    def _hit(self, cx, cy):
        for p in reversed(self.placements):
            if p.page != self.page_index:
                continue
            x0, y0, x1, y1 = self._canvas_rect(p.rect)
            if p is self.selected and x1 - HANDLE - 2 <= cx <= x1 + 2 and y1 - HANDLE - 2 <= cy <= y1 + 2:
                return p, "resize"
            if x0 <= cx <= x1 and y0 <= cy <= y1:
                return p, "move"
        return None, None

    # ------------------------------------------------------------ mouse
    def _set_placing(self, on):
        self.placing = bool(on and self.doc and self.signature)
        self.canvas.config(cursor="crosshair" if self.placing else "")
        if self.placing:
            self.status.set("Bấm vào vị trí trên trang để đặt chữ ký (Esc để huỷ)")

    def _cancel(self):
        if self.placing:
            self._set_placing(False)
            self.status.set("Đã huỷ đặt chữ ký.")
        elif self.selected:
            self.selected = None
            self._draw_placements()

    def _place_at(self, cx, cy):
        page_rect = self.doc[self.page_index].rect
        x, y = self._to_pdf(cx, cy)
        if not (0 <= x <= page_rect.width and 0 <= y <= page_rect.height):
            self.status.set("Hãy bấm vào bên trong trang.")
            return
        aspect = self.signature.aspect
        w = min(DEFAULT_SIG_WIDTH, page_rect.width * 0.8, page_rect.height * 0.8 / aspect)
        h = w * aspect
        x0 = min(max(x - w / 2, 0), page_rect.width - w)
        y0 = min(max(y - h / 2, 0), page_rect.height - h)
        p = Placement(self.page_index, fitz.Rect(x0, y0, x0 + w, y0 + h), self.signature)
        self.placements.append(p)
        self.selected = p
        self.dirty = True
        self._set_placing(False)
        self._draw_placements()
        self._update_controls()
        self.status.set("Đã đặt chữ ký · Kéo để di chuyển, kéo góc để đổi cỡ")
        warning = self._multi_image_message()
        if warning:
            self.status.set(f"Cảnh báo: đã có {len(self.placements)} ảnh chữ ký trên tài liệu")
            messagebox.showwarning(APP_NAME, warning)

    def _on_press(self, e):
        self.canvas.focus_set()
        cx, cy = self._event_xy(e)
        if not self.doc:
            if self._in_drop_zone(cx, cy):
                self._set_drop_hover(False)
                self.open_pdf()
            return
        if self.placing:
            self._place_at(cx, cy)
            return
        p, mode = self._hit(cx, cy)
        self.selected = p
        self.drag = (mode, cx, cy, fitz.Rect(p.rect)) if p else None
        self._draw_placements()

    def _on_drag(self, e):
        if not self.drag or not self.selected:
            return
        mode, sx, sy, orig = self.drag
        cx, cy = self._event_xy(e)
        dx, dy = (cx - sx) / self.zoom, (cy - sy) / self.zoom
        page_rect = self.doc[self.page_index].rect
        if mode == "move":
            x0 = min(max(orig.x0 + dx, 0), page_rect.width - orig.width)
            y0 = min(max(orig.y0 + dy, 0), page_rect.height - orig.height)
            r = fitz.Rect(x0, y0, x0 + orig.width, y0 + orig.height)
        else:  # resize, giữ nguyên tỉ lệ ảnh
            aspect = orig.height / orig.width
            w = max(MIN_SIG_WIDTH, orig.width + max(dx, dy / aspect))
            w = min(w, page_rect.width - orig.x0, (page_rect.height - orig.y0) / aspect)
            r = fitz.Rect(orig.x0, orig.y0, orig.x0 + w, orig.y0 + w * aspect)
        if r != self.selected.rect:
            self.selected.rect = r
            self.dirty = True
            self._draw_placements()

    def _on_release(self, _e):
        if self.drag:
            self.drag = None
            self._draw_placements()  # vẽ lại với chất lượng cao
            self._update_controls()

    def _on_motion(self, e):
        if not self.doc:
            inside = self._in_drop_zone(*self._event_xy(e))
            self.canvas.config(cursor="hand2" if inside else "")
            self._set_drop_hover(inside)
            return
        if self.placing:
            return
        _, mode = self._hit(*self._event_xy(e))
        cursor = {"move": "fleur", "resize": "size_nw_se"}.get(mode, "")
        if self.canvas.cget("cursor") != cursor:
            self.canvas.config(cursor=cursor)

    def _on_right_click(self, e):
        if not self.doc:
            return
        p, _ = self._hit(*self._event_xy(e))
        if p:
            self.selected = p
            self._draw_placements()
            self.menu.tk_popup(e.x_root, e.y_root)

    def delete_selected(self):
        if self.selected in self.placements:
            self.placements.remove(self.selected)
            self.selected = None
            self.dirty = True
            self._draw_placements()
            self._update_controls()
            self.status.set("Đã xoá chữ ký.")

    @staticmethod
    def _wheel_steps(e):
        return -1 if e.delta > 0 else 1

    def _on_wheel(self, e):
        if not self.doc:
            return
        first, last = self.canvas.yview()
        if self.doc and first <= 0 and last >= 1:
            # cả trang đã hiển thị (ví dụ chế độ vừa khung): lăn chuột để chuyển trang
            now = time.monotonic()
            if now - self._last_flip > 0.25:  # mỗi lần lăn chỉ chuyển một trang
                self._last_flip = now
                self.go_to(self.page_index + self._wheel_steps(e))
            return
        self.canvas.yview_scroll(self._wheel_steps(e) * 3, "units")

    def _on_shift_wheel(self, e):
        if not self.doc:
            return
        self.canvas.xview_scroll(self._wheel_steps(e) * 3, "units")

    def _on_ctrl_wheel(self, e):
        if self._wheel_steps(e) < 0:
            self.zoom_in()
        else:
            self.zoom_out()


class BusyWindow:
    """Cửa sổ nhỏ báo đang xử lý, chặn thao tác với cửa sổ chính."""

    def __init__(self, parent, message):
        self.win = tk.Toplevel(parent)
        self.win.title(APP_NAME)
        self.win.transient(parent)
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", lambda: None)
        frame = ttk.Frame(self.win, padding=16)
        frame.pack()
        ttk.Label(frame, text=message, justify=tk.CENTER).pack()
        bar = ttk.Progressbar(frame, mode="indeterminate", length=260)
        bar.pack(pady=(12, 0))
        bar.start(12)
        self.win.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.win.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.win.winfo_height()) // 3
        self.win.geometry(f"+{x}+{y}")
        self.win.grab_set()

    def close(self):
        self.win.grab_release()
        self.win.destroy()


def main():
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)  # chữ sắc nét trên màn hình DPI cao
    except Exception:
        pass
    root = tk.Tk()
    PdfSignApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
