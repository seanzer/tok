import sys

n = int(sys.argv[1])
primes = []
for i in range(2, n + 1):
    if all(i % d for d in range(2, int(i ** 0.5) + 1)):
        primes.append(str(i))
print(" ".join(primes))
