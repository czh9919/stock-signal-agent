import logging
import os
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logger = logging.getLogger(__name__)

BACKUP_DIR = Path("data/email_backup")


class Mailer:
    def __init__(self, config: dict):
        self.config = config

    def send(self, html_body: str) -> bool:
        """Send daily report. Writes local backup on failure. Returns True on success."""
        subject = f"{self.config.get('subject_prefix', '[Stock Agent]')} Daily Report {date.today()}"
        recipient = self.config.get("recipient") or os.environ.get("RECIPIENT_EMAIL")

        if not recipient:
            logger.error("No recipient email configured")
            return False

        use_smtp = self.config.get("use_smtp", False)
        try:
            if use_smtp:
                success = self._send_smtp(recipient, subject, html_body)
            else:
                success = self._send_sendgrid(recipient, subject, html_body)

            if success:
                logger.info(f"Email sent to {recipient}")
            return success
        except Exception as e:
            logger.error(f"Email send failed: {e}")
            self._backup(html_body)
            return False

    def _send_sendgrid(self, recipient: str, subject: str, html: str) -> bool:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail

        api_key = os.environ.get("SENDGRID_API_KEY") or self.config.get("sendgrid_api_key")
        if not api_key:
            logger.error("SENDGRID_API_KEY not set")
            self._backup(html)
            return False

        sender = self.config.get("sender", "stock-agent@example.com")
        message = Mail(
            from_email=sender,
            to_emails=recipient,
            subject=subject,
            html_content=html,
        )
        sg = SendGridAPIClient(api_key)
        response = sg.send(message)
        return response.status_code in (200, 202)

    def _send_smtp(self, recipient: str, subject: str, html: str) -> bool:
        host     = os.environ.get("SMTP_HOST") or self.config.get("smtp_host", "smtp.gmail.com")
        port     = int(os.environ.get("SMTP_PORT") or self.config.get("smtp_port", 587))
        user     = os.environ.get("SMTP_USER") or self.config.get("smtp_user", "")
        password = os.environ.get("SMTP_PASS") or os.environ.get("SMTP_PASSWORD") or self.config.get("smtp_password", "")
        sender   = self.config.get("sender", user)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = sender
        msg["To"]      = recipient
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP(host, port) as server:
            server.ehlo()
            server.starttls()
            if user and password:
                server.login(user, password)
            server.sendmail(sender, recipient, msg.as_string())
        return True

    def _backup(self, html: str):
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        path = BACKUP_DIR / f"report_{date.today()}.html"
        path.write_text(html, encoding="utf-8")
        logger.info(f"Email backup written to {path}")
