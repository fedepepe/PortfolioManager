from typing import List, Optional
from datetime import datetime


def list_to_str(lst: List[str], sep: str = ', ') -> Optional[str]:
    return sep.join(lst) if lst is not None else None


def str_to_date(s: str) -> Optional[datetime]:
    return datetime.strptime(s, '%Y-%m-%d') if s is not None else None
