import sys

best_name, best_age = None, -1
for a in sys.argv[1:]:
    name, age = a.split(":")
    if int(age) > best_age:
        best_name, best_age = name, int(age)
print(best_name)
