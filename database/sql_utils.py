from datetime import datetime


def list_to_str(lst: list[str], sep: str = ', ') -> str | None:
    return sep.join(lst) if lst is not None else None


def str_to_date(s: str) -> datetime | None:
    return datetime.strptime(s, '%Y-%m-%d') if s is not None else None
