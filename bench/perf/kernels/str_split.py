import sys
n = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 15
text = "\n".join(f"id{i},name{i},{i % 97},end" for i in range(1, n + 1))
total = 0
for r in range(rounds):
    for line in text.splitlines():
        fields = line.split(",")
        total += int(fields[2]) + len(fields[1])
print(total)
