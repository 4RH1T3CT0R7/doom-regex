/* Tetris для RegexVM (G1d). Компилируется 8cc -> EIR -> RVM.
 *
 * Ограничения среды: без mul/div/битовых операций (builtins медленные),
 * без float. getchar() возвращает 0 при пустом вводе (неблокирующий
 * поллинг). Фреймбуфер: слова 0xf00000+i, 64x32, байт=пиксель.
 *
 * Управление: a=влево d=вправо w=поворот s=вниз q=выход.
 */

#define FB 0xf00000
#define W 10
#define H 20

int board[200];          /* 10x20, 0=пусто, иначе цвет 2..8 */
int rowoff[20];          /* y -> y*W (аддитивно) */
int fbrow[20];           /* y -> FB-адрес строки поля (аддитивно) */

/* 7 фигур x 4 поворота x 4 блока x (x,y): координаты 0..3 */
int shapes[448];

int cur, rot, px, py, colr;
int seed;

static void put_px(int addr, int c) { *(int *)addr = c; }

static void shp(int p, int r, int i, int x, int y) {
    /* индекс = ((p*4 + r)*4 + i)*2, аддитивно: p<<5 == p*32 */
    int idx = 0;
    int k;
    for (k = 0; k < p; k++) idx += 64;
    for (k = 0; k < r; k++) idx += 8;
    idx += i + i;
    shapes[idx] = x;
    shapes[idx + 1] = y;
}

static int shx(int p, int r, int i) {
    int idx = 0;
    int k;
    for (k = 0; k < p; k++) idx += 64;
    for (k = 0; k < r; k++) idx += 8;
    return shapes[idx + i + i];
}

static int shy(int p, int r, int i) {
    int idx = 0;
    int k;
    for (k = 0; k < p; k++) idx += 64;
    for (k = 0; k < r; k++) idx += 8;
    return shapes[idx + i + i + 1];
}

static void init_shapes(void) {
    int r;
    /* I */
    shp(0,0,0, 0,1); shp(0,0,1, 1,1); shp(0,0,2, 2,1); shp(0,0,3, 3,1);
    shp(0,1,0, 2,0); shp(0,1,1, 2,1); shp(0,1,2, 2,2); shp(0,1,3, 2,3);
    shp(0,2,0, 0,2); shp(0,2,1, 1,2); shp(0,2,2, 2,2); shp(0,2,3, 3,2);
    shp(0,3,0, 1,0); shp(0,3,1, 1,1); shp(0,3,2, 1,2); shp(0,3,3, 1,3);
    /* O */
    for (r = 0; r < 4; r++) {
        shp(1,r,0, 1,0); shp(1,r,1, 2,0); shp(1,r,2, 1,1); shp(1,r,3, 2,1);
    }
    /* T */
    shp(2,0,0, 1,0); shp(2,0,1, 0,1); shp(2,0,2, 1,1); shp(2,0,3, 2,1);
    shp(2,1,0, 1,0); shp(2,1,1, 1,1); shp(2,1,2, 2,1); shp(2,1,3, 1,2);
    shp(2,2,0, 0,1); shp(2,2,1, 1,1); shp(2,2,2, 2,1); shp(2,2,3, 1,2);
    shp(2,3,0, 1,0); shp(2,3,1, 0,1); shp(2,3,2, 1,1); shp(2,3,3, 1,2);
    /* S */
    shp(3,0,0, 1,0); shp(3,0,1, 2,0); shp(3,0,2, 0,1); shp(3,0,3, 1,1);
    shp(3,1,0, 1,0); shp(3,1,1, 1,1); shp(3,1,2, 2,1); shp(3,1,3, 2,2);
    shp(3,2,0, 1,1); shp(3,2,1, 2,1); shp(3,2,2, 0,2); shp(3,2,3, 1,2);
    shp(3,3,0, 0,0); shp(3,3,1, 0,1); shp(3,3,2, 1,1); shp(3,3,3, 1,2);
    /* Z */
    shp(4,0,0, 0,0); shp(4,0,1, 1,0); shp(4,0,2, 1,1); shp(4,0,3, 2,1);
    shp(4,1,0, 2,0); shp(4,1,1, 1,1); shp(4,1,2, 2,1); shp(4,1,3, 1,2);
    shp(4,2,0, 0,1); shp(4,2,1, 1,1); shp(4,2,2, 1,2); shp(4,2,3, 2,2);
    shp(4,3,0, 1,0); shp(4,3,1, 0,1); shp(4,3,2, 1,1); shp(4,3,3, 0,2);
    /* J */
    shp(5,0,0, 0,0); shp(5,0,1, 0,1); shp(5,0,2, 1,1); shp(5,0,3, 2,1);
    shp(5,1,0, 1,0); shp(5,1,1, 2,0); shp(5,1,2, 1,1); shp(5,1,3, 1,2);
    shp(5,2,0, 0,1); shp(5,2,1, 1,1); shp(5,2,2, 2,1); shp(5,2,3, 2,2);
    shp(5,3,0, 1,0); shp(5,3,1, 1,1); shp(5,3,2, 0,2); shp(5,3,3, 1,2);
    /* L */
    shp(6,0,0, 2,0); shp(6,0,1, 0,1); shp(6,0,2, 1,1); shp(6,0,3, 2,1);
    shp(6,1,0, 1,0); shp(6,1,1, 1,1); shp(6,1,2, 1,2); shp(6,1,3, 2,2);
    shp(6,2,0, 0,1); shp(6,2,1, 1,1); shp(6,2,2, 2,1); shp(6,2,3, 0,2);
    shp(6,3,0, 0,0); shp(6,3,1, 1,0); shp(6,3,2, 1,1); shp(6,3,3, 1,2);
}

static int collides(int p, int r, int nx, int ny) {
    int i;
    for (i = 0; i < 4; i++) {
        int x = nx + shx(p, r, i);
        int y = ny + shy(p, r, i);
        if (x < 0) return 1;
        if (x >= W) return 1;
        if (y >= H) return 1;
        if (y >= 0) {
            if (board[rowoff[y] + x]) return 1;
        }
    }
    return 0;
}

static void draw(void) {
    int y, x, i;
    /* поле + застывшие блоки (рамку рисуем один раз в main) */
    for (y = 0; y < H; y++) {
        int fa = fbrow[y];
        int ba = rowoff[y];
        for (x = 0; x < W; x++) {
            int c = board[ba + x];
            if (c) put_px(fa + x, c); else put_px(fa + x, 0);
        }
    }
    /* текущая фигура */
    for (i = 0; i < 4; i++) {
        int x = px + shx(cur, rot, i);
        int y = py + shy(cur, rot, i);
        if (y >= 0) put_px(fbrow[y] + x, colr);
    }
}

static void lock_and_clear(void) {
    int i, y, x;
    for (i = 0; i < 4; i++) {
        int x2 = px + shx(cur, rot, i);
        int y2 = py + shy(cur, rot, i);
        if (y2 >= 0) board[rowoff[y2] + x2] = colr;
    }
    /* очистка полных строк: сверху вниз, сдвиг вниз */
    for (y = 0; y < H; y++) {
        int full = 1;
        for (x = 0; x < W; x++)
            if (board[rowoff[y] + x] == 0) full = 0;
        if (full) {
            int yy;
            for (yy = y; yy > 0; yy--)
                for (x = 0; x < W; x++)
                    board[rowoff[yy] + x] = board[rowoff[yy - 1] + x];
            for (x = 0; x < W; x++) board[x] = 0;
            putchar(42);              /* '*' в OUT за каждую линию */
        }
    }
}

static void next_piece(void) {
    seed += 5;
    while (seed >= 7) seed -= 7;
    cur = seed;
    rot = 0;
    px = 3;
    py = -1;
    colr = cur + 2;
}

int main(void) {
    int i, y, x;
    int grav = 0;
    int alive = 1;

    for (i = 0, y = 0; i < H; i++, y += W) rowoff[i] = y;
    /* поле в FB: колонки 2..11, строки 4..23; рамка цветом 8 */
    {
        int fb0 = FB;
        for (i = 0; i < 4; i++) fb0 += 64;   /* строка 4 */
        fb0 += 2;                             /* колонка 2 */
        for (i = 0; i < H; i++) { fbrow[i] = fb0; fb0 += 64; }
    }
    for (y = 0; y < H; y++) {                 /* рамка */
        put_px(fbrow[y] - 1, 8);
        put_px(fbrow[y] + W, 8);
    }
    for (x = -1; x <= W; x++) put_px(fbrow[H - 1] + 64 + x, 8);

    init_shapes();
    seed = 3;
    next_piece();
    draw();

    while (alive) {
        int c = getchar();               /* 0 = нет ввода */
        int moved = 0;
        if (c == 113) break;             /* q */
        if (c == 97) {                   /* a: влево */
            if (!collides(cur, rot, px - 1, py)) { px -= 1; moved = 1; }
        } else if (c == 100) {           /* d: вправо */
            if (!collides(cur, rot, px + 1, py)) { px += 1; moved = 1; }
        } else if (c == 119) {           /* w: поворот */
            int nr = rot + 1;
            if (nr == 4) nr = 0;
            if (!collides(cur, nr, px, py)) { rot = nr; moved = 1; }
        } else if (c == 115) {           /* s: шаг вниз */
            grav = 60;
        }
        grav += 1;
        if (grav >= 60) {
            grav = 0;
            if (!collides(cur, rot, px, py + 1)) {
                py += 1;
            } else {
                if (py < 0) { alive = 0; }   /* переполнение — конец */
                lock_and_clear();
                next_piece();
                if (collides(cur, rot, px, py)) alive = 0;
            }
            moved = 1;
        }
        if (moved) draw();
    }
    putchar(71); putchar(89);            /* "GY" — game over */
    return 0;
}
