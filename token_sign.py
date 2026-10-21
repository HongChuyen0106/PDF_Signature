"""Ký số PDF (chuẩn PAdES) bằng USB token qua giao diện PKCS#11."""

import ctypes
import glob
import os
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

import pkcs11
from asn1crypto import x509
from PIL import Image, ImageDraw, ImageFont
from pkcs11 import Attribute, ObjectClass
from pkcs11 import exceptions as p11_errors
from pyhanko.pdf_utils.images import PdfImage
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.layout import AxisAlignment, InnerScaling, SimpleBoxLayoutRule
from pyhanko.sign import fields, signers, timestamps
from pyhanko.sign.pkcs11 import PKCS11Signer
from pyhanko.stamp import TextStampStyle

SYSTEM_DIR = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")

# Tên thư viện PKCS#11 thường gặp của các nhà cung cấp chữ ký số tại Việt Nam và quốc tế
KNOWN_LIBS = {
    "vnpt-ca_v34.dll": "VNPT-CA",
    "vnpt-ca_csp11.dll": "VNPT-CA",
    "vnptca_p11_v6.dll": "VNPT-CA",
    "viettel-ca_v4.dll": "Viettel-CA",
    "viettel-ca_v5.dll": "Viettel-CA",
    "viettel-ca_v6.dll": "Viettel-CA",
    "viettel-ca.dll": "Viettel-CA",
    "fpt-ca.dll": "FPT-CA",
    "fpt-ca-stx.dll": "FPT-CA",
    "bkavca.dll": "BKAV-CA",
    "bkavcav2.dll": "BKAV-CA",
    "bkavcsp11.dll": "BKAV-CA",
    "ca2-v34.dll": "CA2 (Nacencomm)",
    "ca2_v34.dll": "CA2 (Nacencomm)",
    "newca_v34.dll": "NewCA",
    "safe-ca.dll": "Safe-CA",
    "smartsign.dll": "SmartSign",
    "eps2003csp11.dll": "ePass2003",
    "shuttlecsp11_3003.dll": "ePass3003",
    "etpkcs11.dll": "SafeNet eToken",
    "idprimepkcs11.dll": "Thales IDPrime",
    "gclib.dll": "Gemalto",
    "opensc-pkcs11.dll": "OpenSC",
    "softhsm2-x64.dll": "SoftHSM2 (thử nghiệm)",
}
NAME_HINTS = ("pkcs11", "p11", "csp11", "-ca", "_ca", "ca_v", "token")

FONT_CANDIDATES = ("arial.ttf", "tahoma.ttf", "segoeui.ttf")
TIMESTAMP_FORMAT = "%d/%m/%Y %H:%M:%S"
APPEARANCE_DPI = 300
TEXT_COLOR = (20, 20, 120, 255)


class TokenError(Exception):
    """Lỗi có thông báo thân thiện để hiển thị cho người dùng."""


@dataclass
class TokenCert:
    lib_path: str
    slot_id: int
    token_label: str
    label: str
    cert_id: bytes
    der: bytes
    has_key: bool

    @property
    def x509(self):
        return x509.Certificate.load(self.der)

    @property
    def subject(self):
        name = self.x509.subject.native
        return name.get("common_name") or self.x509.subject.human_friendly

    @property
    def issuer(self):
        name = self.x509.issuer.native
        return name.get("common_name") or self.x509.issuer.human_friendly

    @property
    def not_after(self):
        return self.x509["tbs_certificate"]["validity"]["not_after"].native

    @property
    def not_before(self):
        return self.x509["tbs_certificate"]["validity"]["not_before"].native


@dataclass
class VisibleSignature:
    page: int                 # chỉ số trang (từ 0)
    box: tuple                # (x0, y0, x1, y1) trong hệ toạ độ PDF (gốc dưới-trái)
    image: object = None      # PIL.Image hoặc None
    show_text: bool = True    # hiện "Ký bởi / Thời gian" dưới hình chữ ký


# ---------------------------------------------------------------- thư viện
def _exports_pkcs11(path):
    """Kiểm tra DLL có hàm C_GetFunctionList mà không chạy mã khởi tạo của DLL."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LoadLibraryExW.restype = ctypes.c_void_p
    kernel32.LoadLibraryExW.argtypes = (ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32)
    kernel32.GetProcAddress.restype = ctypes.c_void_p
    kernel32.GetProcAddress.argtypes = (ctypes.c_void_p, ctypes.c_char_p)
    kernel32.FreeLibrary.argtypes = (ctypes.c_void_p,)
    handle = kernel32.LoadLibraryExW(path, None, 0x00000001)  # DONT_RESOLVE_DLL_REFERENCES
    if not handle:
        return False
    try:
        return bool(kernel32.GetProcAddress(handle, b"C_GetFunctionList"))
    finally:
        kernel32.FreeLibrary(handle)


def find_pkcs11_libs():
    """Tìm các thư viện PKCS#11 của USB token đã cài trên máy. Trả về [(tên hiển thị, đường dẫn)]."""
    found = {}
    for path in glob.glob(os.path.join(SYSTEM_DIR, "*.dll")):
        name = os.path.basename(path).lower()
        if name in KNOWN_LIBS or any(h in name for h in NAME_HINTS):
            try:
                if _exports_pkcs11(path):
                    found[os.path.normcase(path)] = (KNOWN_LIBS.get(name, os.path.basename(path)), path)
            except OSError:
                pass
    return sorted(found.values(), key=lambda item: item[0].lower())


_libs = {}


def _load_lib(lib_path):
    # PKCS#11 chỉ được khởi tạo một lần cho mỗi thư viện trong một tiến trình
    if lib_path not in _libs:
        try:
            _libs[lib_path] = pkcs11.lib(lib_path)
        except Exception as ex:
            raise TokenError(f"Không nạp được thư viện token:\n{lib_path}\n\n{ex}") from ex
    return _libs[lib_path]


def _friendly(ex):
    if isinstance(ex, TokenError):
        return ex
    messages = {
        p11_errors.PinIncorrect: "Mã PIN không đúng.",
        p11_errors.PinLocked: "Token đã bị khoá do nhập sai PIN quá nhiều lần. "
                              "Hãy liên hệ nhà cung cấp chữ ký số.",
        p11_errors.PinExpired: "Mã PIN đã hết hạn, hãy đổi PIN bằng phần mềm của token.",
        p11_errors.PinLenRange: "Độ dài mã PIN không hợp lệ.",
        p11_errors.TokenNotPresent: "Không tìm thấy USB token. Hãy cắm token vào máy.",
        p11_errors.DeviceRemoved: "USB token đã bị rút ra.",
    }
    for cls, msg in messages.items():
        if isinstance(ex, cls):
            return TokenError(msg)
    return TokenError(f"Lỗi token: {type(ex).__name__} {ex}".strip())


def _attr(obj, attribute, default=None):
    try:
        return obj[attribute]
    except Exception:
        return default


def _tokens(lib):
    slots = lib.get_slots(token_present=True)
    if not slots:
        raise TokenError("Không tìm thấy USB token. Hãy cắm token vào máy rồi thử lại.")
    return slots


def list_certificates(lib_path, pin):
    """Đăng nhập token bằng PIN và liệt kê các chứng thư số có khoá bí mật đi kèm."""
    lib = _load_lib(lib_path)
    certs, errors = [], []
    try:
        slots = _tokens(lib)
    except Exception as ex:
        raise _friendly(ex) from ex
    for slot in slots:
        try:
            token = slot.get_token()
            with token.open(user_pin=pin) as session:
                keys = list(session.get_objects({Attribute.CLASS: ObjectClass.PRIVATE_KEY}))
                key_ids = {_attr(k, Attribute.ID) for k in keys} - {None, b""}
                key_labels = {_attr(k, Attribute.LABEL) for k in keys} - {None, ""}
                for obj in session.get_objects({Attribute.CLASS: ObjectClass.CERTIFICATE}):
                    der = _attr(obj, Attribute.VALUE)
                    if not der:
                        continue
                    cert_id = _attr(obj, Attribute.ID, b"") or b""
                    label = _attr(obj, Attribute.LABEL, "") or ""
                    has_key = (cert_id in key_ids) or (label in key_labels)
                    certs.append(TokenCert(lib_path, slot.slot_id, token.label.strip(), label,
                                           cert_id, bytes(der), has_key))
        except Exception as ex:
            errors.append(ex)
    if not certs and errors:
        raise _friendly(errors[0]) from errors[0]
    with_key = [c for c in certs if c.has_key]
    return with_key or certs


# ---------------------------------------------------------------- ký số
def _font_path():
    fonts_dir = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Fonts")
    for name in FONT_CANDIDATES:
        path = os.path.join(fonts_dir, name)
        if os.path.exists(path):
            return path
    return None


def _fit_font(draw, lines, max_w, max_h):
    """Chọn cỡ chữ lớn nhất để các dòng vừa khung max_w x max_h (px)."""
    font_path = _font_path()
    size = max(6, int(max_h / (len(lines) * 1.25)))
    while True:
        font = (ImageFont.truetype(font_path, size) if font_path
                else ImageFont.load_default(size))
        widths = [draw.textlength(line, font=font) for line in lines]
        if (max(widths) <= max_w and size * 1.25 * len(lines) <= max_h) or size <= 6:
            return font, size
        size -= 1


def render_appearance(visible, signer_name, when=None):
    """Vẽ hình thức chữ ký thành ảnh: ảnh chữ ký phía trên, thông tin người ký phía dưới."""
    x0, y0, x1, y1 = visible.box
    scale = APPEARANCE_DPI / 72
    W, H = max(1, round((x1 - x0) * scale)), max(1, round((y1 - y0) * scale))
    canvas = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    draw = ImageDraw.Draw(canvas)
    lines = [f"Ký bởi: {signer_name}",
             f"Thời gian: {(when or datetime.now()).strftime(TIMESTAMP_FORMAT)}"]

    if visible.image is not None:
        img_h = round(H * 0.6) if visible.show_text else H
        img = visible.image.convert("RGBA")
        img.thumbnail((W, img_h), Image.LANCZOS)
        if img.width < W and img.height < img_h:  # phóng to ảnh nhỏ cho vừa khung
            ratio = min(W / img.width, img_h / img.height)
            img = img.resize((round(img.width * ratio), round(img.height * ratio)), Image.LANCZOS)
        canvas.alpha_composite(img, ((W - img.width) // 2, (img_h - img.height) // 2))
        text_top = img_h
    else:
        draw.rectangle((0, 0, W - 1, H - 1), outline=TEXT_COLOR, width=max(1, round(scale)))
        text_top = 0

    if visible.show_text or visible.image is None:
        pad = round(3 * scale)
        area_h = H - text_top - pad
        font, size = _fit_font(draw, lines, W - 2 * pad, area_h)
        y = text_top + max(0, (area_h - size * 1.25 * len(lines)) / 2)
        for line in lines:
            draw.text((pad, y), line, font=font, fill=TEXT_COLOR)
            y += size * 1.25
    return canvas


def _stamp_style(visible, signer_name):
    return TextStampStyle(
        stamp_text=" ", border_width=0,
        background=PdfImage(render_appearance(visible, signer_name)),
        background_opacity=1,
        background_layout=SimpleBoxLayoutRule(AxisAlignment.ALIGN_MID, AxisAlignment.ALIGN_MID,
                                              inner_content_scaling=InnerScaling.STRETCH_FILL),
    )


def _signer_name(signer):
    cert = signer.signing_cert
    return cert.subject.native.get("common_name") or cert.subject.human_friendly


def _free_field_name(writer):
    existing = {name for name, _, _ in fields.enumerate_sig_fields(writer)}
    n = 1
    while f"Signature{n}" in existing:
        n += 1
    return f"Signature{n}"


def has_signatures(pdf_bytes):
    """True nếu file PDF đã có chữ ký số."""
    from pyhanko.pdf_utils.reader import PdfFileReader
    try:
        return bool(PdfFileReader(BytesIO(pdf_bytes), strict=False).embedded_signatures)
    except Exception:
        return False


def sign_with_signer(pdf_bytes, out_path, signer, visible=None, reason=None, location=None,
                     tsa_url=None, password=None):
    """Ký pdf_bytes bằng một pyHanko Signer bất kỳ (token hoặc file .p12) và ghi ra out_path."""
    writer = IncrementalPdfFileWriter(BytesIO(pdf_bytes), strict=False)
    if writer.prev.encrypted:
        writer.encrypt(password or "")
    field_name = _free_field_name(writer)
    stamp_style = None
    if visible is not None:
        fields.append_signature_field(
            writer, fields.SigFieldSpec(field_name, on_page=visible.page, box=visible.box))
        stamp_style = _stamp_style(visible, _signer_name(signer))
    meta = signers.PdfSignatureMetadata(
        field_name=field_name, md_algorithm="sha256",
        reason=reason or None, location=location or None,
        subfilter=fields.SigSeedSubFilter.PADES,
    )
    timestamper = timestamps.HTTPTimeStamper(tsa_url) if tsa_url else None
    pdf_signer = signers.PdfSigner(meta, signer=signer, stamp_style=stamp_style,
                                   timestamper=timestamper)
    output = BytesIO()
    pdf_signer.sign_pdf(writer, output=output)
    # chỉ ghi file khi ký thành công, tránh để lại file hỏng
    with open(out_path, "wb") as f:
        f.write(output.getvalue())


def sign_with_token(pdf_bytes, out_path, cert, pin, visible=None, reason=None, location=None,
                    tsa_url=None, password=None):
    """Ký bằng khoá bí mật trên USB token tương ứng với chứng thư cert."""
    lib = _load_lib(cert.lib_path)
    try:
        slot = next((s for s in _tokens(lib) if s.slot_id == cert.slot_id), None)
        if slot is None:
            raise TokenError("Không tìm thấy token chứa chứng thư đã chọn. Token có bị rút ra không?")
        with slot.get_token().open(user_pin=pin) as session:
            key_args = {"key_id": cert.cert_id} if cert.cert_id else {"key_label": cert.label}
            last_error = None
            # Một số token đời cũ không hỗ trợ cơ chế ký kèm băm (SHA256-RSA): thử lại với RSA thuần
            for raw in (False, True):
                signer = PKCS11Signer(session, signing_cert=cert.x509, other_certs_to_pull=None,
                                      use_raw_mechanism=raw, **key_args)
                try:
                    sign_with_signer(pdf_bytes, out_path, signer, visible, reason, location,
                                     tsa_url, password)
                    return
                except (p11_errors.MechanismInvalid, p11_errors.FunctionNotSupported,
                        p11_errors.MechanismParamInvalid) as ex:
                    last_error = ex
            raise last_error
    except Exception as ex:
        if isinstance(ex, (p11_errors.PKCS11Error, TokenError)):
            raise _friendly(ex) from ex
        raise


def describe_validity(cert, now=None):
    now = now or datetime.now(cert.not_after.tzinfo)
    if now > cert.not_after:
        return "Đã hết hạn"
    if now < cert.not_before:
        return "Chưa có hiệu lực"
    return "Còn hiệu lực"
