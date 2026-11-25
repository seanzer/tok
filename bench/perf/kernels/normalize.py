import sys
from math import sqrt

def mean(xs):
    s = 0.0
    for x in xs:
        s += x
    return s / len(xs)

def normalize(xs):
    m = mean(xs)
    s = sqrt(mean([(x - m) * (x - m) for x in xs]))
    return [(x - m) / s for x in xs]

n = int(sys.argv[1]) if len(sys.argv) > 1 else 1000000
rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 8
xs = [float(i % 1000) * 0.5 for i in range(1, n + 1)]
total = 0.0
for r in range(1, rounds + 1):
    ys = normalize(xs)
    total = total + ys[r] + ys[n // 2] + max(ys)
print(int(total * 1000.0))
