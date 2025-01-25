import pandas as pd

import yfinance_api as yf
from charts import load_portfolio_products, load_fx_rates
from definitions import DATA_DIR, PORTFOLIO_NAME, BASE_CURRENCY


def get_instruments_adj_prices():
	products_df = load_portfolio_products()
	tickers = products_df['isin'].to_list()
	close_adj_df = pd.DataFrame()
	for t in tickers:
		df = yf.search_get_history(ticker=t)
		close_adj_df = pd.concat([close_adj_df, df], axis=1)
	close_adj_df.index = pd.to_datetime(close_adj_df.index)
	close_adj_df = close_adj_df.sort_index()
	close_adj_df = close_adj_df.resample('B').last()
	# convert to base currency
	product_symbols = [s.split("__")[0].split('.')[0] for s in close_adj_df.columns]
	product_curr = [s.split("__")[1] for s in close_adj_df.columns]
	curr_foreign_lst = [c for c in list(set(product_curr)) if c != BASE_CURRENCY]
	fx_rates_df = load_fx_rates(curr_foreign_lst=curr_foreign_lst, index=close_adj_df.index)
	fx_rates_df = fx_rates_df.resample('B').last()
	# average prices across exchanges
	close_adj_base_cur_df = pd.DataFrame()
	for symbol, cur, old_symbol in zip(product_symbols, product_curr, close_adj_df.columns.to_list()):
		ser = close_adj_df[old_symbol].mul(fx_rates_df.loc[close_adj_df.index, f'{cur}/{BASE_CURRENCY}'])
		close_adj_base_cur_df = pd.concat([close_adj_base_cur_df, ser.rename(symbol)], axis=1)
	close_adj_base_cur_df = close_adj_base_cur_df.sort_index()
	return close_adj_base_cur_df


if __name__ == '__main__':
	df = get_instruments_adj_prices()
