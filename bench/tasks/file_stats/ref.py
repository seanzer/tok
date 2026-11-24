import sys

try:
    with open(sys.argv[1]) as f:
        lines = f.read().splitlines()
except OSError:
    lines = []
words = sum(len(line.split(" ")) - line.split(" ").count("") for line in lines)
print(f"{len(lines)} lines, {words} words")
