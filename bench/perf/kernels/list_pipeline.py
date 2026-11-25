import sys
n = int(sys.argv[1]) if len(sys.argv) > 1 else 1000000
rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 8
total = 0
for r in range(1, rounds + 1):
    xs = []
    for i in range(1, n + 1):
        xs.append(i + r)
    ys = [x * 2 + 1 for x in xs]
    zs = [x for x in ys if x % 3 == 0]
    total += sum(zs) + len(zs)
print(total)
