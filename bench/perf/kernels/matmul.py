import sys

def make(n, seed):
    return [[float((i * seed + j) % 10) for j in range(n)] for i in range(n)]

def mul(a, b, n):
    c = []
    for i in range(n):
        row = []
        ai = a[i]
        for j in range(n):
            s = 0.0
            for k in range(n):
                s = s + ai[k] * b[k][j]
            row.append(s)
        c.append(row)
    return c

n = int(sys.argv[1]) if len(sys.argv) > 1 else 300
rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 2
a = make(n, 3)
b = make(n, 7)
total = 0.0
for r in range(rounds):
    c = mul(a, b, n)
    total = total + c[0][0] + c[n // 2][n // 3] + c[n - 1][n - 1]
print(int(total))
