# DigitalSoul project context

DigitalSoul learns a user's repeated work from consented observations and compiles reviewed workflows for deterministic desktop execution.

The user explicitly selected **browser-use** for browser automation. Use its BrowserSession/CDP/Actor APIs; do not introduce Playwright imports or dependencies. Preserve deterministic workflow execution and agent-side approvals. OmniRoute is the local model gateway at `http://127.0.0.1:20128/v1`; use a configured model/combo and distinguish service startup, HTTP connectivity, and successful model inference when reporting tests.

The user identified **Blinky**, at `C:\Projects\Blinky`, as our previous, closely related product and explicitly asked us to use it as a technical reference. Keep this context when planning and implementing DigitalSoul. Read [BLINKY_REFERENCE.md](BLINKY_REFERENCE.md) for inspected source paths, useful patterns, differences, and adaptation notes. If that local checkout is unavailable, use the recorded notes and state the limitation.

Before building a related desktop capture, UI targeting, execution, overlay, or remote-control component, consult the corresponding Blinky implementation. Prefer adapting a relevant existing pattern where it fits. Blinky is a reference dependency, not an automatic runtime dependency; its schemas and security choices do not override DigitalSoul's contracts. The current task authorizes reference inspection and documentation in DigitalSoul, not changes to Blinky.

Contract entry points: [CONTRACTS.md](CONTRACTS.md), [contracts/README.md](contracts/README.md), and [contracts/REVIEW.md](contracts/REVIEW.md). JSON schemas define wire shapes; REVIEW.md defines additional runtime semantics. Validate contract changes with `python contracts/validate.py`. This check does not test an actual agent, relay, or cryptographic verifier.
