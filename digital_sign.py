"""Ký số PDF (chuẩn PAdES) bằng chứng chỉ số dạng file .p12 / .pfx.

Không cần USB token: khoá bí mật nằm trong file PKCS#12 (.p12/.pfx). File chứng chỉ
có thể tự tạo trong mục **Cài đặt** (chứng chỉ tự ký) hoặc nhập từ file đã có của
nhà cung cấp chữ ký số.
"""

import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from PIL import Image, ImageDraw, ImageFont
from pyhanko.pdf_utils.images import PdfImage
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.layout import AxisAlignment, InnerScaling, SimpleBoxLayoutRule
from pyhanko.sign import fields, signers, timestamps
from pyhanko.stamp import TextStampStyle

# Nơi lưu chứng chỉ tự tạo: %USERPROFILE%\.pdf_sign\certs
CERTS_DIR = os.path.join(os.path.expanduser("~"), ".pdf_sign", "certs")

FONT_CANDIDATES = ("arial.ttf", "tahoma.ttf", "segoeui.ttf")
TIMESTAMP_FORMAT = "%d/%m/%Y %H:%M:%S"
APPEARANCE_DPI = 300
TEXT_COLOR = (20, 20, 120, 255)
KEY_SIZE = 2048              # độ dài khoá RSA (bit)
MAX_YEARS = 30               # thời hạn tối đa của chứng chỉ tự tạo


class SignError(Exception):
    """Lỗi có thông báo thân thiện để hiển thị cho người dùng."""


@dataclass
class VisibleSignature:
    page: int                 # chỉ số trang (từ 0)
    box: tuple                # (x0, y0, x1, y1) trong hệ toạ độ PDF (gốc dưới-trái)
    image: object = None      # PIL.Image hoặc None
    show_text: bool = True    # hiện "Ký bởi / Thời gian" dưới hình chữ ký


# ------------------------------------------------------------ chứng chỉ số
def _common_name(name):
    attrs = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    return str(attrs[0].value) if attrs else name.rfc4514_string()


def cert_info(cert, path, cert_path="", has_password=True, origin="Tự tạo"):
    """Đóng gói thông tin chứng chỉ (đối tượng cryptography) thành dict lưu trong cài đặt."""
    return {
        "id": uuid.uuid4().hex,
        "name": _common_name(cert.subject),
        "path": os.path.normpath(path),
        "cert_path": os.path.normpath(cert_path) if cert_path else "",
        "subject": cert.subject.rfc4514_string(),
        "issuer": cert.issuer.rfc4514_string(),
        "not_before": cert.not_valid_before_utc.isoformat(),
        "not_after": cert.not_valid_after_utc.isoformat(),
        "has_password": bool(has_password),
        "origin": origin,
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def create_certificate(common_name, organisation="", unit="", country="", email="",
                       years=1, password="", directory=None):
    """Tự tạo chứng chỉ số tự ký kèm khoá bí mật, lưu thành file .p12 (và .crt).

    Trả về dict thông tin chứng chỉ (dạng dict như cert_info) để lưu vào cài đặt.
    """
    cn = (common_name or "").strip()
    if not cn:
        raise SignError("Hãy nhập họ và tên của người ký.")
    country = (country or "").strip().upper()
    if country and (len(country) != 2 or not country.isalpha()):
        raise SignError("Quốc gia gồm đúng 2 chữ cái, ví dụ: VN.")
    email = (email or "").strip()
    try:
        years = int(years)
    except (TypeError, ValueError):
        years = 1
    if not 1 <= years <= MAX_YEARS:
        raise SignError(f"Thời hạn phải từ 1 đến {MAX_YEARS} năm.")

    attrs = [x509.NameAttribute(NameOID.COMMON_NAME, cn)]
    for oid, value in ((NameOID.ORGANIZATION_NAME, organisation),
                       (NameOID.ORGANIZATIONAL_UNIT_NAME, unit),
                       (NameOID.COUNTRY_NAME, country),
                       (NameOID.EMAIL_ADDRESS, email)):
        value = (value or "").strip()
        if value:
            attrs.append(x509.NameAttribute(oid, value))
    name = x509.Name(attrs)

    key = rsa.generate_private_key(public_exponent=65537, key_size=KEY_SIZE)
    now = datetime.now(timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)                      # tự ký: người phát hành = người sở hữu
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))   # trừ 1 ngày tránh lệch giờ máy
        .not_valid_after(now + timedelta(days=365 * years))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True,
                                    key_encipherment=True, data_encipherment=False,
                                    key_agreement=False, key_cert_sign=False,
                                    crl_sign=False, encipher_only=False,
                                    decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH,
                                              ExtendedKeyUsageOID.EMAIL_PROTECTION]),
                       critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                       critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
                       critical=False)
    )
    if email:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.RFC822Name(email)]), critical=False)
    cert = builder.sign(key, hashes.SHA256())

    directory = directory or CERTS_DIR
    try:
        os.makedirs(directory, exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9]+", "_", cn).strip("_")[:24] or "chung_chi"
        base = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{slug}_{uuid.uuid4().hex[:6]}"
        p12_path = os.path.join(directory, f"{base}.p12")
        crt_path = os.path.join(directory, f"{base}.crt")
        password = (password or "").encode("utf-8")
        encryption = (serialization.BestAvailableEncryption(password) if password
                      else serialization.NoEncryption())
        p12_bytes = pkcs12.serialize_key_and_certificates(
            base.encode("ascii"), key, cert, None, encryption)
        with open(p12_path, "wb") as f:
            f.write(p12_bytes)
        with open(crt_path, "wb") as f:  # bản PEM để xem/cài đặt mà không cần mật khẩu
            f.write(cert.public_bytes(serialization.Encoding.PEM))
    except OSError as ex:
        raise SignError(f"Không ghi được file chứng chỉ:\n{ex}") from ex
    return cert_info(cert, p12_path, crt_path, has_password=bool(password),
                     origin="Tự tạo")


def import_certificate(path, password=""):
    """Đọc chứng chỉ từ file .p12/.pfx đã có (cần đúng mật khẩu nếu file có mật khẩu)."""
    if not os.path.exists(path):
        raise SignError(f"Không tìm thấy file chứng chỉ:\n{path}")
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as ex:
        raise SignError(f"Không đọc được file chứng chỉ:\n{ex}") from ex

    wanted = password.encode("utf-8") if password else None
    # Thử mật khẩu người dùng nhập; nếu file không được mã hoá thì bỏ qua mật khẩu.
    for candidate in ([wanted] if wanted else []) + [None]:
        try:
            key, cert, _chain = pkcs12.load_key_and_certificates(data, candidate)
        except (ValueError, TypeError):
            continue
        if cert is None:
            raise SignError("File này không chứa chứng chỉ số.")
        if key is None:
            raise SignError("File này không chứa khoá bí mật nên không dùng để ký được.")
        return cert_info(cert, path, "", has_password=candidate is not None,
                         origin="Nhập từ file")
    if wanted:
        raise SignError("Sai mật khẩu của file chứng chỉ.")
    raise SignError("Không mở được file chứng chỉ. File có thể bị hỏng hoặc đã mã hoá "
                    "bằng mật khẩu khác.")


def load_signer(path, password=None):
    """Mở khoá bí mật trong file .p12 và trả về Signer dùng để ký."""
    if not os.path.exists(path):
        raise SignError(f"Không tìm thấy file chứng chỉ:\n{path}")
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as ex:
        raise SignError(f"Không đọc được file chứng chỉ:\n{ex}") from ex

    wanted = password.encode("utf-8") if password else None
    # Kiểm tra mật khẩu trước bằng cryptography để không phải chạy vào nhánh lỗi ồn ào
    # của pyhanko (nó ghi traceback vào console khi mật khẩu sai).
    passphrase = None
    found = False
    for candidate in ([wanted] if wanted else []) + [None]:
        try:
            key, _cert, _chain = pkcs12.load_key_and_certificates(data, candidate)
        except (ValueError, TypeError):
            continue
        passphrase, found = candidate, True
        if key is None:
            raise SignError("File này không chứa khoá bí mật nên không dùng để ký được.")
        break
    if not found:
        if wanted:
            raise SignError("Sai mật khẩu chứng chỉ hoặc file .p12 không hợp lệ.")
        raise SignError("Không mở được file chứng chỉ. Nếu file được bảo vệ bằng mật khẩu, "
                        "hãy nhập mật khẩu ở hộp thoại ký.")

    try:
        signer = signers.SimpleSigner.load_pkcs12_data(data, [], passphrase=passphrase)
    except Exception as ex:
        raise SignError(f"Không đọc được khoá bí mật trong file chứng chỉ:\n{ex}") from ex
    if signer is None or signer.signing_key is None:
        raise SignError("Không đọc được khoá bí mật trong file chứng chỉ.")
    return signer


def _parse_when(value):
    try:
        when = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def describe_validity(entry, now=None):
    """Trạng thái hiệu lực của chứng chỉ theo thông tin đã lưu trong entry."""
    not_before = _parse_when(entry.get("not_before", ""))
    not_after = _parse_when(entry.get("not_after", ""))
    if not not_before or not not_after:
        return "Không rõ"
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.astimezone()
    if now > not_after:
        return "Đã hết hạn"
    if now < not_before:
        return "Chưa có hiệu lực"
    return "Còn hiệu lực"


# ---------------------------------------------------------------- hình thức
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


# ---------------------------------------------------------------- ký số
def sign_with_signer(pdf_bytes, out_path, signer, visible=None, reason=None, location=None,
                     tsa_url=None, password=None):
    """Ký pdf_bytes bằng một pyHanko Signer bất kỳ và ghi ra out_path."""
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
