import sys

print(max(sys.argv[1:], key=lambda a: int(a.split(":")[1])).split(":")[0])
