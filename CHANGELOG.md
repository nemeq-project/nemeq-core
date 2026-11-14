# Changelog

## [Unreleased]

## [0.1.0] - 2026-11-14

### Added

- **Kernel (Wave 1).** `kernel/libbu.py`: six primitives — session loop,
  context RAM, dispatch, capability check, spawn/reap, supervise; standard
  library only. Codes 10–19, invariants I-1…I-5, deterministic behaviour.
- **Runtime agents (Wave 2).** `agents/init.py`, `agents/reu.py`,
  `agents/adannu.py`, `agents/capd.py`, manifest `riksu.toml`. Composition via
  subprocess (no shell), JSON on stdout, restart of a killed agent ≤ 5 s.
- **Tests and mutation harness.** 48 + 1 kernel tests (97.3 % line coverage,
  32 mutants) and 14 agent tests (87.9 % coverage, 5 mutants).
- **SDD v1.3.1** for Libbu: closed contract for the calling subject,
  capability mapping, timeout mechanism, token accounting, session identity.

### Notes

- `test_supervise_history` is container-only: it requires a PID namespace
  (`unshare --user --pid --fork`) because `NotSupervisor` is raised outside
  PID 1 by contract.
