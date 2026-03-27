from __future__ import annotations

from io import BytesIO

import qrcode


def make_qr_bytes(data: str) -> bytes:
    """
    Генерирует QR-код (PNG) в виде байтов.

    Внутри используется пакет `qrcode[pil]`, поэтому PIL задействован автоматически.
    """
    qr = qrcode.QRCode(
        version=None,  # автоподбор размера
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

