/* doomregex_demo.c - live demo of DOOM computed by regex substitution.
 *
 * Double-click and the window launches rvm.exe (the Markov machine on
 * PCRE2) and watches it through the live export (fb.rvfb + live.json),
 * which is nothing but copies of substrings; the machine never knows
 * it is being observed.
 *
 * Viewer features:
 *   - resizable window, frame scales to fit;
 *   - tabs: FEED (scrollable substitution history), RULES (which rules
 *     fire most), MAP (the whole 96 MB string as a bar with zones and
 *     a marker where the last substitution landed);
 *   - pps sparkline, pause, BMP screenshots, playable DOOM keys.
 *
 * Build: demo/build_demo.sh (mingw gcc -mwindows).
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "playpal.h"

#define FB_W 320
#define FB_H 200
#define PANEL_MIN 210

/* --- live state --------------------------------------------------------- */

static unsigned char g_frame[FB_W * FB_H];
static unsigned int g_pix[FB_W * FB_H];
static char g_rule[64] = "-";
static char g_before[256] = "";
static char g_after[256] = "";
static unsigned long long g_pass = 0;
static double g_pps = 0;
static unsigned long long g_len = 0;
static long long g_pos = -1;       /* окно диффа: позиция внутри вырезки */
static long long g_abs = -1;       /* абсолютная позиция замены в строке */
static int g_alive = 0;
static int g_paused = 0;
static int g_tab = 0;              /* 0 feed, 1 rules, 2 map */
static int g_help = 0;
static char g_dir[MAX_PATH];
static char g_lastkey[48] = "";
static int g_done = 0;             /* машина дошла до ST:hlt */
static char g_snap[MAX_PATH];      /* выбранный снапшот */
static PROCESS_INFORMATION g_engine;

/* лента: кольцевой буфер последних подстановок */
#define FEED_N 512
typedef struct { unsigned long long pass; long long pos; char rule[40]; } FeedRec;
static FeedRec g_feed[FEED_N];
static int g_feed_len = 0, g_feed_head = 0, g_feed_scroll = 0;

/* статистика правил */
#define RN 96
static struct { char name[40]; unsigned count; } g_rules[RN];
static int g_rules_n = 0;

/* спарклайн pps */
#define SPARK_N 120
static double g_spark[SPARK_N];
static int g_spark_n = 0;

/* карта зон строки (боевой профиль; для отображения) */
static const struct { const char *name; long long start; COLORREF c; } ZONES[] = {
    {"header", 0,        RGB(0xe8, 0xa3, 0x3d)},
    {"tables", 154,      RGB(0x8a, 0x7d, 0x72)},
    {"#N ram", 47450,    RGB(0x4f, 0x8f, 0xd0)},
    {"#P code", 58767708, RGB(0x9f, 0xe0, 0x8a)},
    {"#F frame", 82884958, RGB(0xff, 0x5a, 0x36)},
    {"#W wad", 83460960, RGB(0xb0, 0x8f, 0xd0)},
    {"#M+io", 96568122,  RGB(0xd0, 0xd0, 0x5a)},
};
#define NZONES 7

/* --- helpers ------------------------------------------------------------ */

static char *read_all(const char *path, size_t *n) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buf = malloc((size_t)sz + 1);
    if (!buf) { fclose(f); return NULL; }
    if (fread(buf, 1, (size_t)sz, f) != (size_t)sz) {
        free(buf); fclose(f); return NULL;
    }
    fclose(f);
    buf[sz] = 0;
    if (n) *n = (size_t)sz;
    return buf;
}

static int hex1(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return 0;
}

static void poll_fb(void) {
    char path[MAX_PATH];
    snprintf(path, sizeof path, "%s\\live\\fb.rvfb", g_dir);
    size_t n;
    char *t = read_all(path, &n);
    if (!t) return;
    char *z = strchr(t, '\n');
    if (z) {
        z++;
        /* ячейка "[oooo:vv]" = 9 символов (формат v2.0) */
        size_t cells = (n - (size_t)(z - t)) / 9;
        if (cells > FB_W * FB_H) cells = FB_W * FB_H;
        for (size_t i = 0; i < cells; i++) {
            const char *c = z + i * 9;
            g_frame[i] = (unsigned char)(hex1(c[6]) * 16 + hex1(c[7]));
        }
        for (int i = 0; i < FB_W * FB_H; i++) {
            const unsigned char *p = PAL + g_frame[i] * 3;
            g_pix[i] = (unsigned)p[2] | ((unsigned)p[1] << 8)
                     | ((unsigned)p[0] << 16);
        }
    }
    free(t);
}

static void json_str(const char *j, const char *key, char *out, size_t cap) {
    char pat[48];
    snprintf(pat, sizeof pat, "\"%s\": \"", key);
    const char *p = strstr(j, pat);
    out[0] = 0;
    if (!p) return;
    p += strlen(pat);
    size_t i = 0;
    while (p[i] && p[i] != '"' && i + 1 < cap) { out[i] = p[i]; i++; }
    out[i] = 0;
}

static double json_num(const char *j, const char *key) {
    char pat[48];
    snprintf(pat, sizeof pat, "\"%s\": ", key);
    const char *p = strstr(j, pat);
    return p ? atof(p + strlen(pat)) : 0;
}

static void bump_rule(const char *name) {
    for (int i = 0; i < g_rules_n; i++)
        if (!strcmp(g_rules[i].name, name)) { g_rules[i].count++; return; }
    if (g_rules_n < RN) {
        strncpy(g_rules[g_rules_n].name, name, 39);
        g_rules[g_rules_n].count = 1;
        g_rules_n++;
    }
}

static void poll_live(void) {
    char path[MAX_PATH];
    snprintf(path, sizeof path, "%s\\live\\live.json", g_dir);
    char *t = read_all(path, NULL);
    if (!t) { g_alive = 0; return; }
    g_alive = 1;
    unsigned long long pass = (unsigned long long)json_num(t, "pass");
    if (pass != g_pass && !g_paused) {
        g_pass = pass;
        g_len = (unsigned long long)json_num(t, "len");
        g_pps = json_num(t, "pps");
        double w0 = json_num(t, "w0");
        g_abs = (long long)json_num(t, "pos");
        g_pos = g_abs - (long long)w0;
        json_str(t, "rule", g_rule, sizeof g_rule);
        json_str(t, "before", g_before, sizeof g_before);
        json_str(t, "after", g_after, sizeof g_after);
        char head[64];
        json_str(t, "head", head, sizeof head);
        g_done = strstr(head, "ST:hlt") != NULL;
        /* лента + статы + спарклайн */
        FeedRec *r = &g_feed[g_feed_head];
        r->pass = pass; r->pos = g_abs;
        strncpy(r->rule, g_rule, 39);
        g_feed_head = (g_feed_head + 1) % FEED_N;
        if (g_feed_len < FEED_N) g_feed_len++;
        bump_rule(g_rule);
        if (g_spark_n < SPARK_N) g_spark[g_spark_n++] = g_pps;
        else {
            memmove(g_spark, g_spark + 1, (SPARK_N - 1) * sizeof(double));
            g_spark[SPARK_N - 1] = g_pps;
        }
    }
    free(t);
}

/* --- screenshot --------------------------------------------------------- */

static void save_screenshot(void) {
    static int n = 0;
    char path[MAX_PATH];
    snprintf(path, sizeof path, "%s\\shot_%03d.bmp", g_dir, n++);
    FILE *f = fopen(path, "wb");
    if (!f) return;
    int data = FB_W * FB_H;
    int off = 14 + 40 + 256 * 4;
    unsigned char fh[14] = {'B', 'M'};
    *(int *)(fh + 2) = off + data; *(int *)(fh + 10) = off;
    fwrite(fh, 1, 14, f);
    unsigned char ih[40] = {40};
    *(int *)(ih + 4) = FB_W; *(int *)(ih + 8) = -FB_H;
    *(short *)(ih + 12) = 1; *(short *)(ih + 14) = 8;
    fwrite(ih, 1, 40, f);
    for (int i = 0; i < 256; i++) {
        unsigned char q[4] = {PAL[i * 3 + 2], PAL[i * 3 + 1], PAL[i * 3], 0};
        fwrite(q, 1, 4, f);
    }
    fwrite(g_frame, 1, data, f);
    fclose(f);
    snprintf(g_lastkey, sizeof g_lastkey, "saved shot_%03d.bmp", n - 1);
}

/* --- input to the machine ----------------------------------------------- */

static unsigned char vk_to_doom(WPARAM vk) {
    switch (vk) {
    case VK_UP:    case 'W': return 0xad;
    case VK_DOWN:  case 'S': return 0xaf;
    case VK_LEFT:            return 0xac;
    case VK_RIGHT:           return 0xae;
    case 'A':                return 0xa0;
    case 'D':                return 0xa1;
    case VK_CONTROL:         return 0xa3;
    case VK_SPACE:           return 0xa2;
    case VK_SHIFT:           return 0x80 + 0x36;
    case VK_RETURN:          return 13;
    case VK_ESCAPE:          return 27;
    case 'Y':                return 'y';
    default:                 return 0;
    }
}

static void send_key(int pressed, unsigned char key) {
    char path[MAX_PATH];
    snprintf(path, sizeof path, "%s\\input.bin", g_dir);
    FILE *f = fopen(path, "ab");
    if (!f) return;
    unsigned char ev[2] = { (unsigned char)(pressed ? 0x81 : 0x80), key };
    fwrite(ev, 1, 2, f);
    fclose(f);
}

/* --- painting ----------------------------------------------------------- */

static COLORREF C_BG    = RGB(0x14, 0x10, 0x0f);
static COLORREF C_DIM   = RGB(0x8a, 0x7d, 0x72);
static COLORREF C_RED   = RGB(0xff, 0x5a, 0x36);
static COLORREF C_AMBER = RGB(0xe8, 0xa3, 0x3d);
static COLORREF C_GREEN = RGB(0x9f, 0xe0, 0x8a);
static COLORREF C_DEL   = RGB(0xff, 0x8f, 0x70);
static COLORREF C_TAB   = RGB(0x2a, 0x22, 0x1e);

static HFONT g_fnt, g_fnt_b;
static int g_chw = 8;              /* ширина символа моноширинного шрифта */

static void text_clip(HDC dc, int x, int y, int maxw, const char *s,
                      COLORREF col) {
    int n = (int)strlen(s);
    int fit = maxw / g_chw;
    if (fit < 1) return;
    SetTextColor(dc, col);
    TextOutA(dc, x, y, s, n < fit ? n : fit);
}

static void diff_line(HDC dc, int x, int y, int maxw, const char *label,
                      const char *s, long long cut, COLORREF hi) {
    SetTextColor(dc, C_DIM);
    TextOutA(dc, x, y, label, (int)strlen(label));
    x += 62;
    maxw -= 62;
    int fit = maxw / g_chw;
    int n = (int)strlen(s);
    if (n > fit) n = fit;
    if (cut < 0 || cut > n) cut = n;
    SetTextColor(dc, RGB(0xa8, 0x9c, 0x8f));
    TextOutA(dc, x, y, s, (int)cut);
    SIZE sz;
    GetTextExtentPoint32A(dc, s, (int)cut, &sz);
    SetTextColor(dc, hi);
    TextOutA(dc, x + sz.cx, y, s + cut, (int)(n - cut));
}

static void draw_tab_feed(HDC dc, RECT rc) {
    int rows = (rc.bottom - rc.top - 6) / 20;
    if (rows < 1) return;
    if (g_feed_scroll > g_feed_len - rows) g_feed_scroll = g_feed_len - rows;
    if (g_feed_scroll < 0) g_feed_scroll = 0;
    for (int i = 0; i < rows; i++) {
        int idx = g_feed_len - 1 - i - g_feed_scroll;
        if (idx < 0) break;
        FeedRec *r = &g_feed[(g_feed_head - 1 - i - g_feed_scroll
                              + 2 * FEED_N) % FEED_N];
        char line[128];
        snprintf(line, sizeof line, "%10llu  %-28s @ %lld",
                 r->pass, r->rule, r->pos);
        text_clip(dc, rc.left, rc.top + 4 + i * 20,
                  rc.right - rc.left, line,
                  i == 0 && !g_feed_scroll ? C_GREEN : C_DIM);
    }
}

static void draw_tab_rules(HDC dc, RECT rc) {
    /* топ по счётчику */
    int order[RN];
    for (int i = 0; i < g_rules_n; i++) order[i] = i;
    for (int i = 0; i < g_rules_n; i++)
        for (int j = i + 1; j < g_rules_n; j++)
            if (g_rules[order[j]].count > g_rules[order[i]].count) {
                int t = order[i]; order[i] = order[j]; order[j] = t;
            }
    unsigned maxc = g_rules_n ? g_rules[order[0]].count : 1;
    int rows = (rc.bottom - rc.top - 6) / 20;
    for (int i = 0; i < rows && i < g_rules_n; i++) {
        int y = rc.top + 4 + i * 20;
        char line[80];
        snprintf(line, sizeof line, "%-24s %7u",
                 g_rules[order[i]].name, g_rules[order[i]].count);
        text_clip(dc, rc.left, y, 300, line, C_DIM);
        int barw = (int)((long long)(rc.right - rc.left - 320)
                         * g_rules[order[i]].count / maxc);
        if (barw > 0) {
            RECT b = {rc.left + 310, y + 4, rc.left + 310 + barw, y + 16};
            HBRUSH hb = CreateSolidBrush(C_AMBER);
            FillRect(dc, &b, hb);
            DeleteObject(hb);
        }
    }
}

static void draw_tab_map(HDC dc, RECT rc) {
    if (!g_len) return;
    int y0 = rc.top + 26;
    int h = 34;
    int w = rc.right - rc.left;
    /* полоса зон */
    for (int i = 0; i < NZONES; i++) {
        long long a = ZONES[i].start;
        long long b = (i + 1 < NZONES) ? ZONES[i + 1].start
                                       : (long long)g_len;
        if (a >= (long long)g_len) break;
        if (b > (long long)g_len) b = (long long)g_len;
        int xa = (int)(rc.left + (long long)w * a / (long long)g_len);
        int xb = (int)(rc.left + (long long)w * b / (long long)g_len);
        if (xb <= xa) xb = xa + 1;
        RECT z = {xa, y0, xb, y0 + h};
        HBRUSH hb = CreateSolidBrush(ZONES[i].c);
        FillRect(dc, &z, hb);
        DeleteObject(hb);
    }
    /* маркер последней записи */
    if (g_abs >= 0 && g_abs <= (long long)g_len) {
        int x = (int)(rc.left + (long long)w * g_abs / (long long)g_len);
        RECT m = {x - 1, y0 - 8, x + 2, y0 + h + 8};
        HBRUSH hb = CreateSolidBrush(RGB(255, 255, 255));
        FillRect(dc, &m, hb);
        DeleteObject(hb);
    }
    /* подписи и текущая зона */
    const char *cur = "?";
    for (int i = 0; i < NZONES; i++) {
        long long a = ZONES[i].start;
        if (g_abs >= a) cur = ZONES[i].name;
        int xa = (int)(rc.left + (long long)w * a / (long long)g_len);
        text_clip(dc, xa + 2, y0 + h + 10, 110, ZONES[i].name, ZONES[i].c);
    }
    char line[160];
    snprintf(line, sizeof line,
             "the whole %llu-char string; last write at %lld (%s)",
             g_len, g_abs, cur);
    text_clip(dc, rc.left, rc.top + 2, w, line, C_DIM);
}

static void draw_spark(HDC dc, int x, int y, int w, int h) {
    if (g_spark_n < 2) return;
    double mx = 1;
    for (int i = 0; i < g_spark_n; i++)
        if (g_spark[i] > mx) mx = g_spark[i];
    HPEN pen = CreatePen(PS_SOLID, 1, C_GREEN);
    HGDIOBJ old = SelectObject(dc, pen);
    for (int i = 1; i < g_spark_n; i++) {
        int x0 = x + (i - 1) * w / (SPARK_N - 1);
        int x1 = x + i * w / (SPARK_N - 1);
        int y0 = y + h - (int)(g_spark[i - 1] / mx * h);
        int y1 = y + h - (int)(g_spark[i] / mx * h);
        MoveToEx(dc, x0, y0, NULL);
        LineTo(dc, x1, y1);
    }
    SelectObject(dc, old);
    DeleteObject(pen);
}

static void paint(HWND w) {
    PAINTSTRUCT ps;
    HDC dc = BeginPaint(w, &ps);
    RECT rc;
    GetClientRect(w, &rc);
    HDC mem = CreateCompatibleDC(dc);
    HBITMAP bmp = CreateCompatibleBitmap(dc, rc.right, rc.bottom);
    SelectObject(mem, bmp);

    HBRUSH bg = CreateSolidBrush(C_BG);
    FillRect(mem, &rc, bg);
    DeleteObject(bg);

    SelectObject(mem, g_fnt);
    SetBkMode(mem, TRANSPARENT);
    SIZE csz;
    GetTextExtentPoint32A(mem, "M", 1, &csz);
    g_chw = csz.cx ? csz.cx : 8;

    /* кадр: максимально влезающий целый масштаб, минимум 1 */
    int availh = rc.bottom - PANEL_MIN - 24;
    int scale = (rc.right - 32) / FB_W;
    if (availh / FB_H < scale) scale = availh / FB_H;
    if (scale < 1) scale = 1;
    int fw = FB_W * scale, fh = FB_H * scale;
    int fx = (rc.right - fw) / 2;
    if (fx < 16) fx = 16;

    BITMAPINFO bi = {0};
    bi.bmiHeader.biSize = sizeof bi.bmiHeader;
    bi.bmiHeader.biWidth = FB_W;
    bi.bmiHeader.biHeight = -FB_H;
    bi.bmiHeader.biPlanes = 1;
    bi.bmiHeader.biBitCount = 32;
    bi.bmiHeader.biCompression = BI_RGB;
    StretchDIBits(mem, fx, 12, fw, fh, 0, 0, FB_W, FB_H,
                  g_pix, &bi, DIB_RGB_COLORS, SRCCOPY);

    int y = 12 + fh + 8;
    int maxw = rc.right - 32;
    char line[512];

    /* телеметрия + спарклайн */
    SetTextColor(mem, g_alive ? (g_paused ? C_AMBER : C_GREEN) : C_DIM);
    if (g_done) SetTextColor(mem, C_AMBER);
    TextOutA(mem, 16, y,
             g_done ? "DONE   " :
             g_paused ? "PAUSED " : (g_alive ? "RUNNING" : "WAITING"), 7);
    snprintf(line, sizeof line, "pass %llu | %.0f/s | len %llu",
             g_pass, g_pps, g_len);
    text_clip(mem, 100, y, maxw - 300, line, C_AMBER);
    draw_spark(mem, rc.right - 176, y - 2, 160, 22);

    /* последняя замена */
    y += 26;
    SelectObject(mem, g_fnt_b);
    snprintf(line, sizeof line, "[%s]", g_rule);
    text_clip(mem, 16, y, maxw, line, C_RED);
    SelectObject(mem, g_fnt);
    y += 24;
    diff_line(mem, 16, y, maxw, "before|", g_before, g_pos, C_DEL);
    y += 20;
    diff_line(mem, 16, y, maxw, "after |", g_after, g_pos, C_GREEN);
    y += 26;

    /* вкладки */
    const char *tabs[3] = {"FEED", "RULES", "MAP"};
    int tx = 16;
    for (int i = 0; i < 3; i++) {
        RECT tb = {tx, y, tx + 76, y + 22};
        if (i == g_tab) {
            HBRUSH hb = CreateSolidBrush(C_TAB);
            FillRect(mem, &tb, hb);
            DeleteObject(hb);
        }
        SetTextColor(mem, i == g_tab ? C_AMBER : C_DIM);
        TextOutA(mem, tx + 14, y + 2, tabs[i], (int)strlen(tabs[i]));
        tx += 84;
    }
    text_clip(mem, tx + 10, y + 2, maxw - tx,
              "Tab switch | wheel scroll | P pause | H help", C_DIM);
    y += 28;

    RECT body = {16, y, rc.right - 16, rc.bottom - 24};
    if (g_tab == 0) draw_tab_feed(mem, body);
    else if (g_tab == 1) draw_tab_rules(mem, body);
    else draw_tab_map(mem, body);

    /* нижняя строка */
    const char *foot = g_lastkey[0] ? g_lastkey :
        "WASD/arrows move | Ctrl fire | Space use | Esc menu | F12 shot";
    text_clip(mem, 16, rc.bottom - 20, maxw, foot, C_DIM);

    if (g_help) {
        RECT hb = {rc.right / 2 - 220, 40, rc.right / 2 + 220, 300};
        HBRUSH b2 = CreateSolidBrush(C_TAB);
        FillRect(mem, &hb, b2);
        DeleteObject(b2);
        const char *ht[] = {
            "DOOM on regex - viewer help",
            "",
            "game: WASD or arrows, Ctrl fire, Space use,",
            "      Shift run, Esc/Enter menu (frame takes minutes)",
            "Tab   switch FEED / RULES / MAP",
            "wheel scroll the feed",
            "P     pause the telemetry (machine keeps going)",
            "F12   save frame as BMP next to the exe",
            "H     close this help",
        };
        for (int i = 0; i < 9; i++)
            text_clip(mem, hb.left + 16, hb.top + 12 + i * 24, 420,
                      ht[i], i ? C_DIM : C_AMBER);
    }

    BitBlt(dc, 0, 0, rc.right, rc.bottom, mem, 0, 0, SRCCOPY);
    DeleteObject(bmp);
    DeleteDC(mem);
    EndPaint(w, &ps);
}

/* --- window ------------------------------------------------------------- */

static LRESULT CALLBACK wndproc(HWND w, UINT m, WPARAM wp, LPARAM lp) {
    switch (m) {
    case WM_KEYDOWN:
        if (wp == VK_TAB) { g_tab = (g_tab + 1) % 3; return 0; }
        if (wp == 'P') { g_paused = !g_paused; return 0; }
        if (wp == 'H') { g_help = !g_help; return 0; }
        if (wp == VK_F12) { save_screenshot(); return 0; }
        if (!(lp & 0x40000000)) {
            unsigned char k = vk_to_doom(wp);
            if (k) {
                send_key(1, k);
                snprintf(g_lastkey, sizeof g_lastkey,
                         "key 0x%02x -> input.bin (machine reads it via GETC)",
                         k);
            }
        }
        return 0;
    case WM_KEYUP: {
        unsigned char k = vk_to_doom(wp);
        if (k) send_key(0, k);
        return 0;
    }
    case WM_MOUSEWHEEL:
        g_feed_scroll += GET_WHEEL_DELTA_WPARAM(wp) > 0 ? 3 : -3;
        InvalidateRect(w, NULL, FALSE);
        return 0;
    case WM_GETMINMAXINFO: {
        MINMAXINFO *mm = (MINMAXINFO *)lp;
        mm->ptMinTrackSize.x = FB_W + 64;
        mm->ptMinTrackSize.y = FB_H + PANEL_MIN + 80;
        return 0;
    }
    case WM_SIZE:
        InvalidateRect(w, NULL, TRUE);
        return 0;
    case WM_TIMER: {
        poll_fb();
        poll_live();
        char title[160];
        snprintf(title, sizeof title,
                 "DOOM on regex - pass %llu, %.0f/s", g_pass, g_pps);
        SetWindowTextA(w, title);
        InvalidateRect(w, NULL, FALSE);
        return 0;
    }
    case WM_PAINT:
        paint(w);
        return 0;
    case WM_ERASEBKGND:
        return 1;
    case WM_DESTROY:
        if (g_engine.hProcess) TerminateProcess(g_engine.hProcess, 0);
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProc(w, m, wp, lp);
}

int WINAPI WinMain(HINSTANCE hi, HINSTANCE prev, LPSTR cmd, int show) {
    (void)prev; (void)cmd;
    GetModuleFileNameA(NULL, g_dir, sizeof g_dir);
    char *slash = strrchr(g_dir, '\\');
    if (slash) *slash = 0;

    char live[MAX_PATH];
    snprintf(live, sizeof live, "%s\\live", g_dir);
    CreateDirectoryA(live, NULL);

    /* два режима: интерактивная игра (snapshot.rvstate) или наблюдение
     * рендера одного кадра до фикс-точки (snapshot_frame.rvstate) */
    snprintf(g_snap, sizeof g_snap, "%s\\snapshot.rvstate", g_dir);
    char fsnap[MAX_PATH];
    snprintf(fsnap, sizeof fsnap, "%s\\snapshot_frame.rvstate", g_dir);
    if (GetFileAttributesA(fsnap) != INVALID_FILE_ATTRIBUTES) {
        int r = MessageBoxA(NULL,
            "YES - play DOOM interactively (a frame takes minutes)\n"
            "NO  - watch the machine paint one frame and halt",
            "doom-regex demo: pick a mode", MB_YESNO | MB_ICONQUESTION);
        if (r == IDNO)
            strncpy(g_snap, fsnap, sizeof g_snap - 1);
    }

    char inp[MAX_PATH];
    snprintf(inp, sizeof inp, "%s\\input.bin", g_dir);
    FILE *fi = fopen(inp, "wb");
    if (fi) fclose(fi);

    char eng[2048];
    snprintf(eng, sizeof eng,
             "\"%s\\rvm.exe\" --rules \"%s\\rules_rvm.rgxset\" "
             "--state \"%s\" --live-dir \"%s\" "
             "--live-every 15 --input-file \"%s\" --io-every 2000 "
             "--io-journal \"%s\\input.journal\" --quiet",
             g_dir, g_dir, g_snap, live, inp, g_dir);
    STARTUPINFOA si = { sizeof si };
    if (!CreateProcessA(NULL, eng, NULL, NULL, FALSE,
                        CREATE_NO_WINDOW, NULL, g_dir, &si, &g_engine)) {
        MessageBoxA(NULL,
            "rvm.exe / rules_rvm.rgxset / snapshot.rvstate must be next to "
            "this exe.\nSee README.", "doom-regex demo", MB_ICONERROR);
        return 1;
    }

    WNDCLASSA wc = {0};
    wc.lpfnWndProc = wndproc;
    wc.hInstance = hi;
    wc.lpszClassName = "doomregexdemo";
    wc.hCursor = LoadCursor(NULL, IDC_ARROW);
    RegisterClassA(&wc);

    g_fnt = CreateFontA(16, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET,
                        0, 0, CLEARTYPE_QUALITY, FIXED_PITCH, "Consolas");
    g_fnt_b = CreateFontA(18, 0, 0, 0, FW_BOLD, 0, 0, 0, DEFAULT_CHARSET,
                          0, 0, CLEARTYPE_QUALITY, FIXED_PITCH, "Consolas");

    RECT need = {0, 0, FB_W * 2 + 64, FB_H * 2 + PANEL_MIN + 60};
    AdjustWindowRect(&need, WS_OVERLAPPEDWINDOW, FALSE);
    HWND w = CreateWindowA("doomregexdemo",
        "DOOM computed by regex substitution - live",
        WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT, CW_USEDEFAULT,
        need.right - need.left, need.bottom - need.top,
        NULL, NULL, hi, NULL);
    ShowWindow(w, show);
    SetTimer(w, 1, 300, NULL);

    MSG msg;
    while (GetMessage(&msg, NULL, 0, 0)) {
        TranslateMessage(&msg);
        DispatchMessage(&msg);
    }
    return 0;
}
