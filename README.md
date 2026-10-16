# Media2Text - Full Email OTP Version

## Run

1. Install requirements:
   python -m pip install -r requirements.txt
2. Confirm PostgreSQL database settings in `config.py` or set `DATABASE_URL`.
3. `.env` contains the Gmail sender and App Password.
4. Run:
   python app.py
5. Open:
   http://127.0.0.1:5000/register

## Gmail OTP

The app tries Gmail implicit TLS on port 465 first and STARTTLS on port 587 second.
The password is treated as a Google App Password; spaces are removed automatically.
Do not use the normal Gmail password.

If the App Password has been exposed, revoke it in Google Account > Security > App passwords and create a new one.
