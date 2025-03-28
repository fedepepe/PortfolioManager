import dash_bootstrap_components as dbc
from dash import html

from dash_common import SIDEBAR_STYLE

# Sidebar
sidebar = html.Div(
	dbc.Card(
		dbc.CardBody(
			dbc.ButtonGroup([
				dbc.Button("Portfolio", id="button-portfolio"),
				html.Br(),
				dbc.Button("Instruments", id="button-instruments"),
				html.Br(),
				dbc.Button("Strategies", id="button-strategies"),
			],
				vertical=True,
			)
		), color='dark'),
	style=SIDEBAR_STYLE
)
