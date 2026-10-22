# Digital Ghost contracts

Start with [the specification](../CONTRACTS.md) and [review corrections](REVIEW.md).

Our previous product, Blinky (`C:\Projects\Blinky`), is a technical reference for implementation. See [the source map and adaptation notes](../BLINKY_REFERENCE.md); its legacy wire formats do not override these contracts.

JSON schemas are the wire-format source of truth. This is the breaking `1.1.0-draft` revision.

Generated Pydantic models are present, but generation is not proof of schema
equivalence: current `RawEvent` models omit conditional redaction enforcement.
See [implementation verification](../docs/VALIDATION.md) for reproduced gaps
and actual build/test results.

From the repository root:

```powershell
python -m pip install -r contracts/requirements-dev.txt
python contracts/validate.py
```

The validator checks schemas, positive/negative fixtures, static workflow references and scopes, restricted decision syntax, embedded JSON synchronization, and targeted regression cases. Contract timestamps use UTC `Z` and formats are checked explicitly.

`command.schema.json` defines remote command shapes. `fixtures/command.valid.json` is a shape fixture with a zero placeholder signature, not a valid signed command. Runtime integration and crypto verification are separate gates; fixture shape validation does not prove execution safety. See `REVIEW.md` for corrections, migration requirements, and outstanding implementation gates.
