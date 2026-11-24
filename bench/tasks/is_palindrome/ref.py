import sys

s = sys.argv[1].lower()
print("yes" if s == s[::-1] else "no")
