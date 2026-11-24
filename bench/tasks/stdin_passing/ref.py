import sys

least = int(sys.argv[1])
for line in sys.stdin.read().splitlines():
    name, score = line.split(",")
    if int(score) >= least:
        print(name)
