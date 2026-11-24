import sys

print(sum(1 for a in sys.argv[1:] if int(a) % 2 == 0))
