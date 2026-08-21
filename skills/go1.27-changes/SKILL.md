---
name: go1.27-changes
description: Reference for what changed in Go 1.27 — use when writing or reviewing Go code in a `go 1.27`+ module so you do not fall back on pre-1.27 training-data assumptions. Covers new language features (generic methods), new and changed standard-library APIs (`encoding/json/v2`, `uuid`, `crypto/mldsa`, `strings.CutLast`, …), and dangerous behavior changes (time channels now unbuffered, `net` `UnixConn` returning raw `io.EOF`, `go tool trace -http` binding localhost-only). Triggers include "go 1.27", "golang 1.27", "go1.27", "1.27 の変更点", "Go 1.27 で開発", "is this valid in go 1.27".
---

# Go 1.27 Changes (for LLM-assisted development)

## Overview

Go 1.27 was released in **August 2026**. If your training data predates it, you do **not** reliably know what changed — this skill is the source of truth for the parts that affect how you *write* Go code.

Scope: this reference emphasizes **developer-facing changes** — language, standard library, and `go` command behavior that changes code or workflows — and it deliberately foregrounds **gotchas**: places where pre-1.27 habits now produce wrong code or wrong behavior. Runtime/compiler/linker/ports changes are covered briefly at the end for build-error triage.

Official release notes: <https://go.dev/doc/go1.27>

### How to use this reference

1. When editing Go, first confirm the module targets 1.27 (see [Verification](#verification)). If it targets an older version, ignore the "new API" sections and treat only the removed-`GODEBUG` / requirement notes as relevant to upgrades.
2. Prefer the new idioms below over the pre-1.27 workarounds you were trained on.
3. **Verify exact signatures before emitting code.** Item descriptions here are accurate, but a few precise signatures are marked *(verify)* — confirm them at the linked `pkg.go.dev` page rather than guessing. Never invent an API shape.

---

## Instructions

### 1. Language changes

#### Generic methods (biggest change)

Methods may now declare their **own** type parameters — previously you had to hoist such logic into a package-scoped generic *function*.

```go
// Before 1.27: illegal — a method could not add type parameters.
//   func (r *Rand) N[Int intType](n Int) Int { ... }  // compile error
// You wrote a top-level function instead:
func N[Int intType](r *Rand, n Int) Int { /* ... */ }

// Go 1.27: legal — the method carries its own type parameter.
func (r *Rand) N[Int intType](n Int) Int { /* ... */ }
```

Restriction to remember: **interface methods may not declare type parameters, and interface methods cannot be implemented by generic methods.** So you cannot put `N[Int intType]()` in an interface.

#### Struct literal keys accept any field selector

A key in a struct literal may now be any valid field selector (e.g. a promoted/embedded field path), not just a top-level field name. When you see a keyed struct literal using a nested selector, it is valid 1.27 syntax. *(verify the exact selector forms at the release notes if a specific case matters.)*

#### Generalized function type inference

Type inference now applies in all contexts where a generic function is assigned to, or converted to, a matching function type — so more assignments of generic functions to typed function variables compile without explicit instantiation.

### 2. New standard-library packages — reach for these instead of old workarounds

| Package | Import path | Use it for | Pre-1.27 habit to drop |
|---------|-------------|-----------|------------------------|
| JSON v2 | `encoding/json/v2` | New, faster, stricter JSON | Don't assume `encoding/json` is the only option |
| JSON tokens | `encoding/json/jsontext` | Low-level JSON syntax (`Encoder`, `Decoder`, `Token`, `Value`) | Hand-rolling token scanners |
| UUID | `uuid` (top-level) | Generating/parsing UUIDs | Pulling in `github.com/google/uuid` — check if stdlib suffices |
| ML-DSA | `crypto/mldsa` | Post-quantum signatures (FIPS 204) | — |
| SIMD (experimental) | `simd`, `simd/archsimd` | Vector ops; needs `GOEXPERIMENT=simd` | — |

#### `encoding/json/v2`

Exact entry points: `Marshal`, `MarshalWrite`, `MarshalEncode`, `Unmarshal`, `UnmarshalRead`, `UnmarshalDecode`. All take variadic `Options`.

```go
import jsonv2 "encoding/json/v2"

data, err := jsonv2.Marshal(v)          // v2 API
err = jsonv2.Unmarshal(data, &v)
```

**Gotcha — stricter defaults:** v2 *rejects invalid UTF-8 in JSON strings* and *rejects duplicate object names*, where v1 tolerated both. Code that round-trips lax JSON under `encoding/json` may now error under v2. The v1 `encoding/json` package still exists and is now backed by v2, but keeps v1 defaults; don't assume switching packages is behavior-neutral.

For `uuid`, the package exists to generate and parse UUIDs, but the release notes do not pin the function names — **look up the exact API at <https://pkg.go.dev/uuid> before calling it** (do not assume `google/uuid`-style names).

### 3. Behavior-change gotchas (same code, different result)

These are the highest-risk items: existing-looking code compiles but behaves differently than pre-1.27.

- **Time channels are now always unbuffered (synchronous).** The `asynctimerchan` GODEBUG (added in 1.23) is permanently removed; channels created by package `time` (e.g. `time.Timer.C`, `time.Ticker.C`) are always unbuffered regardless of GODEBUG. Do not rely on a buffered tick being retained while you were busy — semantics are synchronous delivery. Review timer/ticker draining logic. See <https://go.dev/doc/go1.27>.
- **`net` `UnixConn` returns raw `io.EOF`.** `UnixConn` read methods now return `io.EOF` **directly** instead of wrapping it in a `*net.OpError`. Code that type-asserts the error to `*net.OpError` on EOF will stop matching — compare with `errors.Is(err, io.EOF)`.
- **`go tool trace -http` binds localhost by default.** Given only a port (e.g. `-http=:6060`), it now listens on **localhost only** (consistent with `go tool pprof`). To listen on all interfaces you must be explicit: `-http=0.0.0.0:6060`. Fix any container/remote workflow that relied on the old all-interfaces default.
- **`compress/flate` output may differ.** Compression speed improved and the `Writer`'s exact byte output can differ from 1.26. Golden-file/byte-exact tests and reproducibility checks over compressed bytes may need regenerating; decompression is unaffected.
- **Function-literal identity may change.** The compiler now generates simpler, consistent names for function literals and may deduplicate identical literals that capture the same closure data. Code that compared function *pointers* for equality (a fragile pattern already) can observe different results.

### 4. Handy new/changed stdlib APIs

Prefer these where they fit; confirm exact signatures at the linked package before use.

| API | What it does | Note |
|-----|--------------|------|
| `strings.CutLast`, `bytes.CutLast` | Slice around the **last** occurrence of a separator (mirror of `Cut`, which uses the first) | Signature shape parallels `Cut(s, sep) (before, after, found)` |
| `net/url` `URL.Clone`, `Values.Clone` | Deep-copy a `*URL` / `Values` | No more manual struct copies |
| `math/rand/v2` `Rand.N` | Generic method matching the top-level `N` function | `func (r *Rand) N[Int intType](n Int) Int` |
| `net/http/httptest.NewTestServer` | Server on an in-memory fake network, made for `testing/synctest` | *(verify signature at pkg.go.dev/net/http/httptest)* |
| `testing/synctest.Sleep` | Combines `time.Sleep` + `synctest.Wait` | Use inside `synctest` bubbles |
| `math/big` `Int.Divide` | Quotient+remainder with rounding modes `Trunc`, `Floor`, `Round`, `Ceil` | *(verify exact signature at pkg.go.dev/math/big)* |
| `database/sql.ConvertAssign` | Exposed value-conversion helper | — |
| `database/sql/driver.RowsColumnScanner` | Driver interface for direct column scanning | — |
| `bytes` / `strings` | (see `CutLast` above) | — |
| `unicode` | Upgraded Unicode 15 → **Unicode 17** | New code points/properties available |

### 5. `go` command & toolchain changes (affect workflows and reviews)

- **`vet` `stdversion` runs in `go test` by default:** flags use of standard-library symbols newer than the Go version declared in `go.mod`. If you write 1.27 stdlib APIs in a module still declaring `go 1.26`, tests now report it — bump the `go` directive.
- **`go doc` gains `package@version`:** e.g. `go doc example.com/pkg@v1.2.3`; new `-ex` flag lists executable examples, and passing an example name prints its source.
- **`go fix` modernizers changed:** added `atomictypes`, `embedlit`, `slicesbackward`, `unsafefuncs`; removed `fmtappendf`; renamed `waitgroup` → `waitgroupgo`. Use the new names when invoking analyzers.
- **`go mod tidy` merges require blocks:** for `go 1.27+` modules it collapses duplicate `require` blocks into at most two (direct, then indirect), preserving comment blocks. Expect a one-time diff in `go.mod` shape.
- **`go test -json` adds `OutputType`:** `"Action":"output"` lines may carry an optional `"OutputType"` field (`"error"`, `"error-continue"`, `"frame"`). Tooling parsing test JSON can use it but must tolerate its absence.
- **Response files (`@file`):** `compile`, `link`, `asm`, `cgo`, `cover`, `pack` now accept GCC-style `@file` argument files.
- **`crypto/tls` additions:** `MLKEM1024` key exchange and `MLDSA44/65/87` signature schemes are supported; several legacy `GODEBUG`s were permanently **removed** (`tlsunsafeekm`, `tlsrsakex`, `tls3des`, `tls10server`, `x509keypairleaf`) — code depending on those escape hatches will break on upgrade.

### 6. Upgrade / build-triage notes (brief)

- **macOS 13 Ventura or later is required.** Older macOS is discontinued.
- **`bzr` (Bazaar) VCS support removed** from the `go` command.
- **Removed `GODEBUG` settings** (`asynctimerchan`, `gotypesalias`, the `crypto/tls` list above, `x509keypairleaf`): setting them in `go.mod` `godebug`/`//go:debug` to an old value now errors; to the final default value is accepted.
- **Runtime:** faster small-object allocation; new generally-available `goroutineleak` profile (`/debug/pprof/goroutineleak`); goroutine `pprof` labels appear in traceback headers for `go 1.27+` modules (disable with `GODEBUG=tracebacklabels=0`).
- **PowerPC 64-bit big-endian on Linux** now uses the ELFv2 ABI (needs kernel 3.13+); cgo/PIE/external linking gained support there.

---

## Verification

Before relying on anything above in generated code:

1. **Confirm the toolchain/module is on 1.27:**

   ```bash
   go version                 # expect go1.27 or later
   grep -E '^go [0-9]' go.mod # expect "go 1.27" (or higher)
   ```

   If the module declares an older `go` directive, new-API sections do not apply until the directive is bumped; `vet`'s `stdversion` check will otherwise flag the code.

2. **Confirm exact signatures** for any item marked *(verify)* (and for `uuid`) at `https://pkg.go.dev/<import-path>` — e.g. <https://pkg.go.dev/encoding/json/v2>, <https://pkg.go.dev/math/big>, <https://pkg.go.dev/uuid>. Do not emit an invented signature.

3. **When a gotcha in §3 applies**, re-check the specific code path (timer draining, `*net.OpError` type assertions on EOF, `go tool trace` bind address, byte-exact `flate` assertions, function-pointer comparisons) rather than assuming pre-1.27 behavior.

4. Cross-check any uncertain claim against the official notes: <https://go.dev/doc/go1.27>.
