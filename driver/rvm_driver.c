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
#include <windows.h>
#endif

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define MAX_RULES 4096

static double now_sec(void);

typedef struct { const char *lit; size_t n; int group; } RepTok;
/* group >= 0 -> подстановка группы; group < 0 -> литерал [lit, lit+n) */

typedef struct {
    char *name;
    pcre2_code *code;
    char *repl;
    size_t repl_len;
    RepTok toks[160];
    int n_toks;
    double t_probe;              /* --rule-stats */
    unsigned long long n_probe, n_hit;
    unsigned long long n_skip, skip_bytes;   /* identity-skip статистика */
} Rule;

static int g_rule_stats = 0;

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
    pcre2_match_data *splice_md;  /* полный ovector для in-place splice */
    int pure;                     /* эталонный режим: полный substitute */
    char *rbuf;                   /* буфер рендера replacement */
    size_t rcap;
    unsigned long long passes;
    size_t out_seen; /* сколько hex-символов OUT уже выведено */
    size_t out_off;  /* кэш смещения "|OUT:" (v2.0: OUT в хвосте) */
    size_t prev_len;             /* длина строки до последней замены */
    /* live-лента v2.0: окно последней замены считается из сегментов
     * identity-skip ДО записи — полная scratch-копия mend байт на
     * каждый проход (при store_n это 83МБ) больше не нужна */
    size_t live_d;               /* позиция первого содержательного диффа */
    size_t live_w0;              /* начало окна */
    int live_blen;               /* длина сохранённого "до" */
    char live_before[200];
    const char *live_dir;        /* live-экспорт для вьювера (fb + лента) */
    long long live_every;
    const char *fbseq_dir;       /* G2e: экспорт #F по байту  в OUT */
    int fbseq;
    int fbseq_stop;              /* сегмент клипа: стоп после N кадров */
    double t_start;
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
        /* PCRE2_ANCHORED: авто-якорение по \A здесь не срабатывает, и
         * провальный probe платит bump-along по всей строке (~185мкс на
         * 90МБ вместо O(1)). Все правила \A-якорные (проверено выше) —
         * флаг компиляции семантику не меняет, а JIT остаётся в деле
         * (match-время ANCHORED отключало бы JIT-код). */
        r->code = pcre2_compile((PCRE2_SPTR)(pat + 2), PCRE2_ZERO_TERMINATED,
                                PCRE2_ANCHORED, &errcode, &erroff, cctx);
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
        /* O2 in-place splice: токенизация replacement (${имя} -> номер
         * группы через таблицу имён паттерна) */
        r->n_toks = 0;
        {
            const char *p2 = r->repl, *endp = r->repl + r->repl_len;
            while (p2 < endp) {
                if (p2[0] == '$' && p2[1] == '{') {
                    const char *close = strchr(p2 + 2, '}');
                    if (!close) die("кривой ${...} в replacement");
                    char gname[64];
                    size_t gl = (size_t)(close - p2 - 2);
                    if (gl >= sizeof gname) die("длинное имя группы");
                    memcpy(gname, p2 + 2, gl); gname[gl] = 0;
                    int gn;
                    if (gl > 0 && strspn(gname, "0123456789") == gl) {
                        /* номерная группа ${N} (BF-набор) */
                        uint32_t ncap = 0;
                        pcre2_pattern_info(r->code, PCRE2_INFO_CAPTURECOUNT,
                                           &ncap);
                        gn = atoi(gname);
                        if (gn < 1 || (uint32_t)gn > ncap) {
                            fprintf(stderr, "rvm: правило '%s': нет группы "
                                    "номер %s\n", r->name, gname);
                            exit(2);
                        }
                    } else {
                        gn = pcre2_substring_number_from_name(
                            r->code, (PCRE2_SPTR)gname);
                        if (gn < 0) {
                            fprintf(stderr, "rvm: правило '%s': нет группы "
                                    "'%s' в паттерне\n", r->name, gname);
                            exit(2);
                        }
                    }
                    if (r->n_toks >= 160) die("много токенов");
                    r->toks[r->n_toks].lit = NULL;
                    r->toks[r->n_toks].n = 0;
                    r->toks[r->n_toks++].group = gn;
                    p2 = close + 1;
                } else {
                    const char *lit0 = p2;
                    while (p2 < endp && !(p2[0] == '$' && p2[1] == '{'))
                        p2++;
                    if (r->n_toks >= 160) die("много токенов");
                    r->toks[r->n_toks].lit = lit0;
                    r->toks[r->n_toks].n = (size_t)(p2 - lit0);
                    r->toks[r->n_toks++].group = -1;
                }
            }
        }
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

static int splice_apply(Vm *vm, Rule *r) {
    /* O2 (план): in-place splice. С \A-якорем матч ровно один и
     * начинается с нуля => substitute эквивалентен замене префикса
     * [0, end) на рендер replacement из групп. Длиносохраняющие замены
     * (подавляющее большинство) не трогают 90МБ-хвост вообще.
     * Эталонный pure-режим (--pure) сверяется тестами драйверов. */
    int rc = pcre2_match(r->code, (PCRE2_SPTR)vm->state, vm->len, 0,
                         0, vm->splice_md, vm->mctx);
    if (rc < 0) {
        /* Громкий трипваер (HONESTY): лимит-ошибки (MATCHLIMIT,
         * DEPTHLIMIT, JIT_STACKLIMIT...) НЕ равны «не совпало» —
         * тихая переинтерпретация меняет семантику машины. */
        if (rc != PCRE2_ERROR_NOMATCH) {
            PCRE2_UCHAR msg[256];
            pcre2_get_error_message(rc, msg, sizeof msg);
            fprintf(stderr, "rvm: правило '%s': pcre2_match ошибка %d: %s\n",
                    r->name, rc, (char *)msg);
            exit(4);
        }
        return 0;
    }
    if (rc == 0) {
        /* ovector мал — токены читали бы мимо захватов: тихая порча */
        fprintf(stderr, "rvm: правило '%s': ovector переполнен\n", r->name);
        exit(4);
    }
    PCRE2_SIZE *ov = pcre2_get_ovector_pointer(vm->splice_md);
    size_t mend = ov[1];
    /* Сегментированный рендер с IDENTITY-SKIP: group-токен, чей
     * источник уже стоит на своём dest-смещении (ov[2g] == out — т.е.
     * суммарный сдвиг к этой точке нулевой), не рендерится и не
     * копируется. Даёт O(1) замены с гигантскими ${pre}/${mid}
     * (store_n, dspan/dcol v2.0) и сам отклоняет MF-фазы, где источник
     * сдвинут на ширину |MF:. Проверка — сравнение СМЕЩЕНИЙ, не
     * содержимого (honesty); эталон — pure-режим, срабатывание скипа
     * видно в --rule-stats (skip-счётчики). */
    enum { MAX_SEGS = 168 };
    struct Seg { size_t dst, rb0, len; } segs[MAX_SEGS];
    int n_segs = 0;
    size_t out = 0, rb = 0, seg_dst = 0, seg_rb0 = 0;
    for (int k = 0; k < r->n_toks; k++) {
        RepTok *tk = &r->toks[k];
        const char *src; size_t n;
        if (tk->group < 0) { src = tk->lit; n = tk->n; }
        else {
            PCRE2_SIZE a = ov[2 * tk->group], b = ov[2 * tk->group + 1];
            if (a == PCRE2_UNSET) { src = ""; n = 0; }
            else {
                src = vm->state + a;
                n = (size_t)(b - a);
                if ((size_t)a == out && n) {   /* байты уже на месте */
                    if (rb > seg_rb0) {
                        if (n_segs >= MAX_SEGS) die("MAX_SEGS");
                        segs[n_segs].dst = seg_dst;
                        segs[n_segs].rb0 = seg_rb0;
                        segs[n_segs++].len = rb - seg_rb0;
                    }
                    out += n;
                    seg_dst = out;
                    seg_rb0 = rb;
                    r->n_skip++;
                    r->skip_bytes += n;
                    continue;
                }
            }
        }
        if (rb + n + 1 > vm->rcap) {
            vm->rcap = (rb + n) * 2 + 4096;
            vm->rbuf = realloc(vm->rbuf, vm->rcap);
            if (!vm->rbuf) die("oom rbuf");
        }
        memcpy(vm->rbuf + rb, src, n);
        rb += n;
        out += n;
    }
    if (rb > seg_rb0) {
        if (n_segs >= MAX_SEGS) die("MAX_SEGS");
        segs[n_segs].dst = seg_dst;
        segs[n_segs].rb0 = seg_rb0;
        segs[n_segs++].len = rb - seg_rb0;
    }
    /* live-лента: окно первой содержательной записи — из сегментов,
     * ДО их применения (старые байты ещё на месте). Заголовок (первые
     * ~220 симв) пульсирует всегда — предпочитаем дифф за ним. */
    if (vm->live_dir) {
        size_t d = 0; int found = 0;
        for (int pass2 = 0; pass2 < 2 && !found; pass2++) {
            size_t lo = pass2 == 0 ? 220 : 0;
            for (int s = 0; s < n_segs && !found; s++) {
                size_t dst = segs[s].dst, len2 = segs[s].len;
                for (size_t k = dst < lo ? lo - dst : 0; k < len2; k++) {
                    if (dst + k >= mend
                        || vm->state[dst + k] != vm->rbuf[segs[s].rb0 + k]) {
                        d = dst + k; found = 1; break;
                    }
                }
            }
        }
        vm->live_d = d;
        vm->live_w0 = d > 60 ? d - 60 : 0;
        size_t bl = 156;
        if (vm->live_w0 + bl > vm->len) bl = vm->len - vm->live_w0;
        if (bl > sizeof vm->live_before) bl = sizeof vm->live_before;
        memcpy(vm->live_before, vm->state + vm->live_w0, bl);
        vm->live_blen = (int)bl;
        vm->prev_len = vm->len;
    }
    if (out != mend) {
        /* скипы всегда ниже зоны сдвига: скип требует нулевого сдвига
         * до себя, а дельта возникает только после него */
        size_t newlen = vm->len - mend + out;
        if (newlen + 1 > vm->cap) {
            vm->cap = newlen + 65536;
            vm->state = realloc(vm->state, vm->cap);
            if (!vm->state) die("oom splice");
        }
        memmove(vm->state + out, vm->state + mend, vm->len - mend + 1);
        vm->len = newlen;
    }
    for (int s = 0; s < n_segs; s++)
        memcpy(vm->state + segs[s].dst, vm->rbuf + segs[s].rb0, segs[s].len);
    return 1;
}

static int markov_pass(Vm *vm, const char **applied) {
    for (int i = 0; i < vm->n_rules; i++) {
        Rule *r = &vm->rules[i];
        if (!vm->pure) {
            double t0 = g_rule_stats ? now_sec() : 0;
            int hit = splice_apply(vm, r);
            if (g_rule_stats) {
                r->t_probe += now_sec() - t0;
                r->n_probe++;
                if (hit) r->n_hit++;
            }
            if (hit) {
                *applied = r->name;
                return 1;
            }
            continue;
        }
        /* Быстрая проба: pcre2_substitute при НЕсовпадении всё равно
         * готовит полную копию subject (десятки МБ) — при сотнях правил
         * это доминирует в проходе. pcre2_match с \A-якорем отказывает
         * за O(1) без копий; substitute зовём только по факту матча.
         * Семантика подстановки не меняется (тот же паттерн). */
        int prc = pcre2_match(r->code, (PCRE2_SPTR)vm->state, vm->len, 0,
                              0, vm->probe_md, vm->mctx);
        if (prc < 0) {
            if (prc != PCRE2_ERROR_NOMATCH) {
                PCRE2_UCHAR msg[256];
                pcre2_get_error_message(prc, msg, sizeof msg);
                fprintf(stderr, "rvm: правило '%s': pcre2_match ошибка "
                        "%d: %s\n", r->name, prc, (char *)msg);
                exit(4);
            }
            continue;
        }
        for (;;) {
            PCRE2_SIZE outlen = vm->scap;
            /* Без SUBSTITUTE_EXTENDED: ${n} поддержан и в базовом режиме,
             * а backslash в replacement остаётся литеральным. EXTENDED
             * включать только осознанно (ревью G0, finding ccode-2). */
            int rc = pcre2_substitute(
                r->code, (PCRE2_SPTR)vm->state, vm->len, 0,
                PCRE2_SUBSTITUTE_GLOBAL | PCRE2_SUBSTITUTE_OVERFLOW_LENGTH
                    | PCRE2_SUBSTITUTE_UNSET_EMPTY,
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
            vm->prev_len = vm->len;
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

/* --- live-экспорт: fb + лента подстановок для вьювера демо -------------
 * Честность: только КОПИИ подстрок состояния наружу (как export_fb).
 * Дифф старой/новой строки — вычисление НАД ЭКСПОРТОМ, не над машиной. */

static void write_live(Vm *vm, const char *rule) {
    char path[1024], tmp[1040];
    /* fb */
    if (snprintf(path, sizeof path, "%s/fb.rvfb", vm->live_dir)
            < (int)sizeof path)
        export_fb(vm, path);
    /* лента: окно последней замены вычислено в splice_apply из
     * сегментов identity-skip (старые байты — в live_before) */
    size_t d = vm->live_d;
    size_t w0 = vm->live_w0;
    size_t wend_b = d + 96; if (wend_b > vm->len) wend_b = vm->len;
    size_t wend_a = w0 + (size_t)vm->live_blen;
    if (wend_a > d + 96) wend_a = d + 96;
    double el = now_sec() - vm->t_start;
    if (snprintf(path, sizeof path, "%s/live.json", vm->live_dir)
            >= (int)sizeof path) return;
    if (snprintf(tmp, sizeof tmp, "%s.tmp", path) >= (int)sizeof tmp) return;
    FILE *f = fopen(tmp, "wb");
    if (!f) return;
    fprintf(f, "{\"pass\": %llu, \"len\": %zu, \"pps\": %.2f, "
               "\"rule\": \"%s\", \"pos\": %zu, ",
            vm->passes, vm->len, el > 0 ? vm->passes / el : 0.0,
            rule ? rule : "", d);
    fprintf(f, "\"head\": \"%.120s\", ", vm->state);
    fprintf(f, "\"before\": \"%.*s\", ",
            (int)(wend_a > w0 ? wend_a - w0 : 0), vm->live_before);
    fprintf(f, "\"after\": \"%.*s\", \"w0\": %zu}",
            (int)(wend_b > w0 ? wend_b - w0 : 0), vm->state + w0, w0);
    fclose(f);
#ifdef _WIN32
    MoveFileExA(tmp, path, MOVEFILE_REPLACE_EXISTING);
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
    /* v2.0: OUT в хвосте строки — прямой strstr сканировал бы 90МБ на
     * КАЖДЫЙ проход. Кэшируем смещение метки (сдвигается только при
     * вставках перед хвостом — тогда обратный пере-скан от конца). */
    const char *tag = NULL;
    if (vm->out_off && vm->out_off + 5 <= vm->len
        && memcmp(vm->state + vm->out_off, "|OUT:", 5) == 0)
        tag = vm->state + vm->out_off;
    else {
        for (const char *q = vm->state + (vm->len >= 5 ? vm->len - 5 : 0);
             q >= vm->state; q--)
            if (q[0] == '|' && memcmp(q, "|OUT:", 5) == 0) { tag = q; break; }
        if (tag) vm->out_off = (size_t)(tag - vm->state);
    }
    if (!tag) return;
    const char *p = tag + 5;
    const char *end = strchr(p, '|');
    if (!end) return;
    size_t hexlen = (size_t)(end - p);
    if (hexlen <= vm->out_seen) return;
    for (size_t i = vm->out_seen; i + 1 < hexlen; i += 2) {
        unsigned v;
        if (sscanf(p + i, "%2x", &v) != 1) continue;
        fputc((int)v, stdout);
        if (v == 12 && vm->fbseq_dir) {
            /* G2e: form feed от машины = «кадр готов» -> копия #F */
            char fp[1024];
            if (snprintf(fp, sizeof fp, "%s/frame_%05d.rvfb",
                         vm->fbseq_dir, vm->fbseq++) < (int)sizeof fp)
                export_fb(vm, fp);
            if (vm->fbseq_stop && vm->fbseq >= vm->fbseq_stop) {
                /* лимит наблюдателя (как max-passes): сегмент готов */
                fprintf(stderr, "\n-- fb-seq-stop | %d кадров | %llu "
                        "passes\n", vm->fbseq, vm->passes);
                exit(0);
            }
        }
    }
    fflush(stdout);
    vm->out_seen = hexlen;
}

/* Литеральный поиск маркера останова. Поле |ST: всегда в голове
 * состояния (RVM1|ST:... / BF1|ST:...) — ищем в фикс-окне первых 24
 * байт: strstr по всей строке стоил 2x5мс на 90МБ КАЖДЫЙ проход
 * (~60% всего времени драйвера). Семантика честная: тот же
 * литеральный маркер, просто без скана хвоста. */
static const char *head_marker(const Vm *vm) {
    char head[25];
    size_t n = vm->len < 24 ? vm->len : 24;
    memcpy(head, vm->state, n);
    head[n] = 0;
    if (strstr(head, "|ST:hlt")) return "hlt";
    if (strstr(head, "|ST:err")) return "err";
    return NULL;
}

/* --- main --------------------------------------------------------------*/

static double now_sec(void) {
#ifdef _WIN32
    /* clock() тикает раз в ~16мс — пробы дешевле тика невидимы для
     * --rule-stats; QPC даёт наносекундное разрешение */
    static LARGE_INTEGER freq;
    LARGE_INTEGER t;
    if (!freq.QuadPart) QueryPerformanceFrequency(&freq);
    QueryPerformanceCounter(&t);
    return (double)t.QuadPart / (double)freq.QuadPart;
#else
    return (double)clock() / (double)CLOCKS_PER_SEC;
#endif
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
    const char *live_dir = NULL;
    const char *vm_fbseq_dir = NULL;
    int vm_fbseq_stop = 0;
    long long live_every = 25;
    long long save_every = 0;      /* чекпоинт состояния каждые N проходов */
    int pure = 0;
    InTail intail = {NULL, 0, NULL};
    const char *journal_path = NULL, *replay_path = NULL;
    InjectRec *replay = NULL;
    int replay_n = 0, replay_i = 0;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--selftest")) return selftest();
        else if (!strcmp(argv[i], "--rules") && i + 1 < argc) rules_path = argv[++i];
        else if (!strcmp(argv[i], "--state") && i + 1 < argc) state_path = argv[++i];
        else if (!strcmp(argv[i], "--save-final") && i + 1 < argc) save_final = argv[++i];
        else if (!strcmp(argv[i], "--save-every") && i + 1 < argc) save_every = atoll(argv[++i]);
        else if (!strcmp(argv[i], "--max-passes") && i + 1 < argc) max_passes = atoll(argv[++i]);
        else if (!strcmp(argv[i], "--trace-every") && i + 1 < argc) trace_every = atoll(argv[++i]);
        else if (!strcmp(argv[i], "--fb-every") && i + 1 < argc) fb_every = atoll(argv[++i]);
        else if (!strcmp(argv[i], "--fb-file") && i + 1 < argc) fb_path = argv[++i];
        else if (!strcmp(argv[i], "--input-file") && i + 1 < argc) intail.path = argv[++i];
        else if (!strcmp(argv[i], "--io-journal") && i + 1 < argc) journal_path = argv[++i];
        else if (!strcmp(argv[i], "--replay") && i + 1 < argc) replay_path = argv[++i];
        else if (!strcmp(argv[i], "--io-every") && i + 1 < argc) io_every = atoll(argv[++i]);
        else if (!strcmp(argv[i], "--pure")) pure = 1;
        else if (!strcmp(argv[i], "--rule-stats")) g_rule_stats = 1;
        else if (!strcmp(argv[i], "--live-dir") && i + 1 < argc) live_dir = argv[++i];
        else if (!strcmp(argv[i], "--fb-seq-dir") && i + 1 < argc)
            vm_fbseq_dir = argv[++i];
        else if (!strcmp(argv[i], "--fb-seq-stop") && i + 1 < argc)
            vm_fbseq_stop = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--live-every") && i + 1 < argc) live_every = atoll(argv[++i]);
        else if (!strcmp(argv[i], "--quiet")) quiet = 1;
        else { fprintf(stderr, "rvm: неизвестный аргумент %s\n", argv[i]); return 2; }
    }
    if (!rules_path || !state_path)
        die("использование: rvm --rules F.rgxset --state F.rvstate "
            "[--max-passes N] [--trace-every N] [--save-final F] [--selftest]");

    static Vm vm;              /* не на стеке: toks раздули Rule */
    vm.mctx = pcre2_match_context_create(NULL);
    pcre2_set_match_limit(vm.mctx, 100000000);
    pcre2_set_depth_limit(vm.mctx, 10000000);
    /* Ленивый ячеечный скан #M кладёт JIT-фрейм на итерацию: 16МБ
     * потолка хватало на ~350К ячеек, глубже — JIT_STACKLIMIT, который
     * ТИХО читался как «не матч» (load_hit -> load_miss, R=0, машина
     * уходила с траектории). Резервируем 1ГБ (коммит по мере роста). */
    vm.jstack = pcre2_jit_stack_create(1024 * 1024, 1024u * 1024 * 1024, NULL);
    pcre2_jit_stack_assign(vm.mctx, NULL, vm.jstack);
    vm.live_dir = live_dir;
    vm.live_every = live_every;
    vm.fbseq_dir = vm_fbseq_dir;
    vm.fbseq_stop = vm_fbseq_stop;
    vm.t_start = now_sec();
    vm.probe_md = pcre2_match_data_create(64, NULL);
    if (!vm.probe_md) die("oom probe_md");
    vm.pure = pure;
    vm.rcap = 1 << 20;
    vm.rbuf = malloc(vm.rcap);
    if (!vm.rbuf) die("oom rbuf");

    load_rules(&vm, rules_path);
    {   /* ovector по фактическому максимуму групп набора: фикс-размер
         * 200 у fused-правил с деревьями был впритык (rc==0 = тихая
         * порча; теперь и громкая проверка в splice_apply) */
        uint32_t max_caps = 0;
        for (int i2 = 0; i2 < vm.n_rules; i2++) {
            uint32_t nc = 0;
            pcre2_pattern_info(vm.rules[i2].code,
                               PCRE2_INFO_CAPTURECOUNT, &nc);
            if (nc > max_caps) max_caps = nc;
        }
        vm.splice_md = pcre2_match_data_create(max_caps + 1, NULL);
        if (!vm.splice_md) die("oom splice_md");
    }
    load_state(&vm, state_path);
    if (vm.fbseq_dir) {
        /* сегмент клипа: OUT снапшота уже содержит прежние '\f'-маркеры
         * — не эхоить и не считать их (иначе воркер мгновенно
         * экспортирует N копий стартового состояния) */
        const char *tag = NULL;
        for (const char *q = vm.state + (vm.len >= 5 ? vm.len - 5 : 0);
             q >= vm.state; q--)
            if (q[0] == '|' && memcmp(q, "|OUT:", 5) == 0) { tag = q; break; }
        if (tag) {
            const char *p2 = tag + 5;
            const char *end2 = strchr(p2, '|');
            if (end2) {
                vm.out_seen = (size_t)(end2 - p2);
                vm.out_off = (size_t)(tag - vm.state);
            }
        }
    }
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
            const char *st = head_marker(&vm);
            if (st) reason = st;
            break;
        }
        vm.passes++;
        echo_out(&vm);
        if (vm.live_dir && vm.passes % (unsigned long long)vm.live_every == 0)
            write_live(&vm, applied);
        if (save_every && save_final
                && vm.passes % (unsigned long long)save_every == 0) {
            /* чекпоинт: <save_final>.ckpt атомарно (rename поверх) */
            char ck[1024], cktmp[1040];
            if (snprintf(ck, sizeof ck, "%s.ckpt", save_final)
                    < (int)sizeof ck
                && snprintf(cktmp, sizeof cktmp, "%s.tmp", ck)
                    < (int)sizeof cktmp) {
                save_state(&vm, cktmp);
#ifdef _WIN32
                MoveFileExA(cktmp, ck, MOVEFILE_REPLACE_EXISTING);
#else
                rename(cktmp, ck);
#endif
            }
        }
        if (trace_every && vm.passes % (unsigned long long)trace_every == 0) {
            double dt = now_sec() - t0;
            /* %.44s головы — байт-копия для диагностики (PH/CI/PC),
             * как save-final: драйвер строку не разбирает */
            fprintf(stderr, "[pass %llu | %.0f pass/s | len %zu | %.44s | %s]\n",
                    vm.passes, dt > 0 ? (double)vm.passes / dt : 0.0,
                    vm.len, vm.state, applied);
        }
        {
            const char *st = head_marker(&vm);
            if (st) { reason = st; break; }
        }
    }

    double dt = now_sec() - t0;
    if (g_rule_stats) {
        fprintf(stderr, "-- rule stats (top-25 по времени probe) --\n");
        for (int k = 0; k < 25; k++) {
            int best = -1;
            for (int i2 = 0; i2 < vm.n_rules; i2++)
                if (vm.rules[i2].t_probe >= 0 &&
                    (best < 0 ||
                     vm.rules[i2].t_probe > vm.rules[best].t_probe))
                    best = i2;
            if (best < 0 || vm.rules[best].t_probe <= 0) break;
            Rule *r = &vm.rules[best];
            fprintf(stderr, "  %8.3fs %9llu probe %7llu hit %7llu skip"
                    " %10llu skipB  %s\n",
                    r->t_probe, r->n_probe, r->n_hit, r->n_skip,
                    r->skip_bytes, r->name);
            r->t_probe = -1;
        }
    }
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
