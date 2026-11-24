import sys

a, b = int(sys.argv[1]), int(sys.argv[2])
if b == 0:
    print("error: division by zero", file=sys.stderr)
    sys.exit(1)
print(a // b)
