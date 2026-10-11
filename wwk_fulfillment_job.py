# Words We Keep fulfillment job
# Input: one Jotform-style submission. Output: six-page keepsake PDF.
# This does not receive webhooks. A public address has to call run().

from __future__ import annotations

from reportlab.pdfgen import canvas
from reportlab.lib.units import inch
from reportlab.lib.colors import Color
from reportlab.lib.utils import ImageReader
import io
import logging
import re
import urllib.parse
import urllib.request

log = logging.getLogger("wwk")
MAX_PHOTO_BYTES = 15 * 1024 * 1024

# Built-in fonts. Render does not have the local Garamond files.
EB, EBI, EBB = "Times-Roman", "Times-Italic", "Times-Bold"

SLATE = Color(0x33 / 255, 0x40 / 255, 0x4F / 255)
GOLD = Color(0xD9 / 255, 0x9A / 255, 0x1B / 255)
CREAM = Color(0xFB / 255, 0xF8 / 255, 0xF1 / 255)
W, H = 6 * inch, 9 * inch

GAVE_SECTIONS = [
    ("What You Gave Me", "the things I understand differently now", [1, 2, 9]),
    ("What I Saw You Do", "the things I learned by watching", [3, 4]),
    ("Moments I Carry With Me", "the ones that still come back", [5, 6, 7]),
    ("Who I Am Because of You", "the parts of you I still carry", [8]),
]
SEE_SECTIONS = [
    ("Who You Already Are", "what is already theirs", [1, 2, 3, 4]),
    ("Moments I Want to Keep", "the ones that still come back", [5, 6, 9]),
    ("What I Hope You Carry", "what I want left with you", [7, 8]),
]

def clean(text: str) -> str:
    return " ".join((text or "").replace("—", ", ").replace("–", ", ").split())

def answers_from(payload: dict) -> list[str]:
    raw = payload.get("answers") or []
    return [clean(a) for a in raw]

def photo_urls(raw) -> list[str]:
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    out = []
    for item in items:
        if not item:
            continue
        for part in re.split(r"[,\n\r]+", str(item)):
            part = part.strip()
            if part:
                out.append(part)
    return out

def _safe(url: str) -> str:
    return url.split("?")[0].split("#")[0]

def _get(url: str, headers: dict | None = None):
    """Return (bytes|None, status, content_type). Never raises; reads at most 15MB+1."""
    import urllib.error
    req = urllib.request.Request(url, headers={"User-Agent": "WordsWeKeep/1.0", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read(MAX_PHOTO_BYTES + 1), r.status, r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return None, e.code, e.headers.get("Content-Type", "") if e.headers else ""
    except Exception as e:
        return None, type(e).__name__, ""

def _is_image(data, ctype) -> bool:
    if not data or len(data) > MAX_PHOTO_BYTES:
        return False
    if (ctype or "").lower().startswith("image/"):
        return True
    try:
        from PIL import Image
        Image.open(io.BytesIO(data))
        return True
    except Exception:
        return False

def _with_key(url: str, key: str) -> str:
    return url + ("&" if "?" in url else "?") + urllib.parse.urlencode({"apiKey": key})

def _is_jotform_upload(url: str) -> bool:
    u = urllib.parse.urlsplit(url)
    return (u.hostname or "").lower().endswith("jotform.com") and "/uploads/" in u.path

def _fetch_with_key(url: str, key: str):
    data, st, ct = _get(_with_key(url, key))
    if _is_image(data, ct):
        return data
    log.warning("Photo apiKey-param download failed for %s (status=%s, content-type=%s)", _safe(url), st, ct)
    data, st, ct = _get(url, {"APIKEY": key})
    if _is_image(data, ct):
        return data
    log.warning("Photo APIKEY-header download failed for %s (status=%s, content-type=%s)", _safe(url), st, ct)
    return None

def _api_upload_urls(submission_id: str, key: str) -> list[str]:
    import json
    api = f"https://api.jotform.com/submission/{urllib.parse.quote(str(submission_id))}"
    data, st, ct = _get(_with_key(api, key))
    if not data:
        log.warning("Jotform API submission lookup failed (status=%s, content-type=%s)", st, ct)
        return []
    try:
        answers = (json.loads(data).get("content") or {}).get("answers") or {}
    except Exception:
        log.warning("Jotform API submission lookup returned non-JSON (status=%s, content-type=%s)", st, ct)
        return []
    pat = re.compile(r"https?://[^\s\"',]+/uploads/[^\s\"',]+\.(?:jpe?g|png)", re.I)
    out = []
    for m in pat.finditer(json.dumps(answers).replace("\\/", "/")):
        if m.group(0) not in out:
            out.append(m.group(0))
    return out

def fetch_photo(src: str, submission_id: str = ""):
    """Return an ImageReader for a JPEG/PNG URL or local path, or None (never raises)."""
    try:
        from PIL import Image
        import os
        key = os.environ.get("JOTFORM_API_KEY") or ""
        if src.startswith(("http://", "https://")):
            data = None
            if key and _is_jotform_upload(src):
                data = _fetch_with_key(src, key)
                if data is None and submission_id:
                    name = _safe(src).rsplit("/", 1)[-1].lower()
                    alts = _api_upload_urls(submission_id, key)
                    alts.sort(key=lambda u: _safe(u).rsplit("/", 1)[-1].lower() != name)
                    for alt in alts:
                        data = _fetch_with_key(alt, key)
                        if data is not None:
                            break
                if data is None:
                    log.warning("Photo skipped: all keyed downloads failed for %s", _safe(src))
                    return None
            else:
                data, st, ct = _get(src)
                if not _is_image(data, ct):
                    log.warning("Photo skipped: download failed for %s (status=%s, content-type=%s)", _safe(src), st, ct)
                    return None
        else:
            path = src[7:] if src.startswith("file://") else src
            with open(path, "rb") as f:
                data = f.read(MAX_PHOTO_BYTES + 1)
        if len(data) > MAX_PHOTO_BYTES:
            log.warning("Photo skipped: larger than 15MB (%s)", _safe(src))
            return None
        img = Image.open(io.BytesIO(data))
        if img.format not in ("JPEG", "PNG"):
            log.warning("Photo skipped: unsupported type %s (%s)", img.format, _safe(src))
            return None
        try:
            from PIL import ImageOps
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.thumbnail((1800, 1800))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=88)
        buf.seek(0)
        return ImageReader(buf)
    except Exception as e:
        log.warning("Photo skipped: could not load %s (%s)", _safe(src), type(e).__name__)
        return None

def run(payload: dict, out_path: str) -> str:
    product = payload.get("product") or "gave"
    writer = clean(payload.get("writer") or "Someone")
    recipient = clean(payload.get("recipient") or "You")
    date = clean(payload.get("date") or "")
    answers = answers_from(payload)
    title = "What You Gave Me" if product == "gave" else "What I See in You"
    sections = GAVE_SECTIONS if product == "gave" else SEE_SECTIONS
    pages = []
    bucket = []
    for name, sub, nums in sections:
        lines = [answers[n - 1] for n in nums if n <= len(answers) and answers[n - 1]]
        if not lines:
            continue
        bucket.append((name, sub, lines))
        if len(bucket) == 2:
            pages.append(bucket)
            bucket = []
    if bucket:
        pages.append(bucket)
    used = [line for page in pages for _, _, lines in page for line in lines]
    closing = ". ".join(used[:3])
    if closing and not closing.endswith("."):
        closing += "."

    sid = str(payload.get("submissionID") or "")
    if "dedication_photo" in payload or "closing_photo" in payload:
        slot_srcs = [photo_urls(payload.get("dedication_photo"))[:1], photo_urls(payload.get("closing_photo"))[:1]]
    else:  # older callers: ordered list
        legacy = photo_urls(payload.get("photos"))[:2]
        slot_srcs = [legacy[:1], legacy[1:2]]
    ded_img = fetch_photo(slot_srcs[0][0], sid) if slot_srcs[0] else None
    clo_img = fetch_photo(slot_srcs[1][0], sid) if slot_srcs[1] else None
    placed = 0

    def draw_photo(img, x0, y0, bw, bh):
        iw, ih = img.getSize()
        s = min(bw / iw, bh / ih)
        dw, dh = iw * s, ih * s
        x, y = x0 + (bw - dw) / 2, y0 + (bh - dh) / 2
        c.drawImage(img, x, y, dw, dh)
        c.setStrokeColor(GOLD)
        c.setLineWidth(0.6)
        c.rect(x - 4, y - 4, dw + 8, dh + 8, fill=0, stroke=1)

    c = canvas.Canvas(out_path, pagesize=(W, H))
    c.setTitle(f"{title} for {recipient}")

    def cream():
        c.setFillColor(CREAM)
        c.rect(0, 0, W, H, fill=1, stroke=0)
        c.setStrokeColor(GOLD)
        c.setLineWidth(0.8)
        m = 0.38 * inch
        c.rect(m, m, W - 2 * m, H - 2 * m, fill=0, stroke=1)
        c.setFillColor(GOLD)
        for x, y in ((m + 8, m + 8), (W - m - 8, m + 8), (m + 8, H - m - 8), (W - m - 8, H - m - 8)):
            c.circle(x, y, 1.4, fill=1, stroke=0)

    def slate():
        c.setFillColor(SLATE)
        c.rect(0, 0, W, H, fill=1, stroke=0)
        c.setStrokeColor(GOLD)
        c.setLineWidth(0.8)
        m = 0.38 * inch
        c.rect(m, m, W - 2 * m, H - 2 * m, fill=0, stroke=1)
        c.setLineWidth(0.35)
        c.rect(m + 7, m + 7, W - 2 * m - 14, H - 2 * m - 14, fill=0, stroke=1)
        c.setFillColor(GOLD)
        for x, y in ((m + 12, m + 12), (W - m - 12, m + 12), (m + 12, H - m - 12), (W - m - 12, H - m - 12)):
            c.circle(x, y, 1.3, fill=1, stroke=0)

    def rule(cx, y):
        c.setStrokeColor(GOLD)
        c.setLineWidth(0.6)
        c.line(cx - 48, y, cx - 8, y)
        c.setFillColor(GOLD)
        c.circle(cx, y, 1.5, fill=1, stroke=0)
        c.line(cx + 8, y, cx + 48, y)

    def wrap(text, font, size, width):
        lines, cur = [], ""
        for word in text.split():
            trial = word if not cur else cur + " " + word
            if c.stringWidth(trial, font, size) <= width:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        return lines

    slate()
    c.setFillColor(GOLD)
    c.setFont(EB, 9)
    c.drawCentredString(W / 2, H - 1.35 * inch, "W O R D S    W E    K E E P")
    rule(W / 2, H - 1.58 * inch)
    c.setFont(EBI, 11)
    c.drawCentredString(W / 2, H - 1.9 * inch, "A keepsake for")
    c.setFont(EBB, 26)
    c.drawCentredString(W / 2, H / 2 + 10, title)
    rule(W / 2, H / 2 - 28)
    c.setFont(EBI, 12)
    c.drawCentredString(W / 2, H / 2 - 54, f"For {recipient}, from {writer}")
    c.showPage()

    cream()
    dy = 0
    if ded_img is not None:
        # Photo in the upper part of the dedication page; dedication text moves down.
        draw_photo(ded_img, 0.9 * inch, H / 2 - 0.2 * inch, W - 1.8 * inch, H / 2 - 0.85 * inch)
        placed += 1
        dy = -1.6 * inch
    c.setFillColor(GOLD)
    c.setFont(EBI, 12)
    c.drawCentredString(W / 2, H / 2 + 0.45 * inch + dy, "For")
    c.setFont(EBB, 28)
    c.drawCentredString(W / 2, H / 2 + dy, recipient)
    c.setFont(EBI, 13)
    c.drawCentredString(W / 2, H / 2 - 0.4 * inch + dy, f"from {writer}")
    c.showPage()

    def block(name, sub, lines, y):
        c.setFillColor(SLATE)
        c.setFont(EBB, 14)
        c.drawCentredString(W / 2, y, name)
        y -= 16
        c.setFont(EBI, 9)
        c.drawCentredString(W / 2, y, sub)
        y -= 14
        rule(W / 2, y)
        y -= 24
        for n, text in enumerate(lines, 1):
            c.setFillColor(GOLD)
            c.setFont(EBB, 10)
            c.drawString(0.72 * inch, y, str(n))
            c.setFillColor(SLATE)
            c.setFont(EB, 12)
            for line in wrap(text, EB, 12, W - 1.85 * inch):
                c.drawString(1.02 * inch, y, line)
                y -= 16
            y -= 12
        return y

    for i, page in enumerate(pages, 3):
        cream()
        y = H - 1.8 * inch
        for j, item in enumerate(page):
            y = block(*item, y)
            if j == 0:
                y -= 16
        c.setFillColor(SLATE)
        c.setFont(EB, 8)
        c.drawCentredString(W / 2, 0.55 * inch, str(i))
        c.showPage()

    cream()
    if clo_img is not None:
        # Closing photo above the closing note heading (space above it is otherwise empty).
        draw_photo(clo_img, 1.1 * inch, H / 2 + 1.55 * inch, W - 2.2 * inch, H / 2 - 2.5 * inch)
        placed += 1
    c.setFillColor(SLATE)
    c.setFont(EBB, 16)
    c.drawCentredString(W / 2, H / 2 + 1.2 * inch, "A Closing Note")
    rule(W / 2, H / 2 + 0.85 * inch)
    c.setFont(EB, 12)
    y = H / 2 + 0.5 * inch
    note = f"{recipient}, {closing}" if closing else f"{recipient}."
    for line in wrap(note, EB, 12, W - 1.6 * inch):
        c.drawString(0.8 * inch, y, line)
        y -= 16
    c.setFont(EBI, 12)
    c.drawString(0.8 * inch, y - 16, "With love,")
    c.drawString(0.8 * inch, y - 32, writer)
    c.showPage()

    slate()
    c.setFillColor(GOLD)
    c.setFont(EB, 11)
    c.drawCentredString(W / 2, H / 2 + 36, "WORDS WE KEEP")
    c.setFont(EBI, 10)
    c.drawCentredString(W / 2, H / 2 + 8, "Real words, written just for them.")
    c.setFont(EBI, 9)
    c.drawCentredString(W / 2, H / 2 - 28, f"Created from {writer}'s memories for {recipient}")
    c.drawCentredString(W / 2, H / 2 - 46, "Just because.")
    if date:
        c.drawCentredString(W / 2, H / 2 - 78, date)
    c.showPage()
    c.save()
    log.info("Placed %d photo(s) in PDF", placed)
    return out_path
