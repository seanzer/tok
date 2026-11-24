import sys

n = int(sys.argv[1])
print(*(i for i in range(2, n + 1) if all(i % d for d in range(2, int(i ** 0.5) + 1))))
