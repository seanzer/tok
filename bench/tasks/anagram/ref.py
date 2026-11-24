import sys

print("yes" if sorted(sys.argv[1]) == sorted(sys.argv[2]) else "no")
