import sys


def is_prime(n):
    if n < 2:
        return False
    d = 2
    while d * d <= n:
        if n % d == 0:
            return False
        d += 1
    return True


print("prime" if is_prime(int(sys.argv[1])) else "not prime")
