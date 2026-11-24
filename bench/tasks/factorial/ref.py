import sys

f = 1
for i in range(1, int(sys.argv[1]) + 1):
    f *= i
print(f)
