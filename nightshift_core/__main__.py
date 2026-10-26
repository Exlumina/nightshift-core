"""Print an offline plan; never execute jobs or modify a tracker."""
import argparse
import json
import sys
from pathlib import Path
from . import schedule

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tasks", type=Path, help="JSON object with tasks, capabilities and resources")
    parser.add_argument("--remaining-seconds", required=True, type=float)
    parser.add_argument("--now", type=float, default=None)
    args = parser.parse_args()
    try:
        payload = json.loads(args.tasks.read_text())
        if not isinstance(payload, dict):
            raise ValueError("Input must be an object")
        result = schedule(payload["tasks"], remaining_seconds=args.remaining_seconds,
                          capabilities=set(payload.get("capabilities", [])),
                          resources=payload.get("resources", {}), now=args.now)
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.error(str(error))
    json.dump(result, sys.stdout, indent=2)
    print()

if __name__ == "__main__":
    main()
