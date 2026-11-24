import sys

print(*sorted(int(a) for a in sys.argv[1:]))
