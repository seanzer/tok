import sys

try:
    with open(sys.argv[1]) as f:
        lines = f.read().splitlines()
except OSError:
    print("error: cannot read file", file=sys.stderr)
    sys.exit(1)
total = 0
for line in lines:
    try:
        total += int(line.strip())
    except ValueError:
        pass
print(total)
