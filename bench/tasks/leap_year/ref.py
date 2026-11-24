import sys

y = int(sys.argv[1])
print("leap" if y % 400 == 0 or (y % 4 == 0 and y % 100 != 0) else "common")
