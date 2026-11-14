# nemeq-core

**Libbu** — the deterministic kernel of Nemeq OS. Six primitives, nothing else.

Nemeq is an open ecosystem for embodied AI agents. Libbu is its smallest part:
the kernel that runs sessions, routes calls, checks capabilities, spawns
processes and supervises init. It does not think.

## What it is

Six primitives. Nothing else in the kernel.

1. **Session loop** — one agent turn against the LLM, repeated until done
2. **Context RAM** — what is resident in the current window, with a budget
3. **Dispatch** — routing a tool call to a registered function
4. **Capability check** — before any tool or platform call
5. **Spawn and reap** — agent processes
6. **Supervise** — restart the init agent if it dies (PID 1 only)

The kernel is deterministic: same input, same state, same result. It does not
hold state beyond the session. Two `Kernel` instances in one process are fully
isolated. The only non-determinism is the default session identifier (a UUID);
pass your own `session_id` and everything is reproducible.

## What it is not

Everything below lives in agents (`Epishu`), not in the kernel:

| Function | Home |
|---|---|
| Logging | Tuppu |
| Memory consolidation | himem, consolidate |
| Verification | Kittu |
| Data handling | tabled, nd |
| Package management | massaru |
| Scheduling | adannu |
| Sleep / incarnation lifecycle | massaru, adannu |

If you find yourself adding a seventh primitive, stop and re-read the spec.
The boundary is the point.

## Status

**SDD-first.** The specification is primary; code is written against it.

- **Kernel:** implemented (Wave 1). 48 tests + 1 container-only test, line
  coverage 97.3 %, 32/32 mutants caught.
- **Runtime agents:** implemented (Wave 2). `init`, `reu`, `adannu`, `capd`;
  14 tests, coverage 87.9 %, 5/5 mutants.
- **Specification:** SDD v1.3.1 (closes the blocking contract questions from
  two independent reviews).

- Spec repository: [nemeq-project/spec](https://github.com/nemeq-project/spec)
- SDD: `nemeq_libbu_kernel_SDD_v1.3_20261113.md` (v1.3.1)

## Layout

```
nemeq-core/
├── kernel/
│   ├── libbu.py            # six primitives, standard library only
│   ├── test_libbu.py       # unit + integration tests
│   └── mutation_run.py     # mutation harness (32 mutants)
├── agents/
│   ├── init.py             # PID 1: reads riksu.toml, starts agents
│   ├── reu.py              # watchdog: restarts failed services
│   ├── adannu.py           # scheduler: wakes, sleep events
│   ├── capd.py             # capability service (qatum)
│   └── tests/
├── riksu.toml              # manifest: agents, capabilities, schedule
├── CHANGELOG.md
├── LICENSE
└── README.md
```

## Requirements

- Python 3.11 or newer
- Standard library only in `kernel/libbu.py` and `agents/`
- `pytest` and `pytest-cov` for tests (dev dependencies, not runtime)

## Quick start

```bash
python3 -m pytest kernel/test_libbu.py -q
python3 -m pytest kernel/test_libbu.py --cov=kernel.libbu --cov-fail-under=80
python3 kernel/mutation_run.py          # 32 mutants must be caught
python3 -m pytest agents/tests -q
python3 agents/tests/mutation_run.py    # 5 mutants must be caught
```

The container-only test (`test_supervise_history`) needs a PID namespace:

```bash
unshare --user --pid --fork python3 -m pytest kernel/test_libbu.py -k supervise
```

## Exit codes

Per-SDD codes. 0–9 are global; 10+ are assigned by each SDD and are not shared
between agents.

| Code | Meaning |
|---|---|
| 0 | Success |
| 10 | `MaxTurnsExceeded` |
| 11 | `LLMError` |
| 12 | `ToolError` (`UnknownTool`, `ToolFailed`, `ToolTimeout`) |
| 13 | `QatumDenied` |
| 14 | `ReapUnknown` |
| 15 | `NotSupervisor` |
| 16 | `ContextOverflow` |
| 17 | `ReapTimeout` |
| 18 | `SpawnFailed` |
| 19 | `SessionUnknown` |

## Terminology

Nemeq uses an English term and a Sumero-Akkadian brand name. The kernel only
touches sessions and processes — not animas, bodies or incarnations — but the
vocabulary matters across the project:

| English | Brand | Meaning |
|---|---|---|
| Kernel | Libbu | The six primitives, deterministic |
| Anima | Zi | Portable soul package |
| Body | Su | Environment where an anima lives |
| Incarnation | Tush | One anima + one body |
| Agent | Epishu | Any running worker |
| Capability | Qatum | A granted right |
| Verification | Kittu | Independent paths + quorum |

Naming rule: `anima_id`, not `soul_id`. The word `soul` is forbidden in
declarations (spec-lint enforces this).

## Quoriosity

Nemeq's central principle: an agent asks and acts only when its curiosity is
confirmed by a sufficient quorum of independent grounds.

The kernel provides the mechanism (capability check, dispatch). Verification
is Kittu's job, not Libbu's. If you are tempted to add a `verify()` primitive
to the kernel, you have misread the boundary.

## Synchronisation

This repository is mirrored to GitHub and back (branches + fast-forward of
`main`, never forced). Conflicts are surfaced as `sync/github-main-*` branches,
never merged silently.

## License

Code: **AGPL-3.0** — see `LICENSE`.
Specification in [nemeq-project/spec](https://github.com/nemeq-project/spec):
**CC BY 4.0**.

## Contact

- Website: [nemeq.org](https://nemeq.org)
- Developer portal: [nemeq.dev](https://nemeq.dev)
- Specification: [nemeq-project/spec](https://github.com/nemeq-project/spec)
- Email: [info@nemeq.org](mailto:info@nemeq.org)
