import sys

try:
    with open(sys.argv[1]) as f:
        lines = f.read().splitlines()
except OSError:
    lines = []
print(f"{len(lines)} lines, {sum(len(line.split()) for line in lines)} words")
