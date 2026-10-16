import os
import uuid
import subprocess
import tempfile
import secrets
import smtplib
import ssl
from email.message import EmailMessage
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from functools import wraps

import speech_recognition as sr
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, send_from_directory, abort
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text, inspect
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

from config import Config


def load_local_env():
    """Load simple KEY=VALUE pairs from a local .env file if present.

    Existing OS environment variables always take priority.
    """
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8-sig") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('\"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


load_local_env()

def utc_now_naive():
    # Keep database timestamps as naive UTC for compatibility with the existing schema,
    # while avoiding the deprecated utc_now_naive() API on Python 3.14+.
    return datetime.now(timezone.utc).replace(tzinfo=None)


app = Flask(__name__)
app.config.from_object(Config)

db = SQLAlchemy(app)

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
os.makedirs(app.config["PDF_FOLDER"], exist_ok=True)


# ---------------------------------------------------------------------------
# Language configuration
# ---------------------------------------------------------------------------

# SpeechRecognition/Google speech language codes.
SPEECH_LANGUAGES = {
    "mr-IN": "Marathi",
    "hi-IN": "Hindi",
    "en-IN": "English (India)",
    "en-US": "English (US)",
    "en-GB": "English (UK)",
    "gu-IN": "Gujarati",
    "bn-IN": "Bengali",
    "ta-IN": "Tamil",
    "te-IN": "Telugu",
    "kn-IN": "Kannada",
    "ml-IN": "Malayalam",
    "pa-IN": "Punjabi",
    "ur-PK": "Urdu",
    "ne-NP": "Nepali",
    "es-ES": "Spanish",
    "fr-FR": "French",
    "de-DE": "German",
    "it-IT": "Italian",
    "pt-PT": "Portuguese",
    "ru-RU": "Russian",
    "ja-JP": "Japanese",
    "ko-KR": "Korean",
    "zh-CN": "Chinese (Simplified)",
    "ar-SA": "Arabic",
    "tr-TR": "Turkish",
    "id-ID": "Indonesian",
    "th-TH": "Thai",
    "vi-VN": "Vietnamese",
    "nl-NL": "Dutch",
    "pl-PL": "Polish",
    "uk-UA": "Ukrainian",
}

DEFAULT_SPEECH_LANGUAGE = app.config.get("DEFAULT_SPEECH_LANGUAGE", "mr-IN")

# ---------------------------------------------------------------------------
# Single Admin account
# ---------------------------------------------------------------------------
# Change these two values if you want a different Admin login.
# Only this account can have role="admin". All registrations are normal users.
ADMIN_EMAIL = os.environ.get("MEDIA2TEXT_ADMIN_EMAIL", "admin@media2text.com")
ADMIN_PASSWORD = os.environ.get("MEDIA2TEXT_ADMIN_PASSWORD", "Admin@12345")

# ---------------------------------------------------------------------------
# Email / OTP verification
# ---------------------------------------------------------------------------
# Gmail address used to SEND verification OTP emails.
# For Gmail, use a Google App Password (not the normal Gmail password).
SMTP_EMAIL = os.environ.get("MEDIA2TEXT_SMTP_EMAIL", "janardhnaahire69@gmail.com").strip()
SMTP_PASSWORD = os.environ.get("MEDIA2TEXT_SMTP_PASSWORD", "").replace(" ", "").strip()
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_SSL_PORT = 465
OTP_EXPIRY_SECONDS = 300  # 5 minutes

if not SMTP_PASSWORD:
    print("WARNING: MEDIA2TEXT_SMTP_PASSWORD is empty. Put your Google App Password in the .env file (same folder as app.py).")


def _send_via_starttls(msg):
    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30, local_hostname="localhost") as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.send_message(msg)


def _send_via_ssl(msg):
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_SSL_PORT, timeout=30, context=context, local_hostname="localhost") as server:
        server.ehlo()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.send_message(msg)


def send_otp_email(recipient_email, otp, purpose="verification"):
    """Send a 6-digit OTP through Gmail SMTP.

    purpose can be ``verification`` for registration or ``password_reset``
    for the forgot-password flow.
    """
    if not SMTP_PASSWORD:
        raise RuntimeError(
            "SMTP password is not configured. Set MEDIA2TEXT_SMTP_PASSWORD "
            "to the Google App Password for the sender account."
        )

    msg = EmailMessage()
    if purpose == "password_reset":
        msg["Subject"] = "Media2Text - Password Reset OTP"
        message = (
            f"Hello,\n\n"
            f"Your Media2Text password reset OTP is: {otp}\n\n"
            f"This OTP is valid for 5 minutes. Do not share it with anyone.\n\n"
            f"If you did not request a password reset, you can ignore this email.\n\n"
            f"Media2Text"
        )
    else:
        msg["Subject"] = "Media2Text - Email Verification OTP"
        message = (
            f"Hello,\n\n"
            f"Your Media2Text email verification OTP is: {otp}\n\n"
            f"This OTP is valid for 5 minutes. Do not share it with anyone.\n\n"
            f"If you did not request this, you can ignore this email.\n\n"
            f"Media2Text"
        )
    msg["From"] = SMTP_EMAIL
    msg["To"] = recipient_email
    msg.set_content(message)

    errors = []
    for method_name, method in (("SSL 465", _send_via_ssl), ("STARTTLS 587", _send_via_starttls)):
        try:
            method(msg)
            app.logger.info("Registration OTP sent successfully using Gmail %s.", method_name)
            return
        except Exception as exc:
            errors.append(f"{method_name}: {type(exc).__name__}: {exc}")
            app.logger.warning("Gmail SMTP %s failed: %s", method_name, exc)

    raise RuntimeError(
        "Gmail OTP sending failed on both SMTP methods. "
        + " | ".join(errors)
        + " Check that the Google App Password is valid, 2-Step Verification is enabled, "
          "and the sender address matches the Google account that created the App Password."
    )



# ---------------------------------------------------------------------------
# Database models
# ---------------------------------------------------------------------------

class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="user", nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now_naive)
    last_login = db.Column(db.DateTime, nullable=True)
    last_seen = db.Column(db.DateTime, nullable=True)
    total_usage_seconds = db.Column(db.Integer, default=0, nullable=False)
    login_count = db.Column(db.Integer, default=0, nullable=False)

    transcriptions = db.relationship(
        "Transcription",
        backref="user",
        lazy=True,
        cascade="all, delete-orphan"
    )


class Transcription(db.Model):
    __tablename__ = "transcriptions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # Kept for compatibility with the user's existing database.
    audio_filename = db.Column(db.String(255), nullable=False)

    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False)
    media_type = db.Column(db.String(20), nullable=False)
    language = db.Column(db.String(30), nullable=False)

    transcript = db.Column(db.Text, default="")
    target_language = db.Column(db.String(20), nullable=True)
    translated_text = db.Column(db.Text, nullable=True)

    pdf_filename = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now_naive)
    updated_at = db.Column(
        db.DateTime,
        default=utc_now_naive,
        onupdate=utc_now_naive
    )


# ---------------------------------------------------------------------------
# Safe database migration for the existing PostgreSQL database
# ---------------------------------------------------------------------------

def migrate_existing_database():
    """Add new columns without deleting or recreating existing data."""
    try:
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()

        # Safe migration for user activity fields. These additions preserve
        # all existing accounts and records.
        if "users" in tables:
            user_existing = {
                col["name"]
                for col in inspector.get_columns("users")
            }
            user_statements = []

            if "last_login" not in user_existing:
                user_statements.append(
                    "ALTER TABLE users ADD COLUMN last_login TIMESTAMP"
                )
            if "last_seen" not in user_existing:
                user_statements.append(
                    "ALTER TABLE users ADD COLUMN last_seen TIMESTAMP"
                )
            if "total_usage_seconds" not in user_existing:
                user_statements.append(
                    "ALTER TABLE users ADD COLUMN total_usage_seconds INTEGER DEFAULT 0"
                )
            if "login_count" not in user_existing:
                user_statements.append(
                    "ALTER TABLE users ADD COLUMN login_count INTEGER DEFAULT 0"
                )

            if user_statements:
                with db.engine.begin() as conn:
                    for statement in user_statements:
                        conn.execute(text(statement))

            with db.engine.begin() as conn:
                conn.execute(text(
                    "UPDATE users SET total_usage_seconds = 0 "
                    "WHERE total_usage_seconds IS NULL"
                ))
                conn.execute(text(
                    "UPDATE users SET login_count = 0 "
                    "WHERE login_count IS NULL"
                ))

        if "transcriptions" not in tables:
            return

        existing = {
            col["name"]
            for col in inspector.get_columns("transcriptions")
        }

        statements = []

        if "audio_filename" not in existing:
            statements.append(
                "ALTER TABLE transcriptions "
                "ADD COLUMN audio_filename VARCHAR(255)"
            )

        if "target_language" not in existing:
            statements.append(
                "ALTER TABLE transcriptions "
                "ADD COLUMN target_language VARCHAR(20)"
            )

        if "translated_text" not in existing:
            statements.append(
                "ALTER TABLE transcriptions "
                "ADD COLUMN translated_text TEXT"
            )

        if "updated_at" not in existing:
            statements.append(
                "ALTER TABLE transcriptions "
                "ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
            )

        if statements:
            with db.engine.begin() as conn:
                for statement in statements:
                    conn.execute(text(statement))

        # Backfill the compatibility column from existing filename data.
        with db.engine.begin() as conn:
            conn.execute(text(
                "UPDATE transcriptions "
                "SET audio_filename = original_filename "
                "WHERE audio_filename IS NULL "
                "AND original_filename IS NOT NULL"
            ))

            conn.execute(text(
                "UPDATE transcriptions "
                "SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)"
            ))

            # Only set NOT NULL when there are no remaining NULL rows.
            result = conn.execute(text(
                "SELECT COUNT(*) FROM transcriptions "
                "WHERE audio_filename IS NULL"
            )).scalar()

            if result == 0:
                conn.execute(text(
                    "ALTER TABLE transcriptions "
                    "ALTER COLUMN audio_filename SET NOT NULL"
                ))

    except Exception:
        app.logger.exception(
            "Database migration check failed. "
            "Existing data was not intentionally deleted."
        )


# ---------------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------------

def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in app.config["ALLOWED_EXTENSIONS"]
    )


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None

    try:
        return db.session.get(User, user_id)
    except Exception:
        db.session.rollback()
        return None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Please login first.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()

        if not user or user.role != "admin":
            flash("Admin access required.", "danger")
            return redirect(url_for("dashboard"))

        return view(*args, **kwargs)

    return wrapped


IST = ZoneInfo("Asia/Kolkata")


def format_ist(value):
    if not value:
        return "Never"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(IST).strftime("%d-%m-%Y %H:%M")


def format_duration(seconds):
    total = max(0, int(seconds or 0))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def record_user_activity(user):
    """Add only the new active interval since the previous request/heartbeat."""
    if not user:
        return

    now_ts = datetime.now(timezone.utc).timestamp()
    previous_ts = session.get("last_activity_ts")

    if previous_ts:
        delta = int(max(0, min(now_ts - float(previous_ts), 300)))
        if delta:
            user.total_usage_seconds = (user.total_usage_seconds or 0) + delta

    user.last_seen = utc_now_naive()
    session["last_activity_ts"] = now_ts


def media_type_from_filename(filename):
    ext = filename.rsplit(".", 1)[1].lower()

    video_extensions = {
        "mp4", "mkv", "avi", "mov", "webm", "mpeg", "mpg",
        "m4v", "3gp", "3g2", "ts", "mts", "m2ts", "flv",
        "wmv", "asf", "vob", "ogv", "rm", "rmvb", "f4v"
    }

    return "video" if ext in video_extensions else "audio"


def convert_to_wav(input_path):
    """Convert almost any FFmpeg-readable media to mono 16 kHz WAV."""
    output_name = f"{uuid.uuid4().hex}.wav"
    output_path = os.path.join(app.config["UPLOAD_FOLDER"], output_name)

    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-i", input_path,
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-sample_fmt", "s16",
        output_path
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        error_text = result.stderr.strip() or "Unknown FFmpeg error."
        raise RuntimeError(
            "FFmpeg could not process this media file.\n"
            f"{error_text}"
        )

    return output_path


def speech_to_text(wav_path, language):
    """
    Recognize speech in chunks so longer audio/video files are more reliable.
    Google Speech Recognition has practical request-size/time limits, so
    45-second WAV chunks are sent one by one.
    """
    recognizer = sr.Recognizer()

    with tempfile.TemporaryDirectory(
        dir=app.config["UPLOAD_FOLDER"]
    ) as temp_dir:

        chunk_pattern = os.path.join(temp_dir, "chunk_%04d.wav")

        command = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-i", wav_path,
            "-f", "segment",
            "-segment_time", "45",
            "-ac", "1",
            "-ar", "16000",
            "-reset_timestamps", "1",
            chunk_pattern
        ]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if result.returncode != 0:
            raise RuntimeError(
                "Could not split audio for speech recognition.\n"
                + (result.stderr.strip() or "")
            )

        chunks = sorted(
            os.path.join(temp_dir, name)
            for name in os.listdir(temp_dir)
            if name.lower().endswith(".wav")
        )

        if not chunks:
            chunks = [wav_path]

        parts = []

        for chunk in chunks:
            with sr.AudioFile(chunk) as source:
                audio = recognizer.record(source)

            try:
                part = recognizer.recognize_google(
                    audio,
                    language=language
                )
                if part:
                    parts.append(part.strip())

            except sr.UnknownValueError:
                # Skip a silent/noisy chunk and continue with the rest.
                continue

        if not parts:
            raise sr.UnknownValueError()

        return " ".join(parts)


# ---------------------------------------------------------------------------
# PDF generation with Marathi/Hindi/Devanagari support
# ---------------------------------------------------------------------------

def find_pdf_font():
    """Prefer bundled Noto Sans Devanagari for Marathi/Hindi PDF text."""
    candidates = [
        os.path.join(
            app.root_path,
            "fonts",
            "NotoSansDevanagari-Regular.ttf"
        ),
        r"C:\Windows\Fonts\Nirmala.ttf",
        r"C:\Windows\Fonts\NirmalaUI.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    ]

    for path in candidates:
        if os.path.exists(path):
            return path

    return None


PDF_FONT_NAME = "Media2TextUnicode"
PDF_FONT_PATH = find_pdf_font()
IST = ZoneInfo("Asia/Kolkata")

if PDF_FONT_PATH:
    try:
        pdfmetrics.registerFont(
            TTFont(PDF_FONT_NAME, PDF_FONT_PATH)
        )
    except Exception:
        app.logger.exception("Could not register Unicode PDF font.")
        PDF_FONT_PATH = None


def create_pdf(record):
    filename = (
        f"transcription_{record.id}_{uuid.uuid4().hex[:8]}.pdf"
    )
    path = os.path.join(app.config["PDF_FOLDER"], filename)

    doc = SimpleDocTemplate(
        path,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title="Media2Text Transcription"
    )

    styles = getSampleStyleSheet()

    # IMPORTANT: Keep the PDF title/meta in a standard Latin font.
    # The bundled Devanagari font is used only for transcript text;
    # using it for English metadata can hide Latin characters in some
    # PDF viewers.
    title_font = "Helvetica-Bold"
    meta_font = "Helvetica"
    body_font = PDF_FONT_NAME if PDF_FONT_PATH else "Helvetica"

    title_style = ParagraphStyle(
        "M2TTitle",
        parent=styles["Title"],
        fontName=title_font,
        fontSize=18,
        leading=24,
        textColor=colors.HexColor("#4f46e5"),
        spaceAfter=10 * mm,
        alignment=TA_LEFT
    )

    meta_style = ParagraphStyle(
        "M2TMeta",
        parent=styles["Normal"],
        fontName=meta_font,
        fontSize=9.5,
        leading=14,
        textColor=colors.HexColor("#333333"),
        spaceAfter=2.5 * mm
    )

    heading_style = ParagraphStyle(
        "M2THeading",
        parent=styles["Heading2"],
        fontName=title_font,
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#111827"),
        spaceBefore=5 * mm,
        spaceAfter=3 * mm
    )

    body_style = ParagraphStyle(
        "M2TBody",
        parent=styles["BodyText"],
        fontName=body_font,
        fontSize=10.5,
        leading=16,
        textColor=colors.HexColor("#111827"),
        spaceAfter=3 * mm,
        wordWrap="LTR"
    )

    # Escape text for ReportLab Paragraph XML.
    from xml.sax.saxutils import escape

    # PDF header: project name, file name, current IST date/time,
    # and human-readable language type.
    generated_at_ist = datetime.now(IST)
    language_name = SPEECH_LANGUAGES.get(
        record.language, record.language or "Unknown"
    )

    story = [
        Paragraph("Media2Text - Transcription", title_style),
        Paragraph(
            f"<b>File:</b> {escape(record.original_filename or "Unknown")}",
            meta_style
        ),
        Paragraph(
            f"<b>Date &amp; Time:</b> {generated_at_ist.strftime('%d-%m-%Y %H:%M')}",
            meta_style
        ),
        Paragraph(
            f"<b>Language Type:</b> {escape(language_name)} ({escape(record.language or "")})",
            meta_style
        ),
        Spacer(1, 3 * mm),
        Paragraph("Transcript", heading_style),
    ]

    transcript = escape(record.transcript or "")
    transcript = transcript.replace("\n", "<br/>")
    story.append(
        Paragraph(
            transcript if transcript else "No transcript.",
            body_style
        )
    )

    if not PDF_FONT_PATH:
        story.append(
            Paragraph(
                "Note: A Unicode font was not found. "
                "For Marathi/Hindi PDF output, keep the bundled "
                "NotoSansDevanagari-Regular.ttf file in the fonts folder.",
                meta_style
            )
        )

    doc.build(story)
    return filename


# ---------------------------------------------------------------------------
# Template context
# ---------------------------------------------------------------------------

@app.context_processor
def inject_user():
    return {
        "logged_user": current_user(),
        "speech_languages": SPEECH_LANGUAGES,
        "default_speech_language": DEFAULT_SPEECH_LANGUAGE,
        "format_ist": format_ist,
        "format_duration": format_duration,
        "now_utc": utc_now_naive(),
        "active_delta": timedelta(minutes=5),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if not username or not email or not password:
            flash("All fields are required.", "danger")
            return redirect(url_for("register"))

        if password != confirm:
            flash("Passwords do not match.", "danger")
            return redirect(url_for("register"))

        if len(password) < 6:
            flash("Password must contain at least 6 characters.", "danger")
            return redirect(url_for("register"))

        if User.query.filter(
            (User.username == username) | (User.email == email)
        ).first():
            flash("Username or email already exists.", "danger")
            return redirect(url_for("register"))

        otp = f"{secrets.randbelow(1000000):06d}"
        session["registration_pending"] = {
            "username": username,
            "email": email,
            "password_hash": generate_password_hash(password),
            "otp": otp,
            "otp_expires_at": (
                datetime.now(timezone.utc).timestamp() + OTP_EXPIRY_SECONDS
            ),
        }

        try:
            send_otp_email(email, otp)
        except Exception:
            session.pop("registration_pending", None)
            app.logger.exception("Could not send registration OTP.")
            flash(
                "OTP could not be sent. Check the Gmail SMTP App Password configuration.",
                "danger"
            )
            return redirect(url_for("register"))

        flash("OTP sent to your email. It is valid for 5 minutes.", "success")
        return redirect(url_for("verify_otp"))

    return render_template("register.html")


@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    if current_user():
        return redirect(url_for("dashboard"))

    pending = session.get("registration_pending")
    if not pending:
        flash("No pending email verification. Please register again.", "warning")
        return redirect(url_for("register"))

    if request.method == "POST":
        entered_otp = request.form.get("otp", "").strip()
        now = datetime.now(timezone.utc).timestamp()

        if now > float(pending.get("otp_expires_at", 0)):
            session.pop("registration_pending", None)
            flash("OTP expired. Please register again.", "danger")
            return redirect(url_for("register"))

        if not secrets.compare_digest(
            entered_otp, str(pending.get("otp", ""))
        ):
            flash(
                "Incorrect OTP. Please check your email and try again.",
                "danger"
            )
            return render_template(
                "verify_otp.html",
                email=pending.get("email")
            )

        if User.query.filter(
            (User.username == pending["username"])
            | (User.email == pending["email"])
        ).first():
            session.pop("registration_pending", None)
            flash("Username or email already exists.", "danger")
            return redirect(url_for("register"))

        user = User(
            username=pending["username"],
            email=pending["email"],
            password_hash=pending["password_hash"],
            role="user"
        )

        try:
            db.session.add(user)
            db.session.commit()
            session.pop("registration_pending", None)
        except Exception:
            db.session.rollback()
            app.logger.exception("Verified registration failed.")
            flash("Account creation failed. Please try again.", "danger")
            return redirect(url_for("register"))

        flash(
            "Email verified successfully. Account created. Please login.",
            "success"
        )
        return redirect(url_for("login"))

    return render_template("verify_otp.html", email=pending.get("email"))


@app.route("/resend-otp", methods=["POST"])
def resend_otp():
    if current_user():
        return redirect(url_for("dashboard"))

    pending = session.get("registration_pending")
    if not pending:
        flash("No pending registration. Please register again.", "warning")
        return redirect(url_for("register"))

    otp = f"{secrets.randbelow(1000000):06d}"
    pending["otp"] = otp
    pending["otp_expires_at"] = (
        datetime.now(timezone.utc).timestamp() + OTP_EXPIRY_SECONDS
    )
    session["registration_pending"] = pending

    try:
        send_otp_email(pending["email"], otp)
    except Exception:
        app.logger.exception("Could not resend registration OTP.")
        flash("Could not resend OTP. Check SMTP configuration.", "danger")
        return redirect(url_for("verify_otp"))

    flash("A new OTP has been sent. It is valid for 5 minutes.", "success")
    return redirect(url_for("verify_otp"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user():
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not email:
            flash("Email is required.", "danger")
            return redirect(url_for("forgot_password"))

        user = User.query.filter_by(email=email).first()
        if not user:
            flash("No registered account was found with this email address.", "danger")
            return redirect(url_for("forgot_password"))

        otp = f"{secrets.randbelow(1000000):06d}"
        session["password_reset_pending"] = {
            "user_id": user.id,
            "email": user.email,
            "otp_hash": generate_password_hash(otp),
            "otp_expires_at": datetime.now(timezone.utc).timestamp() + OTP_EXPIRY_SECONDS,
            "attempts": 0,
        }

        try:
            send_otp_email(user.email, otp, purpose="password_reset")
        except Exception:
            session.pop("password_reset_pending", None)
            app.logger.exception("Could not send password reset OTP.")
            flash("OTP could not be sent. Check the Gmail SMTP App Password configuration.", "danger")
            return redirect(url_for("forgot_password"))

        flash("Password reset OTP sent to your registered email. It is valid for 5 minutes.", "success")
        return redirect(url_for("verify_reset_otp"))

    return render_template("forgot_password.html")


@app.route("/verify-reset-otp", methods=["GET", "POST"])
def verify_reset_otp():
    pending = session.get("password_reset_pending")
    if not pending:
        flash("No pending password reset. Please enter your email again.", "warning")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        entered_otp = request.form.get("otp", "").strip()
        now = datetime.now(timezone.utc).timestamp()

        if now > float(pending.get("otp_expires_at", 0)):
            session.pop("password_reset_pending", None)
            flash("OTP expired. Please request a new OTP.", "danger")
            return redirect(url_for("forgot_password"))

        if not check_password_hash(pending.get("otp_hash", ""), entered_otp):
            pending["attempts"] = int(pending.get("attempts", 0)) + 1
            if pending["attempts"] >= 5:
                session.pop("password_reset_pending", None)
                flash("Too many incorrect OTP attempts. Please request a new OTP.", "danger")
                return redirect(url_for("forgot_password"))
            session["password_reset_pending"] = pending
            flash("Incorrect OTP. Please check your email and try again.", "danger")
            return render_template("verify_reset_otp.html", email=pending.get("email"))

        pending["verified"] = True
        pending.pop("otp_hash", None)
        pending.pop("otp_expires_at", None)
        session["password_reset_pending"] = pending
        flash("Email verified. Please create your new password.", "success")
        return redirect(url_for("reset_password"))

    return render_template("verify_reset_otp.html", email=pending.get("email"))


@app.route("/resend-reset-otp", methods=["POST"])
def resend_reset_otp():
    pending = session.get("password_reset_pending")
    if not pending:
        flash("No pending password reset. Please enter your email again.", "warning")
        return redirect(url_for("forgot_password"))

    user = User.query.get(pending.get("user_id"))
    if not user or user.email.lower() != str(pending.get("email", "")).lower():
        session.pop("password_reset_pending", None)
        flash("Password reset session is invalid. Please try again.", "danger")
        return redirect(url_for("forgot_password"))

    otp = f"{secrets.randbelow(1000000):06d}"
    pending.update({
        "otp_hash": generate_password_hash(otp),
        "otp_expires_at": datetime.now(timezone.utc).timestamp() + OTP_EXPIRY_SECONDS,
        "attempts": 0,
        "verified": False,
    })
    session["password_reset_pending"] = pending

    try:
        send_otp_email(user.email, otp, purpose="password_reset")
    except Exception:
        app.logger.exception("Could not resend password reset OTP.")
        flash("Could not resend OTP. Check SMTP configuration.", "danger")
        return redirect(url_for("verify_reset_otp"))

    flash("A new password reset OTP has been sent. It is valid for 5 minutes.", "success")
    return redirect(url_for("verify_reset_otp"))


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    pending = session.get("password_reset_pending")
    if not pending or not pending.get("verified"):
        flash("Please verify the OTP before creating a new password.", "warning")
        return redirect(url_for("forgot_password"))

    user = User.query.get(pending.get("user_id"))
    if not user or user.email.lower() != str(pending.get("email", "")).lower():
        session.pop("password_reset_pending", None)
        flash("Password reset session is invalid. Please try again.", "danger")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if len(password) < 6:
            flash("New password must contain at least 6 characters.", "danger")
            return redirect(url_for("reset_password"))

        if password != confirm:
            flash("Passwords do not match.", "danger")
            return redirect(url_for("reset_password"))

        user.password_hash = generate_password_hash(password)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception("Could not update password.")
            flash("Could not update the password. Please try again.", "danger")
            return redirect(url_for("reset_password"))

        session.pop("password_reset_pending", None)
        flash("Password changed successfully. Please login with your new password.", "success")
        return redirect(url_for("login"))

    return render_template("reset_password.html", email=user.email)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        identity = request.form.get("identity", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter(
            (User.email == identity.lower())
            | (User.username == identity)
        ).first()

        if not user or not check_password_hash(
            user.password_hash,
            password
        ):
            flash(
                "Invalid username/email or password.",
                "danger"
            )
            return redirect(url_for("login"))

        session.clear()
        session["user_id"] = user.id
        session["last_activity_ts"] = datetime.now(timezone.utc).timestamp()

        user.last_login = utc_now_naive()
        user.last_seen = utc_now_naive()
        user.login_count = (user.login_count or 0) + 1
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

        flash("Login successful.", "success")
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    user = current_user()
    if user:
        record_user_activity(user)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


@app.route("/heartbeat", methods=["POST"])
@login_required
def heartbeat():
    user = current_user()
    record_user_activity(user)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return {"ok": False}, 500
    return {"ok": True}


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()

    records = Transcription.query.filter_by(
        user_id=user.id
    ).order_by(
        Transcription.created_at.desc()
    ).limit(6).all()

    total = Transcription.query.filter_by(
        user_id=user.id
    ).count()

    pdf_count = Transcription.query.filter(
        Transcription.user_id == user.id,
        Transcription.pdf_filename.isnot(None)
    ).count()

    return render_template(
        "dashboard.html",
        user=user,
        records=records,
        total=total,
        pdf_count=pdf_count
    )


@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "POST":
        file = request.files.get("media")
        language = request.form.get(
            "language",
            DEFAULT_SPEECH_LANGUAGE
        )

        if language not in SPEECH_LANGUAGES:
            language = DEFAULT_SPEECH_LANGUAGE

        if not file or not file.filename:
            flash(
                "Please select an audio or video file.",
                "danger"
            )
            return redirect(url_for("upload"))

        if not allowed_file(file.filename):
            flash(
                "Unsupported format. Please choose an FFmpeg-supported "
                "audio/video extension.",
                "danger"
            )
            return redirect(url_for("upload"))

        original = secure_filename(file.filename)

        if not original:
            flash("Invalid filename.", "danger")
            return redirect(url_for("upload"))

        stored = f"{uuid.uuid4().hex}_{original}"
        input_path = os.path.join(
            app.config["UPLOAD_FOLDER"],
            stored
        )

        file.save(input_path)
        wav_path = None

        try:
            wav_path = convert_to_wav(input_path)
            transcript = speech_to_text(
                wav_path,
                language
            )

            # IMPORTANT:
            # audio_filename is required by the existing PostgreSQL table.
            record = Transcription(
                user_id=current_user().id,
                audio_filename=original,
                original_filename=original,
                stored_filename=stored,
                media_type=media_type_from_filename(original),
                language=language,
                transcript=transcript,
                target_language=None,
                translated_text=None
            )

            try:
                db.session.add(record)
                db.session.commit()
            except Exception:
                db.session.rollback()
                raise

            flash(
                "File converted successfully.",
                "success"
            )

            return redirect(
                url_for("editor", record_id=record.id)
            )

        except sr.UnknownValueError:
            db.session.rollback()
            flash(
                "Speech could not be understood. "
                "Try a clearer recording.",
                "danger"
            )

        except sr.RequestError:
            db.session.rollback()
            flash(
                "Speech recognition service is unavailable. "
                "Check your internet connection.",
                "danger"
            )

        except Exception as e:
            db.session.rollback()
            app.logger.exception(e)
            flash(
                f"Conversion failed: {str(e)}",
                "danger"
            )

        finally:
            if wav_path and os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except OSError:
                    pass

    return render_template("upload.html")


@app.route("/editor/<int:record_id>", methods=["GET", "POST"])
@login_required
def editor(record_id):
    record = db.session.get(
        Transcription,
        record_id
    )

    if not record or record.user_id != current_user().id:
        abort(404)

    if request.method == "POST":
        try:
            record.transcript = request.form.get(
                "transcript",
                ""
            ).strip()
            record.updated_at = utc_now_naive()
            db.session.commit()
            flash("Transcript saved.", "success")
        except Exception:
            db.session.rollback()
            app.logger.exception("Could not save transcript.")
            flash("Could not save changes. Please try again.", "danger")

        return redirect(
            url_for("editor", record_id=record.id)
        )

    return render_template(
        "editor.html",
        record=record
    )


@app.route("/generate-pdf/<int:record_id>")
@login_required
def generate_pdf(record_id):
    record = db.session.get(
        Transcription,
        record_id
    )

    if not record or record.user_id != current_user().id:
        abort(404)

    if not (record.transcript or "").strip():
        flash(
            "Transcript is empty.",
            "warning"
        )
        return redirect(
            url_for("editor", record_id=record.id)
        )

    try:
        if record.pdf_filename:
            old_path = os.path.join(
                app.config["PDF_FOLDER"],
                record.pdf_filename
            )

            if os.path.exists(old_path):
                os.remove(old_path)

        record.pdf_filename = create_pdf(record)
        record.updated_at = utc_now_naive()

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        flash(
            "PDF generated successfully. "
            "Marathi/Hindi Unicode text is supported.",
            "success"
        )

        return redirect(
            url_for("editor", record_id=record.id)
        )

    except Exception:
        db.session.rollback()
        app.logger.exception("PDF generation failed.")
        flash(
            "PDF generation failed.",
            "danger"
        )
        return redirect(
            url_for("editor", record_id=record.id)
        )


@app.route("/download-pdf/<int:record_id>")
@login_required
def download_pdf(record_id):
    record = db.session.get(
        Transcription,
        record_id
    )

    if not record or record.user_id != current_user().id:
        abort(404)

    if not record.pdf_filename:
        flash(
            "PDF has not been generated yet.",
            "warning"
        )
        return redirect(
            url_for("editor", record_id=record.id)
        )

    return send_from_directory(
        app.config["PDF_FOLDER"],
        record.pdf_filename,
        as_attachment=True
    )


@app.route("/history")
@login_required
def history():
    records = Transcription.query.filter_by(
        user_id=current_user().id
    ).order_by(
        Transcription.created_at.desc()
    ).all()

    return render_template(
        "history.html",
        records=records
    )


@app.route("/delete/<int:record_id>", methods=["POST"])
@login_required
def delete_record(record_id):
    record = db.session.get(
        Transcription,
        record_id
    )

    if not record or record.user_id != current_user().id:
        abort(404)

    try:
        if record.pdf_filename:
            pdf_path = os.path.join(
                app.config["PDF_FOLDER"],
                record.pdf_filename
            )

            if os.path.exists(pdf_path):
                os.remove(pdf_path)

        media_path = os.path.join(
            app.config["UPLOAD_FOLDER"],
            record.stored_filename
        )

        if os.path.exists(media_path):
            os.remove(media_path)

        db.session.delete(record)
        db.session.commit()

        flash(
            "Record deleted.",
            "info"
        )

    except Exception:
        db.session.rollback()
        app.logger.exception("Record deletion failed.")
        flash(
            "Could not delete the record.",
            "danger"
        )

    return redirect(url_for("history"))


@app.route("/profile")
@login_required
def profile():
    return render_template(
        "profile.html",
        user=current_user()
    )


@app.route("/admin")
@admin_required
def admin():
    users = User.query.order_by(User.created_at.desc()).all()
    records = Transcription.query.order_by(Transcription.created_at.desc()).limit(100).all()

    active_cutoff = utc_now_naive() - timedelta(minutes=5)
    active_users = User.query.filter(
        User.last_seen.isnot(None),
        User.last_seen >= active_cutoff
    ).count()

    total_usage_seconds = db.session.query(
        db.func.coalesce(db.func.sum(User.total_usage_seconds), 0)
    ).scalar() or 0

    stats = {
        "users": User.query.count(),
        "regular_users": User.query.filter_by(role="user").count(),
        "admins": User.query.filter_by(role="admin").count(),
        "active_users": active_users,
        "records": Transcription.query.count(),
        "pdfs": Transcription.query.filter(
            Transcription.pdf_filename.isnot(None)
        ).count(),
        "usage_seconds": int(total_usage_seconds),
    }

    return render_template(
        "admin.html",
        users=users,
        records=records,
        stats=stats,
        now_utc=utc_now_naive(),
        active_delta=timedelta(minutes=5)
    )


@app.errorhandler(413)
def too_large(error):
    flash(
        "File is too large. Maximum size is 500 MB.",
        "danger"
    )
    return redirect(url_for("upload"))


@app.errorhandler(404)
def not_found(error):
    return render_template("404.html"), 404


# ---------------------------------------------------------------------------
# Application startup
# ---------------------------------------------------------------------------

with app.app_context():
    db.create_all()
    migrate_existing_database()

    admin_email = ADMIN_EMAIL
    admin_password = ADMIN_PASSWORD

    # Exactly ONE admin is allowed. Registration always creates normal users.
    # If MEDIA2TEXT_ADMIN_EMAIL is configured, that account is the single
    # permanent admin and every other account is forced to role=user.
    # If the environment variables are not configured, the first existing
    # admin is retained and any additional admin accounts are demoted.
    try:
        designated_admin = None

        if admin_email and admin_password:
            designated_admin = User.query.filter_by(
                email=admin_email.lower()
            ).first()

            if not designated_admin:
                designated_admin = User(
                    username="admin",
                    email=admin_email.lower(),
                    password_hash=generate_password_hash(admin_password),
                    role="admin"
                )
                db.session.add(designated_admin)
                db.session.flush()
            else:
                designated_admin.role = "admin"

        else:
            designated_admin = User.query.filter_by(
                role="admin"
            ).order_by(User.id.asc()).first()

        # Demote every other admin so the database can never have more than
        # one privileged account. Existing user data is preserved.
        if designated_admin:
            User.query.filter(
                User.role == "admin",
                User.id != designated_admin.id
            ).update({"role": "user"}, synchronize_session=False)

        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Could not enforce single-admin rule.")


if __name__ == "__main__":
    app.run(debug=True)
