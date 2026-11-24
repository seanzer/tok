import sys

distinct = sorted(set(int(a) for a in sys.argv[1:]))
print(distinct[-2] if len(distinct) >= 2 else "none")
