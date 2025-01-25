from typing import List, NamedTuple, Optional
from enum import Enum

import pandas as pd
import yfinance as yf


class YFinPriceCols:
	open = 'Open'
	high = 'High'
	low = 'Low'
	close = 'Close'
	adj_close = 'Adj Close'
	volume = 'Volume'
	dividends = 'Dividends'
	stock_splits = 'Stock Splits'
	capital_gains = 'Capital Gains'


class Exchange(NamedTuple):
	name: str


class Exchanges(Exchange, Enum):
	SW = Exchange('SW')
	MI = Exchange('MI')
	DE = Exchange('DE')
	BE = Exchange('BE')
	MU = Exchange('MU')
	DU = Exchange('DU')
	SG = Exchange('SG')
	L = Exchange('L')
	XC = Exchange('XC')
	XD = Exchange('XD')


def search_ticker(ticker: str) -> yf.search.Search:
	return yf.Search(query=ticker, include_research=True)


def get_history_single(ticker: str | yf.Ticker):
	if isinstance(ticker, str):
		ticker = yf.Ticker(ticker=ticker)
	elif isinstance(ticker, yf.Ticker):
		pass
	else:
		raise TypeError
	df = ticker.history(period="10y", interval='1d', auto_adjust=False)
	df = df.resample('D').last().dropna(how='all')
	df.index = df.index.tz_localize(None)
	return df


def get_history(tickers: str | List[str] | yf.Ticker | List[yf.Ticker],
                column: str | YFinPriceCols = YFinPriceCols.adj_close
                ) -> pd.DataFrame:
	if isinstance(tickers, str) or isinstance(tickers, yf.Ticker):
		tickers = [tickers]
	tickers = [yf.Ticker(t) if isinstance(t, str) else t for t in tickers]
	df = pd.DataFrame()
	for ticker in tickers:
		df = pd.concat([df, get_history_single(ticker=ticker)[column].rename(ticker.ticker)], axis=1)
	df.index = pd.to_datetime(df.index)
	df = df.sort_index()
	return df


def search_get_history(ticker: Optional[str],
                       isin: Optional[str],
                       column: str | YFinPriceCols = YFinPriceCols.adj_close
                       ) -> pd.DataFrame:
	if ticker is not None:
		search = search_ticker(ticker=ticker).all['quotes']
		match = [e for e in search if e['symbol'] == ticker or e['symbol'] in [f'{ticker}.{x.name}' for x in Exchanges]]
	elif isin is not None:
		match = search_ticker(ticker=isin).all['quotes']
	else:
		raise ValueError('Ticker or ISIN not provided.')
	hist_df = pd.DataFrame()
	for t in match:
		ticker = t['symbol']
		try:
			currency = yf.Ticker(ticker).info['currency']
		except KeyError:
			continue
		df = get_history(tickers=ticker, column=column)
		df = df.rename(columns={ticker: f"{ticker}__{currency}"})
		hist_df = pd.concat([hist_df, df], axis=1)
	return hist_df


if __name__ == '__main__':
	ticker = 'IE00077FRP95'
	df = search_get_history(ticker=ticker)
	pass
