"""Small stand-alone Python programs executed *inside* Mininet hosts.

They are started with ``python -m netguard.traffic.workers.<name>`` and only
use the standard library, except the DNS client which crafts and parses
packets with Scapy.
"""
