import sys
from dataclasses import dataclass

@dataclass
class Body:
    x: float
    y: float
    vx: float
    vy: float
    name: str

def step(bs, dt):
    return [Body(b.x + b.vx * dt, b.y + b.vy * dt, b.vx * 0.999, b.vy - 9.8 * dt, b.name) for b in bs]

n = int(sys.argv[1]) if len(sys.argv) > 1 else 50000
steps = int(sys.argv[2]) if len(sys.argv) > 2 else 40
bs = [Body(0.0, 0.0, float(i % 17), float(i % 5), f"b{i % 100}") for i in range(1, n + 1)]
for s in range(steps):
    bs = step(bs, 0.01)
total = 0.0
chars = 0
for b in bs:
    total = total + b.x + b.y
    chars += len(b.name)
print(int(total))
print(chars)
