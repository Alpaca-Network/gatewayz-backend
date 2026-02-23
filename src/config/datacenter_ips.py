"""
Datacenter IP Configuration

This module contains known datacenter, cloud provider, and hosting company
IP ranges and ASNs for improved IP classification and rate limiting.

Sources:
- ARIN, RIPE, APNIC, LACNIC, AFRINIC (Regional Internet Registries)
- Major cloud provider documentation
- BGP route tables and ASN databases
"""

import os
from ipaddress import IPv4Address, IPv4Network, ip_address, ip_network

# ==================== Autonomous System Numbers (ASNs) ====================
# Map of well-known datacenter and cloud provider ASNs

DATACENTER_ASNS = {
    # Amazon Web Services (AWS)
    16509: "Amazon AWS",
    14618: "Amazon AWS",
    8987: "Amazon AWS",
    # Google Cloud Platform (GCP)
    15169: "Google Cloud",
    19527: "Google Cloud",
    36040: "Google Cloud",
    36384: "Google Cloud",
    36385: "Google Cloud",
    36492: "Google Cloud",
    139070: "Google Cloud",
    139190: "Google Cloud",
    # Microsoft Azure
    8075: "Microsoft Azure",
    12076: "Microsoft Azure",
    # Digital Ocean
    14061: "DigitalOcean",
    393406: "DigitalOcean",
    # Linode (Akamai)
    63949: "Linode",
    # Vultr
    20473: "Vultr",
    # Hetzner
    24940: "Hetzner",
    # OVHcloud
    16276: "OVHcloud",
    35540: "OVHcloud",
    # IBM Cloud / SoftLayer
    36351: "IBM Cloud",
    # Oracle Cloud Infrastructure
    31898: "Oracle Cloud",
    20940: "Oracle Cloud",
    # Alibaba Cloud
    37963: "Alibaba Cloud",
    45102: "Alibaba Cloud",
    # Tencent Cloud
    45090: "Tencent Cloud",
    132203: "Tencent Cloud",
    # Huawei Cloud
    136907: "Huawei Cloud Hong Kong",
    55990: "Huawei Cloud",
    # Cloudflare
    13335: "Cloudflare",
    209242: "Cloudflare",
    # Fastly
    54113: "Fastly",
    # StackPath / MaxCDN
    33438: "StackPath",
    # Rackspace
    27357: "Rackspace",
    33070: "Rackspace",
    # CenturyLink / Lumen
    3356: "Lumen / CenturyLink",
    # Scaleway
    12876: "Scaleway",
    51167: "Scaleway",
    # Contabo
    51167: "Contabo",  # noqa: F601
    # Data centers / hosting providers
    63949: "Linode",  # noqa: F601
    46475: "Corero / Limestone Networks",
    # VPN / Proxy providers
    30722: "VPN",
    396986: "VPN/Proxy Provider",
    # Render
    200645: "Render",
    # Railway (Cloud Run Platform)
    46887: "Railway",
    60068: "Railway CDN",
    # Vercel
    64272: "Vercel Edge Network",
    13335: "Vercel (via Cloudflare)",  # noqa: F601
    # Heroku (Salesforce)
    33517: "Heroku",
    # Fly.io
    200092: "Fly.io",
    # Netlify
    36459: "Netlify",
}

# ==================== CIDR Ranges ====================
# Major cloud provider IP ranges
# Note: These are representative samples. In production, use IP2Location, MaxMind,
# or fetch from cloud provider APIs (AWS: ip-ranges.json, Azure: ServiceTags, GCP: _cloud-netblocks)

# AWS IP Ranges (sample - AWS has 7000+ ranges)
AWS_CIDRS = [
    "3.0.0.0/8",         # AWS Global
    "13.32.0.0/15",      # AWS Global
    "13.34.0.0/16",      # AWS Global
    "13.35.0.0/16",      # AWS CloudFront
    "13.48.0.0/13",      # AWS EC2 eu-north-1
    "15.152.0.0/16",     # AWS Global
    "15.230.0.0/16",     # AWS EC2 sa-east-1
    "18.130.0.0/16",     # AWS EC2 eu-west-2
    "18.144.0.0/15",     # AWS EC2 us-west-1
    "18.184.0.0/15",     # AWS EC2 eu-central-1
    "18.208.0.0/13",     # AWS EC2 us-east-1
    "34.192.0.0/12",     # AWS EC2 us-east-1
    "35.72.0.0/13",      # AWS Global
    "52.0.0.0/11",       # AWS Global
    "52.32.0.0/14",      # AWS EC2 us-west-2
    "52.56.0.0/14",      # AWS EC2 eu-west-1
    "52.192.0.0/12",     # AWS EC2 ap-northeast-1
    "54.64.0.0/14",      # AWS EC2 ap-northeast-1
    "54.144.0.0/13",     # AWS EC2 us-east-1
    "99.77.0.0/16",      # AWS CloudFront
   "205.251.192.0/19",  # AWS CloudFront
]

# Google Cloud IP Ranges (sample)
GCP_CIDRS = [
    "8.34.208.0/20",     # GCP
    "8.35.192.0/20",     # GCP
    "35.184.0.0/13",     # GCP
    "35.192.0.0/12",     # GCP
    "35.208.0.0/12",     # GCP
    "104.154.0.0/15",    # GCP
    "104.196.0.0/14",    # GCP
    "107.167.160.0/19",  # GCP
    "107.178.192.0/18",  # GCP
    "130.211.0.0/16",    # GCP
    "146.148.0.0/17",    # GCP
    "162.216.148.0/22",  # GCP
    "162.222.176.0/21",  # GCP
    "173.255.112.0/20",  # GCP
]

# Microsoft Azure IP Ranges (sample)
AZURE_CIDRS = [
    "13.64.0.0/11",      # Azure
    "13.96.0.0/13",      # Azure
    "13.104.0.0/14",     # Azure
    "20.33.0.0/16",      # Azure
    "20.34.0.0/15",      # Azure
    "20.36.0.0/14",      # Azure
    "20.40.0.0/13",      # Azure
    "20.48.0.0/12",      # Azure
    "20.64.0.0/10",      # Azure
    "40.64.0.0/10",      # Azure
    "51.4.0.0/15",       # Azure UK
    "51.103.0.0/16",     # Azure UK
    "52.136.0.0/13",     # Azure
    "52.224.0.0/11",     # Azure
]

# Digital Ocean IP Ranges
DIGITALOCEAN_CIDRS = [
    "104.131.0.0/16",    # DigitalOcean NYC
    "104.236.0.0/16",    # DigitalOcean NYC
    "107.170.0.0/16",    # DigitalOcean NYC
    "138.68.0.0/16",     # DigitalOcean LON
    "139.59.0.0/16",     # DigitalOcean BLR
    "159.65.0.0/16",     # DigitalOcean NYC/SFO
    "161.35.0.0/16",     # DigitalOcean NYC
    "165.227.0.0/16",    # DigitalOcean NYC/LON
    "167.99.0.0/16",     # DigitalOcean NYC/SFO
    "167.172.0.0/16",    # DigitalOcean NYC/SFO
    "178.62.0.0/16",     # DigitalOcean LON/AMS
    "188.166.0.0/16",    # DigitalOcean AMS/LON
    "206.81.0.0/16",     # DigitalOcean NYC
]

# Linode IP Ranges
LINODE_CIDRS = [
    "45.33.0.0/16",      # Linode
    "45.56.0.0/16",      # Linode
    "45.79.0.0/16",      # Linode
    "50.116.0.0/16",     # Linode
    "66.175.208.0/20",   # Linode
    "69.164.192.0/20",   # Linode
    "74.207.224.0/20",   # Linode
    "85.159.208.0/20",   # Linode
    "96.126.96.0/19",    # Linode
    "139.144.0.0/16",    # Linode
    "172.104.0.0/15",    # Linode
]

# Vultr IP Ranges
VULTR_CIDRS = [
    "45.32.0.0/16",      # Vultr
    "45.63.0.0/16",      # Vultr
    "45.76.0.0/16",      # Vultr
    "45.77.0.0/16",      # Vultr
    "64.98.128.0/17",    # Vultr
    "66.42.32.0/20",     # Vultr
    "95.179.128.0/17",   # Vultr
    "104.156.224.0/19",  # Vultr
    "108.61.0.0/16",     # Vultr
    "140.82.0.0/16",     # Vultr
    "144.202.0.0/16",    # Vultr
    "149.28.0.0/16",     # Vultr
    "155.138.128.0/17",  # Vultr
    "207.148.0.0/18",    # Vultr
    "209.222.0.0/17",    # Vultr
]

# Hetzner IP Ranges
HETZNER_CIDRS = [
    "5.9.0.0/16",        # Hetzner
    "46.4.0.0/16",       # Hetzner
    "78.46.0.0/15",      # Hetzner
    "88.198.0.0/16",     # Hetzner
    "88.99.0.0/16",      # Hetzner
    "94.130.0.0/16",     # Hetzner
    "95.216.0.0/16",     # Hetzner
    "116.202.0.0/16",    # Hetzner
    "135.181.0.0/16",    # Hetzner
    "136.243.0.0/16",    # Hetzner
    "138.201.0.0/16",    # Hetzner
    "144.76.0.0/16",     # Hetzner
    "148.251.0.0/16",    # Hetzner
    "157.90.0.0/16",     # Hetzner
    "159.69.0.0/16",     # Hetzner
    "168.119.0.0/16",    # Hetzner
    "176.9.0.0/16",      # Hetzner
]

# OVHcloud IP Ranges
OVH_CIDRS = [
    "5.39.0.0/16",       # OVH
    "5.135.0.0/16",      # OVH
    "37.59.0.0/16",      # OVH
    "51.68.0.0/16",      # OVH
    "51.75.0.0/16",      # OVH
    "51.77.0.0/16",      # OVH
    "51.79.0.0/16",      # OVH
    "51.81.0.0/16",      # OVH
    "51.83.0.0/16",      # OVH
    "51.89.0.0/16",      # OVH
    "51.91.0.0/16",      # OVH
    "54.36.0.0/16",      # OVH
    "54.37.0.0/16",      # OVH
    "54.38.0.0/16",      # OVH
    "54.39.0.0/16",      # OVH
    "137.74.0.0/16",     # OVH
    "139.99.0.0/16",     # OVH
    "141.94.0.0/16",     # OVH
    "141.95.0.0/16",     # OVH
    "144.217.0.0/16",    # OVH
    "146.59.0.0/16",      # OVH
    "147.135.0.0/16",    # OVH
    "148.113.0.0/16",    # OVH
    "151.80.0.0/16",     # OVH
    "152.228.128.0/17",  # OVH
    "167.114.0.0/17",    # OVH
    "178.32.0.0/15",     # OVH
    "185.15.68.0/22",    # OVH
    "188.165.0.0/16",    # OVH
    "192.95.0.0/16",     # OVH
    "193.70.0.0/16",     # OVH
    "195.154.0.0/16",    # OVH
    "198.27.64.0/18",    # OVH
    "198.50.128.0/17",   # OVH
]

# Huawei Cloud IP Ranges (including the IP from issue #1092)
HUAWEI_CIDRS = [
    "182.160.0.0/20",    # Huawei Cloud Hong Kong (includes 182.160.0.40)
    "119.8.0.0/16",      # Huawei Cloud
    "119.9.0.0/16",      # Huawei Cloud
]

# Alibaba Cloud IP Ranges
ALIBABA_CIDRS = [
    "47.88.0.0/16",      # Alibaba Cloud
    "47.89.0.0/16",      # Alibaba Cloud
    "47.90.0.0/16",      # Alibaba Cloud
    "47.91.0.0/16",      # Alibaba Cloud
    "47.92.0.0/16",      # Alibaba Cloud
    "47.93.0.0/16",      # Alibaba Cloud
    "47.94.0.0/16",      # Alibaba Cloud
    "47.95.0.0/16",      # Alibaba Cloud
]

# Cloudflare IP Ranges
CLOUDFLARE_CIDRS = [
    "103.21.244.0/22",   # Cloudflare
    "103.22.200.0/22",   # Cloudflare
    "103.31.4.0/22",     # Cloudflare
    "104.16.0.0/13",     # Cloudflare
    "104.24.0.0/14",     # Cloudflare
    "108.162.192.0/18",  # Cloudflare
    "131.0.72.0/22",     # Cloudflare
    "141.101.64.0/18",   # Cloudflare
    "162.158.0.0/15",    # Cloudflare
    "172.64.0.0/13",     # Cloudflare
    "173.245.48.0/20",   # Cloudflare
    "188.114.96.0/20",   # Cloudflare
    "190.93.240.0/20",   # Cloudflare
    "197.234.240.0/22",  # Cloudflare
    "198.41.128.0/17",   # Cloudflare
]

# Combine all CIDR ranges
ALL_DATACENTER_CIDRS = (
    AWS_CIDRS +
    GCP_CIDRS +
    AZURE_CIDRS +
    DIGITALOCEAN_CIDRS +
    LINODE_CIDRS +
    VULTR_CIDRS +
    HETZNER_CIDRS +
    OVH_CIDRS +
    HUAWEI_CIDRS +
    ALIBABA_CIDRS +
    CLOUDFLARE_CIDRS
)

# ==================== Configuration ====================

# Allow additional CIDRs via environment variable
ADDITIONAL_DATACENTER_CIDRS = os.getenv("ADDITIONAL_DATACENTER_CIDRS", "").split(",")
ADDITIONAL_DATACENTER_CIDRS = [cidr.strip() for cidr in ADDITIONAL_DATACENTER_CIDRS if cidr.strip()]

# Combine with environment-provided CIDRs
ALL_DATACENTER_CIDRS.extend(ADDITIONAL_DATACENTER_CIDRS)

# Convert CIDR strings to IPv4Network objects for efficient lookups
_DATACENTER_NETWORKS = None


def get_datacenter_networks() -> list[IPv4Network]:
    """
    Get list of IPv4Network objects for datacenter CIDRs.
    Cached after first call for performance.
    """
    global _DATACENTER_NETWORKS
    if _DATACENTER_NETWORKS is None:
        _DATACENTER_NETWORKS = []
        for cidr in ALL_DATACENTER_CIDRS:
            try:
                network = ip_network(cidr, strict=False)
                if isinstance(network, IPv4Network):
                    _DATACENTER_NETWORKS.append(network)
            except ValueError:
                continue  # Skip invalid CIDR
    return _DATACENTER_NETWORKS


def is_datacenter_ip(ip: str) -> bool:
    """
    Check if an IP address belongs to a known datacenter/cloud provider.

    Args:
        ip: IP address string (e.g., "182.160.0.40")

    Returns:
        True if IP is in a known datacenter range, False otherwise

    Example:
        >>> is_datacenter_ip("182.160.0.40")
        True  # Huawei Cloud Hong Kong
        >>> is_datacenter_ip("1.2.3.4")
        False  # Not a datacenter IP
    """
    try:
        ip_obj = ip_address(ip)
        if not isinstance(ip_obj, IPv4Address):
            return False  # Only support IPv4 for now

        networks = get_datacenter_networks()
        for network in networks:
            if ip_obj in network:
                return True
        return False
    except ValueError:
        return False  # Invalid IP address


def is_datacenter_asn(asn: int) -> bool:
    """
    Check if an ASN belongs to a known datacenter/cloud provider.

    Args:
        asn: Autonomous System Number

    Returns:
        True if ASN is a known datacenter, False otherwise

    Example:
        >>> is_datacenter_asn(136907)
        True  # Huawei Cloud Hong Kong
        >>> is_datacenter_asn(12345)
        False  # Not a datacenter ASN
    """
    return asn in DATACENTER_ASNS


def get_datacenter_name(asn: int) -> str | None:
    """
    Get the name of a datacenter/cloud provider by ASN.

    Args:
        asn: Autonomous System Number

    Returns:
        Name of the provider, or None if not found

    Example:
        >>> get_datacenter_name(136907)
        'Huawei Cloud Hong Kong'
    """
    return DATACENTER_ASNS.get(asn)
