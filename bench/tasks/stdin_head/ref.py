import sys

for line in sys.stdin.read().splitlines()[:int(sys.argv[1])]:
    print(line)
