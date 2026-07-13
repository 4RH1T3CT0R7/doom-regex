/* rvm_driver.c — продакшн-драйвер regex-машины (PCRE2, JIT).
 *
 * ЧЕСТНОСТЬ (см. HONESTY.md): драйвер — «материнская плата», не CPU.
 * Разрешено ТОЛЬКО:
 *   - применять правила подстановки по алгоритму Маркова (первое
 *     совпавшее правило фиксированного упорядоченного набора);
 *   - литеральный поиск |ST:hlt / |ST:err для останова;
 *   - копировать hex-содержимое зоны |OUT: наружу (транскод = UART);
 *   - (позже) литеральный splice входных байтов после |IN:.
 * Запрещено: разбор состояния, арифметика над ним, знание о программе.
 * Аудит-инвариант: единственные вызовы, читающие состояние, — это
 * pcre2_substitute, strstr по литеральным маркерам и hex-транскод OUT.
 *
 * Сборка: scripts/build_driver.sh (gcc + статический libpcre2-8, LINK_SIZE=4).
 */
#define PCRE2_CODE_UNIT_WIDTH 8
#include <pcre2.h>
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define MAX_RULES 4096

typedef struct {
    char *name;
    pcre2_code *code;
    char *repl;
    size_t repl_len;
} Rule;

/* --- SHA-256 (компактная реализация по FIPS 180-4) --------------------- */

typedef struct { unsigned int h[8]; unsigned long long n; unsigned char buf[64]; size_t fill; } Sha256;

static const unsigned int SHA_K[64] = {
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};

#define ROR(x,r) (((x) >> (r)) | ((x) << (32 - (r))))

static void sha_block(Sha256 *s, const unsigned char *p) {
    unsigned int w[64], a, b, c, d, e, f, g, h;
    for (int i = 0; i < 16; i++)
        w[i] = (unsigned)p[4*i] << 24 | (unsigned)p[4*i+1] << 16
             | (unsigned)p[4*i+2] << 8 | p[4*i+3];
    for (int i = 16; i < 64; i++) {
        unsigned int s0 = ROR(w[i-15],7) ^ ROR(w[i-15],18) ^ (w[i-15] >> 3);
        unsigned int s1 = ROR(w[i-2],17) ^ ROR(w[i-2],19) ^ (w[i-2] >> 10);
        w[i] = w[i-16] + s0 + w[i-7] + s1;
    }
    a=s->h[0]; b=s->h[1]; c=s->h[2]; d=s->h[3];
    e=s->h[4]; f=s->h[5]; g=s->h[6]; h=s->h[7];
    for (int i = 0; i < 64; i++) {
        unsigned int S1 = ROR(e,6) ^ ROR(e,11) ^ ROR(e,25);
        unsigned int ch = (e & f) ^ (~e & g);
        unsigned int t1 = h + S1 + ch + SHA_K[i] + w[i];
        unsigned int S0 = ROR(a,2) ^ ROR(a,13) ^ ROR(a,22);
        unsigned int mj = (a & b) ^ (a & c) ^ (b & c);
        unsigned int t2 = S0 + mj;
        h=g; g=f; f=e; e=d+t1; d=c; c=b; b=a; a=t1+t2;
    }
    s->h[0]+=a; s->h[1]+=b; s->h[2]+=c; s->h[3]+=d;
    s->h[4]+=e; s->h[5]+=f; s->h[6]+=g; s->h[7]+=h;
}

static void sha256_hex(const unsigned char *data, size_t len, char out[65]) {
    Sha256 s = {{0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,
                 0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19}, 0, {0}, 0};
    size_t i = 0;
    for (; i + 64 <= len; i += 64) sha_block(&s, data + i);
    unsigned char tail[128] = {0};
    size_t rest = len - i;
    memcpy(tail, data + i, rest);
    tail[rest] = 0x80;
    size_t tlen = (rest < 56) ? 64 : 128;
    unsigned long long bits = (unsigned long long)len * 8;
    for (int j = 0; j < 8; j++)
        tail[tlen - 1 - j] = (unsigned char)(bits >> (8 * j));
    sha_block(&s, tail);
    if (tlen == 128) sha_block(&s, tail + 64);
    for (int j = 0; j < 8; j++)
        sprintf(out + 8 * j, "%08x", s.h[j]);
}

typedef struct {
    Rule rules[MAX_RULES];
    int n_rules;
    char *state;    /* буфер A (NUL-терминирован) */
    size_t len, cap;
    char *scratch;  /* буфер B */
    size_t scap;
    pcre2_match_context *mctx;
    pcre2_jit_stack *jstack;
    pcre2_match_data *probe_md;   /* быстрая проба «правило совпало?» */
    unsigned long long passes;
    size_t out_seen; /* сколько hex-символов OUT уже выведено */
} Vm;

static void die(const char *msg) {
    fprintf(stderr, "rvm: %s\n", msg);
    exit(2);
}

static char *read_file(const char *path, size_t *out_len) {
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "rvm: cannot open %s\n", path); exit(2); }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buf = malloc((size_t)n + 1);
    if (!buf) die("oom");
    if (fread(buf, 1, (size_t)n, f) != (size_t)n) die("short read");
    fclose(f);
    buf[n] = 0;
    *out_len = (size_t)n;
    return buf;
}

/* --- загрузка .rgxset ------------------------------------------------- */

static void strip_cr(char *line) {
    size_t n = strlen(line);
    if (n && line[n - 1] == '\r') line[n - 1] = 0;
}

static void load_rules(Vm *vm, const char *path) {
    size_t n;
    char *text = read_file(path, &n);

    /* Верификация целостности ДО разбора: sha256 сырого тела правил
     * (всё после второй \n) обязан совпасть с заявленным в заголовке.
     * Файл обязан быть байт-точным LF (.gitattributes: *.rgxset -text);
     * CRLF-конверсия/подмена правил => mismatch => отказ. */
    char *nl1 = memchr(text, '\n', n);
    char *nl2 = nl1 ? memchr(nl1 + 1, '\n', n - (size_t)(nl1 + 1 - text)) : NULL;
    if (!nl1 || !nl2) die("повреждённый заголовок .rgxset");
    char actual[65];
    sha256_hex((unsigned char *)(nl2 + 1), n - (size_t)(nl2 + 1 - text), actual);

    char *save = NULL;
    char *line = strtok_r(text, "\n", &save);
    if (line) strip_cr(line);
    if (!line || strcmp(line, "#rgxset/1") != 0) die("не .rgxset v1");
    line = strtok_r(NULL, "\n", &save);
    if (line) strip_cr(line);
    if (!line || strncmp(line, "#sha256:", 8) != 0) die("нет sha256");
    if (strlen(line + 8) != 64 || memcmp(line + 8, actual, 64) != 0)
        die("sha256 mismatch: правила подменены или файл повреждён (CRLF?)");

    pcre2_compile_context *cctx = pcre2_compile_context_create(NULL);
    pcre2_set_parens_nest_limit(cctx, 10000);

    while ((line = strtok_r(NULL, "\n", &save)) != NULL) {
        strip_cr(line);
        if (!*line) continue;
        if (strncmp(line, "#rule ", 6) != 0) die("ожидалась '#rule'");
        if (vm->n_rules >= MAX_RULES) die("слишком много правил");
        Rule *r = &vm->rules[vm->n_rules];
        r->name = strdup(line + 6);

        char *pat = strtok_r(NULL, "\n", &save);
        char *rep = strtok_r(NULL, "\n", &save);
        if (!pat || !rep || strncmp(pat, "P:", 2) || strncmp(rep, "R:", 2))
            die("ожидались строки P:/R:");
        strip_cr(pat); strip_cr(rep);
        /* Верность модели Маркова: GLOBAL-substitute == одна левейшая
         * замена ТОЛЬКО для \A-заякоренных паттернов — требуем якорь. */
        if (strncmp(pat + 2, "\\A", 2) != 0)
            die("правило без \\A-якоря нарушает модель одной замены");

        int errcode; PCRE2_SIZE erroff;
        r->code = pcre2_compile((PCRE2_SPTR)(pat + 2), PCRE2_ZERO_TERMINATED,
                                0, &errcode, &erroff, cctx);
        if (!r->code) {
            PCRE2_UCHAR msg[256];
            pcre2_get_error_message(errcode, msg, sizeof msg);
            fprintf(stderr, "rvm: правило '%s' не компилируется @%zu: %s\n",
                    r->name, (size_t)erroff, (char *)msg);
            exit(2);
        }
        if (pcre2_jit_compile(r->code, PCRE2_JIT_COMPLETE) != 0)
            fprintf(stderr, "rvm: warning: JIT недоступен для '%s'\n", r->name);
        r->repl = strdup(rep + 2);
        r->repl_len = strlen(r->repl);
        vm->n_rules++;
    }
    pcre2_compile_context_free(cctx);
    free(text);
}

/* --- загрузка/сохранение .rvstate ------------------------------------- */

static void load_state(Vm *vm, const char *path) {
    size_t n;
    char *raw = read_file(path, &n);
    char *nl = memchr(raw, '\n', n);
    if (!nl) die("нет заголовка .rvstate");
    size_t body = n - (size_t)(nl + 1 - raw);
    vm->cap = body * 2 + 4096;
    vm->state = malloc(vm->cap);
    vm->scap = vm->cap;
    vm->scratch = malloc(vm->scap);
    if (!vm->state || !vm->scratch) die("oom");
    memcpy(vm->state, nl + 1, body);
    vm->state[body] = 0;
    vm->len = body;
    free(raw);
}

static void save_state(const Vm *vm, const char *path) {
    FILE *f = fopen(path, "wb");
    if (!f) die("cannot write final state");
    fprintf(f, "{\"fmt\": \"rvstate/1\", \"pass\": %llu, \"len\": %zu, "
               "\"sha256\": null}\n", vm->passes, vm->len);
    fwrite(vm->state, 1, vm->len, f);
    fclose(f);
}

/* --- один проход Маркова: первое совпавшее правило -------------------- */

static int markov_pass(Vm *vm, const char **applied) {
    for (int i = 0; i < vm->n_rules; i++) {
        Rule *r = &vm->rules[i];
        /* Быстрая проба: pcre2_substitute при НЕсовпадении всё равно
         * готовит полную копию subject (десятки МБ) — при сотнях правил
         * это доминирует в проходе. pcre2_match с \A-якорем отказывает
         * за O(1) без копий; substitute зовём только по факту матча.
         * Семантика подстановки не меняется (тот же паттерн). */
        if (pcre2_match(r->code, (PCRE2_SPTR)vm->state, vm->len, 0,
                        0, vm->probe_md, vm->mctx) < 0)
            continue;
        for (;;) {
            PCRE2_SIZE outlen = vm->scap;
            /* Без SUBSTITUTE_EXTENDED: ${n} поддержан и в базовом режиме,
             * а backslash в replacement остаётся литеральным. EXTENDED
             * включать только осознанно (ревью G0, finding ccode-2). */
            int rc = pcre2_substitute(
                r->code, (PCRE2_SPTR)vm->state, vm->len, 0,
                PCRE2_SUBSTITUTE_GLOBAL | PCRE2_SUBSTITUTE_OVERFLOW_LENGTH,
                NULL, vm->mctx, (PCRE2_SPTR)r->repl, r->repl_len,
                (PCRE2_UCHAR *)vm->scratch, &outlen);
            if (rc == PCRE2_ERROR_NOMEMORY) {
                vm->scap = outlen + 4096;
                vm->scratch = realloc(vm->scratch, vm->scap);
                if (!vm->scratch) die("oom scratch");
                continue;
            }
            if (rc < 0) {
                PCRE2_UCHAR msg[256];
                pcre2_get_error_message(rc, msg, sizeof msg);
                fprintf(stderr, "rvm: substitute '%s' failed: %s\n",
                        r->name, (char *)msg);
                exit(2);
            }
            if (rc == 0) break;      /* правило не совпало — следующее */
            /* применилось: swap буферов */
            char *t = vm->state; vm->state = vm->scratch; vm->scratch = t;
            size_t tc = vm->cap; vm->cap = vm->scap; vm->scap = tc;
            vm->len = outlen;
            vm->state[vm->len] = 0;
            *applied = r->name;
            return 1;
        }
    }
    return 0; /* фикс-точка */
}

/* --- FB: честная копия зоны #F в файл (атомарно через rename) ---------- */

#ifdef _WIN32
#include <windows.h>
#endif

static void export_fb(const Vm *vm, const char *path) {
    const char *z = strstr(vm->state, "#F");
    if (!z) return;
    const char *end = strchr(z + 2, '#');
    if (!end) return;
    char tmp[1024];
    if (snprintf(tmp, sizeof tmp, "%s.tmp", path) >= (int)sizeof tmp) {
        fprintf(stderr, "rvm: слишком длинный --fb-file путь\n");
        return;
    }
    FILE *f = fopen(tmp, "wb");
    if (!f) return;
    fprintf(f, "%llu\n", vm->passes);
    fwrite(z + 2, 1, (size_t)(end - z - 2), f);
    fclose(f);
#ifdef _WIN32
    MoveFileExA(tmp, path, MOVEFILE_REPLACE_EXISTING);  /* атомарно */
#else
    rename(tmp, path);
#endif
}

/* --- ввод: literal-splice hex-байтов в ХВОСТ зоны |IN: (FIFO) ----------- */

typedef struct { const char *path; long pos; FILE *journal; } InTail;

static void splice_in_hex(Vm *vm, const char *hex, size_t hexlen) {
    char *tag = strstr(vm->state, "|IN:");
    if (!tag) return;
    char *zone_end = strchr(tag + 4, '|');       /* хвост очереди (FIFO) */
    if (!zone_end) return;
    size_t off = (size_t)(zone_end - vm->state);
    size_t need = vm->len + hexlen + 1;
    if (need > vm->cap) {
        vm->cap = need + 4096;
        vm->state = realloc(vm->state, vm->cap);
        if (!vm->state) die("oom input");
    }
    memmove(vm->state + off + hexlen, vm->state + off, vm->len - off + 1);
    memcpy(vm->state + off, hex, hexlen);
    vm->len += hexlen;
}

static void inject_input(Vm *vm, InTail *t) {
    if (!t->path) return;
    FILE *f = fopen(t->path, "rb");
    if (!f) return;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    if (n <= t->pos) { fclose(f); return; }
    long cnt = n - t->pos;
    fseek(f, t->pos, SEEK_SET);
    unsigned char *raw = malloc((size_t)cnt);
    if (!raw || fread(raw, 1, (size_t)cnt, f) != (size_t)cnt) {
        free(raw); fclose(f); return;
    }
    fclose(f);
    t->pos = n;

    char *hex = malloc((size_t)cnt * 2 + 1);
    if (!hex) die("oom hex");
    static const char hexd[] = "0123456789abcdef";
    for (long i = 0; i < cnt; i++) {
        hex[i * 2] = hexd[raw[i] >> 4];
        hex[i * 2 + 1] = hexd[raw[i] & 0xF];
    }
    hex[cnt * 2] = 0;
    splice_in_hex(vm, hex, (size_t)cnt * 2);
    /* журнал детерминизма: (проход, hex) — прогон воспроизводим */
    if (t->journal) {
        fprintf(t->journal, "%llu %s\n", vm->passes, hex);
        fflush(t->journal);
    }
    free(hex);
    free(raw);
}

/* --- replay: повтор журнала инжекций байт-в-байт ------------------------ */

typedef struct { unsigned long long pass; char *hex; } InjectRec;

static InjectRec *load_journal(const char *path, int *out_n) {
    size_t n;
    char *text = read_file(path, &n);
    InjectRec *recs = NULL;
    int cnt = 0, cap = 0;
    char *save = NULL;
    for (char *line = strtok_r(text, "\n", &save); line;
         line = strtok_r(NULL, "\n", &save)) {
        strip_cr(line);
        if (!*line) continue;
        char *sp = strchr(line, ' ');
        if (!sp) die("кривой журнал инжекций");
        *sp = 0;
        if (cnt == cap) {
            cap = cap ? cap * 2 : 16;
            recs = realloc(recs, (size_t)cap * sizeof *recs);
            if (!recs) die("oom journal");
        }
        recs[cnt].pass = strtoull(line, NULL, 10);
        recs[cnt].hex = strdup(sp + 1);
        cnt++;
    }
    free(text);
    *out_n = cnt;
    return recs;
}

/* --- OUT: механический hex->байты транскод (аналог UART) --------------- */

static void echo_out(Vm *vm) {
    const char *tag = strstr(vm->state, "|OUT:");
    if (!tag) return;
    const char *p = tag + 5;
    const char *end = strchr(p, '|');
    if (!end) return;
    size_t hexlen = (size_t)(end - p);
    if (hexlen <= vm->out_seen) return;
    for (size_t i = vm->out_seen; i + 1 < hexlen; i += 2) {
        unsigned v;
        if (sscanf(p + i, "%2x", &v) == 1) fputc((int)v, stdout);
    }
    fflush(stdout);
    vm->out_seen = hexlen;
}

/* --- main --------------------------------------------------------------*/

static double now_sec(void) {
    /* clock() на Windows возвращает wall-время процесса — достаточно */
    return (double)clock() / (double)CLOCKS_PER_SEC;
}

static int selftest(void) {
    unsigned int linksize = 0, jit = 0;
    char ver[64];
    pcre2_config(PCRE2_CONFIG_LINKSIZE, &linksize);
    pcre2_config(PCRE2_CONFIG_JIT, &jit);
    pcre2_config(PCRE2_CONFIG_VERSION, ver);
    printf("pcre2 %s | LINKSIZE=%u | JIT=%u\n", ver, linksize, jit);
    if (linksize < 3) { fprintf(stderr, "rvm: LINKSIZE<3 — пересоберите PCRE2\n"); return 1; }
    if (!jit) { fprintf(stderr, "rvm: JIT выключен — пересоберите PCRE2\n"); return 1; }
    return 0;
}

int main(int argc, char **argv) {
#ifdef _WIN32
    _setmode(_fileno(stdout), _O_BINARY);   /* OUT-эхо — сырые байты */
#endif
    const char *rules_path = NULL, *state_path = NULL, *save_final = NULL;
    const char *fb_path = NULL;
    long long max_passes = -1;
    long long trace_every = 0, fb_every = 0, io_every = 64;
    int quiet = 0;
    InTail intail = {NULL, 0, NULL};
    const char *journal_path = NULL, *replay_path = NULL;
    InjectRec *replay = NULL;
    int replay_n = 0, replay_i = 0;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--selftest")) return selftest();
        else if (!strcmp(argv[i], "--rules") && i + 1 < argc) rules_path = argv[++i];
        else if (!strcmp(argv[i], "--state") && i + 1 < argc) state_path = argv[++i];
        else if (!strcmp(argv[i], "--save-final") && i + 1 < argc) save_final = argv[++i];
        else if (!strcmp(argv[i], "--max-passes") && i + 1 < argc) max_passes = atoll(argv[++i]);
        else if (!strcmp(argv[i], "--trace-every") && i + 1 < argc) trace_every = atoll(argv[++i]);
        else if (!strcmp(argv[i], "--fb-every") && i + 1 < argc) fb_every = atoll(argv[++i]);
        else if (!strcmp(argv[i], "--fb-file") && i + 1 < argc) fb_path = argv[++i];
        else if (!strcmp(argv[i], "--input-file") && i + 1 < argc) intail.path = argv[++i];
        else if (!strcmp(argv[i], "--io-journal") && i + 1 < argc) journal_path = argv[++i];
        else if (!strcmp(argv[i], "--replay") && i + 1 < argc) replay_path = argv[++i];
        else if (!strcmp(argv[i], "--io-every") && i + 1 < argc) io_every = atoll(argv[++i]);
        else if (!strcmp(argv[i], "--quiet")) quiet = 1;
        else { fprintf(stderr, "rvm: неизвестный аргумент %s\n", argv[i]); return 2; }
    }
    if (!rules_path || !state_path)
        die("использование: rvm --rules F.rgxset --state F.rvstate "
            "[--max-passes N] [--trace-every N] [--save-final F] [--selftest]");

    Vm vm = {0};
    vm.mctx = pcre2_match_context_create(NULL);
    pcre2_set_match_limit(vm.mctx, 100000000);
    pcre2_set_depth_limit(vm.mctx, 10000000);
    vm.jstack = pcre2_jit_stack_create(64 * 1024, 16 * 1024 * 1024, NULL);
    pcre2_jit_stack_assign(vm.mctx, NULL, vm.jstack);
    vm.probe_md = pcre2_match_data_create(64, NULL);
    if (!vm.probe_md) die("oom probe_md");

    load_rules(&vm, rules_path);
    load_state(&vm, state_path);
    if (journal_path) {
        intail.journal = fopen(journal_path, "wb");
        if (!intail.journal) die("не открыть --io-journal");
    }
    if (replay_path) replay = load_journal(replay_path, &replay_n);

    double t0 = now_sec();
    const char *reason = "fixpoint-run";
    const char *applied = NULL;

    for (;;) {
        if (max_passes >= 0 && (long long)vm.passes >= max_passes) {
            reason = "max-passes";
            break;
        }
        if (replay) {
            while (replay_i < replay_n && replay[replay_i].pass == vm.passes) {
                splice_in_hex(&vm, replay[replay_i].hex,
                              strlen(replay[replay_i].hex));
                replay_i++;
            }
        } else if (intail.path
                   && vm.passes % (unsigned long long)io_every == 0)
            inject_input(&vm, &intail);
        if (fb_every && fb_path
            && vm.passes % (unsigned long long)fb_every == 0)
            export_fb(&vm, fb_path);
        if (!markov_pass(&vm, &applied)) {
            if (strstr(vm.state, "|ST:hlt")) reason = "hlt";
            else if (strstr(vm.state, "|ST:err")) reason = "err";
            break;
        }
        vm.passes++;
        echo_out(&vm);
        if (trace_every && vm.passes % (unsigned long long)trace_every == 0) {
            double dt = now_sec() - t0;
            fprintf(stderr, "[pass %llu | %.0f pass/s | len %zu | %s]\n",
                    vm.passes, dt > 0 ? (double)vm.passes / dt : 0.0,
                    vm.len, applied);
        }
        if (strstr(vm.state, "|ST:hlt")) { reason = "hlt"; break; }
        if (strstr(vm.state, "|ST:err")) { reason = "err"; break; }
    }

    double dt = now_sec() - t0;
    if (fb_path) export_fb(&vm, fb_path);
    if (save_final) save_state(&vm, save_final);
    if (!quiet)
        fprintf(stderr, "\n-- %s | %llu passes | %.2fs | %.0f pass/s | final len %zu\n",
                reason, vm.passes, dt,
                dt > 0 ? (double)vm.passes / dt : 0.0, vm.len);

    if (!strcmp(reason, "hlt")) return 0;
    if (!strcmp(reason, "err")) {
        fprintf(stderr, "state head: %.120s\n", vm.state);
        return 3;
    }
    if (!strcmp(reason, "max-passes")) return 4;
    return 5; /* fixpoint при ST:run — нарушение тотальности правил */
}
