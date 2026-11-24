import sys

words = sys.argv[1:]
print("yes" if words == words[::-1] else "no")
