import sys
n = int(sys.argv[1]) if len(sys.argv) > 1 else 500000
rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 3
total = 0
x = 12345
for r in range(rounds):
    xs = []
    for i in range(n):
        x = (x * 1103515245 + 12345) % 2147483648
        xs.append(x)
    s = sorted(xs)
    total += s[0] + s[n // 2] + s[n - 1]
print(total)
