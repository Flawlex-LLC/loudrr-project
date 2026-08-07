from datetime import datetime, UTC


def utcnow() -> datetime:
    """Tz-naive UTC datetime — the replacement for the deprecated datetime.utcnow().

    Returns a naive datetime to match the TIMESTAMP WITHOUT TIME ZONE columns
    used throughout the schema.
    """
    return datetime.now(UTC).replace(tzinfo=None)
