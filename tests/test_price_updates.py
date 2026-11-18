from datetime import date, datetime

import pandas as pd
import pytest

import portfolio_manager.storage.queries as sql
from portfolio_manager.market_data.freshness import due_listings, final_since, latest_final_day


@pytest.mark.parametrize(
    'now, final',
    [
        ('2026-11-18 07:59', '2026-11-16'),  # Wednesday before 8: Tuesday not final yet
        ('2026-11-18 08:00', '2026-11-17'),  # Wednesday from 8: Tuesday final
        ('2026-11-14 07:00', '2026-11-12'),  # Saturday before 8: Friday not final yet
        ('2026-11-14 09:00', '2026-11-13'),  # Saturday from 8: Friday final
        ('2026-11-16 09:00', '2026-11-13'),  # Monday: still Friday (no trading at the weekend)
        ('2026-11-17 07:00', '2026-11-13'),  # Tuesday before 8: Monday not final yet
    ],
)
def test_prices_final_the_next_morning(now, final):
    assert latest_final_day(datetime.fromisoformat(now)) == date.fromisoformat(final)


def test_final_since_the_next_morning_at_8():
    assert final_since(date(2026, 11, 13)) == datetime(2026, 11, 14, 8, 0)


def test_listings_missing_the_latest_final_day(temp_db):
    listings = [
        ('NEW.SW', '2026-11-17'),
        ('OLD.SW', '2026-11-13'),
        ('MONTH.SW', '2026-10-19'),  # last price 30 days ago: still updated
        ('STALE.SW', '2026-10-16'),  # last price 33 days ago: no longer trading, left out
    ]
    for ticker, last in listings:
        instrument_id = sql.upsert_yahoo_instrument(ticker, {'currency': 'CHF'})
        days = pd.bdate_range(end=last, periods=5)
        sql.save_yahoo_prices(instrument_id, pd.DataFrame({'close': 1.0, 'volume': 10.0}, index=days))
    now = datetime(2026, 11, 18, 9, 0)  # Tuesday 17 final
    tickers = ['NEW.SW', 'OLD.SW', 'MONTH.SW', 'STALE.SW', 'NONE.SW']
    assert due_listings(tickers, now) == {
        'OLD.SW': date(2026, 11, 13),
        'MONTH.SW': date(2026, 10, 19),
        'NONE.SW': None,  # nothing stored: its whole history is downloaded
    }
    assert due_listings([], now) == {}


def test_update_button_hint(monkeypatch):
    import portfolio_manager.dashboard.data_service as service
    from portfolio_manager.config.accounts import Account, Brokers

    account = Account('Portfolio CHF', Brokers.DEGIRO, 'config.json', 'CHF')
    now = datetime(2026, 11, 18, 9, 0)  # Tuesday 17 final since Wednesday 8:00
    monkeypatch.setattr(service, 'tables_version', lambda folder: datetime(2026, 11, 18, 8, 30).timestamp())
    assert service.available_update(account, now) is None  # updated after 8:00
    monkeypatch.setattr(service, 'tables_version', lambda folder: datetime(2026, 11, 17, 20, 0).timestamp())
    assert service.available_update(account, now) == (
        'The prices of Tue 17 Nov are final since Wed 18 Nov 08:00: press Update'
    )
