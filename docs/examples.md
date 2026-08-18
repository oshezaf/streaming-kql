# Examples

Runnable recipes for common tasks. Every query here uses only
[supported KQL](supported-kql.md). Records are plain Python `dict`s; the input
table is always `source`.

```python
import streaming_kql as kql
from datetime import datetime, timezone
```

## Filter and project (DCR-style)

Keep only error messages and emit two columns — the canonical Azure Monitor
[transformation](https://learn.microsoft.com/en-us/azure/azure-monitor/data-collection/data-collection-transformations-kql).

```python
q = kql.compile("""
    source
    | where Message has 'error'
    | project TimeGenerated, Message
""")

q.match({"TimeGenerated": "2021-11-07T09:13:06Z", "Message": "an error occurred"})
# -> {'TimeGenerated': '2021-11-07T09:13:06Z', 'Message': 'an error occurred'}

q.match({"TimeGenerated": "2021-11-07T09:14:00Z", "Message": "all good"})
# -> None
```

## Enrich with computed columns

`extend` adds columns; later assignments can reference earlier ones.

```python
q = kql.compile("source | extend A = 2, B = A + 3")
q.match({})
# -> {'A': 2, 'B': 5}

q = kql.compile("source | extend Full = strcat(First, ' ', Last)")
q.match({"First": "Ada", "Last": "Lovelace"})
# -> {'First': 'Ada', 'Last': 'Lovelace', 'Full': 'Ada Lovelace'}
```

## Conditional bands

```python
q = kql.compile("source | extend Band = iif(Price > 50, 'high', 'low')")
[q.match(r) for r in ({"Price": 90}, {"Price": 10})]
# -> [{'Price': 90, 'Band': 'high'}, {'Price': 10, 'Band': 'low'}]
```

## Conditional pipelines

The tabular `case` extension routes each row through the first matching
sub-pipeline. Its final, unconditional sub-pipeline is the default.

```python
q = kql.compile("""
    source
    | case (Severity >= 4, (project Alert = Message),
            Severity >= 2, (project Warning = Message),
            (project Info = Message))
""")
q.transform({"Severity": 4, "Message": "disk full"})
# -> [{'Alert': 'disk full'}]
```

## Parse structured strings

Extract typed columns out of a message with `parse`. Use `parse-where` to drop
records that don't match.

```python
q = kql.compile('source | parse Message with "user=" User " code=" Code:long')
q.match({"Message": "user=ada code=200"})
# -> {'Message': 'user=ada code=200', 'User': 'ada', 'Code': 200}
```

### Key/value pairs

```python
q = kql.compile("""
    source
    | parse-kv Msg as (user:string, code:long)
      with (pair_delimiter=';', kv_delimiter='=')
""")
q.match({"Msg": "user=ada;code=200;action=login"})
# -> {'Msg': 'user=ada;code=200;action=login', 'user': 'ada', 'code': 200}
```

## Work with dynamic (JSON) data

Parse a JSON string column, then reach into it with `.` access. This mirrors the
Azure Monitor "dynamic data handling" transformation.

```python
schema = kql.Schema({"AdditionalContext": "dynamic"})
q = kql.compile("""
    source
    | extend parsed = parse_json(AdditionalContext)
    | extend Level = toint(parsed.Level)
    | extend DeviceId = tostring(parsed.DeviceID)
    | project TimeGenerated, Level, DeviceId
""", schema=schema)

q.match({
    "TimeGenerated": "2021-11-07T09:13:06.570354Z",
    "AdditionalContext": '{"Level": 2, "DeviceID": "apollo13"}',
})
# -> {'TimeGenerated': '2021-11-07T09:13:06.570354Z', 'Level': 2, 'DeviceId': 'apollo13'}
```

Missing paths yield `null` rather than raising:

```python
kql.compile("source | extend x = Ctx.a.b.c").match({"Ctx": {}})
# -> {'Ctx': {}, 'x': None}
```

### Unpack a bag into columns

```python
q = kql.compile("source | evaluate bag_unpack(Context, 'ctx_')")
q.match({"Context": {"user": "ada", "code": 200}})
# -> {'ctx_user': 'ada', 'ctx_code': 200}
```

## IP and URL enrichment (ASIM-style)

```python
q = kql.compile("""
    source
    | extend
        SrcInt   = parse_ipv4('1.2.3.4'),
        IsPriv   = ipv4_is_private('10.0.0.1'),
        InRange  = ipv4_is_in_range('10.0.0.5', '10.0.0.0/24')
""")
q.match({})
# -> {'SrcInt': 16909060, 'IsPriv': True, 'InRange': True}

q = kql.compile("source | extend U = parse_url('https://host.com:8080/a/b?x=1#frag')")
q.match({})["U"]["Host"]
# -> 'host.com'
```

## Reusable constants with `let`

```python
q = kql.compile("""
    let threshold = 80;
    source | where Price > threshold
""")
q.match({"Sym": "MSFT", "Price": 90})   # -> {'Sym': 'MSFT', 'Price': 90}
q.match({"Sym": "MSFT", "Price": 50})   # -> None
```

## Coerce a real-world (string) feed

Log feeds deliver everything as strings. A `Schema` types the columns so KQL
comparisons and dynamic access work without manual conversion.

```python
schema = kql.Schema({"Level": "int", "Created": "datetime", "Ctx": "dynamic"})
q = kql.compile("""
    source
    | where Level >= 3 and Created > ago(1d)
    | extend Device = Ctx.DeviceId
""", schema=schema, options=kql.Options(now=datetime(2026, 1, 2, tzinfo=timezone.utc)))

q.match({"Level": "5", "Created": "2026-01-01T12:00:00Z", "Ctx": '{"DeviceId": "abc"}'})
# -> {'Level': 5, 'Created': datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
#     'Ctx': {'DeviceId': 'abc'}, 'Device': 'abc'}
```

## Enrich from a reference table (`lookup`)

Join each record against a **constant** lookup table defined inline with
`datatable`. This is stateless — the table is fixed at compile time.

```python
q = kql.compile("""
    let Countries = datatable(Code:string, Name:string) [
        "US", "United States",
        "IL", "Israel"
    ];
    source | lookup Countries on Code
""")

q.match({"Code": "US", "Hits": 10})
# -> {'Code': 'US', 'Hits': 10, 'Name': 'United States'}
q.match({"Code": "ZZ", "Hits": 3})       # leftouter (default): kept, Name null
# -> {'Code': 'ZZ', 'Hits': 3, 'Name': None}
```

Use `kind=inner` to drop unmatched records, and `on Left == Right` when the key
columns differ:

```python
kql.compile("""
    let Dim = datatable(Key:string, Label:string) [ "a", "Alpha" ];
    source | lookup kind=inner Dim on ProdKey == Key
""").transform({"ProdKey": "a"})
# -> [{'ProdKey': 'a', 'Label': 'Alpha'}]
```

## Enrich from a local file (`externaldata`)

The reference table can also be read from a local CSV/TSV/JSON file. Remote URLs
are rejected — streaming-kql is offline by design.

```python
# reference/geo.csv (no header):  1.2.3.0/24,DataCenter-A
q = kql.compile("""
    let Geo = externaldata(Range:string, Zone:string)
        [ "reference/geo.csv" ] with (format="csv");
    source | lookup Geo on Range
""")
```

## Expand arrays into rows (`mv-expand`)

Turn one record with an array column into one record per element (1 → N).

```python
q = kql.compile("source | mv-expand Port | where Port > 80")
q.transform({"Host": "h1", "Port": [22, 80, 443]})
# -> [{'Host': 'h1', 'Port': 443}]
```

Expand an array of objects, then reach into each element; add the element index:

```python
q = kql.compile("source | mv-expand with_itemindex=i Event | extend User = Event.user")
q.transform({"Event": [{"user": "ada"}, {"user": "bob"}]})
# -> [{'Event': {'user': 'ada'}, 'i': 0, 'User': 'ada'},
#     {'Event': {'user': 'bob'}, 'i': 1, 'User': 'bob'}]
```

## Split a record into variants (`union`)

Emit the record on multiple branches — each branch is a `source` subquery
evaluated against the same record. Useful for tagging or duplicating a row.

```python
q = kql.compile("""
    source
    | union (source | where Bytes > 1000000 | extend Alert = "large-transfer")
""")
q.transform({"Bytes": 2000000})
# -> [{'Bytes': 2000000, 'Alert': None},
#     {'Bytes': 2000000, 'Alert': 'large-transfer'}]
```

`union` can also append the rows of a constant reference table (a `let`-bound
`datatable`/`externaldata`).

## Aggregate within a record (`summarize` after `mv-expand`)

`summarize` (and `sort`/`top`/`distinct`/`take`) aggregate the rows produced
**from a single input record** — most useful right after `mv-expand`. Here we
count and total the events carried inside one record, grouped by kind:

```python
q = kql.compile("""
    source
    | mv-expand event = Events
    | summarize Count = count(), Bytes = sum(event.bytes) by Kind = event.kind
    | sort by Bytes desc
""")

q.transform({"Events": [
    {"kind": "read",  "bytes": 100},
    {"kind": "write", "bytes": 500},
    {"kind": "read",  "bytes": 300},
]})
# -> [{'Kind': 'write', 'Count': 1, 'Bytes': 500},
#     {'Kind': 'read',  'Count': 2, 'Bytes': 400}]
```

> This is per-record aggregation, not a stream-wide total: each input record is
> summarized on its own. See
> [Supported KQL](supported-kql.md#aggregating--reordering-operators-per-record-row-set).

## Join within a record (`join`)

`join` combines the per-record row-set with a **bounded** right table — a
constant reference table, or another `source` subquery over the same record.
Every join kind works because both sides are in memory at once.

```python
q = kql.compile("""
    let Names = datatable(Code:string, Name:string) [
        "US", "United States",
        "IL", "Israel"
    ];
    source | mv-expand Code = Codes | join kind=leftouter Names on Code
""")

q.transform({"Codes": ["US", "ZZ"]})
# -> [{'Codes': ['US','ZZ'], 'Code': 'US', 'Code1': 'US', 'Name': 'United States'},
#     {'Codes': ['US','ZZ'], 'Code': 'ZZ', 'Code1': None, 'Name': None}]
```

Both sides can be expansions of the same record (a self-join), and any kind —
`inner`, `leftouter`, `rightouter`, `fullouter`, `leftsemi`/`rightsemi`,
`leftanti`/`rightanti` — is available.

## Name and reuse a stream slice (`as`)

`as` captures the row-set at a point in the pipeline so you can reference it as a
table later — here, keep a snapshot before filtering, then self-join against it.

```python
q = kql.compile("""
    source
    | mv-expand v = V
    | as Snapshot
    | where v > 1
    | join kind=inner Snapshot on v
""")
q.transform({"V": [1, 2, 3]})
# -> [{'V': [1, 2, 3], 'v': 2, 'V1': [1, 2, 3], 'v1': 2},
#     {'V': [1, 2, 3], 'v': 3, 'V1': [1, 2, 3], 'v1': 3}]
```

### A real use case: correlate events inside one record

A single sign-in record can carry a batch of `Attempts`. `mv-expand` turns the
batch into a per-attempt row-set, `as` snapshots it, and a self-`join` lines up
two rows from the *same* record — here, a user who **failed and then succeeded**
in the same batch (a password-spray attempt that eventually got in).

```python
q = kql.compile("""
    source
    | mv-expand attempt = Attempts
    | project User = tostring(attempt.user),
              Result = tostring(attempt.result),
              IP = tostring(attempt.ip)
    | as Attempts
    | where Result == "failure"
    | join kind=inner Attempts on User
    | where Result1 == "success"
    | project User, FailedFromIP = IP, SucceededFromIP = IP1
""")

q.transform({"Attempts": [
    {"user": "alice", "result": "failure", "ip": "10.0.0.9"},
    {"user": "alice", "result": "success", "ip": "10.0.0.9"},
    {"user": "bob",   "result": "failure", "ip": "45.9.1.2"},
    {"user": "bob",   "result": "success", "ip": "203.0.113.7"},
    {"user": "carol", "result": "failure", "ip": "8.8.8.8"},
]})
# -> [{'User': 'alice', 'FailedFromIP': '10.0.0.9',  'SucceededFromIP': '10.0.0.9'},
#     {'User': 'bob',   'FailedFromIP': '45.9.1.2',  'SucceededFromIP': '203.0.113.7'}]
# carol only failed, so she is dropped by the inner join.
```

The snapshot holds *every* attempt, so the right side of the join carries both
failures and successes for a user; the `where Result1 == "success"` after the
join keeps only the pairs that ended in a successful sign-in. (The right side of
a `join` must be a `source`/`datatable`/`range` subquery, so a named `as` table
is joined directly and filtered *after* the join, as shown.)

## Generate rows with `range`

```python
kql.compile("range x from 1 to 5 step 1 | where x > 3").transform({})
# -> [{'x': 4}, {'x': 5}]

# as a reference table:
kql.compile("""
    let Allowed = range port from 80 to 443 step 363;   // 80, 443
    source | mv-expand port = Ports | join kind=inner Allowed on port
""").transform({"Ports": [22, 80, 443]})
# -> rows for 80 and 443
```

## Deterministic time with `Options`

Fix the clock so `now()` / `ago()` produce reproducible output.

```python
opts = kql.Options(now=datetime(2026, 1, 1, tzinfo=timezone.utc))
q = kql.compile("source | extend GeneratedAt = now()", options=opts)
q.match({})
# -> {'GeneratedAt': datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)}
```

## Fan out: many queries, one feed

```python
node = kql.Node()
node.add("high", "source | where Price > 80")
node.add("low",  "source | where Price < 10 | project Symbol, Price")

for name, rec in node.push({"Symbol": "MSFT", "Price": 90}):
    print(name, rec)
# high {'Symbol': 'MSFT', 'Price': 90}
```

## Register a custom function

```python
@kql.function("domain_of")
def domain_of(email: str) -> str:
    return email.split("@", 1)[-1]

kql.compile("source | extend d = domain_of(From)").match({"From": "ada@example.com"})
# -> {'From': 'ada@example.com', 'd': 'example.com'}
```

## Stream a feed

```python
q = kql.compile("source | where Price > 80 | project Symbol, Price")

def read_events():
    yield {"Symbol": "MSFT", "Price": 90}
    yield {"Symbol": "AAPL", "Price": 10}
    yield {"Symbol": "NVDA", "Price": 120}

for out in q.stream(read_events()):
    print(out)
# {'Symbol': 'MSFT', 'Price': 90}
# {'Symbol': 'NVDA', 'Price': 120}
```

## Handling unsupported queries

Recognized operators that are not implemented are rejected at compile time.

```python
try:
    kql.compile("source | top-nested 3 of Category by count()")
except kql.KqlUnsupportedError as e:
    print("cannot run:", e)
# cannot run: operator 'top-nested' is recognized but not yet implemented
```

---

See [Supported KQL](supported-kql.md) for the complete operator and function
list, and [Usage & API reference](usage.md) for the full API.
