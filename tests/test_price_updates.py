from datetime import date, datetime, timedelta

import pandas as pd
import pytest

import portfolio_manager.analytics.price_updates as updates
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


@pytest.fixture
def fake_listings(monkeypatch):
    # A gets its new prices, B is not published yet; C is up to date
    state = {'due': {'A': date(2026, 11, 13), 'B': date(2026, 11, 13)}, 'downloads': [], 'cleared': 0}

    def fake_update(ticker):
        state['downloads'].append(ticker)
        if ticker == 'A':
            state['due'].pop('A', None)
            return 'updated'
        return 'not found'

    monkeypatch.setattr(updates, 'priority_tickers', lambda: ['C', 'A', 'B'])
    monkeypatch.setattr(
        updates, 'due_listings', lambda tickers, now=None: {t: state['due'][t] for t in tickers if t in state['due']}
    )
    monkeypatch.setattr(updates, 'update_listing', fake_update)
    monkeypatch.setattr(updates, 'is_yahoo_reachable', lambda: True)
    monkeypatch.setattr(updates, 'clear_day_cache', lambda: state.update(cleared=state['cleared'] + 1))
    monkeypatch.setattr(updates, 'PAUSE', 0)
    return state


def test_background_pass_updates_the_due_listings_and_retries_later(fake_listings):
    updater = updates.BackgroundUpdater()
    now = datetime(2026, 11, 18, 9, 0)
    assert updater.run_pass(now) == 1
    assert fake_listings['downloads'] == ['A', 'B']  # in order of priority, not the up-to-date C
    assert fake_listings['cleared'] == 1  # the candidates of the allocations computed again
    status = updater.status
    assert not status.running and status.updated == 1 and status.missing == 1 and status.final_day == date(2026, 11, 17)
    assert updater.run_pass(now + timedelta(hours=1)) == 0  # B tried an hour ago: not again yet
    assert fake_listings['downloads'] == ['A', 'B'] and fake_listings['cleared'] == 1  # nothing new: not cleared
    updater.run_pass(now + timedelta(hours=3))
    assert fake_listings['downloads'] == ['A', 'B', 'B']  # tried again after 3 hours


def test_background_pass_waits_for_the_downloads_started_from_the_dashboard(fake_listings):
    import threading

    updater = updates.BackgroundUpdater()
    with updater.user_download():  # e.g. the Update button of the Portfolio page
        thread = threading.Thread(target=updater.run_pass, args=(datetime(2026, 11, 18, 9, 0),))
        thread.start()
        thread.join(timeout=0.5)
        assert thread.is_alive() and fake_listings['downloads'] == []  # waiting before its first listing
        assert updater.status.paused and 'paused while your download runs' in updates.status_text(updater.status)
    thread.join(timeout=5)
    assert not thread.is_alive() and fake_listings['downloads'] == ['A', 'B']  # went on once it ended
    assert not updater.status.paused


def test_dashboard_downloads_can_overlap(fake_listings):
    updater = updates.BackgroundUpdater()
    with updater.user_download(), updater.user_download():
        assert updater._user_downloads == 2
    assert updater._user_downloads == 0
    assert updater.run_pass(datetime(2026, 11, 18, 9, 0)) == 1  # not waiting any more


def test_no_pass_without_connection(fake_listings, monkeypatch):
    monkeypatch.setattr(updates, 'is_yahoo_reachable', lambda: False)
    assert updates.BackgroundUpdater().run_pass(datetime(2026, 11, 18, 9, 0)) == 0
    assert fake_listings['downloads'] == []


def test_status_text():
    assert updates.status_text(updates.UpdateStatus(running=True, done=1234, total=5917)) == (
        'Updating prices in the background: 1,234 / 5,917 listings'
    )
    done = updates.UpdateStatus(finished_at=datetime(2026, 11, 18, 9, 40), missing=3, final_day=date(2026, 11, 17))
    assert updates.status_text(done) == (
        'Prices up to date with Tue 17 Nov (checked 09:40); 3 listings without it yet, tried again later'
    )


def test_day_cache_cleared_after_new_prices(temp_db):
    import os

    import portfolio_manager.analytics.saa as saa
    from portfolio_manager.storage.tables import derived_folder

    folder = derived_folder(saa.CACHE_KIND, f'{date.today().isoformat()}_listings_x')
    os.makedirs(folder)
    saa.clear_day_cache()
    assert not os.path.exists(os.path.dirname(folder))


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


def test_backtest_asks_before_downloading_new_prices(monkeypatch):
    import portfolio_manager.dashboard.saa_page as page
    from tests.test_saa_page import CHF, balanced

    monkeypatch.setattr(page, 'get_account', lambda name: CHF)
    monkeypatch.setattr(page, 'backtest_tickers', lambda account, allocation: ['CHSPI.SW', 'VWRL.SW', 'CHCORP.SW'])
    monkeypatch.setattr(page, 'due_listings', lambda tickers: {'VWRL.SW': date(2026, 11, 13), 'CHCORP.SW': None})
    monkeypatch.setattr(page, 'latest_final_day', lambda: date(2026, 11, 17))
    displayed, message, request = page.prepare_backtest(1, balanced().to_dict(), 'Portfolio CHF')
    assert displayed and request is page.no_update
    assert 'for 2 of the 3 instruments' in message and 'VWRL.SW: last stored Fri 13 Nov' in message
    assert 'CHCORP.SW: no prices stored' in message
    monkeypatch.setattr(page, 'due_listings', lambda tickers: {})
    displayed, _, request = page.prepare_backtest(1, balanced().to_dict(), 'Portfolio CHF')
    assert displayed is False and request is not page.no_update  # nothing new: run at once
