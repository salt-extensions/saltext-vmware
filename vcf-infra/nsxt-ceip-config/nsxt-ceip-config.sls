{% from "nsxt-ceip-config/map.jinja" import cfg with context %}

nsxt-ceip-config:
  vcf_nsx_telemetry.optin_set:
    - optin: {{ cfg.ceip.optin }}
