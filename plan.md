# QoS Traffic Management System — Project Plan

A UDP-based traffic generator with SDN-controlled priority, built on Mininet, Ryu, and Python.

## Project Goal

Build a UDP-based application that generates different classes of traffic, creates controlled network congestion, and then uses SDN to dynamically prioritize selected flows.

```text
Generate UDP traffic
        ↓
Create multiple traffic classes
        ↓
Measure normal network behavior
        ↓
Create controlled congestion
        ↓
Introduce SDN controller
        ↓
Prioritize selected flows
        ↓
Change policies dynamically
        ↓
Measure + compare results
        ↓
Analyze fairness
```

> **Build bottom-up.** Do NOT start with the SDN controller. Get the UDP application, measurements, Mininet topology, and congestion experiment working independently first. Only then add SDN.

---

## 1. System Architecture

### 1.1 High-level diagram

![Architecture](image-1.png)

### 1.2 Components

| Component | Built by | Role |
|---|---|---|
| Mininet topology | Script (Python) | Creates hosts and switches, wires them with virtual links, sets bandwidth on the bottleneck link |
| Sender/Client (`sender.py`) | Runs on sender hosts | Generates UDP traffic for one flow/class: announces the run (START), sends data packets (flow ID, sequence number, run ID, timestamp) at a chosen rate, then announces the end (END + packet count) |
| Receiver (`receiver.py`) | Runs on the receiver host | One process listening on all four flow ports; answers START/END, tracks each run in memory, and when a run ends hands the totals to `metrics.py` |
| SDN Controller (Ryu) | Separate process | Talks to the switch over OpenFlow; classifies UDP flows by port; installs/updates priority rules; polls traffic stats |
| OVS Queues | Configured on the bottleneck port | Give each flow/class a guaranteed minimum and a maximum bandwidth share |
| Metrics (`metrics.py`) | Called by the receiver | Computes loss, goodput, latency (mean/p50/p95) per flow per run, plus Jain's fairness; appends to `metrics.csv` / `fairness.csv` |
| Analysis script (later) | Runs after the experiments | Reads the CSVs and draws the comparison graphs |

### 1.3 Two separate communication paths

1. **Data path:** senders → switch → receiver (actual UDP packets, your traffic)
2. **Control path:** Controller ↔ switch (OpenFlow messages: rules in, stats out — no application data travels here)

The controller never touches your UDP packets directly. It only tells the switch *how* to treat them.

### 1.4 Packet formats (defined once in `protocol.py`)

There are **two kinds of messages**, both sent over UDP to the flow's port.

**Data packet** (the traffic being measured), 28-byte header (`struct` format `"!IQQq"`):

```
┌──────────┬──────────────┬──────────┬─────────────────┬──────────────────┐
│ Flow ID  │ Sequence no. │  Run ID  │ Send timestamp  │ Padding (filler) │
│  4 bytes │   8 bytes    │  8 bytes │  8 bytes (ns)   │  size − 28 bytes │
└──────────┴──────────────┴──────────┴─────────────────┴──────────────────┘
```

**Control message** (session bookkeeping), exactly 21 bytes (`"!4sBQII"`):

```
┌──────────────┬──────────┬──────────┬──────────┬───────────────────┐
│ Magic "QOS1" │ Type     │  Run ID  │ Flow ID  │ Packet count      │
│   4 bytes    │  1 byte  │  8 bytes │  4 bytes │ 4 bytes           │
└──────────────┴──────────┴──────────┴──────────┴───────────────────┘
```

Types: START, END, START_ACK, END_ACK. START and END carry the number of data packets the sender will send / has sent.


- Packet size (the `--size` option) is the whole UDP payload **including the 28-byte header**, so the smallest allowed size is 28 bytes.

- UDP itself gives none of this: no sequence numbers, no ACKs, no retransmission, no reliability.
- These fields are added purely as **application-level measurement information**. 
- Lost data packets are never retransmitted, because loss is what we are measuring as we are not turning UDP into TCP.

### 1.5 Flow identity / port mapping

| Flow | UDP Port | Traffic Class | Packet Size | Rate |
|---|---:|---|---:|---:|
| Flow 1 | 5001 | High (voice-like) | 200 B | 1 Mbps |
| Flow 2 | 5002 | Medium (video-like) | 500 B | 2 Mbps |
| Flow 3 | 5003 | Low (file-transfer-like) | 1000 B | 3 Mbps |
| Flow 4 | 5004 | Background | 1400 B | 4 Mbps |

At the UDP layer, "priority" is only a **label** at this stage — just a name you've assigned. It becomes real priority only once the SDN controller (Phase 4 below) reads these port numbers and treats the flows differently. These are **synthetic** traffic classes — they don't need to be real voice/video/file applications, just packets shaped similarly to them.

---

## 2. Team Roles — Quick Summary

| Person | Main responsibility |
|---|---|
| **A — UDP + Measurement** | UDP traffic generator, multiple traffic classes, sequence numbers, timestamps, throughput/latency/loss measurement, data collection |
| **B — Mininet + Network** | Topology, hosts, switches, links, bandwidth limits, delay/loss configuration, congestion setup |
| **C — SDN** | Ryu, Open vSwitch, OpenFlow, flow classification, priority rules, dynamic policies |

Everyone should still understand the complete architecture for the final demo/viva — see Section 5 for how each role's work spans both deliverables in detail.

---

## 3. Build Order: Phased Roadmap

This is the order to actually build things in — each phase should fully work before you touch the next one. Don't try to learn everything at once.

```text
PHASE 1: Basic UDP application
        ↓
PHASE 2: Multiple traffic classes + measurements
        ↓
PHASE 3: Mininet network + controlled congestion
        ↓
PHASE 4: SDN controller — classification, priority, dynamic policy
        ↓
PHASE 5: Fairness analysis
        ↓
PHASE 6: Final experiments + comparison
```

### PHASE 1 — Basic UDP Application

**Goal:** understand exactly what the UDP application does, before introducing anything else. Run it on one machine, or between two Mininet hosts directly.

```text
UDP Client (h1)  ──── UDP packet ────►  UDP Server (h2)
```

- **Step 1.1 — Build a UDP server:** create socket → bind to IP/port → `recvfrom()` in a loop → print what arrived.
- **Step 1.2 — Build a UDP client:** create socket → `sendto()` a handful of packets (e.g., 10).

Understand as you go: what a UDP socket/IP/port is, what `sendto()`/`recvfrom()` do, why UDP has no TCP-style reliability, and what happens if the server isn't running or the client sends to the wrong port.

### PHASE 2 — Multiple UDP Traffic Classes

**Goal:** turn one UDP flow into several flows that behave differently. One Python traffic generator can produce all of them — you don't need separate applications per flow.

- **Step 2.1 — Give every flow an identity:** a different UDP destination port each (Section 1.5 table).
- **Step 2.2 — Give flows different characteristics:** different packet size and rate per flow (Section 1.5 table).
- **Step 2.3 — Add sequence numbers + timestamps + flow ID** to every packet (Section 1.4 format) — this is what lets you detect loss later (gaps in the sequence) and measure latency.
- **Step 2.4 — Measure throughput:** `data received ÷ time`, per flow.
- **Step 2.5 — Measure packet loss:** `lost packets ÷ sent packets × 100`, found by comparing sent vs. received sequence numbers.
- **Step 2.6 — Measure latency:** `received time − sent time`, per packet.

**✅ Phase 2 checkpoint — before moving on, confirm:**
- [x] Traffic generator produces 3-4 distinct flows on distinct ports
- [x] Receiver logs every flow and correctly detects gaps in sequence numbers
- [x] You can print throughput, latency, and packet loss per flow

### PHASE 3 — Mininet Network (still no SDN)

**Goal:** put the UDP application into an actual emulated network.

- **Step 3.1 — Start with a simple topology:** `h1 ── s1 ── h2`. Learn to create hosts/switches, connect them, assign IPs, run commands on hosts. Verify with `h1 ping h2` before anything else.
- **Step 3.2 — Run your UDP application inside Mininet:** h1 = traffic generator, h2 = UDP server, traffic now flows through the emulated switch instead of localhost.
- **Step 3.3 — Create a bottleneck:** extend to the project topology `h1, h2, h3 ── s1 ══(5 Mbps)══ s2 ── h4`, where the `s1-s2` link is deliberately bandwidth-limited (bottleneck port on s1 is `s1-eth4`).
- **Step 3.4 — Create controlled congestion:** make total flow demand exceed the bottleneck capacity (the four flows add up to 1 + 2 + 3 + 4 = 10 Mbps of demand vs. a 5 Mbps link). This forces flows to compete for a limited resource, producing real loss and delay.
- **Step 3.5 — Measure BEFORE SDN (critical):** run the congested scenario with no SDN priority at all, and record throughput/latency/loss per flow. Save these numbers — this is your baseline for every later comparison.

**✅ Pre-SDN checkpoint — before touching the controller, you should be able to demonstrate:** basic UDP client/server, multiple UDP flows, port-based classification, sequence numbers, throughput/latency/loss measurement, a working Mininet topology, a controlled bottleneck, real congestion (demand > capacity), and saved baseline (no-SDN) results.

### PHASE 4 — Introduce SDN

Only after Phase 3 fully works do you add the controller:

```text
                 Ryu Controller
                       │
                    OpenFlow
                       │
                       ↓
h1 ─────────────── OVS ─────────────── h2
```

**What the three pieces are:**
- **SDN** = instead of switches deciding everything independently, a separate program (the controller) tells them how to behave. The controller is the programmable *brain*; the switch does the actual forwarding.
- **Open vSwitch (OVS)** = a software-based switch (no physical hardware needed); Mininet uses this by default.
- **Ryu** = a Python framework for writing the controller. You write rules like *"if UDP destination port == 5001, treat Flow 1 as high priority,"* and Ryu communicates that to OVS over OpenFlow.

- **Phase 4.1 — Classify flows with SDN:** the controller reads the port numbers your flows already have (5001→high, 5002→medium, 5003/5004→low) and recognizes each flow.
- **Phase 4.2 — Apply priority:** rerun the exact same congestion experiment from Step 3.5, now with SDN priority active. Compare throughput/latency/loss, no-SDN vs. with-priority, per flow.
- **Phase 4.3 — Dynamic policy changes:** make the controller change priorities *while the experiment is running* — e.g., Flow 1 normal for the first 30 seconds, then promoted to HIGH for the next 30, while Flow 2 becomes HIGH in a later window. This is what proves "dynamic," not just a fixed setup.

### PHASE 5 — Fairness Analysis

**Goal:** investigate the trade-off — if you prioritize one class, what happens to the others?

Example of what you're looking for:
```text
Before:  Flow1=2Mbps  Flow2=2Mbps  Flow3=2Mbps
After prioritizing Flow1:  Flow1=4Mbps  Flow2=1.5Mbps  Flow3=0.5Mbps
```
Quantify this with **Jain's Fairness Index** once the basic experiment works — don't worry about the formula until you have real numbers to plug in.

### PHASE 6 — Final Results

Your final comparison should cover five experiments:

| # | Experiment | What it shows |
|---|---|---|
| 1 | No congestion | True baseline |
| 2 | Congestion, no SDN | Problem demonstrated |
| 3 | Congestion + SDN priority | Static fix |
| 4 | Congestion + dynamic SDN policy | Full solution |
| 5 | Fairness analysis across the above | Trade-off quantified |

---

## 4. Marks Map: D1 / D2 Alignment

The phases above map onto your two graded deliverables like this:

| Person | D1 items (of 15) | D2 items (of 25) | Total marks this person drives |
|---|---|---|---|
| **A — UDP + Measurement** | TCP/UDP Socket Implementation (4) — Phases 1-2 | Complete Application Functionality (4), Performance Evaluation & Comparison (5) — Phases 2, 6 | 13 |
| **B — Mininet + Network** | Mininet + SDN Implementation — topology half (2 of 4) — Phase 3 | Dynamic SDN Functionality — queue half (2 of 6) — Phase 4 | 4 |
| **C — SDN** | Mininet + SDN Implementation — controller half (2 of 4) — Phase 4 start | Dynamic SDN Functionality — controller half (4 of 6) — Phase 4.1-4.3 | 6 |
| **Shared (whole team)** | Problem Understanding & Architecture (2), Socket–SDN Integration (3), Initial Testing & Demo (2) | Challenging Scenario/Robustness (4) — Phase 5 trade-off, Documentation/Final Demo/Viva (6) — Phase 6 | 17 |

The "Shared" row is deliberate — the rubric rewards integration and viva understanding, which only work if all three of you can speak to the whole system, not just your own file.

---

## 5. Elaborated Role Breakdown

### 5.1 Person A — UDP + Measurement

**Owns throughout the whole project:** packet generation, logging, and turning raw logs into results. This role has the most continuous arc — you build the thing that produces data in D1 (Phases 1-2), and the thing that proves QoS worked in D2 (Phases 5-6).

**D1 tasks (target: TCP/UDP Socket Implementation, 4 marks)**
- [ ] `sender.py`: opens a UDP socket, builds packets (Section 1.4), sends at a configurable rate (Phase 1.2, 2.1-2.3)
- [ ] `receiver.py`: opens a UDP socket, listens on one port, logs each packet (seq, send time, recv time, size) to CSV (Phase 1.1)
- [ ] Command-line arguments for flow ID, destination, port, packet size, rate, duration
- [ ] Basic error handling (e.g., graceful behavior if receiver isn't up yet)
- [ ] Throughput, latency, and packet-loss calculations working on logged data (Phase 2.4-2.6)

**D1 shared contribution**
- [ ] Run `sender.py`/`receiver.py` inside Mininet hosts with Person B, confirm logs fill correctly (Socket–SDN Integration)
- [ ] Demo the sender → receiver flow live (Initial Testing & Demo)

**D2 tasks (target: Complete Application Functionality, 4 marks + Performance Evaluation, 5 marks)**
- [ ] Extend to all flows running simultaneously, each with its own size/rate profile
- [ ] Receiver handles all flow ports at once (threads or separate processes)
- [ ] Logging robust enough for a full multi-minute experiment without errors
- [ ] `analyze.py`: per-flow throughput, average latency, packet loss, for each of the 5 experiment runs (Phase 6 table)
- [ ] Compute Jain's Fairness Index per run (Phase 5)
- [ ] Build comparison graphs (no-congestion vs. congestion-no-SDN vs. static priority vs. dynamic priority)

**D2 shared contribution**
- [ ] Help design the challenging scenario by predicting what the logs *should* show if it works
- [ ] Write the results section of the documentation

### 5.2 Person B — Mininet + Network

**Owns throughout the whole project:** the network itself — building it in D1 (Phase 3), then making the bottleneck meaningful with real QoS queues in D2 (Phase 4).

**D1 tasks (target: half of Mininet + SDN Implementation)**
- [ ] `topology.py`: hosts, switch(es), bottleneck link with bandwidth cap via `TCLink` (Phase 3.1-3.3)
- [ ] `RemoteController` configured, pointing at the Ryu controller (keep a no-controller mode in `topology.py` for the first tests)
- [ ] Verified with `pingall` — all hosts reachable
- [ ] Controlled congestion set up: flow demand exceeding bottleneck capacity (Phase 3.4)
- [ ] Baseline (no-SDN) measurements recorded and saved (Phase 3.5) — critical, do this before SDN work starts. Run it through C's forwarding-only controller with the same bandwidth and queue settings you will use later, 3 runs per scenario, and record which `run_id` is which scenario

**D1 shared contribution**
- [ ] Run `sender.py`/`receiver.py` inside Mininet hosts with Person A (Socket–SDN Integration)
- [ ] Help draw the architecture diagram — you know the topology best (Problem Understanding & Architecture)

**D2 tasks (target: half of Dynamic SDN Functionality)**
- [ ] Create OVS queues on the bottleneck port (`ovs-vsctl`): one queue per flow, each with min/max rate
- [ ] Hand off queue IDs to Person C for the controller's `SetQueue()` rules
- [ ] Script the experiment runner so all five runs (Phase 6 table) are repeatable with one command

**D2 shared contribution**
- [ ] Design and trigger the challenging scenario (e.g., congestion spike, sender joining mid-run) — this is primarily a network-level event
- [ ] Write the architecture/setup section of the documentation

### 5.3 Person C — SDN

**Owns throughout the whole project:** the "brain" — a plain forwarding switch in D1, growing into real classification and live policy changes in D2 (Phase 4). This role carries the single highest-weighted D2 item (Dynamic SDN Functionality, 6 marks).

**D1 tasks (target: half of Mininet + SDN Implementation)**
- [ ] `baseline_controller.py`: a working Ryu learning-switch (forwarding only, no QoS yet)
- [ ] Confirm it correctly installs forwarding rules and supports `pingall`
- [ ] Learn the SDN/OVS/Ryu relationship well enough to explain it (see Phase 4 intro above)

**D1 shared contribution**
- [ ] Confirm packets from Person A pass through the switch correctly under your controller (Socket–SDN Integration)
- [ ] Explain the control path vs. data path for the architecture write-up (Problem Understanding & Architecture)

**D2 tasks (target: Dynamic SDN Functionality, 6 marks)**
- [ ] `qos_controller.py`: classification rules — match UDP by destination port → `SetQueue()` → output (using Person B's queue IDs, Section 1.5 mapping) — Phase 4.1
- [ ] Verify with `ovs-ofctl dump-flows s1` that rule packet-counters increase correctly
- [ ] Rerun the congestion experiment with priority active and compare against the saved baseline — Phase 4.2
- [ ] `dynamic_controller.py`: periodic stats polling (`hub.sleep` loop) + live rule updates mid-experiment (e.g., shifting which flow is HIGH every 30 seconds) — Phase 4.3

**D2 shared contribution**
- [ ] Make the controller respond visibly to the challenging scenario (e.g., detect the spike and change rules)
- [ ] Write the controller/SDN-logic section of the documentation

---

## 6. Recommended Technology Stack

| Component | Technology |
|---|---|
| Operating system | Ubuntu / WSL |
| Network emulator | **Mininet** |
| Virtual switch | **Open vSwitch** |
| SDN controller | **Ryu** |
| Programming | **Python** |
| UDP traffic | Python sockets |
| Traffic generation/measurement | Python + optionally `iperf3` |
| Packet analysis | Wireshark |
| Graphs | matplotlib |
| Version control | Git/GitHub |

---

## 7. Suggested Timeline

| Focus | Target |
|---|---|
| Phase 1-2: each person builds their own D1 part in isolation (A: UDP client/server + multi-flow + measurement; B+C: baseline controller + `pingall`) | Parts work individually |
| Phase 3 + A+B integrate: real sender/receiver traffic running through Mininet, bottleneck + congestion created | Socket–SDN Integration done |
| Finish Phase 3.5 (baseline results) and remaining D1 shared items, submit | **D1 submitted (15 marks)** |
| Phase 4.1: B sets up OVS queues; C builds classification rules; verify with `dump-flows` | Static half of Dynamic SDN Functionality done |
| Phase 4.2-4.3: C adds dynamic stats polling + live rule updates; team designs and runs the challenging scenario together | Dynamic SDN Functionality + Challenging Scenario done |
| Phase 5-6: run all five experiment types; A builds analysis, fairness index, and graphs | Performance Evaluation done |
|Everyone writes their section of the documentation, polish repo, rehearse viva together | **D2 submitted (25 marks)** |

---

## 8. Quick Reference: Commands You'll Use Often

```bash
# --- first tests, one machine, no Mininet (two terminals) ---
python3 receiver.py                                              # terminal 1: listens on 5001-5004
python3 sender.py --flow high --size 200 --rate 1 --count 1000   # terminal 2

# --- inside the Mininet CLI: start the receiver first, then all senders with the SAME --run-id ---
h4 python3 receiver.py > /tmp/recv.log 2>&1 &
h1 python3 sender.py --ip 10.0.0.4 --flow high       --size 200  --rate 1 --count 18750 --run-id 1 &
h2 python3 sender.py --ip 10.0.0.4 --flow medium     --size 500  --rate 2 --count 15000 --run-id 1 &
h3 python3 sender.py --ip 10.0.0.4 --flow low        --size 1000 --rate 3 --count 11250 --run-id 1 &
h3 python3 sender.py --ip 10.0.0.4 --flow background --size 1400 --rate 4 --count 10714 --run-id 1 &
# counts above = 30 seconds of traffic:  count = rate * 1e6 * seconds / (size * 8)

# results: one row per flow per run in metrics.csv (next to metrics.py); fairness.csv when all 4 flows finished

# Check the controller's installed rules and their hit-counters
ovs-ofctl -O OpenFlow13 dump-flows s1

# Check queue / drop stats on the bottleneck port (s1-eth4 in the 3-sender topology)
tc -s qdisc show dev s1-eth4
tc -s class show dev s1-eth4

# Verify bottleneck bandwidth (start the iperf server on h4 first)
h4 iperf -s -u -i 1 &
h1 iperf -u -c 10.0.0.4 -b 20M -t 10
```

---

## 9. First Milestone (done)

Do not start with Mininet or Ryu. Your very first mini-project is just:

```text
Python UDP Client
        │  10 packets
        ↓
Python UDP Server
        ↓
Measurements:

Packets sent:       10
Packets received:   10
Packets lost:         0
Average latency:    XX ms
Throughput:         XX Mbps
```

Once this works, move to multiple UDP flows (Phase 2).

**Key idea to remember:** the project is layers built one at a time — basic UDP → multiple flows → measurements → Mininet → congestion → baseline → *then* SDN control, priority, and dynamic policies on top. If a layer doesn't work yet, don't build the next one on top of it.

---

## 10. Next Milestone — Mininet Network with Bottleneck 

Milestone 1 (basic UDP application) is done. The next milestone runs the application inside an emulated network with a shared bottleneck, **still without priority**. This finishes D1.

```text
h1 ──┐
h2 ──┼── s1 ═══ 5 Mbps ═══ s2 ── h4 (UDP receiver)
h3 ──┘
```

### Topology (`mininet/topology.py`)

- Create hosts h1, h2, h3, h4 (10.0.0.1 – 10.0.0.4) and switches s1, s2.
- Connect h1, h2, h3 to s1, h4 to s2, and s1–s2 as the 5 Mbps bottleneck (port `s1-eth4`).
- Support two modes: no controller (first tests) and `RemoteController` pointing at Ryu.
- Keep the bottleneck queue short (about 100 packets).

### Bottleneck Check

- Verify the cap with iperf (send 20 Mbps over UDP, expect about 5 Mbps received).
- Check early whether the `TCLink` cap and OVS queues can share the bottleneck port. If not, make the 5 Mbps cap with the OVS queue instead, and write the decision down.

### Running the UDP Application

- Start the receiver on h4 and the senders on h1, h2 and h3 (h3 runs both low and background).
- Use `--ip 10.0.0.4` and the **same `--run-id`** for all four senders.
- Set `--count` from the wanted duration: `count = rate × 1e6 × seconds / (size × 8)`.

### Experiment Runner (`mininet/run_experiment.py`)


- Run one scenario with one command: *no congestion* (flows 1 and 2) or *congestion* (all four).
- Start the receiver first, wait for the senders to finish plus about 2 s, then stop the receiver.
- Record which `run_id` belongs to which scenario in a small manifest file.

### Baseline

- Run each scenario 3 times and confirm: demand (10 Mbps) exceeds capacity (5 Mbps), every flow loses packets, total throughput is about 5 Mbps.
- Repeat through Person C's forwarding-only controller, then save the baseline.

### Completion Checklist

- [ ] `pingall` works (no-controller mode and with Ryu)
- [ ] iperf confirms about 5 Mbps
- [ ] Receiver on h4 receives from h1, h2 and h3
- [ ] Demand (10 Mbps) > capacity (5 Mbps), all flows lose packets
- [ ] `run_experiment.py` runs a scenario with one command
- [ ] Baseline saved (3 runs per scenario)
- [ ] TCLink vs. OVS-queue decision written down

---