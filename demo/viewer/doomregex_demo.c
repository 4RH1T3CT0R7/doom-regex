/* doomregex_demo.c — нативное живое демо «DOOM на regex-подстановках».
 *
 * Один дабл-клик: окно (Win32/GDI, без зависимостей) само запускает
 * rvm.exe (честная машина Маркова на PCRE2) и показывает:
 *   - кадр E1M1, строящийся в видеозоне #F строки-состояния;
 *   - ленту подстановок: правило + дифф «до/после»;
 *   - телеметрию: номер подстановки, pass/s, длина строки.
 * Вьювер читает только live-экспорт машины (fb.rvfb + live.json) —
 * копии подстрок, как UART; машина о наблюдателе не знает.
 *
 * Сборка: см. demo/build_demo.sh (mingw gcc -mwindows).
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "playpal.h"

#define FB_W 320
#define FB_H 200
#define SCALE 2
#define PANEL_H 240
#define WIN_W (FB_W * SCALE + 32)
#define WIN_H (FB_H * SCALE + PANEL_H + 48)

static unsigned char g_frame[FB_W * FB_H];
static unsigned int g_pix[FB_W * FB_H];      /* BGRA для StretchDIBits */
static char g_rule[64] = "-";
static char g_before[256] = "";
static char g_after[256] = "";
static char g_head[160] = "";
static unsigned long long g_pass = 0;
static double g_pps = 0;
static unsigned long long g_len = 0;
static long long g_pos = -1;
static int g_alive = 0;
static char g_dir[MAX_PATH];
static PROCESS_INFORMATION g_engine;

/* --- разбор live-файлов -------------------------------------------------- */

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
        /* зона #F: подряд ячейки [oooo:vv] по 11 символов */
        size_t cells = (n - (size_t)(z - t)) / 11;
        if (cells > FB_W * FB_H) cells = FB_W * FB_H;
        for (size_t i = 0; i < cells; i++) {
            const char *c = z + i * 11;
            int v = hex1(c[6]) * 16 + hex1(c[7]);
            g_frame[i] = (unsigned char)v;
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

static void poll_live(void) {
    char path[MAX_PATH];
    snprintf(path, sizeof path, "%s\\live\\live.json", g_dir);
    char *t = read_all(path, NULL);
    if (!t) { g_alive = 0; return; }
    g_alive = 1;
    g_pass = (unsigned long long)json_num(t, "pass");
    g_len = (unsigned long long)json_num(t, "len");
    g_pps = json_num(t, "pps");
    g_pos = (long long)(json_num(t, "pos") - json_num(t, "w0"));
    json_str(t, "rule", g_rule, sizeof g_rule);
    json_str(t, "before", g_before, sizeof g_before);
    json_str(t, "after", g_after, sizeof g_after);
    json_str(t, "head", g_head, sizeof g_head);
    free(t);
}

/* --- отрисовка ------------------------------------------------------------ */

static COLORREF C_BG    = RGB(0x14, 0x10, 0x0f);
static COLORREF C_PANEL = RGB(0x1d, 0x17, 0x15);
static COLORREF C_INK   = RGB(0xd8, 0xcf, 0xc4);
static COLORREF C_DIM   = RGB(0x8a, 0x7d, 0x72);
static COLORREF C_RED   = RGB(0xff, 0x5a, 0x36);
static COLORREF C_AMBER = RGB(0xe8, 0xa3, 0x3d);
static COLORREF C_GREEN = RGB(0x9f, 0xe0, 0x8a);
static COLORREF C_DEL   = RGB(0xff, 0x8f, 0x70);

static HFONT g_fnt, g_fnt_b;

static void diff_line(HDC dc, int x, int y, const char *label,
                      const char *s, long long cut, COLORREF hi) {
    SetTextColor(dc, C_DIM);
    TextOutA(dc, x, y, label, (int)strlen(label));
    x += 62;
    if (cut < 0 || cut > (long long)strlen(s)) cut = (long long)strlen(s);
    SetTextColor(dc, RGB(0xa8, 0x9c, 0x8f));
    TextOutA(dc, x, y, s, (int)cut);
    SIZE sz;
    GetTextExtentPoint32A(dc, s, (int)cut, &sz);
    SetTextColor(dc, hi);
    TextOutA(dc, x + sz.cx, y, s + cut, (int)(strlen(s) - cut));
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

    /* кадр */
    BITMAPINFO bi = {0};
    bi.bmiHeader.biSize = sizeof bi.bmiHeader;
    bi.bmiHeader.biWidth = FB_W;
    bi.bmiHeader.biHeight = -FB_H;
    bi.bmiHeader.biPlanes = 1;
    bi.bmiHeader.biBitCount = 32;
    bi.bmiHeader.biCompression = BI_RGB;
    StretchDIBits(mem, 16, 16, FB_W * SCALE, FB_H * SCALE,
                  0, 0, FB_W, FB_H, g_pix, &bi, DIB_RGB_COLORS, SRCCOPY);

    SelectObject(mem, g_fnt);
    SetBkMode(mem, TRANSPARENT);

    int y = FB_H * SCALE + 24;
    char line[512];

    /* телеметрия */
    SetTextColor(mem, g_alive ? C_GREEN : C_DIM);
    TextOutA(mem, 16, y, g_alive ? "\xE2\x97\x8F \xD0\xBC\xD0\xB0\xD1\x88\xD0\xB8\xD0\xBD\xD0\xB0 \xD1\x80\xD0\xB0\xD0\xB1\xD0\xBE\xD1\x82\xD0\xB0\xD0\xB5\xD1\x82"
                        : "\xE2\x97\x8B \xD0\xBE\xD0\xB6\xD0\xB8\xD0\xB4\xD0\xB0\xD0\xBD\xD0\xB8\xD0\xB5",
             g_alive ? 17 : 10);
    snprintf(line, sizeof line,
             "pass %llu   |   %.1f pass/s   |   len %llu",
             g_pass, g_pps, g_len);
    SetTextColor(mem, C_AMBER);
    TextOutA(mem, 190, y, line, (int)strlen(line));

    /* правило */
    y += 30;
    SelectObject(mem, g_fnt_b);
    SetTextColor(mem, C_RED);
    snprintf(line, sizeof line, "[%s]", g_rule);
    TextOutA(mem, 16, y, line, (int)strlen(line));
    SelectObject(mem, g_fnt);

    /* дифф */
    y += 28;
    diff_line(mem, 16, y, "\xD0\xB4\xD0\xBE   \xE2\x94\x82", g_before, g_pos, C_DEL);
    y += 22;
    diff_line(mem, 16, y, "\xD0\xBF\xD0\xBE\xD1\x81\xD0\xBB\xD0\xB5\xE2\x94\x82", g_after, g_pos, C_GREEN);

    /* заголовок состояния */
    y += 30;
    SetTextColor(mem, C_DIM);
    TextOutA(mem, 16, y, g_head, (int)strlen(g_head));

    y += 30;
    SetTextColor(mem, RGB(0x55, 0x49, 0x3f));
    const char *foot = "DOOM computed by find&replace \xC2\xB7 544 rules \xC2\xB7 PCRE2 \xC2\xB7 "
                       "WASD+arrows/Ctrl/Space \xC2\xB7 honest Markov machine";
    TextOutA(mem, 16, y, foot, (int)strlen(foot));

    BitBlt(dc, 0, 0, rc.right, rc.bottom, mem, 0, 0, SRCCOPY);
    DeleteObject(bmp);
    DeleteDC(mem);
    EndPaint(w, &ps);
}

/* --- G2f: клавиатура -> input.bin (машина читает через GETC) -----------
 * Протокол DG_GetKey: байт 0x81 (нажата) / 0x80 (отпущена), затем байт
 * doom-клавиши. Драйвер вливает байты файла в |IN: как hex (FIFO). */
static unsigned char vk_to_doom(WPARAM vk) {
    switch (vk) {
    case VK_UP:    case 'W': return 0xad;   /* KEY_UPARROW    */
    case VK_DOWN:  case 'S': return 0xaf;   /* KEY_DOWNARROW  */
    case VK_LEFT:            return 0xac;   /* KEY_LEFTARROW  */
    case VK_RIGHT:           return 0xae;   /* KEY_RIGHTARROW */
    case 'A':                return 0xa0;   /* KEY_STRAFE_L   */
    case 'D':                return 0xa1;   /* KEY_STRAFE_R   */
    case VK_CONTROL:         return 0xa3;   /* KEY_FIRE       */
    case VK_SPACE:           return 0xa2;   /* KEY_USE        */
    case VK_SHIFT:           return 0x80 + 0x36; /* KEY_RSHIFT: бег */
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

static LRESULT CALLBACK wndproc(HWND w, UINT m, WPARAM wp, LPARAM lp) {
    switch (m) {
    case WM_KEYDOWN:
        if (!(lp & 0x40000000)) {            /* без автоповтора */
            unsigned char k = vk_to_doom(wp);
            if (k) send_key(1, k);
        }
        return 0;
    case WM_KEYUP: {
        unsigned char k = vk_to_doom(wp);
        if (k) send_key(0, k);
        return 0;
    }
    case WM_TIMER:
        poll_fb();
        poll_live();
        InvalidateRect(w, NULL, FALSE);
        return 0;
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

    /* live-папка */
    char live[MAX_PATH];
    snprintf(live, sizeof live, "%s\\live", g_dir);
    CreateDirectoryA(live, NULL);

    /* пустой входной файл клавиш (машина поллит его через GETC) */
    char inp[MAX_PATH];
    snprintf(inp, sizeof inp, "%s\\input.bin", g_dir);
    FILE *fi = fopen(inp, "wb");
    if (fi) fclose(fi);

    /* запуск машины: rvm.exe рядом с exe */
    char eng[2048];
    snprintf(eng, sizeof eng,
             "\"%s\\rvm.exe\" --rules \"%s\\rules_rvm.rgxset\" "
             "--state \"%s\\snapshot.rvstate\" --live-dir \"%s\" "
             "--live-every 15 --input-file \"%s\" --io-every 2000 "
             "--io-journal \"%s\\input.journal\" --quiet",
             g_dir, g_dir, g_dir, live, inp, g_dir);
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

    g_fnt = CreateFontA(17, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET,
                        0, 0, CLEARTYPE_QUALITY, FIXED_PITCH, "Consolas");
    g_fnt_b = CreateFontA(19, 0, 0, 0, FW_BOLD, 0, 0, 0, DEFAULT_CHARSET,
                          0, 0, CLEARTYPE_QUALITY, FIXED_PITCH, "Consolas");

    RECT need = {0, 0, WIN_W, WIN_H};
    AdjustWindowRect(&need, WS_OVERLAPPEDWINDOW & ~WS_THICKFRAME, FALSE);
    HWND w = CreateWindowA("doomregexdemo",
        "DOOM computed by regex substitution \xE2\x80\x94 live",
        (WS_OVERLAPPEDWINDOW & ~WS_THICKFRAME & ~WS_MAXIMIZEBOX),
        CW_USEDEFAULT, CW_USEDEFAULT,
        need.right - need.left, need.bottom - need.top,
        NULL, NULL, hi, NULL);
    ShowWindow(w, show);
    SetTimer(w, 1, 350, NULL);

    MSG msg;
    while (GetMessage(&msg, NULL, 0, 0)) {
        TranslateMessage(&msg);
        DispatchMessage(&msg);
    }
    return 0;
}
