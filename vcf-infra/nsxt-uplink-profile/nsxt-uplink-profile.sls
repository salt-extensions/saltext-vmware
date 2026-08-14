{% from "nsxt-uplink-profile/map.jinja" import cfg with context %}

{{ cfg.uplink_profile.name }}:
  vcf_nsx_uplink_profile.present:
    - teaming: {{ cfg.uplink_profile.teaming }}
    - mtu: {{ cfg.uplink_profile.mtu }}
