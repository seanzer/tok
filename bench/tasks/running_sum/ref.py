import sys

total = 0
out = []
for a in sys.argv[1:]:
    total += int(a)
    out.append(str(total))
print(" ".join(out))
