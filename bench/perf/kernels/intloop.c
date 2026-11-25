#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
int main(int argc, char** argv) {
  int64_t n = argc > 1 ? atoll(argv[1]) : 60000000;
  int64_t s = 0;
  for (int64_t i = 1; i <= n; i++) s = s + i % 7 * (i % 13) - i / 3 % 5;
  printf("%lld\n", (long long)s);
  return 0;
}
