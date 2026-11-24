import sys

seen = []
for a in sys.argv[1:]:
    if a not in seen:
        seen.append(a)
print(" ".join(seen))
