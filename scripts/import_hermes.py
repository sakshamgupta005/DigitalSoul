#!/usr/bin/env python3
"""CLI utility to copy and import skills directly from NousResearch/hermes-agent into DigitalSoul."""

import argparse
import sys
from pathlib import Path

# Add project root to sys.path
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

from agent.ghost.learning.hermes_sync import (
    list_hermes_skills,
    copy_hermes_skill,
    sync_all_curated_skills,
    get_hermes_repo_root,
)


def main():
    parser = argparse.ArgumentParser(description="Hermes Agent Skill Importer for DigitalSoul")
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # List
    subparsers.add_parser("list", help="List all available skills from Hermes Agent")

    # Copy
    copy_parser = subparsers.add_parser("copy", help="Copy a specific skill from Hermes Agent")
    copy_parser.add_argument("skill_name", help="Name of the skill to copy (e.g. computer-use, youtube-content)")

    # Sync
    subparsers.add_parser("sync", help="Copy all curated skills from Hermes Agent into DigitalSoul")

    args = parser.parse_args()

    repo = get_hermes_repo_root()
    print(f"[*] Hermes Agent source: {repo or 'GitHub Raw'}")

    if args.command == "list" or not args.command:
        skills = list_hermes_skills()
        print(f"\n{'NAME':<28} {'CATEGORY':<22} {'STATUS':<12} {'DESCRIPTION'}")
        print("-" * 90)
        for s in skills:
            status = "Installed" if s["installed"] else "Available"
            if s["is_curated"]:
                status += " (*)"
            raw_desc = s["description"][:45] + ("..." if len(s["description"]) > 45 else "")
            desc = raw_desc.encode("ascii", "replace").decode("ascii")
            print(f"{s['name']:<28} {s['category']:<22} {status:<12} {desc}")
        print(f"\nTotal: {len(skills)} skills available. Run 'python scripts/import_hermes.py copy <name>' to import.")

    elif args.command == "copy":
        ok, msg, path = copy_hermes_skill(args.skill_name)
        if ok:
            print(f"[+] Success: {msg}")
        else:
            print(f"[-] Error: {msg}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "sync":
        print("[*] Syncing curated Hermes skills...")
        res = sync_all_curated_skills()
        for k, v in res.items():
            symbol = "+" if v["success"] else "-"
            print(f" [{symbol}] {k}: {v['message']}")


if __name__ == "__main__":
    main()
