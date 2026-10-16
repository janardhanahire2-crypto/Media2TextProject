import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get(
        "SECRET_KEY",
        "media2text-tybcs-secret-key"
    )

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg2://postgres:ritesh%40123@localhost:5432/media2text"
    )

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Increased for larger audio/video files.
    MAX_CONTENT_LENGTH = 500 * 1024 * 1024

    UPLOAD_FOLDER = os.path.join(
        BASE_DIR,
        "uploads"
    )

    PDF_FOLDER = os.path.join(
        BASE_DIR,
        "generated_pdfs"
    )

    # FFmpeg supports many containers/codecs. These are common audio/video
    # filename extensions accepted by the web app.
    ALLOWED_EXTENSIONS = {
        # Audio
        "wav", "mp3", "m4a", "aac", "ogg", "oga", "opus",
        "flac", "wma", "aiff", "aif", "au", "amr", "3ga",
        "ac3", "alac", "caf", "weba",

        # Video
        "mp4", "mkv", "avi", "mov", "webm", "mpeg", "mpg",
        "m4v", "3gp", "3g2", "ts", "mts", "m2ts", "flv",
        "wmv", "asf", "vob", "ogv", "rm", "rmvb", "f4v",
    }

    # Default speech language on the Upload page.
    # Change to "en-IN" if English should be the default.
    DEFAULT_SPEECH_LANGUAGE = os.environ.get(
        "MEDIA2TEXT_DEFAULT_LANGUAGE",
        "mr-IN"
    )
