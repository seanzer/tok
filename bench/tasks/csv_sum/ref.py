import sys

total = 0
for field in sys.argv[1].split(","):
    try:
        total += int(field.strip())
    except ValueError:
        pass
print(total)
