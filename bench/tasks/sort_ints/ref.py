import sys

print(" ".join(str(n) for n in sorted(int(a) for a in sys.argv[1:])))
