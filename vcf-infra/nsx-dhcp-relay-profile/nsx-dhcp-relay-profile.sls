{% from "nsx-dhcp-relay-profile/map.jinja" import cfg with context %}

{{ cfg.dhcp_relay.name }}:
  vcf_nsx_dhcp.relay_present:
    - server_addresses: {{ cfg.dhcp_relay.server_addresses }}
