import os, smtplib
from email.message import EmailMessage
SMTP_HOST = os.getenv('SMTP_HOST')
SMTP_PORT = int(os.getenv('SMTP_PORT','587'))
SMTP_USER = os.getenv('SMTP_USER')
SMTP_PASS = os.getenv('SMTP_PASS')
FROM = os.getenv('SMTP_FROM','no-reply@zhaz.com')

def send_email(to, subject, body):
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        print(f'[DEV EMAIL]\nTo:{to}\nSubj:{subject}\n{body}\n')
        return
    msg = EmailMessage()
    msg['From']=FROM
    msg['To']=to
    msg['Subject']=subject
    msg.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)
