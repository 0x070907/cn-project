# QoS Traffic Management System

A UDP-based traffic management system using Software-Defined Networking
(SDN) to dynamically prioritize different classes of traffic during network
congestion.

The system is implemented and evaluated using Mininet, Open vSwitch, and a
Ryu SDN controller.

---

## 1. Project Overview

In a congested network, different types of traffic can have different
performance requirements.

For example, real-time traffic is generally more sensitive to latency and
packet loss, while bulk or background traffic can tolerate higher delay and
lower throughput.

This project creates a controlled network environment where multiple UDP
traffic classes compete for a shared network bottleneck. An SDN controller
monitors the network and dynamically modifies QoS policies to prioritize
selected traffic flows during congestion.

The system demonstrates:

- UDP socket programming
- Multiple concurrent traffic flows
- Controlled network congestion
- Mininet-based network emulation
- SDN-based network control
- Dynamic traffic prioritization
- Throughput, latency, and packet-loss measurement
- Fairness analysis
- Comparison between baseline and QoS-enabled network behaviour

---

## 2. System Architecture

```text
                              ┌──────────────────────┐
                              │    Ryu Controller    │
                              │                      │
                              │  • Monitor network   │
                              │  • Detect congestion │
                              │  • Classify flows    │
                              │  • Modify QoS rules  │
                              └──────────┬───────────┘
                                         │
                                      OpenFlow
                                         │
                                         ▼
┌───────────────────────────────────────────────────────────────────────┐
│                         MININET NETWORK                               │
│                                                                       │
│     ┌──────┐                                                         │
│     │  h1  │────── HIGH ──────┐                                     │
│     └──────┘                   │                                     │
│                                │      ┌──────────┐                   │
│     ┌──────┐                   ├─────►│    s1    │                   │
│     │  h2  │────── MEDIUM ─────┤      │   OVS    │                   │
│     └──────┘                   │      └────┬─────┘                   │
│                                │           │                          │
│     ┌──────┐                   │        BOTTLENECK                   │
│     │  h3  │────── LOW ───────┘        LINK                          │
│     └──────┘                              │                           │
│                                           │                           │
│                                      ┌────▼─────┐                     │
│                                      │    s2     │                    │
│                                      │   OVS     │                    │
│                                      └────┬──────┘                    │
│                                           │                           │
│                                      ┌────▼─────┐                     │
│                                      │    h4     │                    │
│                                      │ UDP Server│                    │
│                                      └───────────┘                    │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
```

### Data Path

The actual application traffic follows:

```text
UDP Clients → s1 → Bottleneck Link → s2 → UDP Server
```

All traffic classes pass through the same bottleneck so that they compete
for the same limited network resource.

### Control Path

The SDN controller communicates with the Open vSwitch switches using
OpenFlow:

```text
Ryu Controller ←──── OpenFlow ────→ OVS Switch
```

The controller does not carry the application traffic. It monitors network
conditions and installs or modifies rules that determine how traffic is
handled by the switches.

---

## 3. Why a Bottleneck is Required

The main purpose of the project is to study how QoS policies behave when
network resources become limited.

The network therefore contains a deliberately bandwidth-limited link:

```text
             ┌──────┐
             │  h1  │──────┐
             └──────┘      │
             ┌──────┐      │     ┌────┐      ┌────┐
             │  h2  │──────┼────►│ s1 │══════│ s2 │────► h4
             └──────┘      │     └────┘      └────┘    Server
             ┌──────┐      │       bottleneck
             │  h3  │──────┘
             └──────┘
```

For example, the traffic generators could collectively produce more
traffic than the bottleneck can carry:

```text
Traffic demand  = 9 Mbps
Bottleneck      = 5 Mbps
```

This causes the flows to compete for the available bandwidth.

The SDN controller can then apply QoS policies to give selected flows
preferential treatment.

---

## 4. Main Components

### UDP Traffic Generators

The clients generate multiple UDP flows with different characteristics.

Each flow can have:

- A traffic class
- A UDP port
- A packet size
- A sending rate
- A flow ID

Multiple flows can run simultaneously to create competition for the
bottleneck.

### UDP Server

The server receives packets from all traffic flows.

It records information from received packets so that network performance
can be evaluated after an experiment.

### Mininet

Mininet provides the virtual network environment.

It is responsible for creating:

- Hosts
- Switches
- Virtual network links
- Bandwidth constraints
- Delay and packet-loss conditions

### Open vSwitch

Open vSwitch (OVS) acts as the software-defined network switch.

It forwards packets according to the flow and QoS rules installed by the
SDN controller.

### Ryu Controller

Ryu provides the SDN control layer.

The controller is responsible for:

- Monitoring network statistics
- Identifying traffic flows
- Detecting congestion
- Classifying traffic
- Applying QoS policies
- Dynamically modifying traffic treatment

### Analysis

The collected measurements are processed to evaluate the behaviour of the
network under different conditions.

---

## 5. Traffic Classes

Traffic classes are initially identified using UDP destination ports.

| UDP Port | Traffic Class | Example |
|----------|----------------|---------|
| `5001` | HIGH | Real-time / voice-like |
| `5002` | MEDIUM | Video / normal application |
| `5003` | LOW | Bulk transfer |
| `5004` | BACKGROUND | Background traffic |

These are synthetic traffic classes.

They represent different traffic requirements and priorities rather than
actual implementations of voice, video, or file-transfer applications.

The UDP destination port provides a simple and consistent way for the SDN
controller to identify each flow.

---

## 6. Application Packet Format

The UDP application adds measurement information to every packet.

```text
┌──────────┬─────────────────┬────────────────┬─────────────┐
│ Flow ID  │ Sequence Number │ Send Timestamp │   Payload   │
└──────────┴─────────────────┴────────────────┴─────────────┘
```

### Flow ID

Identifies which traffic flow generated the packet.

### Sequence Number

Allows the receiver to identify missing packets and calculate packet loss.

### Send Timestamp

Allows the receiver to calculate packet latency.

### Payload

Filler data used to reach the desired packet size for the traffic flow.

These fields are part of the application's own protocol. They are not
provided by UDP itself.

---

## 7. Network Behaviour

The system operates in several stages.

### Normal Operation

Traffic flows through the network without congestion.

```text
Clients → s1 → s2 → Server
```

### Congested Operation

The combined traffic demand exceeds the bottleneck capacity.

```text
Multiple UDP flows
       ↓
Traffic demand > bottleneck capacity
       ↓
Competition for bandwidth
       ↓
Increased delay / packet loss / reduced throughput
```

### QoS Operation

The SDN controller monitors the network and applies policies to selected
traffic classes.

```text
Congestion detected
        ↓
Identify traffic classes
        ↓
Apply QoS policy
        ↓
Prioritize selected flows
        ↓
Measure performance
```

### Dynamic QoS

The controller can modify its policies while the experiment is running
based on changing network conditions.

This distinguishes the project from a purely static priority system.

---

## 8. Metrics

### Throughput

The amount of successfully received data per unit of time.

Measured separately for each traffic flow.

### Latency

The time between a packet being sent and received.

Application-level timestamps are used to calculate packet latency.

### Packet Loss

The percentage of transmitted packets that are not received.

Sequence numbers are used to identify missing packets.

### Fairness

Measures how evenly the available bandwidth is distributed among the
traffic flows.

Jain's Fairness Index can be used for the final analysis.

---

## 9. Development Phases

The system is developed incrementally.

```text
Phase 1
Basic UDP Client / Server
        ↓
Phase 2
Multiple UDP Traffic Classes
        ↓
Phase 3
Measurements
        ↓
Phase 4
Mininet Network + Bottleneck
        ↓
Phase 5
SDN Controller + QoS
        ↓
Phase 6
Dynamic QoS Policies
        ↓
Phase 7
Performance Evaluation
```

### Phase 1 — Basic UDP Application

Establish communication between a UDP sender and receiver and implement
the application-level packet format.

### Phase 2 — Multiple Traffic Classes

Introduce multiple simultaneous UDP flows using different ports, packet
sizes, and sending rates.

### Phase 3 — Measurements

Implement measurement of:

- Throughput
- Latency
- Packet loss

### Phase 4 — Mininet and Congestion

Move the UDP application into the Mininet network.

Introduce the shared bottleneck and create controlled congestion by making
the combined traffic demand exceed the available bottleneck bandwidth.

A baseline experiment is performed without dynamic QoS.

### Phase 5 — SDN Controller and QoS

Connect the Mininet network to the Ryu controller.

Implement:

- Flow classification
- Network monitoring
- QoS rules
- Traffic prioritization

### Phase 6 — Dynamic QoS

Allow the controller to modify QoS policies during runtime in response to
network conditions.

### Phase 7 — Performance Evaluation

Compare the behaviour of the network under different conditions and
analyze the resulting performance and fairness.

---

## 10. Milestone 1 — Basic UDP Application

The first implementation milestone establishes the basic application
before introducing Mininet or SDN.

```text
┌──────────────┐       UDP        ┌──────────────┐
│ UDP Sender   │ ────────────────► │ UDP Receiver │
└──────────────┘                  └──────────────┘
```

### Sender

The sender should:

- Create a UDP socket.
- Send packets to a configurable IP address and port.
- Include a flow ID.
- Include a sequence number.
- Include a send timestamp.
- Support configurable packet size.
- Support configurable sending rate.

### Receiver

The receiver should:

- Create and bind a UDP socket.
- Receive UDP packets.
- Extract packet metadata.
- Log received packets.
- Detect missing sequence numbers.
- Calculate packet loss.
- Calculate packet latency.
- Calculate throughput.

### Completion Checklist

- [ ] UDP sender works.
- [ ] UDP receiver works.
- [ ] Sender and receiver communicate successfully.
- [ ] Sequence numbers are included in packets.
- [ ] Timestamps are included in packets.
- [ ] Different UDP ports can represent different flows.
- [ ] Packet loss can be calculated.
- [ ] Basic latency can be calculated.
- [ ] Basic throughput can be calculated.
- [ ] Multiple simultaneous flows are supported.

---

## 11. Development Workflow

Feature development should be performed on separate branches rather than
directly on `main`.

```text
main
 │
 ├── feature/udp-sender
 ├── feature/udp-receiver
 ├── feature/analysis
 ├── feature/mininet-topology
 └── feature/qos-controller
```

### Branch Naming

```text
feature/<component>
fix/<issue>
experiment/<experiment-name>
```

### Commit Messages

Commits should clearly describe the change.

Examples:

```text
Add basic UDP sender
Add UDP receiver
Add packet metadata format
Add traffic classification
Add Mininet topology
Add congestion experiment
Implement QoS policy
```

### Pull Requests

Completed work should be merged through a pull request.

```text
Feature Branch
      ↓
Pull Request
      ↓
Review
      ↓
Merge into main
```

---

## 12. Repository Structure

```text
cn-project/
│
├── app/
│   ├── sender.py
│   └── receiver.py
│
├── controller/
│
├── mininet/
│
├── analysis/
│
├── experiments/
│
├── docs/
│
├── README.md
└── .gitignore
```

---

## 13. Technology Stack

| Component | Technology |
|-----------|------------|
| Programming Language | Python |
| Transport Protocol | UDP |
| Network Emulator | Mininet |
| Virtual Switch | Open vSwitch |
| SDN Controller | Ryu |
| SDN Protocol | OpenFlow |
| Analysis | Python / matplotlib |
| Version Control | Git / GitHub |

---

## 14. Final System

The completed system integrates the UDP application, Mininet network,
Open vSwitch, and Ryu controller.

```text
                              ┌──────────────────────┐
                              │    Ryu Controller    │
                              │                      │
                              │  Monitor             │
                              │  Classify            │
                              │  Detect congestion   │
                              │  Modify QoS policies │
                              └──────────┬───────────┘
                                         │
                                      OpenFlow
                                         │
                                         ▼
     ┌───────────────────────────────────────────────────────────┐
     │                        Mininet                            │
     │                                                           │
     │    ┌──────┐                                               │
     │    │  h1  │──── HIGH ─────┐                               │
     │    └──────┘               │      ┌────────┐               │
     │                           ├─────►│   s1   │               │
     │    ┌──────┐               │      │  OVS   │               │
     │    │  h2  │──── MEDIUM ───┤      └───┬────┘               │
     │    └──────┘               │          │                    │
     │                           │     BOTTLENECK                │
     │    ┌──────┐               │          │                    │
     │    │  h3  │──── LOW ──────┘      ┌───▼────┐               │
     │    └──────┘                      │   s2   │               │
     │                                  │  OVS   │               │
     │                                  └───┬────┘               │
     │                                      │                    │
     │                                 ┌────▼────┐               │
     │                                 │   h4    │               │
     │                                 │UDP Server│              │
     │                                 └─────────┘               │
     │                                                           │
     └───────────────────────────────────────────────────────────┘
```

The final evaluation compares network behaviour under different
conditions, including:

```text
Normal traffic
      ↓
Congestion without QoS
      ↓
Static QoS
      ↓
Dynamic QoS
      ↓
Performance and fairness comparison
```

The objective is to demonstrate that an SDN-controlled QoS policy can
dynamically manage competing UDP traffic flows and improve the performance
of selected traffic classes under network congestion.
