
import struct

flow_ids = {
    "high": 1,
    "medium": 2,
    "low": 3,
    "background": 4
}

flow_ports = {
    1: 5001,
    2: 5002,
    3: 5003,
    4: 5004
}

""" the data header contains flow id, sequence number, run id and timestamp
 ! means network byte order (big-endian)
 I is a 4 byte unsigned integer for flow id
 Q is an 8 byte unsigned integer for sequence number
 Q is an 8 byte unsigned integer for run id
 q is an 8 byte signed integer for timestamp"""

data_format = "!IQQq"
data_header_size = struct.calcsize(data_format)

# control messages use a separate format from data packets
# ! means network byte order (big-endian)
# 4s is 4 bytes for the protocol identifier
# B is a 1 byte unsigned integer for message type
# Q is an 8 byte unsigned integer for run id
# I is a 4 byte unsigned integer for flow id
# I is a 4 byte unsigned integer for expected packet count
control_format = "!4sBQII"
control_header_size = struct.calcsize(control_format)
magic = b"QOS1"

start_message = 1
end_message = 2
start_ack = 3
end_ack = 4


def pack_data(flow_id, sequence_number, run_id, timestamp, payload):
    header = struct.pack(
        data_format,
        flow_id,
        sequence_number,
        run_id,
        timestamp
    )
    return header + payload


def unpack_data(packet):
    if len(packet) < data_header_size:
        raise ValueError("packet is too small")

    flow_id, sequence_number, run_id, timestamp = struct.unpack(
        data_format,
        packet[:data_header_size]
    )

    payload = packet[data_header_size:]

    return flow_id, sequence_number, run_id, timestamp, payload


def pack_control(message_type, run_id, flow_id, count):
    return struct.pack(
        control_format,
        magic,
        message_type,
        run_id,
        flow_id,
        count
    )


def unpack_control(packet):
    if len(packet) != control_header_size:
        return None

    try:
        packet_magic, message_type, run_id, flow_id, count = (
            struct.unpack(control_format, packet)
        )
    except struct.error:
        return None

    if packet_magic != magic:
        return None

    if message_type not in (
        start_message,
        end_message,
        start_ack,
        end_ack
    ):
        return None

    if flow_id not in flow_ports:
        return None

    return message_type, run_id, flow_id, count

