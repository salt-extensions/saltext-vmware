{% from "nsx-dns-servers/map.jinja" import cfg with context %}

nsx-dns-servers:
  vcf_nsx_dns_servers.dns_servers:
    - servers: {{ cfg.dns.servers }}
