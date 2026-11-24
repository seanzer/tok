TOKC = python3 compiler/tokc.py

.PHONY: hello examples fmt-check test bench-check tokens clean

hello:
	$(TOKC) run examples/hello.tok

examples:
	$(TOKC) run examples/logscan.tok -- examples/data/server.log
	$(TOKC) run examples/normalize.tok
	$(TOKC) run examples/users.tok
	$(TOKC) run examples/modules/main.tok
	$(TOKC) run examples/pixels.tok
	$(TOKC) run examples/errors.tok

fmt-check:
	for f in examples/*.tok examples/modules/*.tok; do $(TOKC) fmt $$f --check || exit 1; done

test:
	python3 tests/run_tests.py

bench-check:
	python3 bench/run.py --adapter reference --lang tok
	python3 bench/run.py --adapter reference --lang python

tokens:
	.venv/bin/python bench/count_tokens.py --write

clean:
	rm -rf build
