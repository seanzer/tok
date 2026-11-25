#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
// Same shape as the Tok program: every line and every field is copied into its own string.
static char* dup_n(const char* s, size_t n) { char* r = malloc(n + 1); memcpy(r, s, n); r[n] = 0; return r; }
int main(int argc, char** argv) {
  int64_t n = argc > 1 ? atoll(argv[1]) : 20000, rounds = argc > 2 ? atoll(argv[2]) : 15, total = 0;
  size_t len = 0, cap = 1024;
  char* text = malloc(cap);
  for (int64_t i = 1; i <= n; i++) {
    char row[96];
    int k = snprintf(row, sizeof row, "%sid%lld,name%lld,%lld,end", i > 1 ? "\n" : "", (long long)i, (long long)i, (long long)(i % 97));
    if (len + (size_t)k + 1 > cap) { while (len + (size_t)k + 1 > cap) cap *= 2; text = realloc(text, cap); }
    memcpy(text + len, row, (size_t)k + 1);
    len += (size_t)k;
  }
  for (int64_t r = 1; r <= rounds; r++) {
    const char* p = text;
    while (*p) {
      const char* e = strchr(p, '\n');
      size_t ll = e ? (size_t)(e - p) : strlen(p);
      char* line = dup_n(p, ll);
      char* fields[8];
      int nf = 0;
      const char* q = line;
      for (;;) {
        const char* c = strchr(q, ',');
        if (!c) { fields[nf++] = dup_n(q, strlen(q)); break; }
        fields[nf++] = dup_n(q, (size_t)(c - q));
        q = c + 1;
      }
      total += atoll(fields[2]) + (int64_t)strlen(fields[1]);
      for (int i = 0; i < nf; i++) free(fields[i]);
      free(line);
      p += ll;
      if (*p == '\n') p++;
    }
  }
  free(text);
  printf("%lld\n", (long long)total);
  return 0;
}
