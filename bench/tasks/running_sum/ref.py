import sys
from itertools import accumulate

print(*accumulate(int(a) for a in sys.argv[1:]))
