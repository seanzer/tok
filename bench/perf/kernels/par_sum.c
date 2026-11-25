// OpenMP reference.
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
int main(int argc, char** argv) {
  int64_t n = argc > 1 ? atoll(argv[1]) : 30000000, rounds = argc > 2 ? atoll(argv[2]) : 4;
  int64_t total = 0;
  for (int64_t r = 1; r <= rounds; r++) {
    int64_t s = 0;
    #pragma omp parallel for reduction(+:s) schedule(static)
    for (int64_t i = 1; i <= n; i++) s += i * r % 7 + i % 3;
    total += s;
  }
  printf("%lld\n", (long long)total);
  return 0;
}
