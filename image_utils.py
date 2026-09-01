# -*- coding: utf-8 -*-
"""图片处理：原图保留（HEIC→JPEG 质量95，不缩分辨率）+ 生成缩略图。"""
import io

from PIL import Image, ImageOps
import pillow_heif

pillow_heif.register_heif_opener()

THUMB_SIZE = (200, 200)


def _normalize(img: Image.Image) -> Image.Image:
    """转正 EXIF 方向并统一为 RGB。"""
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    return img


def process(file_storage, orig_path, thumb_path):
    """把上传的图片（含 HEIC）处理为原图 + 缩略图，写入磁盘。

    原图：分辨率不变、画质不压（JPEG quality=95），后期汇报材料直接可用。
    缩略图：长边 200px，列表展示用。
    无法解析的图片抛 ValueError。
    """
    data = file_storage.read()
    if not data:
        raise ValueError("空文件")
    try:
        img = Image.open(io.BytesIO(data))
    except Exception as exc:
        raise ValueError("图片无法解析") from exc

    img = _normalize(img)
    img.save(orig_path, "JPEG", quality=95)

    thumb = img.copy()
    thumb.thumbnail(THUMB_SIZE)
    thumb.save(thumb_path, "JPEG", quality=80)
