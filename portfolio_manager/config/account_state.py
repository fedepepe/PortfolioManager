"""Per-account state saved as JSON in the state folder (e.g. the date of the last update)."""

import json
import os
import re

from portfolio_manager.config.settings import STATE_DIR

DEFAULT_STATE_DCT = {'last_data_update': '01Jan2000'}


class State:
    """Key-value state of one account, saved at each change."""

    def __init__(self, label: str):
        self.label = re.sub(r'[\W_]+', '_', label.lower())
        self._state = DEFAULT_STATE_DCT

    def __post_init__(self):
        self.load_state()

    def get_state_file_path(self):
        """Path of the JSON file of the state."""
        return f'{STATE_DIR}/state_{self.label}.json'

    def load_state(self):
        """Read the state from its file."""
        with open(self.get_state_file_path()) as fp:
            self._state = json.load(fp)

    def save_state(self):
        """Write the state to its file."""
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(self.get_state_file_path(), 'w') as fp:
            json.dump(self._state, fp)

    def get(self, field):
        """Value of a field."""
        return self._state[field]

    def set(self, field, value):
        """Set a field and save the state."""
        self._state[field] = value
        self.save_state()
