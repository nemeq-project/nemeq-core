#!/usr/bin/env python3
"""Model test: exactly-once via stable effect_id; epoch is fencing only.

Scenario: dispatch effect, crash before confirm, reopen with new epoch,
reconcile from settlement journal. Stable effect_id => 1 external effect.
Mutant (effect_id includes epoch) => duplicate effect.
This is a specification model, not product code.
"""
import hashlib, json

def eid(session, step, epoch=None):
    raw = f"{session}:{step}" if epoch is None else f"{session}:{step}:{epoch}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

def run(mutant=False):
    journal = {}
    sends = 0
    # turn 1: intent -> dispatched, crash before confirm
    e = eid("s1", 1, epoch=1 if mutant else None)
    journal[e] = {"status": "dispatched"}
    sends += 1
    # reopen with epoch=2, same logical step
    e2 = eid("s1", 1, epoch=2 if mutant else None)
    if e2 in journal:
        # existing effect: reconcile read-back, do not re-send
        journal[e2]["status"] = "confirmed"
    else:
        # mutant: new id -> duplicate send
        sends += 1
        journal[e2] = {"status": "confirmed"}
    return sends, journal

stable_sends, stable_j = run(mutant=False)
mutant_sends, mutant_j = run(mutant=True)
assert stable_sends == 1, f"stable sent {stable_sends}"
assert mutant_sends == 2, f"mutant sent {mutant_sends}"
print("stable: sends =", stable_sends, "| journal =", json.dumps(stable_j))
print("mutant: sends =", mutant_sends, "| journal =", json.dumps(mutant_j))
print("PASS: stable effect_id => exactly-once; epoch-in-key => duplicate (mutant caught)")
