import sys

for i, line in enumerate(sys.stdin.read().splitlines(), 1):
    print(f"{i}: {line}")
