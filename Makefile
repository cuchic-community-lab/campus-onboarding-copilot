PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
HOST ?= 127.0.0.1
PORT ?= 8000
-include .env.local
export

.PHONY: sync build query chat serve makers-check audit evaluate evaluate-chat publication-audit test demo

sync:
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli sync --all-files

build:
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli build

query:
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli query "$(Q)"

chat:
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli chat "$(Q)"

serve:
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli serve --host $(HOST) --port $(PORT)

makers-check:
	@test "$(CAMPUS_LLM_PROVIDER)" = "makers" || (echo "Set CAMPUS_LLM_PROVIDER=makers in .env.local" && exit 1)
	@test -n "$(CAMPUS_LLM_API_KEY)" || (echo "Set CAMPUS_LLM_API_KEY in .env.local" && exit 1)
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli chat "宿舍的床多大？"

audit:
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli audit

evaluate:
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli evaluate

evaluate-chat:
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli evaluate-chat

publication-audit:
	$(PYTHON) scripts/publication_audit.py

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

demo: build
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli query "宿舍是几人间，能确定吗？"
