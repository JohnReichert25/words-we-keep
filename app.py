from flask import Flask, request
from wwk_fulfillment_job import run
from email.message import EmailMessage
import json
import logging
import os
import re
import smtplib
import tempfile
import threading

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("words-we-keep")


@app.get("/")
def health():
    return {"ok": True, "service": "words-we-keep"}


def _pick(raw: dict, *names):
    # Jotform rawRequest keys look like "q3_writer"; match on the name suffix.
    for key, val in raw.items():
        base = re.sub(r"^q\d+_", "", key).lower()
        if base in names:
            if isinstance(val, dict):
                val = " ".join(str(v) for v in val.values() if v)
            return val
    return None


def parse_jotform(form: dict) -> dict:
    """Turn a Jotform webhook (multipart with rawRequest JSON) into run() input."""
    payload = dict(form)
    try:
        raw = json.loads(form.get("rawRequest") or "{}")
    except ValueError:
        raw = {}
    for field in ("product", "writer", "recipient", "date"):
        if not payload.get(field):
            val = _pick(raw, field)
            if val:
                payload[field] = val
    if not payload.get("answers"):
        answers = [raw[k] for k in sorted(raw, key=lambda k: int(re.match(r"q(\d+)_", k).group(1)))
                   if re.match(r"q\d+_(answer|question|q)\d*", k, re.I) and isinstance(raw[k], str)]
        if answers:
            payload["answers"] = answers
    order = payload.get("etsy_order") or _pick(raw, "etsyorder", "etsy_order", "etsyordernumber", "ordernumber", "order")
    payload["_etsy_order"] = str(order) if order else ""
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
        title = form.get("formTitle") or form.get("formID") or "unknown form"
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
    threading.Thread(target=process, args=(form,), daemon=True).start()
    return {"ok": True}, 200
