.PHONY: all test smoke paper clean

TESTS := $(sort $(wildcard tests/test_*.py))

all: test smoke

test:
	@set -eu; \
	for test_file in $(TESTS); do \
		echo "==> python3 $$test_file"; \
		PYTHONDONTWRITEBYTECODE=1 python3 "$$test_file"; \
	done

smoke:
	PYTHONDONTWRITEBYTECODE=1 python3 scripts/run_smoke.py

paper:
	cd paper && tectonic main.tex

clean:
	find . -type f -path '*/__pycache__/*.pyc' -exec rm -f {} +
