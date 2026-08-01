PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
HOST ?= 127.0.0.1
PORT ?= 8000
-include .env.local
export

.PHONY: sync official-sync official-review build query chat serve makers-check search-check audit evaluate evaluate-chat publication-audit test demo

sync:
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli sync --all-files

official-sync:
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli official-sync

official-review:
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli official-review

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

search-check:
	@test "$(CAMPUS_WEB_SEARCH_PROVIDER)" = "tavily" || (echo "Set CAMPUS_WEB_SEARCH_PROVIDER=tavily in .env.local" && exit 1)
	@test -n "$(CAMPUS_WEB_SEARCH_API_KEY)" || (echo "Set CAMPUS_WEB_SEARCH_API_KEY in .env.local" && exit 1)
	PYTHONPATH=src $(PYTHON) -m campus_copilot.cli chat "智能科学与技术的就业方向有哪些？"

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
