
"""Dynamic QoS controller for the Mininet surge experiment."""

from __future__ import annotations

import csv
import struct
import time
from pathlib import Path

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (
    CONFIG_DISPATCHER,
    MAIN_DISPATCHER,
    DEAD_DISPATCHER,
    set_ev_cls,
)
from ryu.lib import hub
from ryu.lib.packet import packet, ethernet, ipv4, udp
from ryu.ofproto import ofproto_v1_3


class DynamicQosController(app_manager.RyuApp):

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    SWITCH_ID = 1
    BOTTLENECK_PORT = 4
    BOTTLENECK_MBPS = 5.0

    CONGESTION_THRESHOLD = 0.85
    RECOVERY_THRESHOLD = 0.65

    CONGESTION_SAMPLES = 2
    RECOVERY_SAMPLES = 3

    SAMPLE_INTERVAL = 1
    FLOW_IDLE_TIMEOUT = 1

    FLOW_PORTS = {
        1: 5001,
        2: 5002,
        3: 5003,
        4: 5004,
    }

    FLOW_NAMES = {
        1: "high",
        2: "medium",
        3: "low",
        4: "background",
    }

    NORMAL_POLICY = {
        1: 1,
        2: 2,
        3: 3,
        4: 4,
    }

    # Congestion policy: temporarily prioritize HIGH over MEDIUM.
    CONGESTED_POLICY = {
        1: 2,
        2: 1,
        3: 3,
        4: 4,
    }

    DATA_FORMAT = "!IQQq"
    CONTROL_FORMAT = "!4sBQII"
    CONTROL_MAGIC = b"QOS1"
    START_MESSAGE = 1

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.datapaths = {}
        self.mac_table = {}

        self.active_run_id = None
        self.policy = self.NORMAL_POLICY.copy()
        self.congested = False

        self.congestion_count = 0
        self.recovery_count = 0

        self.previous_tx_bytes = None
        self.previous_stats_time = None

        self.experiments_dir = (
            Path(__file__).resolve().parents[1] / "experiments" / "dynamic"
        )
        self.experiments_dir.mkdir(parents=True, exist_ok=True)

        self.monitor_thread = hub.spawn(self._monitor)

        self.logger.info("Dynamic QoS controller initialized")

    # ---------------------------------------------------------
    # Switch registration
    # ---------------------------------------------------------

    @set_ev_cls(ofp_event.EventOFPStateChange,
                [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def state_change_handler(self, ev):
        datapath = ev.datapath

        if ev.state == MAIN_DISPATCHER:
            self.datapaths[datapath.id] = datapath
            self.logger.info("Switch %s connected", datapath.id)

        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(datapath.id, None)
            self.logger.info("Switch %s disconnected", datapath.id)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        match = parser.OFPMatch()
        actions = [
            parser.OFPActionOutput(
                ofproto.OFPP_CONTROLLER,
                ofproto.OFPCML_NO_BUFFER
            )
        ]

        instructions = [
            parser.OFPInstructionActions(
                ofproto.OFPIT_APPLY_ACTIONS,
                actions
            )
        ]

        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=0,
            match=match,
            instructions=instructions
        )

        datapath.send_msg(mod)

    # ---------------------------------------------------------
    # Run ID and policy logging
    # ---------------------------------------------------------

    def _policy_path(self, run_id):
        return self.experiments_dir / f"policy_{run_id}.csv"

    def _start_run(self, run_id):
        if run_id == self.active_run_id:
            return

        self.logger.info("Active experiment run: %s", run_id)

        self.active_run_id = run_id
        self.congested = False
        self.congestion_count = 0
        self.recovery_count = 0

        self.policy = self.NORMAL_POLICY.copy()
        self._apply_policy()

        path = self._policy_path(run_id)

        if not path.exists():
            with path.open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow([
                    "ts_ns",
                    "flow_id",
                    "old_queue",
                    "new_queue",
                    "reason",
                ])

    def _log_policy_change(self, flow_id, old_queue, new_queue, reason):
        if self.active_run_id is None:
            return

        path = self._policy_path(self.active_run_id)

        with path.open("a", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                time.monotonic_ns(),
                self.FLOW_NAMES[flow_id],
                old_queue,
                new_queue,
                reason,
            ])

    # ---------------------------------------------------------
    # Packet parsing
    # ---------------------------------------------------------

    def _extract_run_id(self, payload):
        if len(payload) >= struct.calcsize(self.CONTROL_FORMAT):
            try:
                magic, message_type, run_id, flow_id, count = (
                    struct.unpack(
                        self.CONTROL_FORMAT,
                        payload[:struct.calcsize(self.CONTROL_FORMAT)]
                    )
                )

                if (
                    magic == self.CONTROL_MAGIC
                    and message_type == self.START_MESSAGE
                    and flow_id in self.FLOW_PORTS
                ):
                    return run_id

            except struct.error:
                pass

        if len(payload) >= struct.calcsize(self.DATA_FORMAT):
            try:
                flow_id, sequence, run_id, timestamp = struct.unpack(
                    self.DATA_FORMAT,
                    payload[:struct.calcsize(self.DATA_FORMAT)]
                )

                if flow_id in self.FLOW_PORTS:
                    return run_id

            except struct.error:
                pass

        return None

    # ---------------------------------------------------------
    # Learning switch and queue forwarding
    # ---------------------------------------------------------

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        dpid = datapath.id
        in_port = msg.match["in_port"]

        pkt = packet.Packet(msg.data)

        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None:
            return

        if eth.ethertype == 0x88cc:
            return

        self.mac_table.setdefault(dpid, {})
        self.mac_table[dpid][eth.src] = in_port

        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        udp_pkt = pkt.get_protocol(udp.udp)

        flow_id = None
        run_id = None

        if ip_pkt and udp_pkt:
            for fid, port in self.FLOW_PORTS.items():
                if udp_pkt.dst_port == port:
                    flow_id = fid
                    break

            if pkt.protocols:
                payload = pkt.protocols[-1]

                if isinstance(payload, (bytes, bytearray)):
                    run_id = self._extract_run_id(bytes(payload))

            if run_id is not None:
                self._start_run(run_id)

        dst = eth.dst
        out_port = self.mac_table[dpid].get(
            dst,
            ofproto.OFPP_FLOOD
        )

        actions = []

        if (
            dpid == self.SWITCH_ID
            and out_port == self.BOTTLENECK_PORT
            and flow_id is not None
        ):
            queue_id = self.policy[flow_id]

            actions.append(parser.OFPActionSetQueue(queue_id))

            self._install_queue_flow(
                datapath,
                udp_pkt.dst_port,
                queue_id
            )

        actions.append(parser.OFPActionOutput(out_port))

        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(
                in_port=in_port,
                eth_dst=dst
            )

            instructions = [
                parser.OFPInstructionActions(
                    ofproto.OFPIT_APPLY_ACTIONS,
                    actions
                )
            ]

            mod = parser.OFPFlowMod(
                datapath=datapath,
                priority=1,
                idle_timeout=self.FLOW_IDLE_TIMEOUT,
                match=match,
                instructions=instructions
            )

            datapath.send_msg(mod)

        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data
        )

        datapath.send_msg(out)

    def _install_queue_flow(self, datapath, dst_port, queue_id):
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        match = parser.OFPMatch(
            eth_type=0x0800,
            ip_proto=17,
            udp_dst=dst_port
        )

        actions = [
            parser.OFPActionSetQueue(queue_id),
            parser.OFPActionOutput(self.BOTTLENECK_PORT),
        ]

        instructions = [
            parser.OFPInstructionActions(
                ofproto.OFPIT_APPLY_ACTIONS,
                actions
            )
        ]

        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=10,
            idle_timeout=self.FLOW_IDLE_TIMEOUT,
            match=match,
            instructions=instructions
        )

        datapath.send_msg(mod)

    # ---------------------------------------------------------
    # Apply policy to the bottleneck switch
    # ---------------------------------------------------------

    def _apply_policy(self):
        datapath = self.datapaths.get(self.SWITCH_ID)

        if datapath is None:
            return

        for flow_id, dst_port in self.FLOW_PORTS.items():
            self._install_queue_flow(
                datapath,
                dst_port,
                self.policy[flow_id]
            )

    def _change_policy(self, new_policy, reason):
        if new_policy == self.policy:
            return

        old_policy = self.policy.copy()
        self.policy = new_policy.copy()

        self._apply_policy()

        for flow_id in self.FLOW_PORTS:
            old_queue = old_policy[flow_id]
            new_queue = self.policy[flow_id]

            if old_queue != new_queue:
                self._log_policy_change(
                    flow_id,
                    old_queue,
                    new_queue,
                    reason
                )

                self.logger.info(
                    "%s: queue %s -> %s (%s)",
                    self.FLOW_NAMES[flow_id],
                    old_queue,
                    new_queue,
                    reason
                )

    # ---------------------------------------------------------
    # Port statistics monitoring
    # ---------------------------------------------------------

    def _monitor(self):
        while True:
            datapath = self.datapaths.get(self.SWITCH_ID)

            if datapath is not None:
                parser = datapath.ofproto_parser

                request = parser.OFPPortStatsRequest(
                    datapath,
                    0,
                    self.BOTTLENECK_PORT
                )

                datapath.send_msg(request)

            hub.sleep(self.SAMPLE_INTERVAL)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        msg = ev.msg

        if msg.datapath.id != self.SWITCH_ID:
            return

        now = time.monotonic_ns()

        for stat in msg.body:
            if stat.port_no != self.BOTTLENECK_PORT:
                continue

            tx_bytes = stat.tx_bytes

            if self.previous_tx_bytes is None:
                self.previous_tx_bytes = tx_bytes
                self.previous_stats_time = now
                return

            elapsed = (
                now - self.previous_stats_time
            ) / 1_000_000_000

            if elapsed <= 0:
                return

            if tx_bytes < self.previous_tx_bytes:
                self.previous_tx_bytes = tx_bytes
                self.previous_stats_time = now
                return

            byte_delta = tx_bytes - self.previous_tx_bytes

            rate_mbps = (
                byte_delta * 8 / elapsed
            ) / 1_000_000

            utilization = rate_mbps / self.BOTTLENECK_MBPS

            self.logger.info(
                "Bottleneck TX rate: %.2f Mbps (%.1f%%)",
                rate_mbps,
                utilization * 100
            )

            self.previous_tx_bytes = tx_bytes
            self.previous_stats_time = now

            self._update_congestion(utilization)

    def _update_congestion(self, utilization):
        if not self.congested:
            if utilization >= self.CONGESTION_THRESHOLD:
                self.congestion_count += 1
            else:
                self.congestion_count = 0

            if self.congestion_count >= self.CONGESTION_SAMPLES:
                self.congested = True
                self.congestion_count = 0
                self.recovery_count = 0

                self._change_policy(
                    self.CONGESTED_POLICY,
                    "sustained congestion detected"
                )

        else:
            if utilization <= self.RECOVERY_THRESHOLD:
                self.recovery_count += 1
            else:
                self.recovery_count = 0

            if self.recovery_count >= self.RECOVERY_SAMPLES:
                self.congested = False
                self.recovery_count = 0
                self.congestion_count = 0

                self._change_policy(
                    self.NORMAL_POLICY,
                    "bottleneck utilization recovered"
                )

