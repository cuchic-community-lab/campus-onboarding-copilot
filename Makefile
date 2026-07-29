PYTHON ?= python3

.PHONY: sync build query chat serve audit evaluate evaluate-chat test demo

sync:
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli sync --all-files

build:
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli build

query:
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli query "$(Q)"

chat:
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli chat "$(Q)"

serve:
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli serve

audit:
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli audit

evaluate:
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli evaluate

evaluate-chat:
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli evaluate-chat

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

demo: build
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli query "宿舍是几人间，能确定吗？"
