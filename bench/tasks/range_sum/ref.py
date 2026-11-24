import sys

a, b = int(sys.argv[1]), int(sys.argv[2])
print(sum(range(min(a, b), max(a, b) + 1)))
