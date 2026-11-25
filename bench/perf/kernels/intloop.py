import sys
n = int(sys.argv[1]) if len(sys.argv) > 1 else 60000000
s = 0
for i in range(1, n + 1):
    s = s + i % 7 * (i % 13) - i // 3 % 5
print(s)
