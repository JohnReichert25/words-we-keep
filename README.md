# Words We Keep

Flask service on Render. `POST /jotform` receives Jotform webhooks, builds the keepsake PDF
(`wwk_fulfillment_job.run`) in a background thread, and emails it as an attachment via SMTP.

Start command: `gunicorn app:app` (Procfile). Build: `pip install -r requirements.txt`.

## Environment variables

| Name | Default |
|---|---|
| SMTP_HOST | smtp.gmail.com |
| SMTP_PORT | 587 (STARTTLS) |
| SMTP_USER | john@johnreichert.com |
| SMTP_PASSWORD | (secret, set in Render only; spaces are stripped) |
| MAIL_FROM | support@wordswekeep.co |
| MAIL_TO | support@wordswekeep.co |
