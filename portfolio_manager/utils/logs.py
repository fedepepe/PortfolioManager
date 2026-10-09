"""Logging of the entry points (main.py, tasks.py), without the Degiro login tokens and account numbers."""

import logging
import re

# Degiro session ids (login tokens) and account numbers, as they appear in the messages of degiro-connector
SECRET_PATTERNS = [
    (re.compile(r"""(['"]?sessionId['"]?\s*[:=]\s*['"]?)[^'",;&\s}]+"""), r'\1<hidden>'),
    (re.compile(r'(jsessionid=)[^;&?/\s"\']+', re.IGNORECASE), r'\1<hidden>'),
    (re.compile(r"""(['"]?intAccount['"]?\s*[:=]\s*['"]?)\d+"""), r'\1<hidden>'),
    (re.compile(r'(/account/info/)\d+'), r'\1<hidden>'),
]


def hide_secrets(text: str) -> str:
    """The text with Degiro session ids and account numbers replaced by <hidden>."""
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class HideSecrets(logging.Filter):
    """Removes Degiro session ids and account numbers from the messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Rewrite the message and the traceback of the record (always kept)."""
        message = record.getMessage()
        hidden = hide_secrets(message)
        if hidden != message:
            record.msg, record.args = hidden, None
        # the traceback is formatted from exc_info unless exc_text is already set
        if record.exc_info and not record.exc_text:
            record.exc_text = hide_secrets(logging.Formatter().formatException(record.exc_info))
        return True


def configure_logging(level: int = logging.INFO):
    """Log to the console; degiro-connector only from warnings (its login messages contain the session id), and every
    message without session ids and account numbers.
    """
    logging.basicConfig(level=level, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    logging.getLogger('degiro_connector').setLevel(max(level, logging.WARNING))
    for handler in logging.getLogger().handlers:
        handler.addFilter(HideSecrets())
