import sys

# The same value semantics as the Tok function: the caller's list is not changed.
def with_item(xs, v):
    ys = list(xs)
    ys.append(v)
    return ys

n = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
xs = []
for i in range(1, n + 1):
    xs = with_item(xs, i * 3)
print(sum(xs))
