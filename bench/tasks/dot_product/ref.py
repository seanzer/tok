import sys

nums = [int(a) for a in sys.argv[1:]]
half = len(nums) // 2
print(sum(a * b for a, b in zip(nums[:half], nums[half:])))
