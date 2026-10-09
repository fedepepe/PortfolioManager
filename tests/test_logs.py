import logging

from portfolio_manager.utils.logs import HideSecrets, hide_secrets

# messages as degiro-connector writes them (made-up tokens and numbers)
LOGIN = (
    "login_sucess: {'isPassCodeEnabled': True, 'locale': 'it_CH', 'sessionId': '988C358FAF64.prod_dcv_ch64_2', "
    "'status': 0}"
)
ERROR = '400 Client Error:  for url: https://trader.degiro.nl/trading/secure/v5/account/info/1234567;jsessionid=AB.p_1'


def test_session_ids_and_account_numbers_are_hidden():
    assert hide_secrets(LOGIN) == (
        "login_sucess: {'isPassCodeEnabled': True, 'locale': 'it_CH', 'sessionId': '<hidden>', 'status': 0}"
    )
    assert hide_secrets(ERROR).endswith('/account/info/<hidden>;jsessionid=<hidden>')
    assert hide_secrets('{"intAccount": 1234567, "clientRole": "x"}') == '{"intAccount": <hidden>, "clientRole": "x"}'
    assert hide_secrets('Connected to Degiro as Portfolio CHF') == 'Connected to Degiro as Portfolio CHF'


def test_filter_rewrites_messages_with_arguments():
    record = logging.LogRecord(
        'degiro_connector.x', logging.INFO, '', 0, 'login_sucess: %s', ({'sessionId': 'X1'},), None
    )
    assert HideSecrets().filter(record)
    assert record.getMessage() == "login_sucess: {'sessionId': '<hidden>'}"
