#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
int main(int argc, char** argv) {
  int64_t n = argc > 1 ? atoll(argv[1]) : 2500, rounds = argc > 2 ? atoll(argv[2]) : 10, total = 0;
  for (int64_t r = 1; r <= rounds; r++) {
    size_t len = 0, cap = 16;
    char* s = malloc(cap);
    s[0] = 0;
    for (int64_t i = 1; i <= n; i++) {
      char piece[32];
      int k = snprintf(piece, sizeof piece, "w%lld ", (long long)i);
      if (len + (size_t)k + 1 > cap) { while (len + (size_t)k + 1 > cap) cap *= 2; s = realloc(s, cap); }
      memcpy(s + len, piece, (size_t)k + 1);
      len += (size_t)k;
    }
    total += (int64_t)len;
    free(s);
  }
  printf("%lld\n", (long long)total);
  return 0;
}
