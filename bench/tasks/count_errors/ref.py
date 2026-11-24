import sys

try:
    with open(sys.argv[1]) as f:
        print(sum(1 for line in f if "ERROR" in line))
except OSError:
    print(0)
