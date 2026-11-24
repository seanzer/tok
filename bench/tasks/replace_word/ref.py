import sys

old, new = sys.argv[1], sys.argv[2]
print(" ".join(new if w == old else w for w in sys.argv[3:]))
