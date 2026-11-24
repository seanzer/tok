import sys

nums = [int(a) for a in sys.argv[1:]]
print(max(nums) if nums else "none")
