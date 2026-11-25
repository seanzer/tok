#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
typedef struct { int64_t* d; size_t len, cap; } vec;
static void push(vec* v, int64_t x) {
  if (v->len == v->cap) { v->cap = v->cap ? v->cap * 2 : 4; v->d = realloc(v->d, v->cap * sizeof *v->d); }
  v->d[v->len++] = x;
}
int main(int argc, char** argv) {
  int64_t n = argc > 1 ? atoll(argv[1]) : 1000000, rounds = argc > 2 ? atoll(argv[2]) : 8, total = 0;
  for (int64_t r = 1; r <= rounds; r++) {
    vec xs = {0}, zs = {0};
    for (int64_t i = 1; i <= n; i++) push(&xs, i + r);
    int64_t* ys = malloc(xs.len * sizeof *ys);
    for (size_t i = 0; i < xs.len; i++) ys[i] = xs.d[i] * 2 + 1;
    for (size_t i = 0; i < xs.len; i++) if (ys[i] % 3 == 0) push(&zs, ys[i]);
    int64_t s = 0;
    for (size_t i = 0; i < zs.len; i++) s += zs.d[i];
    total += s + (int64_t)zs.len;
    free(xs.d); free(ys); free(zs.d);
  }
  printf("%lld\n", (long long)total);
  return 0;
}
