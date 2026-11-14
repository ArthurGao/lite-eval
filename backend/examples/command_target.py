"""A minimal subprocess target (design §18.11): the capitals table behind the line protocol.

    command: {argv: [python, command_target.py], cwd: ..}

Prints {"ready": true}, then answers each {"case", "repeat", "config"} line on stdin with one
{"case_id", "repeat", "output"} line on stdout. Anything for humans goes to stderr, which lite-eval
keeps (redacted) as target.stderr.log in the run directory.
"""

import json
import sys

ANSWERS = {"New Zealand": "Wellington", "Australia": "Canberra", "Japan": "Tokyo"}


def main() -> None:
    print("capitals table loaded", file=sys.stderr, flush=True)
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:  # ends when lite-eval closes stdin
        request = json.loads(line)
        case = request["case"]
        country = str(case["input"]["country"])
        if request["config"].get("fuzzy"):
            country = country.title()
        reply = {"case_id": case["id"], "repeat": request["repeat"], "output": ANSWERS.get(country)}
        print(json.dumps(reply), flush=True)


if __name__ == "__main__":
    main()
