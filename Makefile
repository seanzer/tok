TOKC = python3 compiler/tokc.py

.PHONY: hello test clean

hello:
	$(TOKC) run examples/hello.tok

test:
	python3 tests/run_tests.py

clean:
	rm -rf build
