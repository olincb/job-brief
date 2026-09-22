# Tests

Recorded fixtures and how to re-record them. Running the suite is in the
[README](../README.md); what each test module covers is in
[`docs/context-map.md`](../docs/context-map.md).

`conftest.py` replaces `urllib.request.urlopen` for every test, so code reaching
for a live board fails with an assertion instead of passing against whatever that
board returns today. Tests that exercise a fetcher monkeypatch
`jobbrief.sources.get`, the engine's one network call, with a lookup over recorded
bodies.

## Layout

- `fixtures/<source>/<slug>.json`: a board's response saved unmodified.
- `fixtures/posting.txt`: one posting as `condense` receives it, after HTML is
  stripped. It states years of experience, remote policy, and a pay range, the
  three lines condense must keep.
- `fixtures/brief.md`: a hand-written brief in the layout `prompt.md` asks the
  model for. Companies and links are invented.
- `record_fixtures.py`: the recorder, the only file here that touches the network,
  and only when run directly.

Fixtures come from public job boards only. Nothing from a person's sheet,
profile, or inbox goes in here, even redacted.

## Re-recording fixtures

Do this deliberately: when a fetcher changes what it reads from a response, when
a board changes its shape, or when the condense posting has closed. Not as part of
a routine test run.

```
python -m tests.record_fixtures
```

The recorder fetches each board listed in its `BOARDS` table and writes the body
as returned. It refuses to write a body that is not JSON: a corporate proxy answers
some boards with an HTML block page, and a recorded block page is a fixture that
passes for the wrong reason. It then scans the condense board for the first posting
long enough to be cut that carries all three required lines, writes it to
`posting.txt`, and prints the title it chose. If none qualifies, it exits without
writing; point it at a different board.

After recording, run the tests, read the diff, and commit the fixtures with
the change that needed them. To record a new source, add a row to `BOARDS`
and a test that serves it through `get`; see `test_fetch.py`.
