# DOOM on regex

DOOM, running on a computer whose only instruction is a regex find-and-replace.

The entire machine is one long string. Registers, RAM, the framebuffer and the
DOOM engine itself live in that string as plain text. A small driver applies a
fixed, ordered list of substitution rules to it, over and over. Whichever rule
matches first fires once - that's a step. There is no interpreter and no
arithmetic anywhere outside the rules. Delete the ruleset and you're left with
a text file.

This is the machine painting a frame. Green marks the pixels the current
substitutions are writing: walls go down column by column, floors span by
span, and the pauses are the BSP traversal thinking between strokes. Every
green stroke is a regex replacing a few characters inside a 96.6 MB string:

<p align="center">
  <a href="https://4rh1t3ct0r7.github.io/doom-regex/">
    <img src="docs/paint_timelapse.gif" width="640" alt="the machine painting a frame, writes highlighted in green"><br>
    <img src="https://img.shields.io/badge/%E2%96%B6%20%20Interactive%20site%20-%20watch%20the%20machine%20think-c23b22?style=for-the-badge" alt="Open the interactive site">
  </a>
</p>

One frame of E1M1 takes **13 994 067 substitutions** and comes out
byte-identical (SHA-256) to the same frame rendered by natively compiled
DOOM. And it's not a single lucky frame - here are a hundred frames of the
built-in timedemo, the player grabbing the shotgun while demons close in.
Every one of the hundred matches the native oracle byte for byte:

<p align="center">
  <img src="docs/doom_regex_clip.gif" width="640" alt="100 frames of the timedemo computed by substitutions">
</p>

## Why this is even possible

Iterated string rewriting is Turing-complete - it's a Markov algorithm, one of
the classic models of computation. So the question was never *whether* a pile
of regexes can run DOOM, but whether it can do it before the heat death of the
universe, and how you prove it isn't cheating.

The rules implement a small 32-bit CPU (RVM-1):

- **State** is a string like
  `RVM1|ST:run|PH:0|CI:...|PC:00046bbc|R0:...|R7:...|CLK:...|` followed by zones:
  lookup tables, flat RAM (`#N`), the program (`#P`), the framebuffer (`#F`),
  the WAD (`#W`), sparse high memory (`#M`), and the I/O tail.
- **Addition** is eight lookahead probes into a 512-entry full-adder table,
  carry threaded through capture groups. Multiplication and division run as
  micro-phases with a transient accumulator field in the header.
- **Memory access** jumps an *exact number of characters* into the flat RAM
  zone. The jump length is assembled from the address digits by empty "bit
  marker" groups and conditional jumps - a binary tree spelled in regex. No
  scanning; landing on a slot is O(1).
- **Fetch** does the same trick with the program counter to find the current
  instruction slot.

DOOM (via [doomgeneric](https://github.com/ozkl/doomgeneric)) is compiled to
this CPU with [8cc and ELVM](https://github.com/shinh/elvm), following the
trail blazed by [BFDoom](https://github.com/jasperdevs/BFDoom).

None of this is taken on faith: a reference emulator runs the same
instruction set in Python, and the test suite requires the string to equal
the emulator's state byte for byte after every single substitution. The
rendered frames match natively compiled DOOM, SHA-256 to SHA-256.

## Numbers

| | |
|---|---|
| rewrite rules | 544, fixed and SHA-256-hashed before the run |
| machine state | one string, 96.6 MB |
| one frame of E1M1 | 13 994 067 substitutions |
| the 100-frame clip | ~1.25 billion substitutions |
| speed | ~80 000 substitutions/s per core (PCRE2 with JIT, measured across the clip run) |
| first working build | 7 substitutions/s - the last 295 k substitutions of the frame alone took 12 hours |

The four orders of magnitude between the first and the last row is its own story: digit-tree
fetch instead of scanning the program zone, dotall jumps so the JIT advances
a pointer instead of scanning for newlines, a flat memory zone instead of a
sparse cell scan, and an identity-skip splice so an 80 MB prefix that a
substitution keeps verbatim is never copied at all.

## Try it

**[Download the demo](https://github.com/4RH1T3CT0R7/doom-regex/releases/latest)** -
unzip, double-click `doomregex_demo.exe` (Windows). It launches the real
machine on the real ruleset and shows the frame being rendered live, along
with the substitution feed: which rule fired, what it consumed, what it
wrote. A frame takes a few minutes of watching the machine paint.

Or build everything yourself:

```
# rules
py -3.11 vm/genpattern.py
# driver (gcc + static PCRE2, LINK_SIZE=4)
bash scripts/build_driver.sh
# run the machine
rvm.exe --rules vm/rules_rvm.rgxset --state snapshot.rvstate
```

The test suite (`py -3.11 -m pytest tests/`) runs the lockstep diff against
the reference emulator, driver agreement tests (the C driver and a Python
prototype must produce identical bytes), and golden checks.

## Prior art, and what's new here

- Nicholas Carlini's [Regex Chess](https://nicholas.carlini.com/writing/2025/regex-chess.html)
  plays chess with 84 688 substitutions - but as a fixed straight-line
  sequence, deliberately not Turing-complete. This project is the other
  branch: a fixed *cyclic* ruleset, real model-of-computation territory.
- [BFDoom](https://github.com/jasperdevs/BFDoom) runs DOOM compiled to
  Brainfuck. No regex involved; we borrowed their ELVM toolchain patches
  gratefully.
- sed has Tetris and Sokoban; esolang folks proved iterated regex
  replacement Turing-complete years ago.

As far as we can tell, nobody had run DOOM - or any game, or any video - on
an iterated regex substitution loop before. If you know prior art we missed,
open an issue.

## Repository map

```
vm/          ISA, assembler, reference emulator, rule generator, state codec
driver/      the C driver: PCRE2, one loop, honesty contract in comments
doom/        doomgeneric port, ELVM toolchain patches, build scripts
tests/       lockstep suite, driver agreement, goldens
scripts/     snapshot baking, benchmarks, clip/timelapse builders
demo/        the downloadable viewer (Win32/GDI)
docs/        the interactive site (GitHub Pages)
```

## License

GPL-2.0. The repository contains a port of DOOM (via doomgeneric), and the
DOOM source is GPL - everything downstream inherits it. doom1.wad is the
original id Software shareware data and is not covered by the GPL.
