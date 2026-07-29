PYTHON ?= python3

.PHONY: sync build query serve audit evaluate test demo

sync:
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli sync --all-files

build:
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli build

query:
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli query "$(Q)"

serve:
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli serve

audit:
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli audit

evaluate:
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli evaluate

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

demo: build
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli query "宿舍是几人间，能确定吗？"
