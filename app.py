from flask import Flask, request, send_file
from wwk_fulfillment_job import run
import os
import tempfile

app = Flask(__name__)


@app.get("/")
def health():
    return {"ok": True, "service": "words-we-keep"}


@app.post("/jotform")
def jotform():
    payload = request.get_json(silent=True) or request.form.to_dict()
    out = os.path.join(tempfile.gettempdir(), "keepsake.pdf")
    run(payload, out)
    return send_file(out, mimetype="application/pdf", as_attachment=True, download_name="keepsake.pdf")
