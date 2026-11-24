import sys

a, b = 0, 1
for _ in range(int(sys.argv[1])):
    a, b = b, a + b
print(a)
