import sys

xs = [float(w) for w in sys.stdin.read().split()]
print(f"{sum(xs) / len(xs):.2f}" if xs else "none")
