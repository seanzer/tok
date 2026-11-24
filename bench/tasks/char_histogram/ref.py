import sys
from collections import Counter

for c, n in Counter(sys.argv[1]).items():
    print(c, n)
