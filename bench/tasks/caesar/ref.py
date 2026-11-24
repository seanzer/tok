import sys

shift = int(sys.argv[2])
print("".join(chr((ord(c) - 97 + shift) % 26 + 97) if c != " " else c for c in sys.argv[1]))
