import json
import re

from config.definitions import STATE_DIR

DEFAULT_STATE_DCT = {'last_data_update': '01Jan2000'}


class State:
	def __init__(self, label: str):
		self.label = re.sub(r'[\W_]+', '_', label.lower())
		self._state = DEFAULT_STATE_DCT

	def __post_init__(self):
		self.load_state()

	def get_state_file_path(self):
		return f'{STATE_DIR}/state_{self.label}.json'

	def load_state(self):
		with open(self.get_state_file_path()) as fp:
			self._state = json.load(fp)

	def save_state(self):
		with open(self.get_state_file_path(), 'w') as fp:
			json.dump(self._state, fp)

	def get(self, field):
		return self._state[field]

	def set(self, field, value):
		self._state[field] = value
		self.save_state()
