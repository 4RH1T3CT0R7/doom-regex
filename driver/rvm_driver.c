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

typedef struct {
    Rule rules[MAX_RULES];
    int n_rules;
    char *state;    /* буфер A (NUL-терминирован) */
    size_t len, cap;
    char *scratch;  /* буфер B */
    size_t scap;
    pcre2_match_context *mctx;
    pcre2_jit_stack *jstack;
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
    char *save = NULL;
    char *line = strtok_r(text, "\n", &save);
    if (!line || strcmp(line, "#rgxset/1") != 0) die("не .rgxset v1");
    line = strtok_r(NULL, "\n", &save);
    if (!line || strncmp(line, "#sha256:", 8) != 0) die("нет sha256");
    /* хэш проверяется Python-тулингом при генерации; TODO: sha256 и здесь */

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
        for (;;) {
            PCRE2_SIZE outlen = vm->scap;
            int rc = pcre2_substitute(
                r->code, (PCRE2_SPTR)vm->state, vm->len, 0,
                PCRE2_SUBSTITUTE_GLOBAL | PCRE2_SUBSTITUTE_EXTENDED |
                PCRE2_SUBSTITUTE_OVERFLOW_LENGTH,
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
    const char *rules_path = NULL, *state_path = NULL, *save_final = NULL;
    long long max_passes = -1;
    long long trace_every = 0;
    int quiet = 0;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--selftest")) return selftest();
        else if (!strcmp(argv[i], "--rules") && i + 1 < argc) rules_path = argv[++i];
        else if (!strcmp(argv[i], "--state") && i + 1 < argc) state_path = argv[++i];
        else if (!strcmp(argv[i], "--save-final") && i + 1 < argc) save_final = argv[++i];
        else if (!strcmp(argv[i], "--max-passes") && i + 1 < argc) max_passes = atoll(argv[++i]);
        else if (!strcmp(argv[i], "--trace-every") && i + 1 < argc) trace_every = atoll(argv[++i]);
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

    load_rules(&vm, rules_path);
    load_state(&vm, state_path);

    double t0 = now_sec();
    const char *reason = "fixpoint-run";
    const char *applied = NULL;

    for (;;) {
        if (max_passes >= 0 && (long long)vm.passes >= max_passes) {
            reason = "max-passes";
            break;
        }
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
                    vm.passes, (double)vm.passes / dt, vm.len, applied);
        }
        if (strstr(vm.state, "|ST:hlt")) { reason = "hlt"; break; }
        if (strstr(vm.state, "|ST:err")) { reason = "err"; break; }
    }

    double dt = now_sec() - t0;
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
