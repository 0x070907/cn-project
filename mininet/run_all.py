"""Run all 4 experiments with one command => refer docs/MININET help guide.md

   sudo python3 mininet/run_all.py                     # all 4, 3 repeats each, 30 s
    sudo python3 mininet/run_all.py --only static       # just one
    sudo python3 mininet/run_all.py --dry-run           # print the commands, run nothing
"""

#For each exp it starts the right Ryu controller (if one is needed),calls run_experiment.py,then stops Ryu and cleans Mininet before the next one

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time

REPO: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EXPERIMENTS: dict[str, tuple[str, str, str | None, list[str]]] = {
    # name -> (label, scenario, controller file or None, extra run_experiment.py options)
    "baseline":     ("baseline",     "no_congestion", None,                                   ["--mode", "none", "--cap", "tclink"]),
    "baseline_ryu": ("baseline_ryu", "congestion",    "controller/baseline_controller.py",    ["--mode", "remote", "--cap", "tclink"]),
    "static":       ("static",       "congestion",    "controller/qos_controller.py",         ["--mode", "remote", "--cap", "none", "--queues"]),
    "dynamic":      ("dynamic",      "surge",         "controller/dynamic_controller.py",     ["--mode", "remote", "--cap", "none", "--queues"]),
}

# not part of "all" exp:static policy on the surge, =the reference that dynamic is compared against
EXTRA: dict[str, tuple[str, str, str | None, list[str]]] = {
    "static_surge": ("static",       "surge",         "controller/qos_controller.py",         ["--mode", "remote", "--cap", "none", "--queues"]),
}


def ryu_manager() -> str:

    if os.environ.get("RYU_MANAGER"):
        return os.environ["RYU_MANAGER"]
    
    user = os.environ.get("SUDO_USER")
    home = os.path.expanduser(f"~{user}") if user else os.path.expanduser("~")
    return os.path.join(home, "ryuenv", "bin", "ryu-manager")


def clean_mininet() -> None: # mn -c
    subprocess.run(["mn", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def run_one(name: str, 
            spec: tuple[str, str, str | None, list[str]], 
            duration: int, repeat: int,
            dry: bool   # just print what's the experiment,doesn't run it
            ) -> str:

    label, scenario, controller, options = spec

    # this tailors that long command to run a experiment
    cmd = [sys.executable, os.path.join(REPO, "mininet", "run_experiment.py"), scenario,
           *options, "--duration", str(duration), "--repeat", str(repeat), "--label", label]

    if controller and not os.path.exists(os.path.join(REPO, controller)):  # for baseline,there would be no controller or the dynamic controller is not yet done,so skipped
        print(f"[{name}] SKIPPED: {controller} does not exist yet")
        return "skipped"

    print(f"\n---{name} ({scenario}, label={label})---")

    if controller:
        print("controller:", controller)
    print("command   :", " ".join(cmd))

    if dry:
        return "dry-run"

    ryu = None
    ryu_log = None

    try:    # start the controller if available 

        if controller:  # wouldn't be started for baseline exp

            os.makedirs(os.path.join(REPO, "results"), exist_ok=True)
            ryu_log = open(os.path.join(REPO, "results", f"ryu_{name}.log"), "w")   #log the controller rules installed on switches

            ryu = subprocess.Popen([ryu_manager(), os.path.join(REPO, controller)],
                                   stdout=ryu_log, 
                                   stderr=subprocess.STDOUT, 
                                   cwd=REPO)
            time.sleep(4)                           # give Ryu time to open port 6653

            if ryu.poll() is not None:
                print(f"[{name}] FAILED: Ryu exited, see results/ryu_{name}.log")
                return "failed"
            
        result = subprocess.run(cmd, cwd=REPO)  # run the experiment only after starting the controller when needed

        return "ok" if result.returncode == 0 else "failed"
    
    finally:   

        if ryu is not None and ryu.poll() is None:

            ryu.send_signal(signal.SIGINT)

            try:
                ryu.wait(timeout=5)
            except subprocess.TimeoutExpired:
                ryu.kill()

        if ryu_log is not None:
            ryu_log.close()

        clean_mininet()  #runs mn -c
        time.sleep(2)


def main() -> None:

    parser = argparse.ArgumentParser(description="run the 4 experiments with one command")

    parser.add_argument("--only", choices=[*EXPERIMENTS, *EXTRA], 
                        help="run just this experiment")
    parser.add_argument("--duration", type=int, default=30, 
                        help="seconds of traffic per run")
    parser.add_argument("--repeat", type=int, default=3, 
                        help="runs per experiment")
    parser.add_argument("--dry-run", action="store_true", 
                        help="print the commands and exit")
    parser.add_argument("--scenario", choices=["no_congestion", "congestion", "surge"],
                        help="with --only: run that policy on a different scenario")

    args = parser.parse_args()

    if args.scenario and not args.only:
        sys.exit("--scenario needs --only,e.g. --only baseline --scenario congestion")

    if not args.dry_run and os.geteuid() != 0:
        sys.exit("needs root: sudo python3 mininet/run_all.py")

    everything = {**EXPERIMENTS, **EXTRA}

    if args.scenario: #if some custom scenario needed,then acc change the options to run the cmd
        label, _, controller, options = everything[args.only]
        everything[args.only] = (label, args.scenario, controller, options)

    names = [args.only] if args.only else list(EXPERIMENTS)

    clean_mininet() if not args.dry_run else None

    outcome = {n: run_one(n, everything[n], args.duration, args.repeat, args.dry_run) for n in names}

    print("\n--- summary ----")

    for n, status in outcome.items():
        print(f"{n:14s} {status}")

    if "failed" in outcome.values():
        sys.exit(1)


if __name__ == "__main__":
    main()