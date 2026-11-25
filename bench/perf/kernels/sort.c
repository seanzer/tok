#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
static int cmp(const void* a, const void* b) {
  int64_t x = *(const int64_t*)a, y = *(const int64_t*)b;
  return (x > y) - (x < y);
}
int main(int argc, char** argv) {
  int64_t n = argc > 1 ? atoll(argv[1]) : 500000, rounds = argc > 2 ? atoll(argv[2]) : 3, total = 0, x = 12345;
  for (int64_t r = 1; r <= rounds; r++) {
    int64_t* xs = malloc((size_t)n * sizeof *xs);
    for (int64_t i = 0; i < n; i++) { x = (x * 1103515245 + 12345) % 2147483648LL; xs[i] = x; }
    qsort(xs, (size_t)n, sizeof *xs, cmp);
    total += xs[0] + xs[n / 2] + xs[n - 1];
    free(xs);
  }
  printf("%lld\n", (long long)total);
  return 0;
}
