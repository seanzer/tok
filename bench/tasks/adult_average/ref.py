import sys

ages = [int(a.split(":")[1]) for a in sys.argv[1:]]
adults = [age for age in ages if age >= 18]
print(sum(adults) // len(adults) if adults else "none")
