{% from "nsx-ntp-servers/map.jinja" import cfg with context %}

nsx-ntp-servers:
  vcf_nsx_ntp_servers.ntp_servers:
    - servers: {{ cfg.ntp.servers }}
