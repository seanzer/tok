import sys

n = int(sys.argv[1])
total = sum(d for d in range(1, n) if n % d == 0)
print("perfect" if total == n and n > 0 else "not perfect")
