import sys

print(max(sys.stdin.read().splitlines(), key=len, default=""))
