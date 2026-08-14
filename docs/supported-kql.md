# Supported KQL

This page is the authoritative list of what the `streaming-kql` engine accepts.
The baseline is the Azure Monitor
[**transformations (DCR) KQL surface**](https://learn.microsoft.com/en-us/azure/azure-monitor/data-collection/data-collection-transformations-kql)
— "single row in → zero or one row out" — plus the remaining **stateless**
operators. KQL semantics follow the
[official Microsoft KQL reference](https://learn.microsoft.com/en-us/kusto/query/).

Under the per-record model almost every KQL tabular operator has a stateless
per-record form; the few gaps are listed under
[What's not supported](#whats-not-supported).

> **Legend:** ✅ supported · ◐ partial · ☐ planned (raises `KqlUnsupportedError`
> today) · ✗ not planned.

---

## Statements

| Statement | Status | Notes |
|---|---|---|
| `source` | ✅ | The input stream — the only table. Every query starts from `source` (or `print`). |
| [`print`](https://learn.microsoft.com/en-us/kusto/query/print-operator) | ✅ | Emits a single synthetic row; can be piped into operators. |
| [`let`](https://learn.microsoft.com/en-us/kusto/query/let-statement) | ✅ | **Scalar** bindings (`let x = <expr>;`, may chain) and **tabular** bindings to a [`datatable`/`externaldata`/`range`](#reference-tables) table. Scalar-argument function `let` is not yet supported. |

```kusto
let threshold = 80;
source | where Price > threshold
```

```kusto
print x = 2 + 3, y = 'hi'
```

---

## Tabular operators

### Supported

| Operator | Cardinality | Docs |
|---|---|---|
| [`where`](https://learn.microsoft.com/en-us/kusto/query/where-operator) / `filter` | 1 → 0/1 | Keep records matching a predicate. |
| [`extend`](https://learn.microsoft.com/en-us/kusto/query/extend-operator) | 1 → 1 | Add or overwrite calculated columns. |
| [`project`](https://learn.microsoft.com/en-us/kusto/query/project-operator) | 1 → 1 | Select/compute/reorder columns. Supports `Name = expr` and bare column names. |
| [`project-away`](https://learn.microsoft.com/en-us/kusto/query/project-away-operator) | 1 → 1 | Drop the listed columns. |
| [`project-keep`](https://learn.microsoft.com/en-us/kusto/query/project-keep-operator) | 1 → 1 | Keep only the listed columns. |
| [`project-rename`](https://learn.microsoft.com/en-us/kusto/query/project-rename-operator) | 1 → 1 | Rename columns: `NewName = OldName`. |
| [`project-reorder`](https://learn.microsoft.com/en-us/kusto/query/project-reorder-operator) | 1 → 1 | Move listed columns to the front; the rest follow in original order. |
| [`parse`](https://learn.microsoft.com/en-us/kusto/query/parse-operator) | 1 → 1 | Extract columns from a string. Unmatched → nulls. |
| `parse-where` | 1 → 0/1 | Like `parse`, but drops records that don't match. |
| [`parse-kv`](https://learn.microsoft.com/en-us/kusto/query/parse-kv-operator) | 1 → 1 | Extract key/value pairs into typed columns. |
| [`evaluate bag_unpack`](https://learn.microsoft.com/en-us/kusto/query/bag-unpack-plugin) | 1 → 1 | Expand a `dynamic` property bag into columns (optional prefix). |
| [`lookup`](https://learn.microsoft.com/en-us/kusto/query/lookup-operator) | 1 → 0/1 | Enrich each record from a **constant** reference table (`datatable`/`externaldata`). |
| [`join`](https://learn.microsoft.com/en-us/kusto/query/join-operator) | N → M | Join the per-record row-set with a bounded right table (constant table or same-record `source` subquery). |
| [`mv-expand`](https://learn.microsoft.com/en-us/kusto/query/mv-expand-operator) | 1 → N | Expand array/bag column(s) into one row per element. |
| [`union`](https://learn.microsoft.com/en-us/kusto/query/union-operator) | 1 → N | Concatenate the stream with `source` subqueries or constant reference tables. |
| [`summarize`](https://learn.microsoft.com/en-us/kusto/query/summarize-operator) | N → M | Group + aggregate over the **per-record row-set** (see below). |
| [`sort`](https://learn.microsoft.com/en-us/kusto/query/sort-operator) / [`order by`](https://learn.microsoft.com/en-us/kusto/query/order-by-operator) | N → N | Order the per-record row-set. |
| [`top`](https://learn.microsoft.com/en-us/kusto/query/top-operator) | N → ≤N | Highest `N` of the per-record row-set by key(s). |
| [`distinct`](https://learn.microsoft.com/en-us/kusto/query/distinct-operator) | N → ≤N | Distinct column combinations within the per-record row-set. |
| [`take`](https://learn.microsoft.com/en-us/kusto/query/take-operator) / [`limit`](https://learn.microsoft.com/en-us/kusto/query/take-operator) | N → ≤N | First `N` rows of the per-record row-set. |
| [`as`](https://learn.microsoft.com/en-us/kusto/query/as-operator) | N → N | Name the current row-set so later `join`/`union` can reference it. |
| [`partition`](https://learn.microsoft.com/en-us/kusto/query/partition-operator) | N → M | Group the row-set by a column and run a sub-pipeline per group. |
| [`fork`](https://learn.microsoft.com/en-us/kusto/query/fork-operator) | N → N | Run sub-pipelines that capture named side-tables; input passes through. |
| [`count`](https://learn.microsoft.com/en-us/kusto/query/count-operator) | N → 1 | Count the rows of the per-record row-set (`{Count: n}`). |
| [`getschema`](https://learn.microsoft.com/en-us/kusto/query/getschema-operator) | N → M | Describe the row-set's columns as rows. |
| [`serialize`](https://learn.microsoft.com/en-us/kusto/query/serialize-operator) | N → N | Assign columns using **window functions** (`row_number`/`prev`/`next`/`row_cumsum`). |
| [`mv-apply`](https://learn.microsoft.com/en-us/kusto/query/mv-apply-operator) | N → M | Expand array column(s) per row and run a sub-pipeline on each. |
| [`make-series`](https://learn.microsoft.com/en-us/kusto/query/make-series-operator) | N → M | Bin an axis and aggregate into per-bin **arrays**, per group. |
| [`sample`](https://learn.microsoft.com/en-us/kusto/query/sample-operator) | N → ≤N | `N` random rows of the per-record row-set. |
| [`sample-distinct`](https://learn.microsoft.com/en-us/kusto/query/sample-distinctoperator) | N → ≤N | `N` random distinct values of a column. |

#### `parse` details

```kusto
source | parse Message with "user=" User " code=" Code:long
```

- `kind=regex` is accepted; the default follows DCR **full-string match**
  behavior.
- Use `parse-where` to drop non-matching records instead of emitting nulls.

#### `parse-kv` details

```kusto
source
| parse-kv Msg as (user:string, code:long)
  with (pair_delimiter=';', kv_delimiter='=')
```

Keys not present in the input become `null`.

#### `evaluate bag_unpack` details

```kusto
source | evaluate bag_unpack(Context, 'ctx_')
```

The optional second argument is a string prefix applied to the new column names.

#### `lookup` details

`lookup` enriches each streaming record from a **constant reference table** — a
[`datatable`](#datatable) or [`externaldata`](#reference-tables) bound with `let`.
Because the reference table is fixed at compile time and does not depend on the
stream, the operation is fully stateless (1 → 0/1, no row expansion). At most one
matching row is joined (the first, if the reference key is not unique).

```kusto
let Countries = datatable(Code:string, Name:string) [
    "US", "United States",
    "IL", "Israel"
];
source | lookup Countries on Code
```

- **`kind`** — `leftouter` (default) keeps unmatched records with the reference
  columns set to null; `inner` drops unmatched records.
- **`on`** — one or more equi-join keys. Each key is either a shared column name
  (`on Code`) or a mapping (`on LeftCol == RightCol`). Multiple keys are combined
  with AND (`on Sym, Ccy`).
- Reference (non-key) columns are added to the record; on a name collision the
  reference value wins.

> This is the stateless slice of KQL's `lookup`/`join`: joining against a fixed
> reference set. `lookup` keeps at most one match; for multiple matches or other
> join kinds use [`join`](#join-details).

#### `join` details

`join` combines the **per-record row-set** (the left side) with a **bounded
right table** that is fully materialised for the current record — so, unlike a
stream-to-stream join, it needs no cross-record state and **every join kind is
supported**, including right/full outer.

```kusto
source | mv-expand Code = Codes | join kind=leftouter Countries on Code
source | mv-expand x = Items | join kind=inner (source | mv-expand y = Ref | project y) on x == y
```

The **right operand** is one of:

- a **constant reference table** — a `let`-bound
  [`datatable`/`externaldata`](#reference-tables) name (like `lookup`, but it
  emits *all* matches and supports every kind); or
- a **`source` subquery** `(source | …)` — re-derived from **the same input
  record** (e.g. another `mv-expand` of it). It is *not* a second live stream.

Details:

- **`on`** — one or more equi-keys: a shared name (`on Code`) or a mapping
  (`on LeftCol == RightCol`); multiple keys combine with AND.
- **`kind`** — `innerunique` (**default**, dedups the left on the join key),
  `inner`, `leftouter`, `rightouter`, `fullouter`, `leftsemi`, `rightsemi`,
  `leftanti`, `rightanti`.
- Output columns are the left columns plus the right columns; a right column that
  **collides** with a left name is suffixed (`Code` → `Code1`), as in KQL. Semi/
  anti kinds keep only one side's columns.
- A match can produce **multiple rows** (1 → N) when a key repeats on the right.

> Only a *temporal* join of two independent streams is out of scope — and the
> model can't even express it, since `source` always denotes the current record.

#### `mv-expand` details

Expands array or property-bag column(s) into **one row per element** (1 → N).

```kusto
source | mv-expand Ports
source | mv-expand Tag = Tags            // expand into a new column, keep Tags
source | mv-expand with_itemindex=i Items limit 100
```

- **Arrays** expand element-by-element; a **property bag** (`dynamic` object)
  expands into one single-pair bag per key.
- An **empty** array/bag or a **null** produces **zero** rows (the record is
  dropped); a non-array scalar produces one row.
- **Multiple columns** (`mv-expand a, b`) expand in **lockstep** — the *i*-th row
  pairs `a[i]` with `b[i]`; shorter lists are padded with null to the longest.
- `with_itemindex=Name` adds the zero-based element index; `limit N` caps the
  number of elements per input row.

#### `union` details

Concatenates the incoming stream with one or more additional table expressions,
evaluated **per record**. This is the stateless slice of `union`: operands are
restricted to `source` subqueries and **constant** reference tables.

```kusto
source | union (source | where Severity >= 3 | extend Priority = "high")
source | union AllowList                 // AllowList is a let-bound datatable
```

Each operand is one of:

- **`source`** — the incoming record, optionally with its own operators, written
  as a parenthesized subquery `(source | ...)`.
- a **constant reference table** — a `let`-bound
  [`datatable`/`externaldata`](#reference-tables) name.

The incoming (left) row passes through, then each operand's rows are appended.

- **`kind`** — `outer` (default) emits the union of all columns, null-filling
  missing ones; `inner` keeps only columns common to all emitted rows.
- Because operands run on the piped stream, a `where` **before** `union` filters
  records before the branch is evaluated.

> A `union` operand that uses a **stateful** operator (e.g. `summarize`) is
> rejected at compile time, keeping the whole query stateless.

### Aggregating & reordering operators (per-record row-set)

`summarize`, `sort`/`order by`, `top`, `distinct`, and `take`/`limit` are
normally *stream-global* (stateful) in KQL. Here they operate on **the set of
rows produced from a single input record** — the per-record row-set — and never
cross input records, so they stay stateless.

- For a plain record that is still **one row**, these are trivial (a group of
  one, an already-sorted single row, …).
- They become genuinely useful **after a 1 → N operator** — `mv-expand` or
  `union` — that turns one input record into many rows to aggregate/reorder.

> **Important:** this is *not* stream-wide aggregation. `source | summarize
> count()` yields `1` for **every** input record (each record is its own batch),
> not a running total. Cross-record aggregation is stateful and out of scope.

#### `summarize` details

```kusto
source | mv-expand event = Events | summarize Count = count(), Total = sum(event.bytes) by Kind = event.kind
```

- **`by`** groups by one or more key expressions; a bare column `by X` names the
  output column `X`. With **no** `by`, all rows form one group. With **only**
  `by` (no aggregates) it behaves like `distinct` on the keys.
- Output rows contain **only** the group keys and aggregates.
- Anonymous aggregates are auto-named KQL-style: `count()` → `count_`,
  `sum(x)` → `sum_x`.
- Supported aggregates: `count()`, `countif(pred)`, `sum(x)`, `sumif(x, pred)`,
  `avg(x)`, `avgif(x, pred)`, `min(x)`, `max(x)`, `dcount(x)` (exact),
  `make_list(x)`, `make_set(x)`, `any(x)`/`take_any(x)`. Aggregates ignore nulls.

#### `sort` / `top` / `distinct` / `take` details

```kusto
source | mv-expand v = Values | sort by v desc           // order the batch
source | mv-expand v = Values | top 3 by v               // sort desc, keep 3
source | mv-expand v = Values | distinct v               // dedupe within batch
source | mv-expand v = Values | take 5                   // first 5 (== limit 5)
```

- `sort by KeyExpr [asc|desc], …` — default direction is **desc** (KQL default);
  `order by` is a synonym. Nulls sort first under `asc`, last under `desc`.
- `top N by KeyExpr [asc|desc], …` — equivalent to `sort … | take N`.
- `distinct Col, …` — keeps only the listed columns and their distinct
  combinations (bare column names).
- `take N` / `limit N` — the first `N` rows of the batch.

#### `as` / `fork` / `partition` details

These name or reshape the per-record row-set so a stream slice can be **reused as
a table** later in the same record's processing.

```kusto
// `as` names the current row-set; reference it later in join/union:
source | mv-expand v = V | as Snapshot | where v > 1 | join kind=inner Snapshot on v

// `fork` captures named side-tables (the input passes through unchanged):
source | fork Errors=(where sev == "err") Warnings=(where sev == "warn") | union Errors, Warnings

// `partition` groups the row-set and runs a sub-pipeline per group:
source | mv-expand r = Rows | extend g = r.g, n = r.n | partition by g (top 1 by n)
```

- **`as Name`** captures the current row-set (a snapshot) under `Name`; the rows
  flow through unchanged. `Name` is then usable as a table in a later `join`/
  `union` **within the same record**.
- **`fork [Name=](sub-pipeline) …`** runs each sub-pipeline on the current rows
  and stores each result as a named table (auto-named `Fork1`, … if unnamed);
  the operator's own output is the **unchanged input**. Combine the captures with
  `union`/`join`.
- **`partition by Col (sub-pipeline)`** groups the current rows by `Col` and runs
  the sub-pipeline on each group, concatenating the results.

> Scope is **per input record** — names captured by `as`/`fork` live only while
> that record is processed (not across the stream). A true temporal buffer across
> records would require the future stateful extension.

### Reference tables

Constant tables used as `lookup`/`join`/`union` sources (or as a query head).
They are evaluated **once at compile time**.

#### `datatable`

```kusto
datatable(ColumnName:type, ...) [ value, value, ... ]
```

Inline constant rows. Values are listed **row-major** (all columns of row 1, then
row 2, …); the count must be a multiple of the column count. Each value is coerced
to its declared column type (see [type coercion](#type-coercion)).

```kusto
let Tiers = datatable(Plan:string, Level:long) [ "free", 0, "pro", 2 ];
```

#### `range`

```kusto
range Name from Start to Stop step Step
```

Generates a single-column table `Name` with the values `Start, Start+Step, …`
up to (and including) `Stop`. `Start`/`Stop`/`Step` are constant scalars (numbers,
or a `datetime` start/stop with a `timespan` step); `Step` may be negative for a
descending range. Usable as a query head or a `let`-bound table.

```kusto
let Digits = range d from 0 to 9 step 1;
range t from todatetime("2026-01-01") to todatetime("2026-01-03") step 1d
```

#### `externaldata`

```kusto
externaldata(ColumnName:type, ...) [ "path-or-file-uri" ] with (format="csv")
```

Reads constant rows from **local file(s)**. Because streaming-kql is an offline,
in-process library, only local paths and `file://` URIs are supported; remote
schemes (`http`, `https`, `abfss`, …) raise `KqlUnsupportedError`.

Supported `format` values: `csv` (default), `tsv`, `scsv`, `psv`, `txt`/`raw`
(one column per line), `json` (array of objects), and `multijson`/`jsonl`
(newline-delimited objects). Delimited formats have **no header row** and map
fields to the declared columns positionally, matching KQL.

```kusto
let Geo = externaldata(Ip:string, Country:string)
    [ "reference/geo.csv" ] with (format="csv");
source | lookup Geo on Ip
```

### Planned (recognized but not yet implemented)

The remaining KQL tabular operators have a well-defined **stateless per-record
form** (they operate on the current record's row-set, not across records), so
they are implementable — just not built yet. Compiling one raises
`KqlUnsupportedError`:

☐ `scan` (row-by-row state machine) · ☐ `top-nested` (hierarchical top)

---

## Type coercion

When you pass a [`Schema`](usage.md#kqlschema) to `compile`, each declared column
of an input record is **coerced** to its KQL type before the query runs — the
same way an Azure Monitor table or a DCR input arrives already typed. Real feeds
(JSON logs, Event Hubs, syslog) deliver most fields as strings, so this lets you
write natural KQL (`where Created > ago(1d)`) without wrapping every column in
`todatetime(...)`/`toint(...)`.

```python
schema = kql.Schema({"TimeGenerated": "datetime", "Ctx": "dynamic", "Level": "int"})
q = kql.compile("source | where Level >= 3 and TimeGenerated > ago(1h)", schema=schema)
```

| KQL type | Coerced to | Notes |
|---|---|---|
| `string` | `str` | Non-strings are stringified (`true`→`"true"`). |
| `int` / `long` | `int` | Parses numeric strings; `"3.9"`→`3`. |
| `real` / `double` | `float` | |
| `decimal` | `decimal.Decimal` | Exact decimal. |
| `bool` | `bool` | `"true"`/`"1"`→`True`, `"false"`/`"0"`→`False`. |
| `datetime` | tz-aware UTC `datetime` | ISO-8601; `Z` accepted. |
| `timespan` | `timedelta` | `1d`, `2h`, `30m`, `500ms`. |
| `guid` | `str` | Normalized GUID form. |
| `dynamic` | `dict`/`list`/scalar | JSON string is parsed. |

Rules:

- A KQL null (`None`) stays null.
- A value that **cannot** be converted becomes null (default null-tolerant mode).
- **Undeclared** columns are passed through unchanged.
- Values already of the target type pass through untouched.

Coercion also applies to the values of [`datatable`](#datatable) and
[`externaldata`](#reference-tables) reference tables.

---

## Scalar operators

### Comparison

`==` · `!=` · `<` · `<=` · `>` · `>=` — plus case-insensitive string equality
`=~` and inequality `!~`. Comparisons follow KQL
[null / three-valued logic](https://learn.microsoft.com/en-us/kusto/query/logical-operators):
comparing with `null` yields `null` (except `==`/`!=`, which treat two nulls as
equal).

### Logical

`and` · `or` · `not(...)`

### Arithmetic

`+` · `-` · `*` · `/` · `%` · unary `-`. Division/modulo by zero yields `null`.
`+` with any string operand concatenates.

### String operators

All support a leading `!` for negation (e.g. `!has`, `!contains`) and a `_cs`
suffix for the case-sensitive variant:

| Operator | Case-sensitive variant |
|---|---|
| [`has`](https://learn.microsoft.com/en-us/kusto/query/has-operator) | `has_cs` |
| [`contains`](https://learn.microsoft.com/en-us/kusto/query/contains-operator) | `contains_cs` |
| [`startswith`](https://learn.microsoft.com/en-us/kusto/query/startswith-operator) | `startswith_cs` |
| [`endswith`](https://learn.microsoft.com/en-us/kusto/query/endswith-operator) | `endswith_cs` |
| [`in`](https://learn.microsoft.com/en-us/kusto/query/in-cs-operator) | (use with a list literal) |
| [`has_any`](https://learn.microsoft.com/en-us/kusto/query/has-anyoperator) | (case-insensitive; list or dynamic array) |
| [`has_all`](https://learn.microsoft.com/en-us/kusto/query/has-all-operator) | (case-insensitive; list or dynamic array) |
| [`matches regex`](https://learn.microsoft.com/en-us/kusto/query/matches-regex-operator) | — |

`has_any` matches when the target has **any** of the given terms; `has_all`
requires **all** of them. Terms may be a parenthesized list `('a', 'b')` or a
`dynamic` array column; matching is whole-word and case-insensitive.

### Dynamic access

- Member access: `Column.Property`
- Index access: `Column["Property"]` or `Array[0]`

A missing path yields `null` rather than raising.

### Literals

| Literal | Examples |
|---|---|
| number | `42`, `3.14`, `1e6` |
| string | `"double"`, `'single'` (with `\n`, `\t`, `\\`, … escapes) |
| bool | `true`, `false` |
| null | `null` |
| [timespan](https://learn.microsoft.com/en-us/kusto/query/scalar-data-types/timespan) | `1d`, `2h`, `30m`, `15s`, `500ms`, `100microseconds`, `10ticks` |
| list | `(a, b, c)` — e.g. for `in` |

---

## Scalar functions

All functions below are built in and follow the
[Microsoft KQL function reference](https://learn.microsoft.com/en-us/kusto/query/scalarfunctions).
Names are case-insensitive.

### Conversion

`tobool` · `todatetime` · `todouble` · `toreal` · `toint` · `tolong` ·
`tostring` · `toguid` · `totimespan` · `todecimal` · `tohex` · `todynamic`

### DateTime / TimeSpan

`now` · `ago` · `datetime_add` · `datetime_diff` · `datetime_part` ·
`make_datetime` · `make_timespan` · `format_datetime` · `getyear` · `getmonth` ·
`monthofyear` · `dayofmonth` · `dayofweek` · `dayofyear` · `hourofday` ·
`weekofyear` · `week_of_year` · `startofday` · `startofmonth` · `startofyear` ·
`endofday` · `endofmonth` · `endofyear` · `unixtime_seconds_todatetime` ·
`unixtime_milliseconds_todatetime` · `unixtime_microseconds_todatetime`

> `now()` and `ago()` read the clock from [`Options.now`](usage.md#kqloptions)
> when set, making output deterministic for tests.

### String

`strcat` · `strcat_delim` · `strcat_array` · `strlen` · `substring` · `split` ·
`replace` · `replace_string` · `replace_regex` · `extract` · `extract_all` ·
`indexof` · `indexof_regex` · `countof` · `isempty` · `isnotempty` · `tolower` ·
`toupper` · `trim` · `trim_start` · `trim_end` · `reverse` · `strcmp` ·
`translate` · `parse_csv` · `base64_encodestring` · `base64_encode_tostring` ·
`base64_decodestring` · `base64_decode_tostring`

### Hash

`hash_sha256` · `hash_md5` · `hash_sha1`

### Dynamic / array

`parse_json` · `parse_xml` · `pack` · `pack_array` · `array_length` ·
`array_concat` · `array_index_of` · `array_slice` · `array_split` ·
`array_sort_asc` · `array_sort_desc` · `array_reverse` · `array_sum` ·
`array_rotate_left` · `array_rotate_right` · `bag_keys` · `bag_merge` ·
`bag_remove_keys` · `set_union` · `set_intersect` · `set_difference`

### IP / URL

`parse_ipv4` · `ipv4_is_in_range` · `ipv4_is_private` · `ipv4_compare` ·
`ipv4_netmask_suffix` · `format_ipv4` · `parse_url` · `parse_urlquery` ·
`url_encode` · `url_encode_component` · `url_decode`

### Math

`abs` · `bin` · `floor` · `ceiling` · `round` · `sign` · `sqrt` · `pow` ·
`exp` · `exp2` · `exp10` · `log` · `log2` · `log10` · `gcd` · `lcm` ·
`isfinite` · `isinf` · `isnan`

**Trigonometry & more:** `sin` · `cos` · `tan` · `asin` · `acos` · `atan` ·
`atan2` · `degrees` · `radians` · `gamma` · `log_gamma`

### Conditional

`iif` · `iff` · `case` · `coalesce` · `max_of` · `min_of`

### Type

`gettype` · `isnull` · `isnotnull` · `isascii`

### Bitwise

`binary_and` · `binary_or` · `binary_not` · `binary_xor` · `binary_shift_left` ·
`binary_shift_right`

### Special

`parse_cef_dictionary` · `parse_xml`

> **Not planned:** `geo_location` (requires an external network service and does
> not fit an offline, in-process library).

### Custom functions

Register your own scalar functions with [`kql.function`](usage.md#custom-functions);
they become callable in any query compiled afterward.

---

## What's *not* supported

Under the per-record model a "table" is the **row-set of a single input record**,
so almost every KQL tabular operator has a stateless per-record interpretation —
including ones KQL treats as inherently "stateful." As a result there is **no
permanently-rejected operator category**; what's unavailable is either:

- **[Not yet implemented](#planned-recognized-but-not-yet-implemented)** —
  `scan` (row-by-row state machine) and `top-nested` (hierarchical top). Both
  have a per-record form and are planned; compiling raises `KqlUnsupportedError`.
- **Genuinely cross-record** — a *temporal* join of two independent streams (a
  left record matching a right record from a **different** point in the stream).
  This needs buffering/windowing across input records and belongs to a future
  stateful extension. It isn't even expressible here: `source` always denotes the
  current record.

Everything else — including `summarize`, `sort`/`order by`, `top`, `distinct`,
`take`/`limit`, `partition`, `as`, `fork`, `count`, `getschema`, `serialize`,
`mv-apply`, `make-series`, `sample`/`sample-distinct`, and `join`/`union` against
a bounded right side — runs **per input record**, never across
the stream. See [SPEC.md](SPEC.md) §2.4 and §5.6.

```python
kql.compile("source | scan declare (n:long) with (step s: true => n = 1;)")
# raises KqlUnsupportedError: operator 'scan' is recognized but not yet
# implemented; it has a stateless per-record form and is planned
```

---

## References

- Azure Monitor transformations — supported KQL:
  <https://learn.microsoft.com/en-us/azure/azure-monitor/data-collection/data-collection-transformations-kql>
- KQL reference: <https://learn.microsoft.com/en-us/kusto/query/>
- Scalar functions: <https://learn.microsoft.com/en-us/kusto/query/scalarfunctions>
- ASIM parsers: <https://learn.microsoft.com/en-us/azure/sentinel/normalization-about-parsers>
