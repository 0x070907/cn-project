# Static QoS controller: the same learning switch as baseline_controller.py, plus one extra rule type.

# Packets that get no queue rule (ARP, ping, replies, ...) use the default queue 0 of that port.

# Actions used: 
# SetQueue(N) only tags the packet "use queue N"; 
# Output(port) then sends it out of that port, through the tagged queue. (

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet, ipv4, udp, ether_types

# Queues exist only on s1's port towards s2 (s1-eth4 = OpenFlow port 4).
# s2 and the other ports have no queues, so the queue rule must not be used there.
BOTTLENECK_DPID = 1
BOTTLENECK_PORT = 4


class QosController(app_manager.RyuApp):
    # speak OpenFlow 1.3 with the switches
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(QosController, self).__init__(*args, **kwargs)
        # mac_to_port[dpid][mac address] = port that mac was seen on (learned from packets)
        self.mac_to_port = {}

    # runs once for every switch that connects (s1 = dpid 1, s2 = dpid 2)
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        self.logger.info('switch connected: dpid=%s', datapath.id)

        # when a pkt matches many rules in switch,the highest priority means
        #prio 0 = catch-all ; prio 1 = normal forwarding learned from traffic ; prio 10 = qos rules,it beats priority 1,so the traffic of each class goes through its own queue.

        # priority-0 catch-all: anything without another rule goes to the controller
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                         ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

    # sends one rule (match + actions + priority) to a switch
    def add_flow(self, datapath, priority, match, actions, buffer_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                             actions)]
        self.logger.info('dpid=%s install flow priority=%s match=%s',
                         datapath.id, priority, match)
        mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                match=match, instructions=inst)
        datapath.send_msg(mod)

    # runs for every packet a switch has no rule for (the first packets of each flow)
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        # learn: the sender's MAC address is reachable through the port the packet came in on
        dst = eth.dst
        src = eth.src
        dpid = datapath.id
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port

        # known destination : its port; unknown : FLOOD (every port)
        out_port = self.mac_to_port[dpid].get(dst, ofproto.OFPP_FLOOD)

        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        udp_pkt = pkt.get_protocol(udp.udp)

        # only UDP packets can get a queue;classify them by destination port (the flow's class)
        actions = []
        if ip_pkt and udp_pkt:
            dst_port = udp_pkt.dst_port
            queue_id = None

            if dst_port == 5001: queue_id = 1
            elif dst_port == 5002: queue_id = 2
            elif dst_port == 5003: queue_id = 3
            elif dst_port == 5004: queue_id = 4

            # queue rule only on s1 towards s2: that is the only port that has queues
            if (queue_id is not None and out_port != ofproto.OFPP_FLOOD
                    and datapath.id == BOTTLENECK_DPID
                    and out_port == BOTTLENECK_PORT):
                actions = [
                    parser.OFPActionSetQueue(queue_id),  
                    parser.OFPActionOutput(out_port)
                ]

                # match every IPv4/UDP packet with this destination port; priority 10 beats normal forwarding
                match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP,
                                        ip_proto=17, udp_dst=dst_port)
                self.add_flow(datapath, 10, match, actions)

        # no queue rule used : plain forwarding (this is what s2 and all non-UDP packets get)
        if not actions:
            actions = [parser.OFPActionOutput(out_port)]
            if out_port != ofproto.OFPP_FLOOD:
                # normal forwarding rule, so later packets skip the controller
                match = parser.OFPMatch(in_port=in_port, eth_dst=dst,
                                        eth_src=src)
                self.add_flow(datapath, 1, match, actions)

        # also forward this packet now (the rule only helps the packets after it)
        data = msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)