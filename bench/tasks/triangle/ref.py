import sys

a, b, c = (int(x) for x in sys.argv[1:4])
if a + b <= c or a + c <= b or b + c <= a:
    print("invalid")
elif a == b == c:
    print("equilateral")
elif a == b or b == c or a == c:
    print("isosceles")
else:
    print("scalene")
