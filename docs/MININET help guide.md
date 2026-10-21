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

> just to see the proj topology,it is implicitly used by `mininet/.run_experiment.py`


```bash
# no controller
sudo python3 mininet/topology.py     
#at the mininet> prompt (which's like mininet>pingall)
pingall    #expect 0% dropped
links
exit     #close and cleanup
```
```bash
 # with Ryu (start Ryu first)  
sudo python3 mininet/topology.py --mode remote  
# dont try this now,we need from phase 4
#even if u try,when u pingall at the mininet> prompt,it will be 100% dropped
```

 Options: `--mode none|remote` and `--cap tclink|none` (`tclink` = the cable is limited to 5 Mbps, `none` = unlimited cable for OVS-queue limiting).

### One scenario: `mininet/run_experiment.py`

```bash
sudo python3 mininet/run_experiment.py no_congestion --duration 30
sudo python3 mininet/run_experiment.py congestion --duration 30
sudo python3 mininet/run_experiment.py congestion --mode remote --repeat 3 --label static   # this also wil not work now ; only try the first two
```

| Option | Meaning | Default |
|---|---|---|
| `scenario` | `no_congestion` (2 flows) or `congestion` (4 flows) | required |
| `--mode` | `none` = no controller, `remote` = Ryu | `none` |
| `--duration` | seconds of traffic | 30 |
| `--repeat` | number of runs | 1 |
| `--label` | folder name under `experiments/` | `baseline` |

> refer the `mininet/run_experiment.py` file for more info abt these cla's

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

## 3. Using it with a controller (Next phase - doesn't work now)

| Fact | Value |
|---|---|
| Controller address | `127.0.0.1:6653`, OpenFlow 1.3 |
| Switches | `s1` (hosts + bottleneck), `s2` (receiver side) |
| `s1` ports | 1 = h1, 2 = h2, 3 = h3, 4 = bottleneck to s2 (`s1-eth4`) |
| `s2` ports | 1 = s1, 2 = h4 |
| Hosts | h1 = 10.0.0.1, h2 = .2, h3 = .3, receiver h4 = 10.0.0.4 |
| Flows by UDP port | 5001 high, 5002 medium, 5003 low, 5004 background |

Start Ryu in one terminal, then run in a second terminal:

```bash
ryu-manager controller/<your_controller>.py
sudo python3 mininet/topology.py --mode remote       # then: pingall
sudo python3 mininet/run_experiment.py congestion --mode remote --label <name>
sudo ovs-ofctl -O OpenFlow13 dump-flows s1           # see the installed rules
```

If Ryu is not running, nothing is forwarded in `remote` mode.

---

## Troubleshooting (Errors i faced)

| Problem | Fix |
|---|---|
| `File exists` or stale errors | `sudo mn -c` |
| OVS errors | `sudo service openvswitch-switch start` |
| `can't open file ... app/receiver.py` | Run from the repo root |
| `--mode remote` and nothing forwards | Ryu is not running or not on port 6653 |
| No row for a flow in `metrics.csv` | Its END message was lost,so rerun |