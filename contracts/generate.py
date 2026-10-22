"""Regenerate contract models and validate contracts/openapi.yaml.

Run from the repository root (or any directory):

    python contracts/generate.py

Inputs are the standalone JSON Schemas and openapi.yaml; nothing here edits them.
Outputs are checked in: agent/ghost/contracts/*.py and web/src/types/contracts.ts.
Requires contracts/requirements-codegen.txt (Python) and `npm ci` in contracts/ (Node).
"""

import subprocess
import sys
from pathlib import Path

from openapi_spec_validator import validate
from openapi_spec_validator.readers import read_from_filename

CONTRACTS = Path(__file__).resolve().parent
ROOT = CONTRACTS.parent
PY_OUT = ROOT / "agent" / "ghost" / "contracts"
SCHEMAS = ["command", "error", "omniroute_hypothesis", "raw_event", "workflow"]


def run(cmd, cwd):
    subprocess.run(cmd, cwd=cwd, check=True)


def generate_python():
    PY_OUT.mkdir(parents=True, exist_ok=True)
    for name in SCHEMAS:
        run(
            [
                sys.executable,
                "-m",
                "datamodel_code_generator",
                "--input",
                str(CONTRACTS / f"{name}.schema.json"),
                "--input-file-type",
                "jsonschema",
                "--output",
                str(PY_OUT / f"{name}.py"),
                "--output-model-type",
                "pydantic_v2.BaseModel",
                "--target-python-version",
                "3.11",
                "--disable-timestamp",
                "--formatters",
                "black",
                "isort",
            ],
            cwd=ROOT,
        )


def generate_typescript():
    run(["node", "generate-typescript.mjs"], cwd=CONTRACTS)
    run(
        [
            "node",
            "node_modules/typescript/bin/tsc",
            "--noEmit",
            "--strict",
            "--target",
            "es2022",
            "--module",
            "es2022",
            "--moduleResolution",
            "bundler",
            str(ROOT / "web" / "src" / "types" / "contracts.ts"),
        ],
        cwd=CONTRACTS,
    )


def validate_openapi():
    path = CONTRACTS / "openapi.yaml"
    spec, _ = read_from_filename(str(path))
    validate(spec, base_uri=path.resolve().as_uri())
    print(f"PASS: {path.name} is a valid OpenAPI 3.1 document.")


def main():
    generate_python()
    generate_typescript()
    validate_openapi()
    print("Generated agent/ghost/contracts/*.py and web/src/types/contracts.ts.")


if __name__ == "__main__":
    main()
