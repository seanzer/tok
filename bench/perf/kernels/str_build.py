import sys
n = int(sys.argv[1]) if len(sys.argv) > 1 else 2500
rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 10
total = 0
for r in range(1, rounds + 1):
    s = ""
    for i in range(1, n + 1):
        s = s + f"w{i} "
    total += len(s)
print(total)
