"""Project topology:   h1, h2, h3 --> s1 ----(5 Mbps)--- s2 --> h4"""
# this file builds the network(hosts,switches,cables,controller)

from __future__ import annotations

import argparse

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSBridge, OVSSwitch, RemoteController


BOTTLENECK_MBPS: int = 5       # speed of the s1-s2 cable
QUEUE_PACKETS: int = 100       # waiting line in front of that cable
CONTROLLER_IP: str = "127.0.0.1"
CONTROLLER_PORT: int = 6653    # the port Ryu listens on by default


def build(mode: str = "none", cap: str = "tclink") -> Mininet:  # [--mode none|remote] [--cap tclink|none]
  
    if mode == "none":  #no controller mode,so switches are basically bridges(OVSBridge)

        # link=TCLink means links are allowed to have bw/delay/loss settings.
        net = Mininet(
                      switch=OVSBridge, 
                      link=TCLink, 
                      controller=None
                     )
        switch_args: dict[str, str] = {}

    else: # mode = "remote",so switches are controlled by Ryu 

        net = Mininet(
                      switch=OVSSwitch, 
                      link=TCLink, 
                      controller=None
                     )
        
        net.addController("c0", 
                          controller=RemoteController,
                          ip=CONTROLLER_IP, 
                          port=CONTROLLER_PORT
                          )
        
        switch_args = {"protocols": "OpenFlow13"} # Ryu and Open vSwitch(OVS) talk through a protocol called OpenFlow 1.3


    # the four fake computers, with fixed IP addresses
    h1 = net.addHost("h1", ip="10.0.0.1/24")
    h2 = net.addHost("h2", ip="10.0.0.2/24")
    h3 = net.addHost("h3", ip="10.0.0.3/24")
    h4 = net.addHost("h4", ip="10.0.0.4/24")

    # the two fake switches
    s1 = net.addSwitch("s1", **switch_args)
    s2 = net.addSwitch("s2", **switch_args)

    #cables from the senders to s1
    #No bw given hence effectively unlimited(bcz they aren't tclink)
    net.addLink(h1, s1)  
    net.addLink(h2, s1)
    net.addLink(h3, s1)


    if cap == "tclink": # A link only gets a limit if we pass bw to addLink,so the only slow part of the network is the s1-s2 cable

        # bw=5 means at most 5 Mbps. max_queue_size=100 means at most 100 packets may wait in line(the queue in front of the cable)
        # when the line is full, new packets are dropped and measured as loss
        net.addLink(s1, s2, bw=BOTTLENECK_MBPS, max_queue_size=QUEUE_PACKETS)
    else:
        net.addLink(s1, s2)

    # cable from s2 to the receiver (no limit)
    net.addLink(s2, h4)
    return net

# this will never run unless you run it independently like sudo python3 mininet/topology.py
# bcz we use  only the build() of this file in run_experiment.py
if __name__ == "__main__":
    parser = argparse.ArgumentParser()   # [--mode none|remote] [--cap tclink|none]
    parser.add_argument("--mode", choices=["none", "remote"], default="none")
    parser.add_argument("--cap", choices=["tclink", "none"], default="tclink")
    args = parser.parse_args()

    setLogLevel("info")     
    net = build(args.mode, args.cap)
    net.start()       # actually creates the fake computers, switches and cables
    CLI(net)       # gives you the mininet> prompt until you type exit
    net.stop()      # removes everything again

