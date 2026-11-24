import sys

print(sum(c in "aeiou" for c in sys.argv[1].lower()))
