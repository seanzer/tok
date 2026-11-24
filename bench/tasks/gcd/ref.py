import sys

a, b = int(sys.argv[1]), int(sys.argv[2])
while b:
    a, b = b, a % b
print(a)
