"""Bilder: Uploads verarbeiten, Galerie-Ordner lesen, Vorschaubilder erzeugen."""
import hashlib
import os
import re
import secrets

from flask import current_app
from markupsafe import Markup
from PIL import Image, ImageOps

try:  # iPhone-Fotos (HEIC)
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIC = True
except Exception:  # pragma: no cover
    HEIC = False

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif"}
VIDEO_EXT = {".mp4", ".webm", ".mov", ".m4v"}


def uploads_dir():
    return os.path.join(current_app.config["DATA_DIR"], "uploads")


def thumbs_dir():
    return os.path.join(current_app.config["DATA_DIR"], "thumbs")


def photos_dir():
    return current_app.config["PHOTOS_DIR"]


def save_image_upload(file_storage, max_px=2000):
    """Speichert ein hochgeladenes Bild neu als JPEG (dreht korrekt, entfernt Metadaten wie GPS)."""
    with Image.open(file_storage.stream) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "L"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg.paste(im, mask=im.split()[-1])
            else:
                bg.paste(im.convert("RGB"))
            im = bg
        im.thumbnail((max_px, max_px), Image.LANCZOS)
        name = secrets.token_hex(12) + ".jpg"
        im.save(os.path.join(uploads_dir(), name), "JPEG", quality=86, optimize=True)
    return name


def save_avatar(file_storage, size=400):
    """Profilbild: quadratisch zuschneiden, 400 px, ohne Metadaten."""
    with Image.open(file_storage.stream) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im = ImageOps.fit(im, (size, size), Image.LANCZOS)
        name = "avatar-" + secrets.token_hex(10) + ".jpg"
        im.save(os.path.join(uploads_dir(), name), "JPEG", quality=88, optimize=True)
    return name


def save_pdf_upload(file_storage):
    head = file_storage.stream.read(5)
    file_storage.stream.seek(0)
    if head != b"%PDF-":
        raise ValueError("Keine gültige PDF-Datei")
    name = secrets.token_hex(12) + ".pdf"
    file_storage.save(os.path.join(uploads_dir(), name))
    return name


def delete_upload(name):
    if not name or "/" in name or "\\" in name:
        return
    p = os.path.join(uploads_dir(), name)
    if os.path.isfile(p):
        os.remove(p)


# --- Galerie aus Ordnern -------------------------------------------------------

def safe_photo_path(rel):
    """Löst einen relativen Pfad innerhalb des Galerie-Ordners auf – ohne Ausbruch."""
    base = os.path.realpath(photos_dir())
    target = os.path.realpath(os.path.join(base, rel or ""))
    if target != base and not target.startswith(base + os.sep):
        return None
    return target


def list_folder(rel):
    path = safe_photo_path(rel)
    if not path or not os.path.isdir(path):
        return None
    folders, images, videos = [], [], []
    for entry in sorted(os.scandir(path), key=lambda e: e.name.lower()):
        if entry.name.startswith(".") or entry.name.startswith("@"):
            continue
        r = os.path.relpath(entry.path, photos_dir())
        if entry.is_dir():
            cover, count = folder_cover(entry.path)
            folders.append({"name": entry.name, "rel": r, "cover": cover, "count": count})
        else:
            ext = os.path.splitext(entry.name)[1].lower()
            if ext in IMAGE_EXT:
                images.append({"name": entry.name, "rel": r})
            elif ext in VIDEO_EXT:
                videos.append({"name": entry.name, "rel": r})
    return {"folders": folders, "images": images, "videos": videos}


def folder_cover(path):
    cover, count = None, 0
    for root, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "@")))
        for f in sorted(files):
            if os.path.splitext(f)[1].lower() in IMAGE_EXT:
                count += 1
                if cover is None:
                    cover = os.path.relpath(os.path.join(root, f), photos_dir())
        if count > 500:
            break
    return cover, count


def cached_resize(abs_path, size):
    """Erzeugt (einmalig) eine verkleinerte JPEG-Version und gibt den Pfad zurück."""
    st = os.stat(abs_path)
    key = hashlib.sha1(f"{abs_path}|{st.st_mtime_ns}|{size}".encode()).hexdigest()
    out = os.path.join(thumbs_dir(), key[:2], key + ".jpg")
    if not os.path.exists(out):
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with Image.open(abs_path) as im:
            im = ImageOps.exif_transpose(im)
            im = im.convert("RGB")
            im.thumbnail((size, size), Image.LANCZOS)
            tmp = out + ".tmp"
            im.save(tmp, "JPEG", quality=82, optimize=True)
            os.replace(tmp, out)
    return out


# --- Sicheres Markdown für Protokolle -----------------------------------------

_LINK_RE = re.compile(r'href="([^"]*)"')


def render_markdown(text):
    import markdown
    if not text:
        return Markup("")
    # Rohes HTML neutralisieren, damit nur Markdown-Formatierung wirkt
    safe_src = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html = markdown.markdown(safe_src, extensions=["extra", "sane_lists", "nl2br"])

    def fix(m):
        url = m.group(1)
        if url.startswith(("http://", "https://", "mailto:", "/", "#")):
            return f'href="{url}" rel="noopener" target="_blank"'
        return 'href="#"'

    return Markup(_LINK_RE.sub(fix, html))
