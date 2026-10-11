from flask import Flask, request, send_file
from wwk_fulfillment_job import run
from email.message import EmailMessage
import json
import logging
import os
import re
import smtplib
import tempfile
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("words-we-keep")


@app.get("/")
def health():
    return {"ok": True, "service": "words-we-keep"}


FORMS = {
    # formID: (product for run(), title, visible memory question ids in display order)
    "262695808087068": ("gave", "Words We Keep — What You Gave Me", [35, 38, 39, 40, 43, 44, 46, 33, 49]),
    "262696525171160": ("see", "Words We Keep — What I See in You", [35, 36, 37, 38, 43, 45, 47, 34, 49]),
}
# Fixed field ids shared by both forms.
F_ORDER, F_WRITER, F_RECIPIENT, F_CALL, F_RELATION, F_OCCASION, F_NOTES, F_PHOTOS = 3, 4, 5, 6, 7, 8, 30, 31


def _text(val) -> str:
    if isinstance(val, dict):
        return " ".join(str(v) for v in val.values() if v)
    if isinstance(val, list):
        return " ".join(str(v) for v in val if v)
    return str(val or "").strip()


def _by_qid(raw: dict) -> dict:
    # rawRequest keys look like "q35_typeA35"; key on the numeric qN prefix.
    out = {}
    for key, val in raw.items():
        m = re.match(r"q(\d+)_", key)
        if m:
            out[int(m.group(1))] = val
    return out


UPLOAD_RE = re.compile(r"https?://[^\s\"',]*jotform[^\s\"',]*/uploads/[^\s\"',]+\.(?:jpe?g|png)", re.I)


def _strings(val):
    """Yield every string inside val (str/list/dict, nested; JSON strings are expanded)."""
    if isinstance(val, dict):
        for v in val.values():
            yield from _strings(v)
    elif isinstance(val, (list, tuple)):
        for v in val:
            yield from _strings(v)
    elif isinstance(val, str):
        t = val.strip()
        if t[:1] in "[{":
            try:
                yield from _strings(json.loads(t))
                return
            except ValueError:
                pass
        yield val


def _urls_in(val) -> list:
    out = []
    for s in _strings(val):
        for part in re.split(r"[,\n\r]+", s):
            part = part.strip()
            if part.startswith("/uploads/"):
                part = "https://www.jotform.com" + part
            if re.match(r"https?://", part, re.I):
                out.append(part)
    return out


def _find_photos(form: dict, raw: dict) -> list:
    found = []
    for key, val in raw.items():  # (a) q31 by prefix
        if re.match(rf"q{F_PHOTOS}_", key):
            found += _urls_in(val)
    for key, val in raw.items():  # (b) keys mentioning photo/upload
        if re.search(r"photo|upload", key, re.I):
            found += _urls_in(val)
    if not found:  # (c) any Jotform upload URL anywhere
        for src in (raw, {k: v for k, v in form.items() if k != "rawRequest"}):
            for s in _strings(src):
                found += [m.group(0) for m in UPLOAD_RE.finditer(s)]
    out = []
    for u in found:
        if u not in out:
            out.append(u)
    return out[:2]


def parse_jotform(form: dict) -> dict:
    """Turn a Jotform webhook (multipart with rawRequest JSON) into run() input."""
    payload = dict(form)
    try:
        raw = json.loads(form.get("rawRequest") or "{}")
    except ValueError:
        raw = {}
    q = _by_qid(raw)
    form_id = str(form.get("formID") or raw.get("formID") or "")
    product, title, qids = FORMS.get(form_id, (None, None, None))
    if product:
        payload["product"] = product
        payload["_title"] = title
    elif q:  # fallback for unknown forms: all q33+ fields except notes/photos, in id order
        qids = [n for n in sorted(q) if n >= 33]
    for field, qid in (("writer", F_WRITER), ("recipient", F_RECIPIENT), ("call_name", F_CALL),
                       ("relationship", F_RELATION), ("occasion", F_OCCASION), ("notes", F_NOTES)):
        if not payload.get(field) and _text(q.get(qid)):
            payload[field] = _text(q.get(qid))
    if not payload.get("answers") and qids:
        payload["answers"] = [_text(q.get(n)) for n in qids]
    photos = _find_photos(form, raw if isinstance(raw, dict) else {})
    payload["photos"] = photos
    log.info("Jotform keys: form=%s rawRequest=%s; photo URLs found=%d %s",
             sorted(form.keys()), sorted(raw.keys()) if isinstance(raw, dict) else [],
             len(photos), [u.split("?")[0].split("#")[0] for u in photos])
    if not payload.get("date"):
        # Date printed on the PDF is the order/submission date.
        payload["date"] = datetime.now(ZoneInfo("America/New_York")).strftime("%B %-d, %Y")
    payload["_etsy_order"] = _text(q.get(F_ORDER)) or _text(payload.get("etsy_order"))
    return payload


def send_pdf(pdf_path: str, subject: str) -> None:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "john@johnreichert.com")
    password = (os.environ.get("SMTP_PASSWORD") or "").replace(" ", "")
    mail_from = os.environ.get("MAIL_FROM", "support@wordswekeep.co")
    mail_to = os.environ.get("MAIL_TO", "support@wordswekeep.co")
    if not password:
        raise RuntimeError("SMTP_PASSWORD is not set")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = mail_to
    msg.set_content("A new Words We Keep submission arrived. The keepsake PDF is attached.")
    with open(pdf_path, "rb") as f:
        msg.add_attachment(f.read(), maintype="application", subtype="pdf", filename="keepsake.pdf")
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)


def process(form: dict) -> None:
    try:
        payload = parse_jotform(form)
        fd, out = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        run(payload, out)
        log.info("Built keepsake PDF (formID=%s, %d bytes)", form.get("formID"), os.path.getsize(out))
        title = payload.get("_title") or form.get("formTitle") or form.get("formID") or "unknown form"
        subject = f"Words We Keep submission: {title}"
        if payload["_etsy_order"]:
            subject += f" — {payload['_etsy_order']}"
        send_pdf(out, subject)
        log.info("Emailed keepsake PDF: %s", subject)
        os.remove(out)
    except Exception as e:  # never log secrets; exception text from smtplib does not include the password
        log.exception("Jotform processing failed (formID=%s): %s", form.get("formID"), type(e).__name__)


@app.post("/jotform")
def jotform():
    form = request.get_json(silent=True) or request.form.to_dict()
    # Manual/test POSTs can ask for the PDF back directly: /jotform?pdf=1
    if request.args.get("pdf") == "1":
        payload = parse_jotform(form)
        fd, out = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        run(payload, out)
        return send_file(out, mimetype="application/pdf", as_attachment=True, download_name="keepsake.pdf")
    threading.Thread(target=process, args=(form,), daemon=True).start()
    return {"ok": True}, 200
