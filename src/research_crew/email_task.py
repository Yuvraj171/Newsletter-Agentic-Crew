from crewai import Task
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from bs4 import BeautifulSoup  # for generating plain-text version from HTML


def send_email(recipients, subject, body_html):
    sender_email = os.getenv("GMAIL_EMAIL")
    app_password = os.getenv("GMAIL_APP_PASSWORD")
    smtp_server = os.getenv("GMAIL_SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("GMAIL_SMTP_PORT", 587))

    if not sender_email or not app_password:
        raise RuntimeError("Gmail credentials missing in .env")

    # Derive plain-text alternative automatically from HTML
    soup = BeautifulSoup(body_html, "html.parser")
    body_plain = soup.get_text(separator="\n")

    # Create MIME email
    msg = MIMEMultipart("alternative")
    msg["From"] = sender_email
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject

    # Attach both plain text and HTML for compatibility
    msg.attach(MIMEText(body_plain, "plain", _charset="utf-8"))
    msg.attach(MIMEText(body_html, "html", _charset="utf-8"))

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, app_password)
            server.send_message(msg)
        print(f"Email sent successfully to: {', '.join(recipients)}")
    except Exception as e:
        print(f"Error sending email: {e}")


def build_email_task(email_agent, recipients, subject, body_html):
    return Task(
        description=f"Send the newsletter '{subject}' via Gmail SMTP.",
        agent=email_agent,
        expected_output="Email sent successfully through Gmail SMTP.",
        verbose=True,
        async_execution=False,
        callback=lambda _: send_email(recipients, subject, body_html),
    )

