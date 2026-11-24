import sys

left = int(sys.argv[1])
counts = []
for coin in (25, 10, 5, 1):
    counts.append(str(left // coin))
    left %= coin
print(" ".join(counts))
