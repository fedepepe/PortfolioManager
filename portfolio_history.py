from products import fetch_chart, save_charts
from transactions import load_tx_history


def compute_portfolio_nav():
	tx_history_df = load_tx_history()
	product_ids = list(set(tx_history_df['product_id'].to_list()))
	chart_df = fetch_chart(product_ids=product_ids)
	save_charts(chart_df)


if __name__ == '__main__':
	compute_portfolio_nav()
