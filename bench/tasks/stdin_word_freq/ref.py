import sys
from collections import Counter

counts = Counter(sys.stdin.read().split())
if counts:
    print(*counts.most_common(1)[0])
