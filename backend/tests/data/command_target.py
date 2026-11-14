"""A scripted subprocess target for the command-target tests (design §18.11). Never calls anything.

Speaks the line protocol: prints {"ready": true}, then answers each {"case", "repeat", "config"}
line with {"case_id", "repeat", "output"}, the output being the case's expected value. Flags make
it misbehave on one case id; `--state FILE` counts process starts so a crash can happen only on
the first N starts.
"""

import argparse
import json
import os
import random
import signal
import sys
import time
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--crash-on", default="")  # exit with code 3 when one of these ids arrives
p.add_argument("--crash-starts", type=int, default=99)  # ... only on the first N starts
p.add_argument("--state")
p.add_argument("--wrong-id-on")  # answer this case with another case's id
p.add_argument("--garbage-on")  # answer this case with a line that is not JSON
p.add_argument("--big-on")  # answer this case with a line of 5000 bytes
p.add_argument("--sleep-on")  # sleep before answering this case
p.add_argument("--sleep-s", type=float, default=5.0)
p.add_argument("--error-on")  # answer this case with a reported error
p.add_argument("--jitter", action="store_true")  # random short delays
p.add_argument("--stderr", default="")  # write this to stderr at start
p.add_argument("--no-ready", action="store_true")
p.add_argument("--ready-starts", type=int, default=99)  # only the first N starts get ready
p.add_argument("--bad-ready", action="store_true")  # first line {"ready": false}
p.add_argument("--delay", type=float, default=0.0)  # seconds before every answer
p.add_argument("--stubborn", action="store_true")  # ignore stdin EOF and SIGTERM
p.add_argument("--env", action="store_true")  # answer with the environment's variable names
p.add_argument("--echo-env", action="append", default=[])  # print these to stderr and answer
p.add_argument("--leak-env", default="")  # reply with this variable in an unknown field
p.add_argument("--latin1-on", default="")  # this case's reply is not UTF-8
p.add_argument("--ready-echo-env", default="")  # first line: this variable, not the ready line
p.add_argument("--echo-config", action="store_true")  # answer with the request's config
p.add_argument("--exit-after")  # exit (code 0) right after answering this case
p.add_argument("--pid-file")  # write this process's pid here
p.add_argument("--spawn-child")  # start a sleeping, detached child and write its pid here
args = p.parse_args()

start = 1
if args.state:
    state = Path(args.state)
    start = int(state.read_text()) + 1 if state.exists() else 1
    state.write_text(str(start))

if args.pid_file:
    Path(args.pid_file).write_text(str(os.getpid()))
if args.spawn_child:
    import subprocess

    # Detached stdio: the child holds none of our pipes, so only a signal to the group ends it
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    Path(args.spawn_child).write_text(str(child.pid))

if args.stderr:
    print(args.stderr, file=sys.stderr, flush=True)
for name in args.echo_env:  # a program that logs its configuration, secrets included
    print(f"{name}={os.environ.get(name)}", file=sys.stderr, flush=True)
if args.stubborn:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if args.ready_echo_env:  # a program that prints its configuration where the ready line goes
    print(f"{args.ready_echo_env}={os.environ.get(args.ready_echo_env)}", flush=True)
elif not args.no_ready and start <= args.ready_starts:
    print(json.dumps({"ready": not args.bad_ready}), flush=True)

for line in sys.stdin:
    request = json.loads(line)
    case, repeat = request["case"], request["repeat"]
    cid = case["id"]
    time.sleep(args.delay)
    if args.jitter:
        time.sleep(random.uniform(0, 0.02))
    if cid in args.crash_on.split(",") and start <= args.crash_starts:
        sys.exit(3)
    if cid == args.sleep_on:
        time.sleep(args.sleep_s)
    reply: dict[str, object] = {"case_id": cid, "repeat": repeat, "output": case["expected"]}
    if args.echo_config:
        reply["output"] = request["config"]
    if args.env:
        reply["output"] = sorted(os.environ)
    if args.echo_env:
        reply["output"] = {name: os.environ.get(name) for name in args.echo_env}
    if args.leak_env:  # not a field of a reply: invalid_output, with the line in the message
        reply["debug"] = os.environ.get(args.leak_env)
    if cid == args.wrong_id_on:
        reply["case_id"] = "someone-else"
    if cid == args.error_on:
        reply["error"] = {"kind": "truncated", "message": "model hit max_tokens"}
    text = json.dumps(reply)
    if cid == args.latin1_on:  # the answer holds one byte that is not UTF-8
        reply["output"] = "caf"
        line = json.dumps(reply).encode().replace(b'"caf"', b'"caf\xe9"')
        sys.stdout.buffer.write(line + b"\n")
        sys.stdout.flush()
        continue
    if cid == args.garbage_on:
        text = "this is not json"
    if cid == args.big_on:
        text = json.dumps({**reply, "output": "x" * 5000})
    print(text, flush=True)
    if cid == args.exit_after:
        sys.exit(0)

if args.stubborn:
    while True:
        time.sleep(1)
