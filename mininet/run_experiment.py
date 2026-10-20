"""Run one scenario inside Mininet using the real programs in app/. """

# What one run does:
#    1. build + start the network
#    2. start the receiver on h4
#    3. start the senders (shared run id)
#    4. wait for the senders to finish
#    5. stop the receiver, stop the network
#    6. save and print the results

from __future__ import annotations

import argparse
import csv
import os
import shutil
import signal
import subprocess
import sys
import time
from typing import IO, TYPE_CHECKING

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mininet.log import setLogLevel      # noqa: E402
from topology import build              # noqa: E402

if TYPE_CHECKING:
    from mininet.net import Mininet

# settings (where things are, and what each flow looks like)
REPO: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP: str = os.path.join(REPO, "app")
RESULTS: str = os.path.join(REPO, "results")          # same folder metrics.py writes to
EXPERIMENTS: str = os.path.join(REPO, "experiments")  # results we choose to commit
RECEIVER_IP: str = "10.0.0.4"
GRACE_SECONDS: float = 2.0     # receiver waits 1 s after END; leave margin


# so we aren't taking any cla currently(need to fix)
FLOWS: dict[str, tuple[str, int, float]] = {
   #flow-name :    (host ,packet size,rate) 
    "high":       ("h1", 200, 1),
    "medium":     ("h2", 500, 2),
    "low":        ("h3", 1000, 3),
    "background": ("h3", 1400, 4),
}


SCENARIOS: dict[str, list[str]] = {
    # scenario name -> which flows run
    "no_congestion": ["high", "medium"],                       # 3 Mbps  < 5 Mbps
    "congestion":    ["high", "medium", "low", "background"],  # 10 Mbps > 5 Mbps
}


# 2. the sender takes a packet count
def packet_count(size: int, rate_mbps: float, seconds: int) -> int:  #we r again not taking --count and using this formula instead
     
    # packets = (bits per second * seconds) / (bits per packet)
    return int(rate_mbps * 1_000_000 * seconds / (size * 8))


# 3. start the receiver on h4
def start_receiver(net: Mininet,
                    out_dir: str, 
                    logs: list[IO[str]]
                  ) -> subprocess.Popen:

    log = open(os.path.join(out_dir, "receiver.log"), "w") # join() just combines everything with '/'
    logs.append(log)

    # popen() starts a program inside h4 and returns immediately
    return net.get("h4").popen(
                                ["python3", os.path.join(APP, "receiver.py")],
                                stdout=log, 
                                stderr=subprocess.STDOUT,
                                cwd=APP,
                            )


# 4. start one sender per flow,all with the  same run id
def start_senders(net: Mininet, 
                  scenario: str, # congestion or no_congestion
                  duration: int,
                  run_id: int,
                  out_dir: str, 
                  logs: list[IO[str]]
                  ) -> list[subprocess.Popen]:
    
    senders: list[subprocess.Popen] = []

    for name in SCENARIOS[scenario]:
        host, size, rate = FLOWS[name]   #tuple unpacking
        log = open(os.path.join(out_dir, f"sender_{name}.log"), "w")
        logs.append(log)

        cmd = [
            "python3", os.path.join(APP, "sender.py"),
            "--ip", RECEIVER_IP,
            "--flow", name,
            "--size", str(size),
            "--rate", str(rate),
            "--count", str(packet_count(size, rate, duration)),
            "--run-id", str(run_id),   #the run_id is arbitary(need to fix)
        ]
        senders.append(net.get(host).popen(cmd,
                                            stdout=log,
                                            stderr=subprocess.STDOUT,
                                            cwd=APP)
                      )
    return senders


# 5. wait for the senders, then stop the receiver cleanly
def stop_receiver(receiver: subprocess.Popen) -> None:

    receiver.send_signal(signal.SIGINT)    # same as pressing Ctrl+C in the receiver

    try:
        receiver.wait(timeout=5)
    except subprocess.TimeoutExpired:
        receiver.terminate()        # results are already saved per run


# 6. bookkeeping (manifest, printing, a summary file we can commit)
def write_manifest(run_id: int, scenario: str, mode: str, duration: int) -> None:

# metrics.csv has no scenario column, so need to remember which run was which

    os.makedirs(RESULTS, exist_ok=True)
    path = os.path.join(RESULTS, "manifest.csv")
    is_new = not os.path.exists(path)
    
    with open(path, "a", newline="") as f:

        writer = csv.writer(f)
        if is_new:
            writer.writerow(["run_id", "scenario", "mode", "duration_s"])
        writer.writerow([run_id, scenario, mode, duration])


def rows_for_run(run_id: int) -> list[dict[str, str]]:

    path = os.path.join(RESULTS, "metrics.csv")

    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return [r for r in csv.DictReader(f) if r["run_id"] == str(run_id)]


def print_rows(rows: list[dict[str, str]]) -> None:

    if not rows:
        print("no metrics rows for this run (a flow's end message may have been lost)")

    for r in rows:
        print(f"  {r['flow']:<11} loss={r['packet_loss_pct']:>8}%  "
              f"goodput={r['goodput_mbps']:>9} Mbps  "
              f"avg_latency={r['avg_latency_ms']} ms  p95={r['p95_latency_ms']} ms")


def append_summary(label: str,
                    scenario: str,
                    mode: str,
                    rows: list[dict[str, str]]
                   ) -> None:
    
    # experiments/<label>/summary.csv = metrics rows + scenario name"""
    if not rows:
        return
    
    out_dir = os.path.join(EXPERIMENTS, label)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "summary.csv")
    is_new = not os.path.exists(path)
    fields : list[str] = ["scenario", "mode"] + list(rows[0].keys())

    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if is_new:
            writer.writeheader()
        for r in rows:
            writer.writerow({"scenario": scenario, "mode": mode, **r})


# 7. one complete run 
def run_once(scenario: str, 
             mode: str,
             duration: int, 
             label: str
             ) -> None:

    # note : this's how run_id is being generated,need to fix this
    run_id = int(time.time())       # one id shared by every sender
    out_dir = os.path.join(RESULTS, f"{scenario}_{run_id}")
    os.makedirs(out_dir, exist_ok=True)
    logs: list[IO[str]] = []

    net = build(mode)  # this builds the network, build is imported from topology.py
    net.start()

    try:
        receiver = start_receiver(net, out_dir, logs)
        time.sleep(1)          # let the receiver open its 4 ports
        senders = start_senders(net, scenario, duration, run_id, out_dir, logs)

        for p in senders:     # wait until every sender is done
            p.wait()
        time.sleep(GRACE_SECONDS)    # let the receiver finish its last run
        stop_receiver(receiver)

    finally:
        net.stop()

        for log in logs:
            log.close()

    write_manifest(run_id, scenario, mode, duration)
    rows = rows_for_run(run_id)
    print(f"\nscenario={scenario} mode={mode} run_id={run_id}  (logs: {out_dir})")
    print_rows(rows)
    append_summary(label, scenario, mode, rows)


# 8. the command line
def main() -> None:

    parser = argparse.ArgumentParser(description="run one scenario in Mininet")

    parser.add_argument("scenario", choices=SCENARIOS)

    parser.add_argument("--mode",
                        choices=["none", "remote"],
                        default="none",
                        help="none = no controller, remote = Ryu controller")
    
    parser.add_argument("--duration", type=int, default=30, help="seconds of traffic")
    parser.add_argument("--repeat", type=int, default=1, help="how many times to run it")
    parser.add_argument("--label",
                        default="baseline",
                        help="results are also saved in experiments/<label>/summary.csv")
    
    args = parser.parse_args()

    if os.geteuid() != 0:
        sys.exit("Mininet needs root: run it with  sudo python3 mininet/run_experiment.py ...")

    if shutil.which("python3") is None:
        sys.exit("python3 not found")

    setLogLevel("warning")    # keep Mininet quiet,else it prints those links,hosts info and all

    for i in range(args.repeat):
        print(f"--- run {i + 1} of {args.repeat} ---")
        run_once(args.scenario, args.mode, args.duration, args.label)
        if i + 1 < args.repeat:
            time.sleep(2)            # also keeps run ids unique


if __name__ == "__main__":
    main()