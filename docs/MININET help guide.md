i felt like sharing file to help u guys with the setup and to understand the cmds we use inside mininet and also the cmds to run the entire exp using `run_experiment.py`

## Mininet Topology

```text
h1 (10.0.0.1) ──┐
h2 (10.0.0.2) ──┼── s1 ═══ 5 Mbps (s1-eth4) ═══ s2 ── h4 (10.0.0.4, receiver)
h3 (10.0.0.3) ──┘
```

---

## 1. Install (WSL2 Ubuntu or Ubuntu VM)

```bash
sudo apt update
sudo apt install -y mininet openvswitch-switch iperf
sudo service openvswitch-switch start      # repeat after every WSL restart
```

Check the install:

```bash
sudo mn --test pingall      # expect: 0% dropped
```

After any crash or Ctrl+C run `sudo mn -c` to clean up.

| | WSL2 | Ubuntu VM |
|---|---|---|
| Start OVS | `sudo service openvswitch-switch start` after each restart | automatic |
| OVS queues (needed for priority in next phase) | may not work (kernel module) | work |

---

## 2. Run the files

Run everything from the repo root (`cd ~/cn-project`).

### Network only: `mininet/topology.py` 

> just to see the proj topology,it is implicitly used by `mininet/run_experiment.py`


```bash
# no controller
sudo python3 mininet/topology.py     
#at the mininet> prompt (which's like mininet>pingall)
pingall    #expect 0% dropped
links
exit     #close and cleanup
```
```bash
# with Ryu (start Ryu first, see section 3)
sudo python3 mininet/topology.py --mode remote
# pingall at the mininet> prompt is 0% dropped only while Ryu is running
```

 Options: `--mode none|remote` and `--cap tclink|none` (`tclink` = the cable is limited to 5 Mbps, `none` = unlimited cable for OVS-queue limiting).

## 3. Experiment 1 - No controller

```bash
sudo python3 mininet/run_experiment.py congestion --duration 10 --run-id 4 --label baseline   # no controller mode,so label=baseline
sudo python3 mininet/run_experiment.py no_congestion --duration 10 --repeat 3 --run-id 5 --label baseline  # 3 runs, ids 5, 6, 7
```

| Option | Meaning | Default |
|---|---|---|
| `scenario` | `no_congestion` (2 flows) or `congestion` (4 flows) | required |
| `--mode` | `none` = no controller, `remote` = Ryu | `none` |
| `--duration` | seconds of traffic | 30 |
| `--repeat` | number of runs | 1 |
| `--run-id` | id shared by all senders of a run; with `--repeat` it goes up by 1 each run | current time |
| `--label` | folder name under `experiments/` | `baseline` |

> refer the `mininet/run_experiment.py` file for more info abt these cla's

- Use a **new `--run-id` for every run**: all the results are grouped by it, so don't reuse.
- Use `--label test` for practice runs so they don't end up in `experiments/baseline/`.

The script builds the network, starts `receiver.py` on h4 and one `sender.py` per flow on h1, h2 and h3, then saves the results.

### the results

| Path | Content |
|---|---|
| `results/<scenario>_<run_id>/` | `receiver.log` and one `sender_<flow>.log` per flow |
| `results/metrics.csv` | one row per flow per run (loss, goodput, latency) |
| `results/fairness.csv` | Jain's fairness index per finished experiment |
| `results/manifest.csv` | which `run_id` was which scenario and mode |
| `experiments/<label>/summary.csv` | metrics rows plus scenario and mode |


---

## 4. Using it with a Ryu controller (Experiment 2 - baseline_ryu)

### 4.1 One-time Ryu setup

Ryu is an old project (last release 4.34), so it needs **Python 3.9** and a pinned `eventlet`. Use a separate venv so it doesn't touch the rest of your Python(here,its `uvenv`):

```bash
python3 -m venv ~/uvenv  
source ~/uvenv/bin/activate

pip install uv                 ]
uv venv --python 3.9 ~/ryuenv
source ~/ryuenv/bin/activate
uv pip install "setuptools==57.5.0" wheel pbr pip
PBR_VERSION=4.34 python -m pip install --no-build-isolation "ryu==4.34" "eventlet==0.30.2"
ryu-manager --version           # expect 4.34

deactivate
```

- Ryu runs inside `~/ryuenv`. Mininet runs outside it (system Python, with `sudo`).

### 4.2 The 3 terminals

Open three terminals, all in `~/cn-project`. **Start order: Ryu first, then Mininet.**

**Terminal 1: controller**

```bash
source ~/ryuenv/bin/activate
cd ~/cn-project
ryu-manager controller/baseline_controller.py     
```

Expect `switch connected: dpid=1` and `dpid=2` once Mininet starts. Leave it running; it also prints a line for every QoS rule it installs.
No rules installed,just forwarding

**Terminal 2: the network** 

```bash

sudo python3 mininet/run_experiment.py congestion --mode remote --repeat 3 --label baseline_ryu
```

**Terminal 3: look inside** (works while terminal 2 is running)

```bash
sudo ovs-ofctl -O OpenFlow13 dump-flows s1     # rules + packet counters
sudo ovs-ofctl -O OpenFlow13 dump-flows s2
sudo ovs-vsctl show                            # switches + controller connection
tc -s qdisc show dev s1-eth4                   # queue + drops on the bottleneck port
tc -s class show dev s1-eth4                   # per-queue counters (only with OVS queues)
```

### 4.3 Between runs

```bash
# terminal 2: after exit / Ctrl+C
sudo mn -c
# terminal 1: Ctrl+C, then start the other controller if you are switching
```

Always restart Ryu when you switch controllers, so the old one's learned table is gone.

---

## 5. Experiment 3 - Static QoS

Open three terminals, all in `~/cn-project`. **Start order: Ryu first, then Mininet.**

**Terminal 1: controller**

```bash
source ~/ryuenv/bin/activate
ryu-manager controller/qos_controller.py     
```

Expect `switch connected: dpid=1` and `dpid=2` once Mininet starts. Leave it running; it also prints a line for every QoS rule it installs.

**Terminal 2: the network** 

```bash
sudo python3 mininet/run_experiment.py congestion --mode remote --cap none --queues --run-id 186 --duration 30 --label static
```

- **The bug**: rn there's a small bug bcz loses for all flows are 0% and the senders are a bit slow as the packets wait in the sender's socket buffer when the queues are full which also increases the latencies.(will fix next by making sender buffer big,and push the packets directly to the queues and drop them when full)

- the mode must be `remote`,to control the switches in mininet by our controller.
- cap must be `none` for the ovs queue creation else tclink will be used,also use the option `--queues`

**Terminal 3, about 10 seconds into the run:**

```bash
sudo ovs-ofctl -O OpenFlow13 dump-flows s1 
```
- make sure u run it during the run,bcz all switches are virtual and cleaned up after the run.
- this cmd shows the 4 qos rules(priority-10 rules) installed on s1 by the sdn controller,each ending in output:"s1-eth4"


---

## Troubleshooting (Errors i faced)

| Problem | Fix |
|---|---|
| `File exists` or stale errors | `sudo mn -c` |
| OVS errors | `sudo service openvswitch-switch start` |
| `can't open file ... app/receiver.py` | Run from the repo root |
| `--mode remote` and nothing forwards | Ryu is not running or not on port 6653 |
| No row for a flow in `metrics.csv` | Its END message was lost,so rerun |