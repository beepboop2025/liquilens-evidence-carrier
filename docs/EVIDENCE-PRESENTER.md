# Evidence presenter

`liquilens_evidence.present` is the shared Python presenter for LiquiLens,
Seiche and Undertow readings. MCP servers, CLIs, notebooks and other Python
surfaces call it so they all show the same states, clocks, sources and limits.
It implements the evidence presentation contract and the portable surfaces
rules in liquilens-site (`docs/design/EVIDENCE-PRESENTATION-CONTRACT.md` and
`docs/design/PORTABLE-SURFACES.md`) at commit
`59302ce87ef186ed01fb8ce15f665cf29ad4cfb6`, the head of liquilens-site pull
request 224, which was not merged when this module was written.

It is presentation only. It adds no field to any payload, MCP schema or
Evidence Carrier, never verifies a carrier, and never grants authority: every
view ends with the producer's boundary sentence, or "No execution,
recommendation or credit-rating authority."

## One file to copy

`src/liquilens_evidence/present.py` imports only the Python standard library,
runs on Python 3.10 and later, and never imports the rest of this package.
The ten display states and the colour and font tokens it needs are embedded
as constants. Other stdlib-only projects can copy the file byte for byte.

The token exports it embeds are vendored beside it, byte for byte, in
`src/liquilens_evidence/design/` (`ansi.json`, `family-tokens.json`) with
`SOURCE.json` recording the liquilens-site repository, path, commit, git blob
and SHA-256 of each file. They ship as package data. Tests fail when a
vendored digest or an embedded constant drifts. Never edit a vendored copy:
change the source in liquilens-site and copy it again.

## Display states

| Key | Word | Mark | ASCII | Role | Calm |
| --- | --- | --- | --- | --- | --- |
| `observed` | Observed | ● | `*` | good | yes |
| `derived` | Derived | ◆ | `+` | good | yes |
| `structural` | Structural | ■ | `#` | muted | no |
| `research` | Research | ◇ | `^` | muted | no |
| `provisional` | Provisional | ◌ | `~` | warn | no |
| `partial` | Partial | ◐ | `%` | warn | no |
| `stale` | Stale | ○ | `o` | warn | no |
| `restricted` | Restricted | ⊘ | `/` | warn | no |
| `not-covered` | Not covered | ⬚ | `:` | warn | no |
| `unavailable` | Unavailable | ▨ | `x` | bad | no |

Partial, Stale, Restricted, Not covered and Unavailable are gaps. A gap never
shows a value, a zero, `n/a`, a bare `-` or an empty cell, and never takes
the good colour. A value state with nothing to show becomes Unavailable.

## From a carrier

`derive_display_state` applies the contract's section 3 order and returns the
winning state, the winning rule and every reason code that applied.
`reading_from_carrier` builds the presentation slots from a carrier plus the
verification result you already have:

1. not covered, 2. `reject` with `evidence_not_yet_known` or
`evidence_as_of_in_future`, 3. `claim_unavailable`, 4. any rights code or
`claim_restricted` (and an access-gate refusal), 5. insufficient history,
6. `evidence_expired` or the product's stale rule, 7. provisional,
8. `claim.status`.

The value is read from your documented payload path only when the disposition
is `full`. A `metadata_only` carrier still shows its label, clocks and
sources. A `reject` carrier shows only its product, identity and reason codes.

## From a product's own JSON

`state_for(product, native, **qualifiers)` is the contract's section 4 table.
It returns a `ProductState` with the display state, the literal label and a
severity (only for a scored Undertow tier). Unknown products, unknown values
and qualifiers that do not apply raise: nothing falls back to a calm state.

| Product | Native value | State | Label |
| --- | --- | --- | --- |
| LiquiLens | `observed` | Observed | Accepted filing evidence |
| LiquiLens | `observed` with `age_days` over 400 (`annual=True`) or 183 | Stale | Stale evidence |
| LiquiLens | `stale`, `historical` | Stale | Stale evidence, Historical evidence |
| LiquiLens | `unavailable`, `not_disclosed` | Unavailable | Evidence unavailable, Not disclosed |
| LiquiLens | `provisional`; `stale` with `provisional_business_update=True` | Provisional, Stale | Provisional business update, Stale provisional business update |
| LiquiLens | `withheld` | Restricted | Withheld |
| LiquiLens | `not_covered`, `lender_not_listed`, `not_in_slim` | Not covered | Not covered (print the page's sentence as the reason) |
| LiquiLens | `not_applicable`, `not_assessed` | Not covered | Not applicable (print the producer's basis as the reason), Not assessed |
| LiquiLens | `PARTIAL` | Partial | PARTIAL |
| LiquiLens | `identity` | Structural | Identity record |
| LiquiLens | `failure_radar` with `tier_publication` paused | Research | Research only, with no tier or severity |
| Seiche | `observed`, `derived`, `structural`, `restricted`, `unavailable` | the same | the word itself |
| Undertow | `SURPLUS`, `NORMAL`, `STRAINED`, `DEFICIENT` with `scored=True` | Observed, severity good, good, warn, bad | the tier word |
| Undertow | `ACCRUING`, `PARTIAL`, or a tier without `scored=True` | Partial | ACCRUING or PARTIAL |
| Undertow | `UNAVAILABLE` (no qualifying measures; `scored=True` raises) | Unavailable | UNAVAILABLE |
| Undertow | board `behind`, `stale` | Stale | the board's `note` |
| Undertow | board `unreachable`, or `ok=False` | Unavailable | the board's `status` |
| Undertow | `exit_check.available`, `exit_check.unavailable`, `exit_check.partial` | Derived, Unavailable, Partial | Available, Unavailable, Partial |
| Undertow | `restricted` (input-rights boundary) | Restricted | Restricted |
| Undertow | `observed`, `derived`, `structural`, `unavailable` (a reading's own evidence state) | the same | the word capitalised |
| Undertow | Exit Atlas `issuer_disclosed`, `context`, `accruing`, `partial` | Observed, Research, Partial, Partial | Issuer disclosed, Context (display only), Accruing, Partial |

Three things are not readings and never get a display state:

- Check words print through `check_label(product, word)` as words only, with
  no mark or state colour: LiquiLens reconciliation (`reconciled`,
  `within_rounding`, `inconsistent`) and Undertow `verify()` (`match`,
  `mismatch`, `indeterminate`). `inconsistent` and `mismatch` are always
  printed, and `indeterminate` is never shown as a pass or a mismatch.
- Name lookups print through `resolution_notice(status, candidates=...)`:
  "No match for this name" or "Ambiguous (3 candidates)". A name with no match
  may still be covered under another name or key, so it is never Not covered.
- Licence values such as `consent_required` belong in the reading's rights
  and limits.

`reading_from_native(product_state, ...)` builds the slots for a native
reading. `reading_not_covered(message=...)` prints the producer's message
verbatim, and `reading_access_refused(message=...)` turns an access gate's 401
or 402 into Restricted, "Withheld", with the gate's message as the reason.

## Rendering

`render_text` prints a view: each reading as two or three lines, then the
boundary sentence once. `render_reading` prints one reading. Marks are
Unicode or ASCII. Colour is `none`, `truecolor`, `ansi256` or `ansi16` from
the token roles, and goes only on the mark and state word (and a scored
Undertow tier word). It never sets a background, and body text keeps the
terminal's default foreground. `terminal_style` is the only function that
reads the environment: it honours `NO_COLOR`, dumb terminals and output that
is not a terminal by choosing ASCII marks and no colour.

Values print exactly as served: a string is untouched (Indian lakh grouping
included), a number is never rounded or regrouped. A period prints as
`YYYY-MM-DD`, an instant as `YYYY-MM-DD HH:MM UTC`, and nothing is converted
to local time. Producer text cannot start a new line or recolour a terminal:
line breaks become spaces, and other control and bidirectional formatting
characters print as visible escapes.

## Three uses

Every name, figure and message below is synthetic.

### An MCP tool result

Keep the existing serialized JSON as the first text block, unchanged, because
clients and tests may parse it. Append the contract rendering as an additional
text block after the serialized JSON. It states nothing that
`structuredContent` lacks.

```python
import json
from datetime import datetime, timezone

from liquilens_evidence import present

served = {  # synthetic
    "label": "Example gross NPA ratio",
    "institution": "Example Bank",
    "evidence_state": "observed",
    "value": "3.42",
    "unit": "%",
    "period_end": "2026-09-30",
    "filed_at": "2026-10-24",
    "publisher": "Example Publisher",
}

reading = present.reading_from_native(
    present.state_for("liquilens", served["evidence_state"]),
    label=served["label"],
    subject=served["institution"],
    value=served["value"],
    unit=served["unit"],
    period=served["period_end"],
    known_at=served["filed_at"],
    checked_at=datetime.now(timezone.utc),
    sources=[served["publisher"]],
)
result = {
    "content": [
        {"type": "text", "text": json.dumps(served)},
        {"type": "text", "text": present.render_text(reading)},
    ],
    "structuredContent": served,
}
```

The appended block reads:

```text
Example gross NPA ratio, Example Bank: 3.42 %
● Observed (Accepted filing evidence) | period 2026-09-30 | known 2026-10-24 | source Example Publisher

No execution, recommendation or credit-rating authority.
```

An access gate's refusal keeps the same shape:

```python
reading = present.reading_access_refused(
    message="Example gate: an API key is required.", http_status=402
)
```

```text
Not stated: ⊘ Restricted (Withheld)
reason: Example gate: an API key is required. | checked Not stated
```

### A notebook table

```python
from datetime import datetime, timezone

import pandas as pd

from liquilens_evidence import to_flat_row, verify_evidence_carrier
from liquilens_evidence.present import (
    ReadingTable,
    add_display_columns,
    reading_from_carrier,
)

clock = datetime(2026, 11, 12, 8, 30, tzinfo=timezone.utc)
readings, rows = [], []
for carrier in carriers:  # synthetic carriers you already hold
    verified = verify_evidence_carrier(carrier, evaluated_at=clock)
    reading = reading_from_carrier(
        carrier,
        verified,
        evaluated_at=clock,
        value_path=("ratio", "value"),
        unit_path=("ratio", "unit"),
    )
    readings.append(reading)
    if verified.disposition.value != "reject":  # a rejected carrier has no row
        rows.append(add_display_columns(to_flat_row(verified), reading))

ReadingTable(readings)  # renders as the dark, token-styled table in Jupyter
frame = pd.DataFrame(rows)  # value is a float, NaN when absent, never 0
```

`add_display_columns` keeps every `to_flat_row` column as it is and adds
`display_state`, `value`, `unit` and `source_publisher`. The default form, with
NaN for an absent value, is for pandas and other data frames. For JSON
surfaces (APIs, OpenBB `OBBject` rows, Workspace widgets) pass
`json_safe=True`: an absent value is `None`, and a row that would carry NaN or
Infinity raises. `reading_number` always returns NaN for an absent value.

The table has an explicit dark ground from the tokens, right-aligned tabular
figures, a state cell with mark and word, a hatched state word in the value
cell of every gap, and a source and clock line under the table. It has no
script, no stylesheet and no external font or resource, and every producer
string is escaped.

### A terminal

```python
import sys

from liquilens_evidence.present import render_text, terminal_style

marks, colour = terminal_style(sys.stdout)
print(render_text(readings, marks=marks, colour=colour))
```

With `NO_COLOR` set, or when piped, the same view prints ASCII marks and no
escape codes:

```text
Example funding ratio, Example Series: 12.34 %
* Observed | period 2026-08-24 | known 2026-08-24 09:02 UTC | source Example Publisher

Example series, Example State: x Unavailable
reason: rights_restricted | checked 2026-11-12 08:30 UTC

No execution, recommendation or credit-rating authority.
```

## Decisions where the contract left a choice

Each choice is the reading that never makes a gap look calm.

- A gap never shows a value, Stale included, even when a product's own stale
  rule (not the carrier's expiry) made it stale. Its clocks still print.
- A non-`full` disposition always carries a reason code. An unknown code is
  treated as Restricted, and inputs that no verification produces (a `full`
  disposition with codes, a reject-only code without `reject`) raise.
- A full carrier whose documented value path is missing, null or a
  placeholder is Unavailable rather than an empty calm reading.
- When a product row and the carrier's claim status disagree at rule 8, the
  less calm one wins; a product row's label and severity show only when its
  state is the one displayed.
- A period prints as a date when it is a date or midnight UTC, and in full
  as `YYYY-MM-DD HH:MM UTC` otherwise, so no time is silently dropped.
- A literal label that only repeats the state word in another case is printed
  once. In a numeric column a gap shows its state word, never a label that
  could carry digits.
- Published Failure Radar tiers have no row in the contract yet, so
  `tier_publication` "published" raises. LiquiLens itself treats any other
  state as paused, and so does the presenter.
