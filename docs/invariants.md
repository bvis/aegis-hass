# Invariants

Rules every change must keep. Where a rule has a test, the test checks every
entity or code path it applies to, not just the one a change touched.

## Entities of an offline device are unavailable

When a device is offline, its entities report `unavailable` instead of their
last value. A stale "OK" on an offline device is worse than no value (#585).

Exceptions, each for a stated reason:

- **Connectivity** stays available, because it is what reports the device offline.
- **Event entities** (doorbell, button press) stay available. Coming back from
  `unavailable` restores the last event, and automations read that as a new one.
- **Firmware update** stays available. The installed version doesn't go stale
  while the device is off.

Test: `tests/unit/test_invariants.py` builds every entity of every device type
on every platform. A new entity class follows the rule unless it is added to
the exception list with a reason.

## No more requests to Ajax than the official app makes

Data comes from what already flows (the live streams, the periodic refresh,
push). Every change that touches the network states its request delta in the
PR and in the CHANGELOG entry (**Requests to Ajax:**), and the expected delta
is zero. An integration that floods the API could be blocked for everyone.

Test: per feature, e.g. `tests/unit/test_alarm_call_budget.py`. There is no
global check; this rule is enforced in review.

## Unknown fields from Ajax never break a device

Firmware updates add fields. Parsers ignore what they don't know and keep
reading the rest. An exact match on a list of fields dropped every keyfob when
one new field appeared (#504).

Test: none global yet; enforced in review.

## Diagnostics never contain names the user chose

Names set in the Ajax app (spaces, groups, devices, keyfobs) are replaced by
their length, so a dump can be posted publicly.

Test: `tests/unit/test_diagnostics.py::TestUserChosenNamesAreNotInTheDump::test_no_user_chosen_name_appears_anywhere_in_the_dump`.
