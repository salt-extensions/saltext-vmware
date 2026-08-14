{% from "nsx-dhcp-server-profile/map.jinja" import cfg with context %}

{{ cfg.dhcp_server.name }}:
  vcf_nsx_dhcp.server_present:
    - server_addresses: {{ cfg.dhcp_server.server_addresses }}
    - lease_time: {{ cfg.dhcp_server.lease_time }}
