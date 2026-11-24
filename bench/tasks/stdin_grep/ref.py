import sys

for line in sys.stdin.read().splitlines():
    if sys.argv[1] in line:
        print(line)
